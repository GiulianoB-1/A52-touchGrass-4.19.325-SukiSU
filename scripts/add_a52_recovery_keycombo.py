#!/usr/bin/env python3
"""Add the A52 boot-time Volume Up + Power recovery key combination.

The patch is intentionally device-specific. The Galaxy A52 5G reports the
physical Volume Up key through the ``gpio_keys`` input device and Power through
``qpnp_pon``. Restricting the handler to those names prevents a headset button
from satisfying the recovery combination.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DRIVER_MARKER = "A52_RECOVERY_KEYCOMBO_V1"
CONFIG_SYMBOL = "INPUT_RECOVERY_KEYCOMBO"
DRIVER_PATH = Path("drivers/input/recovery_keycombo.c")
KCONFIG_PATH = Path("drivers/input/Kconfig")
MAKEFILE_PATH = Path("drivers/input/Makefile")
DEFCONFIG_PATH = Path("arch/arm64/configs/a52xq_defconfig")

DRIVER_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * A52 boot-time recovery key combination
 *
 * Reboot into Android recovery when the physical Volume Up and Power keys
 * remain pressed together during the early kernel boot window.
 *
 * A52_RECOVERY_KEYCOMBO_V1
 */

#include <linux/init.h>
#include <linux/input.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/reboot.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/workqueue.h>

#define DRIVER_NAME                 "a52-recovery-keycombo"
#define A52_VOLUME_INPUT_NAME       "gpio_keys"
#define A52_POWER_INPUT_NAME        "qpnp_pon"
#define RECOVERY_HOLD_MS            800
#define RECOVERY_BOOT_WINDOW_MS     30000

struct recovery_key_handle {
	struct input_handle handle;
	bool tracks_power;
	bool tracks_volume_up;
	bool power_down;
	bool volume_up_down;
};

static DEFINE_SPINLOCK(recovery_state_lock);
static int power_down_count;
static int volume_up_down_count;
static bool combo_armed;
static bool recovery_triggered;
static unsigned long recovery_boot_deadline;
static char recovery_reboot_command[] = "recovery";

static bool recovery_combo_is_down_locked(void)
{
	return power_down_count > 0 && volume_up_down_count > 0;
}

static bool recovery_already_booting(void)
{
	if (!saved_command_line)
		return false;

	if (strstr(saved_command_line, "androidboot.boot_recovery=1"))
		return true;

	if (strstr(saved_command_line, "androidboot.mode=recovery"))
		return true;

	if (strstr(saved_command_line, "androidboot.bootmode=recovery"))
		return true;

	return false;
}

static void recovery_reboot_work_function(struct work_struct *work)
{
	unsigned long flags;
	bool should_reboot = false;

	(void)work;

	spin_lock_irqsave(&recovery_state_lock, flags);

	if (!recovery_triggered && combo_armed &&
	    recovery_combo_is_down_locked()) {
		recovery_triggered = true;
		combo_armed = false;
		should_reboot = true;
	}

	spin_unlock_irqrestore(&recovery_state_lock, flags);

	if (!should_reboot)
		return;

	pr_emerg(DRIVER_NAME
		 ": physical Volume Up + Power held during boot; rebooting to recovery\n");

	/* Workqueue context is sleepable and safe for the normal reboot path. */
	kernel_restart(recovery_reboot_command);

	pr_emerg(DRIVER_NAME ": kernel_restart unexpectedly returned\n");
}

static DECLARE_DELAYED_WORK(recovery_reboot_work,
			    recovery_reboot_work_function);

static void recovery_apply_work_action(bool arm, bool cancel)
{
	if (cancel)
		cancel_delayed_work(&recovery_reboot_work);

	if (arm)
		mod_delayed_work(system_wq, &recovery_reboot_work,
				 msecs_to_jiffies(RECOVERY_HOLD_MS));
}

static void recovery_update_key_state(struct recovery_key_handle *key_handle,
				      unsigned int code, bool pressed)
{
	unsigned long flags;
	bool was_combo_down;
	bool is_combo_down;
	bool state_changed = false;
	bool arm = false;
	bool cancel = false;

	spin_lock_irqsave(&recovery_state_lock, flags);

	was_combo_down = recovery_combo_is_down_locked();

	if (code == KEY_POWER && key_handle->tracks_power) {
		if (key_handle->power_down != pressed) {
			key_handle->power_down = pressed;
			if (pressed)
				power_down_count++;
			else if (power_down_count > 0)
				power_down_count--;
			state_changed = true;
		}
	} else if (code == KEY_VOLUMEUP && key_handle->tracks_volume_up) {
		if (key_handle->volume_up_down != pressed) {
			key_handle->volume_up_down = pressed;
			if (pressed)
				volume_up_down_count++;
			else if (volume_up_down_count > 0)
				volume_up_down_count--;
			state_changed = true;
		}
	}

	if (!state_changed)
		goto unlock;

	is_combo_down = recovery_combo_is_down_locked();

	if (!was_combo_down && is_combo_down) {
		if (!recovery_triggered &&
		    time_before_eq(jiffies, recovery_boot_deadline)) {
			combo_armed = true;
			arm = true;
		} else {
			combo_armed = false;
		}
	} else if (was_combo_down && !is_combo_down) {
		combo_armed = false;
		cancel = true;
	}

unlock:
	spin_unlock_irqrestore(&recovery_state_lock, flags);
	recovery_apply_work_action(arm, cancel);
}

static void recovery_key_event(struct input_handle *handle,
			       unsigned int type,
			       unsigned int code,
			       int value)
{
	struct recovery_key_handle *key_handle = handle->private;

	if (type != EV_KEY)
		return;

	if (code == KEY_POWER && !key_handle->tracks_power)
		return;

	if (code == KEY_VOLUMEUP && !key_handle->tracks_volume_up)
		return;

	if (code != KEY_POWER && code != KEY_VOLUMEUP)
		return;

	/* Values 1 and 2 mean pressed or autorepeat; 0 means released. */
	recovery_update_key_state(key_handle, code, value != 0);
}

static bool recovery_key_match(struct input_handler *handler,
			       struct input_dev *input_device)
{
	(void)handler;

	if (!input_device->name)
		return false;

	if (!strcmp(input_device->name, A52_POWER_INPUT_NAME) &&
	    test_bit(KEY_POWER, input_device->keybit))
		return true;

	if (!strcmp(input_device->name, A52_VOLUME_INPUT_NAME) &&
	    test_bit(KEY_VOLUMEUP, input_device->keybit))
		return true;

	return false;
}

static int recovery_key_connect(struct input_handler *handler,
				struct input_dev *input_device,
				const struct input_device_id *id)
{
	struct recovery_key_handle *key_handle;
	unsigned long flags;
	bool was_combo_down;
	bool is_combo_down;
	bool arm = false;
	int error;

	(void)id;

	key_handle = kzalloc(sizeof(*key_handle), GFP_KERNEL);
	if (!key_handle)
		return -ENOMEM;

	key_handle->tracks_power =
		!strcmp(input_device->name, A52_POWER_INPUT_NAME);
	key_handle->tracks_volume_up =
		!strcmp(input_device->name, A52_VOLUME_INPUT_NAME);

	key_handle->handle.dev = input_device;
	key_handle->handle.handler = handler;
	key_handle->handle.name = DRIVER_NAME;
	key_handle->handle.private = key_handle;

	error = input_register_handle(&key_handle->handle);
	if (error)
		goto error_free_handle;

	error = input_open_device(&key_handle->handle);
	if (error)
		goto error_unregister_handle;

	/* Detect keys that were already held before this handler connected. */
	spin_lock_irqsave(&recovery_state_lock, flags);
	was_combo_down = recovery_combo_is_down_locked();

	if (key_handle->tracks_power &&
	    test_bit(KEY_POWER, input_device->key)) {
		key_handle->power_down = true;
		power_down_count++;
	}

	if (key_handle->tracks_volume_up &&
	    test_bit(KEY_VOLUMEUP, input_device->key)) {
		key_handle->volume_up_down = true;
		volume_up_down_count++;
	}

	is_combo_down = recovery_combo_is_down_locked();
	if (!was_combo_down && is_combo_down && !recovery_triggered &&
	    time_before_eq(jiffies, recovery_boot_deadline)) {
		combo_armed = true;
		arm = true;
	}
	spin_unlock_irqrestore(&recovery_state_lock, flags);

	recovery_apply_work_action(arm, false);

	pr_info(DRIVER_NAME ": attached to input device \"%s\"\n",
		input_device->name);
	return 0;

error_unregister_handle:
	input_unregister_handle(&key_handle->handle);
error_free_handle:
	kfree(key_handle);
	return error;
}

static void recovery_key_disconnect(struct input_handle *handle)
{
	struct recovery_key_handle *key_handle = handle->private;
	unsigned long flags;
	bool was_combo_down;
	bool is_combo_down;
	bool cancel = false;

	spin_lock_irqsave(&recovery_state_lock, flags);
	was_combo_down = recovery_combo_is_down_locked();

	if (key_handle->power_down && power_down_count > 0)
		power_down_count--;
	if (key_handle->volume_up_down && volume_up_down_count > 0)
		volume_up_down_count--;

	key_handle->power_down = false;
	key_handle->volume_up_down = false;

	is_combo_down = recovery_combo_is_down_locked();
	if (was_combo_down && !is_combo_down) {
		combo_armed = false;
		cancel = true;
	}
	spin_unlock_irqrestore(&recovery_state_lock, flags);

	recovery_apply_work_action(false, cancel);
	input_close_device(handle);
	input_unregister_handle(handle);
	kfree(key_handle);
}

static const struct input_device_id recovery_key_ids[] = {
	{
		.flags = INPUT_DEVICE_ID_MATCH_EVBIT,
		.evbit = { BIT_MASK(EV_KEY) },
	},
	{ },
};
MODULE_DEVICE_TABLE(input, recovery_key_ids);

static struct input_handler recovery_key_handler = {
	.event = recovery_key_event,
	.match = recovery_key_match,
	.connect = recovery_key_connect,
	.disconnect = recovery_key_disconnect,
	.name = DRIVER_NAME,
	.id_table = recovery_key_ids,
};

static int __init recovery_keycombo_init(void)
{
	int error;

	if (recovery_already_booting()) {
		pr_info(DRIVER_NAME
			": recovery boot detected; key combination disabled\n");
		return 0;
	}

	recovery_boot_deadline =
		jiffies + msecs_to_jiffies(RECOVERY_BOOT_WINDOW_MS);

	error = input_register_handler(&recovery_key_handler);
	if (error) {
		pr_err(DRIVER_NAME
		       ": failed to register input handler: %d\n", error);
		return error;
	}

	pr_info(DRIVER_NAME
		": active for %u ms; hold physical Volume Up + Power for %u ms\n",
		RECOVERY_BOOT_WINDOW_MS, RECOVERY_HOLD_MS);
	return 0;
}

device_initcall(recovery_keycombo_init);

MODULE_DESCRIPTION("A52 boot-time physical-key recovery reboot");
MODULE_LICENSE("GPL v2");
'''

KCONFIG_BLOCK = '''config INPUT_RECOVERY_KEYCOMBO
\tbool "A52 boot-time Volume Up + Power recovery combination"
\tdepends on INPUT
\thelp
\t  Reboot a Samsung Galaxy A52 5G into Android recovery when the
\t  physical Volume Up and Power keys are held together during the
\t  first 30 seconds of kernel initialization.

\t  The handler is restricted to the A52 physical-key input devices
\t  named gpio_keys and qpnp_pon, preventing headset buttons from
\t  satisfying the combination.

\t  This feature is built into the kernel and is not a module.

'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def set_builtin_config(text: str, symbol: str) -> str:
    enabled = f"CONFIG_{symbol}=y"
    disabled = f"# CONFIG_{symbol} is not set"
    lines = [
        line
        for line in text.splitlines()
        if line != enabled and line != disabled and not line.startswith(f"CONFIG_{symbol}=")
    ]
    return "\n".join(lines).rstrip() + f"\n{enabled}\n"


def add_feature(kernel_root: Path) -> dict[str, object]:
    paths = {
        "driver": kernel_root / DRIVER_PATH,
        "kconfig": kernel_root / KCONFIG_PATH,
        "makefile": kernel_root / MAKEFILE_PATH,
        "defconfig": kernel_root / DEFCONFIG_PATH,
    }
    missing = [str(path) for path in paths.values() if not path.parent.exists()]
    if missing:
        raise SystemExit("Kernel source layout is incomplete: " + ", ".join(missing))

    for key in ("kconfig", "makefile", "defconfig"):
        if not paths[key].is_file():
            raise SystemExit(f"Required kernel file is missing: {paths[key]}")

    if paths["driver"].exists():
        existing = paths["driver"].read_text(encoding="utf-8")
        if DRIVER_MARKER not in existing:
            raise SystemExit(f"Refusing to replace unrelated driver: {paths['driver']}")
        if existing != DRIVER_SOURCE:
            raise SystemExit("Existing recovery driver differs from the audited source")
    else:
        paths["driver"].write_text(DRIVER_SOURCE, encoding="utf-8")

    kconfig = paths["kconfig"].read_text(encoding="utf-8")
    if f"config {CONFIG_SYMBOL}" not in kconfig:
        kconfig = replace_once(
            kconfig,
            'comment "Input Device Drivers"\n',
            KCONFIG_BLOCK + 'comment "Input Device Drivers"\n',
            "drivers/input/Kconfig insertion",
        )
        paths["kconfig"].write_text(kconfig, encoding="utf-8")

    makefile = paths["makefile"].read_text(encoding="utf-8")
    make_line = f"obj-$(CONFIG_{CONFIG_SYMBOL})\t+= recovery_keycombo.o"
    if make_line not in makefile:
        anchor = "obj-$(CONFIG_INPUT_KEYCOMBO)\t+= keycombo.o\n"
        makefile = replace_once(
            makefile,
            anchor,
            anchor + make_line + "\n",
            "drivers/input/Makefile insertion",
        )
        paths["makefile"].write_text(makefile, encoding="utf-8")

    defconfig = paths["defconfig"].read_text(encoding="utf-8")
    defconfig = set_builtin_config(defconfig, CONFIG_SYMBOL)
    paths["defconfig"].write_text(defconfig, encoding="utf-8")

    checks = {
        "driver_marker": DRIVER_MARKER in paths["driver"].read_text(encoding="utf-8"),
        "physical_volume_device": '"gpio_keys"' in paths["driver"].read_text(encoding="utf-8"),
        "physical_power_device": '"qpnp_pon"' in paths["driver"].read_text(encoding="utf-8"),
        "recovery_restart": "kernel_restart(recovery_reboot_command);" in paths["driver"].read_text(encoding="utf-8"),
        "recovery_loop_guard": "androidboot.boot_recovery=1" in paths["driver"].read_text(encoding="utf-8"),
        "kconfig_registered": f"config {CONFIG_SYMBOL}" in paths["kconfig"].read_text(encoding="utf-8"),
        "makefile_registered": make_line in paths["makefile"].read_text(encoding="utf-8"),
        "defconfig_enabled": f"CONFIG_{CONFIG_SYMBOL}=y" in paths["defconfig"].read_text(encoding="utf-8"),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("Recovery keycombo audit failed: " + ", ".join(failed))

    return {
        "status": "applied",
        "kernel_root": str(kernel_root),
        "config": f"CONFIG_{CONFIG_SYMBOL}=y",
        "volume_input": "gpio_keys",
        "power_input": "qpnp_pon",
        "hold_ms": 800,
        "boot_window_ms": 30000,
        "reboot_command": "recovery",
        "recovery_guard": "androidboot.boot_recovery=1",
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kernel_root",
        nargs="?",
        default="workspace/touchgrass-a52xq",
        type=Path,
        help="Path to the hydrated A52 kernel source tree",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    root = args.kernel_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"Kernel source directory does not exist: {root}")

    report = add_feature(root)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    print(output, end="")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")


if __name__ == "__main__":
    main()
