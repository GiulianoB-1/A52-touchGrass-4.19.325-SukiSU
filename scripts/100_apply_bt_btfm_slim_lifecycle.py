#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "A52 BT-P3: BTFM SLIM channel lifecycle hardening"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1])
    slim_c = root / "drivers/bluetooth/btfm_slim.c"
    codec_c = root / "drivers/bluetooth/btfm_slim_codec.c"

    text = slim_c.read_text()
    codec = codec_c.read_text()

    if MARKER in text:
        print("BT-P3 already applied")
        return

    # 1) Propagate failed SLIM reads.  The vendor driver currently logs a
    # failed transaction but still returns success, allowing hw_init() to mark
    # the codec enabled with invalid register state.
    text = replace_once(
        text,
        """	if (ret)
		BTFMSLIM_ERR("failed (%d)", ret);

	for (i = 0; i < bytes; i++)
		BTFMSLIM_DBG("Read 0x%02x from reg 0x%x", ((uint8_t *)dest)[i],
			reg + i);

	return 0;
}
""",
        """	if (ret) {
		BTFMSLIM_ERR("failed (%d)", ret);
		return ret;
	}

	for (i = 0; i < bytes; i++)
		BTFMSLIM_DBG("Read 0x%02x from reg 0x%x", ((uint8_t *)dest)[i],
			reg + i);

	return 0;
}
""",
        "SLIM read error propagation",
    )

    # 2) Serialize the enabled-state check with the actual init transaction.
    # The original check occurs before io_lock, so two concurrent DAI startups
    # can both enter the expensive address/port initialization path.
    text = replace_once(
        text,
        """	if (btfmslim->enabled) {
		BTFMSLIM_DBG("Already enabled");
		return 0;
	}

	slim = btfmslim->slim_pgd;
	slim_ifd = &btfmslim->slim_ifd;

	mutex_lock(&btfmslim->io_lock);
""",
        """	slim = btfmslim->slim_pgd;
	slim_ifd = &btfmslim->slim_ifd;

	mutex_lock(&btfmslim->io_lock);
	if (btfmslim->enabled) {
		BTFMSLIM_DBG("Already enabled");
		mutex_unlock(&btfmslim->io_lock);
		return 0;
	}
""",
        "serialized hw init",
    )

    # Do not publish enabled=true if vendor register initialization failed.
    text = replace_once(
        text,
        """	/* Start vendor specific initialization and get port information */
	if (btfmslim->vendor_init)
		ret = btfmslim->vendor_init(btfmslim);

	/* Only when all registers read/write successfully, it set to
	 * enabled status
	 */
	btfmslim->enabled = 1;
error:
""",
        """	/* Start vendor specific initialization and get port information */
	if (btfmslim->vendor_init) {
		ret = btfmslim->vendor_init(btfmslim);
		if (ret) {
			BTFMSLIM_ERR("vendor init failed ret[%d]", ret);
			goto error;
		}
	}

	/* Publish enabled only after every SLIM transaction succeeded. */
	btfmslim->enabled = 1;
error:
""",
        "vendor init success gate",
    )

    # Same race exists on deinit: check state while holding the lifecycle lock.
    text = replace_once(
        text,
        """	if (!btfmslim->enabled) {
		BTFMSLIM_DBG("Already disabled");
		return 0;
	}
	mutex_lock(&btfmslim->io_lock);
	btfmslim->enabled = 0;
	mutex_unlock(&btfmslim->io_lock);
""",
        """	mutex_lock(&btfmslim->io_lock);
	if (!btfmslim->enabled) {
		BTFMSLIM_DBG("Already disabled");
		mutex_unlock(&btfmslim->io_lock);
		return 0;
	}
	btfmslim->enabled = 0;
	mutex_unlock(&btfmslim->io_lock);
""",
        "serialized hw deinit",
    )

    # 3) Make partial channel-enable failures transactional.  Vendor code can
    # leave a PGD port enabled if connect/activate fails, and worse, overwrites
    # the original error with the cleanup result.  Preserve the first failure
    # and force-disable every port enabled by this attempt.
    text = replace_once(
        text,
        """	int ret, i;
	struct slim_ch prop;
""",
        """	int ret, i, cleanup_ret, j;
	int enabled_ports = 0;
	struct slim_ch prop;
""",
        "enable cleanup declarations",
    )

    text = replace_once(
        text,
        """		if (btfmslim->vendor_port_en) {
			ret = btfmslim->vendor_port_en(btfmslim, ch->port,
					rxport, 1);
			if (ret < 0) {
				BTFMSLIM_ERR("vendor_port_en failed ret[%d]",
					ret);
				goto error;
			}
		}
""",
        """		if (btfmslim->vendor_port_en) {
			ret = btfmslim->vendor_port_en(btfmslim, ch->port,
					rxport, 1);
			if (ret < 0) {
				BTFMSLIM_ERR("vendor_port_en failed ret[%d]",
					ret);
				goto remove_channel;
			}
			enabled_ports++;
		}
""",
        "track enabled ports",
    )

    # Replace the cleanup tail.  Keep ret as the primary failure code.
    text = replace_once(
        text,
        """error:
	return ret;

remove_channel:
	/* Remove the channel immediately*/
	ret = slim_control_ch(btfmslim->slim_pgd, (grp ? ch->grph : ch->ch_hdl),
			SLIM_CH_REMOVE, true);
	if (ret < 0)
		BTFMSLIM_ERR("slim_control_ch failed ret[%d]", ret);

	return ret;
}
""",
        """error:
	return ret;

remove_channel:
	/* Remove the channel, but never overwrite the original failure. */
	cleanup_ret = slim_control_ch(btfmslim->slim_pgd,
			(grp ? chan->grph : chan->ch_hdl),
			SLIM_CH_REMOVE, true);
	if (cleanup_ret < 0)
		BTFMSLIM_ERR("cleanup slim_control_ch failed ret[%d]",
			cleanup_ret);

	/* A failed connect/activate must not leave the WCN SLIM port powered. */
	if (btfmslim->vendor_port_en) {
		for (j = 0; j < enabled_ports; j++) {
			cleanup_ret = btfmslim->vendor_port_en(btfmslim,
					(chan + j)->port, rxport, 0);
			if (cleanup_ret < 0)
				BTFMSLIM_ERR("cleanup vendor_port_en failed ret[%d]",
					cleanup_ret);
		}
	}

	return ret;
}
""",
        "transactional enable cleanup",
    )

    # 4) Teardown must always attempt the hardware port-disable path, even when
    # SLIM channel removal/disconnect itself fails.  This is the most important
    # power-leak protection in P3.
    start = text.find("int btfm_slim_disable_ch(struct btfmslim *btfmslim, struct btfmslim_ch *ch,")
    end = text.find("static int btfm_slim_get_logical_addr", start)
    if start < 0 or end < 0:
        raise SystemExit("disable_ch function boundaries not found")

    new_disable = '''int btfm_slim_disable_ch(struct btfmslim *btfmslim, struct btfmslim_ch *ch,
	uint8_t rxport, uint8_t grp, uint8_t nchan)
{
	int ret = 0, tmp_ret, i;
	struct btfmslim_ch *chan = ch;

	if (!btfmslim || !ch)
		return -EINVAL;

	BTFMSLIM_INFO("port:%d, grp: %d, ch->grph:0x%x, ch->ch_hdl:0x%x ",
		ch->port, grp, ch->grph, ch->ch_hdl);

	btfm_is_port_opening_delayed = false;

	/* For 44.1/88.2 KHz A2DP Rx, disconnect the port first. */
	if (rxport &&
		(btfmslim->sample_rate == 44100 ||
		 btfmslim->sample_rate == 88200)) {
		BTFMSLIM_DBG("disconnecting the ports, removing the channel");
		tmp_ret = slim_disconnect_ports(btfmslim->slim_pgd,
				&ch->port_hdl, 1);
		if (tmp_ret < 0) {
			BTFMSLIM_ERR("slim_disconnect_ports failed ret[%d]",
				tmp_ret);
			if (!ret)
				ret = tmp_ret;
		}
	}

	/* Remove the channel immediately. */
	tmp_ret = slim_control_ch(btfmslim->slim_pgd,
			(grp ? chan->grph : chan->ch_hdl),
			SLIM_CH_REMOVE, true);
	if (tmp_ret < 0) {
		BTFMSLIM_ERR("slim_control_ch failed ret[%d]", tmp_ret);
		if (!ret)
			ret = tmp_ret;

		if (btfmslim->sample_rate != 44100 &&
			btfmslim->sample_rate != 88200) {
			tmp_ret = slim_disconnect_ports(btfmslim->slim_pgd,
					&chan->port_hdl, 1);
			if (tmp_ret < 0) {
				BTFMSLIM_ERR("disconnect_ports failed ret[%d]",
					tmp_ret);
				if (!ret)
					ret = tmp_ret;
			}
		}
	}

	/*
	 * Always disable the hardware ports, even if the logical SLIM teardown
	 * failed.  This prevents a failed close from leaving a WCN port enabled.
	 */
	for (i = 0; i < nchan; i++) {
		if (!btfmslim->vendor_port_en)
			break;

		tmp_ret = btfmslim->vendor_port_en(btfmslim,
				(chan + i)->port, rxport, 0);
		if (tmp_ret < 0) {
			BTFMSLIM_ERR("vendor_port_en disable failed ret[%d]",
				tmp_ret);
			if (!ret)
				ret = tmp_ret;
		}
	}

	return ret;
}
'''
    text = text[:start] + new_disable + text[end:]

    # Keep the userspace mixer status aligned with the actual DAI lifecycle.
    codec = replace_once(
        codec,
        """	btfm_slim_disable_ch(btfmslim, ch, rxport, grp, nchan);
	btfm_slim_hw_deinit(btfmslim);
}
""",
        """	btfm_slim_disable_ch(btfmslim, ch, rxport, grp, nchan);
	btfm_slim_hw_deinit(btfmslim);
	bt_soc_enable_status = 0;
}
""",
        "codec shutdown status",
    )

    text += "\n/* " + MARKER + " */\n"
    slim_c.write_text(text)
    codec_c.write_text(codec)

    # Structural invariants for CI.
    final = slim_c.read_text()
    final_codec = codec_c.read_text()
    checks = [
        "return ret;\n\t}\n\n\tfor (i = 0; i < bytes; i++)",
        "vendor init failed ret[%d]",
        "enabled_ports++",
        "cleanup vendor_port_en failed",
        "Always disable the hardware ports",
        "vendor_port_en disable failed",
        MARKER,
    ]
    for needle in checks:
        if needle not in final:
            raise SystemExit(f"missing BT-P3 invariant: {needle}")

    if "bt_soc_enable_status = 0;" not in final_codec:
        raise SystemExit("codec shutdown status reset missing")

    print(f"patched {slim_c}")
    print(f"patched {codec_c}")
    print(MARKER)


if __name__ == "__main__":
    main()
