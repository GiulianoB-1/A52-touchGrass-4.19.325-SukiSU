#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "A52 BT-P4A: Bluetooth power lifecycle hardening with vendor state semantics preserved"


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
        print("BT-P4A already applied")
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
 * A52 BT-P4A: serialize only the hardware sequence.  Keep Qualcomm's two
 * existing software state machines independent: rfkill uses 'previous',
 * while BT_CMD_PWR_CTRL uses 'pwr_state'.  Android/Samsung userspace relies
 * on that ordering and must not have one interface suppress the other.
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
	 * Retention mode leaves the regulator physically enabled but drops its
	 * voltage/load votes.  Restore those votes on every ON transition, even
	 * when is_enabled is already true.
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

	if (!vreg || !vreg->reg || !vreg->is_enabled)
		return 0;

	if (vreg->set_voltage_sup) {
		tmp = regulator_set_voltage(vreg->reg, 0,
					vreg->high_vol_level);
		if (tmp < 0) {
			BT_PWR_ERR("vreg_set_vol(%s) retention failed rc=%d",
					vreg->name, tmp);
			rc = tmp;
		}
	}

	/* Always try the load drop even if voltage unvote failed. */
	if (vreg->load_uA >= 0) {
		tmp = regulator_set_load(vreg->reg, 0);
		if (tmp < 0) {
			BT_PWR_ERR("vreg_set_load(%s) retention failed rc=%d",
					vreg->name, tmp);
			if (!rc)
				rc = tmp;
		}
	}

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

	if (!vreg || !vreg->reg || !vreg->is_enabled)
		return 0;

	rc = regulator_disable(vreg->reg);
	if (rc < 0) {
		BT_PWR_ERR("regulator_disable(%s) failed rc=%d",
				vreg->name, rc);
		return rc;
	}
	vreg->is_enabled = false;

	/* Once physically disabled, attempt both vote cleanups independently. */
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

    # Serialize the underlying hardware sequence without introducing a shared
    # deduplication state.  rfkill's 'previous' and ioctl's 'pwr_state' keep
    # their original Qualcomm/Samsung behavior.
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

static int bluetooth_power(int on)
{
	int rc;

	mutex_lock(&bt_power_lock);
	rc = __bluetooth_power(on);
	mutex_unlock(&bt_power_lock);

	return rc;
}

static int bluetooth_toggle_radio(void *data, bool blocked)
"""
    text = replace_once(text, anchor, wrapper, "serialized power wrapper")

    # Do NOT change the vendor ioctl pwr_state logic.
    if """		if (pwr_state != pwr_cntrl) {
			ret = bluetooth_power(pwr_cntrl);
			if (!ret)
				pwr_state = pwr_cntrl;
""" not in text:
        raise SystemExit("vendor BT_CMD_PWR_CTRL state semantics changed unexpectedly")

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
        "Retentions mode" if False else "Retention mode leaves the regulator physically enabled",
        "rollback vreg_set_load",
        "static int __bluetooth_power(int on)",
        "mutex_lock(&bt_power_lock);",
        "if (pwr_state != pwr_cntrl)",
        "pwr_state = pwr_cntrl;",
        "bt_power_pdata->bt_chip_pwd &&",
        MARKER,
    ]
    for needle in checks:
        if needle not in final:
            raise SystemExit(f"missing BT-P4A invariant: {needle}")

    # The rejected P4 behavior must not return: no cross-interface shared
    # pwr_state suppression inside bluetooth_power().
    wrapper_start = final.find("static int bluetooth_power(int on)")
    wrapper_end = final.find("static int bluetooth_toggle_radio", wrapper_start)
    wrapper_text = final[wrapper_start:wrapper_end]
    if "pwr_state" in wrapper_text:
        raise SystemExit("BT-P4A wrapper incorrectly shares pwr_state")

    print(f"patched {path}")
    print(MARKER)


if __name__ == "__main__":
    main()
