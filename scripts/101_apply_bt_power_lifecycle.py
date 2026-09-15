#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "A52 BT-P4: Bluetooth power-state and regulator rollback hardening"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1])
    path = root / "drivers/bluetooth/bluetooth-power.c"
    text = path.read_text()

    if MARKER in text:
        print("BT-P4 already applied")
        return

    text = replace_once(
        text,
        """#include <linux/clk.h>
#include <linux/uaccess.h>
""",
        """#include <linux/clk.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
""",
        "mutex include",
    )

    text = replace_once(
        text,
        """static int bt_major;
static int soc_id;
""",
        """static int bt_major;
static int soc_id;

/*
 * A52 BT-P4: rfkill and the vendor ioctl are two independent entry points
 * into the same WCN3990 regulator/GPIO sequence.  Serialize the actual
 * hardware transition and keep one canonical power state.
 */
static DEFINE_MUTEX(bt_power_lock);
""",
        "power mutex",
    )

    start = text.find("static int bt_vreg_enable(struct bt_power_vreg_data *vreg)")
    end = text.find("static int bt_vreg_unvote(struct bt_power_vreg_data *vreg)", start)
    if start < 0 or end < 0:
        raise SystemExit("bt_vreg_enable boundaries not found")

    new_enable = r'''static int bt_vreg_enable(struct bt_power_vreg_data *vreg)
{
	int rc = 0;
	int cleanup_rc;

	if (!vreg || !vreg->reg)
		return -EINVAL;

	/*
	 * Always restore the active voltage/load vote.  Retention mode keeps
	 * the regulator physically enabled but drops these votes, so gating
	 * this work only on is_enabled prevents a correct retention -> ON
	 * transition.
	 */
	if (vreg->set_voltage_sup) {
		rc = regulator_set_voltage(vreg->reg,
					vreg->low_vol_level,
					vreg->high_vol_level);
		if (rc < 0) {
			BT_PWR_ERR("vreg_set_vol(%s) failed rc=%d",
					vreg->name, rc);
			return rc;
		}
	}

	if (vreg->load_uA >= 0) {
		rc = regulator_set_load(vreg->reg, vreg->load_uA);
		if (rc < 0) {
			BT_PWR_ERR("vreg_set_load(%s) failed rc=%d",
					vreg->name, rc);
			if (vreg->set_voltage_sup) {
				cleanup_rc = regulator_set_voltage(vreg->reg, 0,
						vreg->high_vol_level);
				if (cleanup_rc < 0)
					BT_PWR_ERR("rollback vreg_set_vol(%s) failed rc=%d",
						vreg->name, cleanup_rc);
			}
			return rc;
		}
	}

	if (!vreg->is_enabled) {
		rc = regulator_enable(vreg->reg);
		if (rc < 0) {
			BT_PWR_ERR("regulator_enable(%s) failed rc=%d",
					vreg->name, rc);

			/* Roll back every vote made by this enable attempt. */
			if (vreg->load_uA >= 0) {
				cleanup_rc = regulator_set_load(vreg->reg, 0);
				if (cleanup_rc < 0)
					BT_PWR_ERR("rollback vreg_set_load(%s) failed rc=%d",
						vreg->name, cleanup_rc);
			}
			if (vreg->set_voltage_sup) {
				cleanup_rc = regulator_set_voltage(vreg->reg, 0,
						vreg->high_vol_level);
				if (cleanup_rc < 0)
					BT_PWR_ERR("rollback vreg_set_vol(%s) failed rc=%d",
						vreg->name, cleanup_rc);
			}
			return rc;
		}
		vreg->is_enabled = true;
	}

	BT_PWR_DBG("vreg active for : %s", vreg->name);
	return 0;
}

'''
    text = text[:start] + new_enable + text[end:]

    start = text.find("static int bt_vreg_unvote(struct bt_power_vreg_data *vreg)")
    end = text.find("static int bt_vreg_disable(struct bt_power_vreg_data *vreg)", start)
    if start < 0 or end < 0:
        raise SystemExit("bt_vreg_unvote boundaries not found")

    new_unvote = r'''static int bt_vreg_unvote(struct bt_power_vreg_data *vreg)
{
	int rc = 0;
	int tmp;

	if (!vreg || !vreg->reg)
		return 0;

	if (!vreg->is_enabled)
		return 0;

	/*
	 * Retention is best-effort per vote: never skip clearing the load vote
	 * merely because the voltage vote failed (or vice versa).  Preserve the
	 * first error so userspace can retry while still minimizing the vote.
	 */
	if (vreg->set_voltage_sup) {
		tmp = regulator_set_voltage(vreg->reg, 0,
					vreg->high_vol_level);
		if (tmp < 0) {
			BT_PWR_ERR("vreg_set_vol(%s) retention failed rc=%d",
					vreg->name, tmp);
			rc = tmp;
		}
	}

	if (vreg->load_uA >= 0) {
		tmp = regulator_set_load(vreg->reg, 0);
		if (tmp < 0) {
			BT_PWR_ERR("vreg_set_load(%s) retention failed rc=%d",
					vreg->name, tmp);
			if (!rc)
				rc = tmp;
		}
	}

	if (!rc)
		BT_PWR_DBG("vreg retention vote dropped for : %s", vreg->name);
	return rc;
}

'''
    text = text[:start] + new_unvote + text[end:]

    start = text.find("static int bt_vreg_disable(struct bt_power_vreg_data *vreg)")
    end = text.find("static int bt_configure_vreg(struct bt_power_vreg_data *vreg)", start)
    if start < 0 or end < 0:
        raise SystemExit("bt_vreg_disable boundaries not found")

    new_disable = r'''static int bt_vreg_disable(struct bt_power_vreg_data *vreg)
{
	int rc = 0;
	int tmp;

	if (!vreg || !vreg->reg)
		return 0;

	if (!vreg->is_enabled)
		return 0;

	/*
	 * Do not drop active votes if regulator_disable itself fails; hardware
	 * may still be live.  Once disabled, however, clear both votes even if
	 * one cleanup operation fails.
	 */
	rc = regulator_disable(vreg->reg);
	if (rc < 0) {
		BT_PWR_ERR("regulator_disable(%s) failed rc=%d",
				vreg->name, rc);
		return rc;
	}
	vreg->is_enabled = false;

	if (vreg->set_voltage_sup) {
		tmp = regulator_set_voltage(vreg->reg, 0,
					vreg->high_vol_level);
		if (tmp < 0) {
			BT_PWR_ERR("vreg_set_vol(%s) cleanup failed rc=%d",
					vreg->name, tmp);
			if (!rc)
				rc = tmp;
		}
	}

	if (vreg->load_uA >= 0) {
		tmp = regulator_set_load(vreg->reg, 0);
		if (tmp < 0) {
			BT_PWR_ERR("vreg_set_load(%s) cleanup failed rc=%d",
					vreg->name, tmp);
			if (!rc)
				rc = tmp;
		}
	}

	if (!rc)
		BT_PWR_DBG("vreg disabled for : %s", vreg->name);
	return rc;
}

'''
    text = text[:start] + new_disable + text[end:]

    text = replace_once(
        text,
        """		rc = bt_enable_bt_reset_gpios_safely();
		if (rc) {
			BT_PWR_ERR("%s:bt_enable_bt_reset_gpios_safely failed",
				__func__);
		}

		msleep(50);
""",
        """		rc = bt_enable_bt_reset_gpios_safely();
		if (rc) {
			BT_PWR_ERR("%s:bt_enable_bt_reset_gpios_safely failed",
				__func__);
			return rc;
		}

		msleep(50);
""",
        "GPIO reset failure propagation",
    )

    text = replace_once(
        text,
        "static int bluetooth_power(int on)\n{",
        "static int __bluetooth_power(int on)\n{",
        "internal power function rename",
    )

    anchor = """out:
	return rc;
}

static int bluetooth_toggle_radio(void *data, bool blocked)
"""
    wrapper = """out:
	return rc;
}

/*
 * All WCN3990 power-mode requests pass through this wrapper.  Besides
 * excluding overlapping rfkill/ioctl transitions, the state check here is
 * inside the lock so two callers that observed an old pwr_state cannot both
 * execute the same regulator sequence.
 */
static int bluetooth_power(int on)
{
	int rc = 0;

	mutex_lock(&bt_power_lock);
	if (pwr_state == on)
		goto out_unlock;

	rc = __bluetooth_power(on);
	if (!rc)
		pwr_state = on;

out_unlock:
	mutex_unlock(&bt_power_lock);
	return rc;
}

static int bluetooth_toggle_radio(void *data, bool blocked)
"""
    text = replace_once(text, anchor, wrapper, "serialized power wrapper")

    # pwr_state is now owned by bluetooth_power(); leave the ioctl's state
    # check as a cheap fast path but remove the unlocked state write.
    text = replace_once(
        text,
        """		if (pwr_state != pwr_cntrl) {
			ret = bluetooth_power(pwr_cntrl);
			if (!ret)
				pwr_state = pwr_cntrl;
		} else {
""",
        """		if (pwr_state != pwr_cntrl) {
			ret = bluetooth_power(pwr_cntrl);
		} else {
""",
        "canonical ioctl state",
    )

    # rfkill destroy and remove path are hardened against optional resources.
    text = replace_once(
        text,
        """	rfkill = platform_get_drvdata(pdev);
	if (rfkill)
		rfkill_unregister(rfkill);
	rfkill_destroy(rfkill);
	platform_set_drvdata(pdev, NULL);
""",
        """	rfkill = platform_get_drvdata(pdev);
	if (rfkill) {
		rfkill_unregister(rfkill);
		rfkill_destroy(rfkill);
	}
	platform_set_drvdata(pdev, NULL);
""",
        "rfkill remove null safety",
    )

    text = replace_once(
        text,
        """	if (bt_power_pdata->bt_chip_pwd->reg)
		regulator_put(bt_power_pdata->bt_chip_pwd->reg);
""",
        """	if (bt_power_pdata->bt_chip_pwd &&
	    bt_power_pdata->bt_chip_pwd->reg)
		regulator_put(bt_power_pdata->bt_chip_pwd->reg);
""",
        "optional chip-pwd null safety",
    )

    text += "\n/* " + MARKER + " */\n"
    path.write_text(text)

    final = path.read_text()
    checks = [
        "static DEFINE_MUTEX(bt_power_lock);",
        "Always restore the active voltage/load vote",
        "rollback vreg_set_load",
        "retention vote dropped",
        "static int __bluetooth_power(int on)",
        "mutex_lock(&bt_power_lock);",
        "if (pwr_state == on)",
        "pwr_state = on;",
        "bt_power_pdata->bt_chip_pwd &&",
        MARKER,
    ]
    for needle in checks:
        if needle not in final:
            raise SystemExit(f"missing BT-P4 invariant: {needle}")

    if final.count("static int bluetooth_power(int on)") != 1:
        raise SystemExit("unexpected bluetooth_power wrapper count")
    if "if (!ret)\n\t\t\t\tpwr_state = pwr_cntrl;" in final:
        raise SystemExit("legacy unlocked ioctl pwr_state write remains")

    print(f"patched {path}")
    print(MARKER)


if __name__ == "__main__":
    main()
