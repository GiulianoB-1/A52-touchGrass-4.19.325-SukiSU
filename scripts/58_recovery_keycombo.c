// SPDX-License-Identifier: GPL-2.0
/*
 * Boot-time recovery key combination for Samsung Galaxy A52 5G.
 *
 * Reboot into Android recovery when the physical Volume Up and Power
 * buttons are held together during the early boot window.
 */

#include <linux/input.h>
#include <linux/init.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/reboot.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/workqueue.h>

#define DRIVER_NAME             "recovery-keycombo"
#define RECOVERY_HOLD_MS        800
#define RECOVERY_BOOT_WINDOW_MS 30000

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
static bool recovery_triggered;
static unsigned long recovery_boot_deadline;
static char recovery_reboot_command[] = "recovery";

extern char *saved_command_line;

static bool recovery_combo_is_down_locked(void)
{
	return power_down_count > 0 && volume_up_down_count > 0;
}

static bool recovery_already_booting(void)
{
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
	if (!recovery_triggered && recovery_combo_is_down_locked() &&
	    time_before(jiffies, recovery_boot_deadline)) {
		recovery_triggered = true;
		should_reboot = true;
	}
	spin_unlock_irqrestore(&recovery_state_lock, flags);

	if (!should_reboot)
		return;

	pr_emerg(DRIVER_NAME
		 ": physical Volume Up + Power held during boot, rebooting to recovery\n");
	kernel_restart(recovery_reboot_command);
	pr_emerg(DRIVER_NAME ": kernel_restart() unexpectedly returned\n");
}

static DECLARE_DELAYED_WORK(recovery_reboot_work,
			    recovery_reboot_work_function);

static void recovery_update_delayed_work(bool arm)
{
	if (arm)
		mod_delayed_work(system_wq, &recovery_reboot_work,
				 msecs_to_jiffies(RECOVERY_HOLD_MS));
	else
		cancel_delayed_work(&recovery_reboot_work);
}

static void recovery_key_event(struct input_handle *handle,
			       unsigned int type,
			       unsigned int code,
			       int value)
{
	struct recovery_key_handle *key_handle = handle->private;
	unsigned long flags;
	bool pressed;
	bool arm_work;
	bool state_changed = false;

	if (type != EV_KEY)
		return;
	if (code == KEY_POWER && !key_handle->tracks_power)
		return;
	if (code == KEY_VOLUMEUP && !key_handle->tracks_volume_up)
		return;
	if (code != KEY_POWER && code != KEY_VOLUMEUP)
		return;

	pressed = value != 0;
	spin_lock_irqsave(&recovery_state_lock, flags);

	if (code == KEY_POWER) {
		if (key_handle->power_down != pressed) {
			key_handle->power_down = pressed;
			if (pressed)
				power_down_count++;
			else if (power_down_count > 0)
				power_down_count--;
			state_changed = true;
		}
	} else if (key_handle->volume_up_down != pressed) {
		key_handle->volume_up_down = pressed;
		if (pressed)
			volume_up_down_count++;
		else if (volume_up_down_count > 0)
			volume_up_down_count--;
		state_changed = true;
	}

	arm_work = state_changed && !recovery_triggered &&
		   time_before(jiffies, recovery_boot_deadline) &&
		   recovery_combo_is_down_locked();
	spin_unlock_irqrestore(&recovery_state_lock, flags);

	if (state_changed)
		recovery_update_delayed_work(arm_work);
}

static int recovery_key_connect(struct input_handler *handler,
				struct input_dev *input_device,
				const struct input_device_id *id)
{
	struct recovery_key_handle *key_handle;
	unsigned long flags;
	bool is_power_device;
	bool is_volume_device;
	bool arm_work;
	int error;

	(void)id;
	if (!input_device->name)
		return -ENODEV;

	is_power_device = !strcmp(input_device->name, "qpnp_pon") &&
		test_bit(KEY_POWER, input_device->keybit);
	is_volume_device = !strcmp(input_device->name, "gpio_keys") &&
		test_bit(KEY_VOLUMEUP, input_device->keybit);
	if (!is_power_device && !is_volume_device)
		return -ENODEV;

	key_handle = kzalloc(sizeof(*key_handle), GFP_KERNEL);
	if (!key_handle)
		return -ENOMEM;

	key_handle->tracks_power = is_power_device;
	key_handle->tracks_volume_up = is_volume_device;
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

	spin_lock_irqsave(&recovery_state_lock, flags);
	if (key_handle->tracks_power && test_bit(KEY_POWER, input_device->key)) {
		key_handle->power_down = true;
		power_down_count++;
	}
	if (key_handle->tracks_volume_up &&
	    test_bit(KEY_VOLUMEUP, input_device->key)) {
		key_handle->volume_up_down = true;
		volume_up_down_count++;
	}
	arm_work = !recovery_triggered &&
		   time_before(jiffies, recovery_boot_deadline) &&
		   recovery_combo_is_down_locked();
	spin_unlock_irqrestore(&recovery_state_lock, flags);

	recovery_update_delayed_work(arm_work);
	pr_info(DRIVER_NAME ": attached to physical input device \"%s\"\n",
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
	bool combo_still_down;

	spin_lock_irqsave(&recovery_state_lock, flags);
	if (key_handle->power_down && power_down_count > 0)
		power_down_count--;
	if (key_handle->volume_up_down && volume_up_down_count > 0)
		volume_up_down_count--;
	combo_still_down = recovery_combo_is_down_locked();
	spin_unlock_irqrestore(&recovery_state_lock, flags);

	if (!combo_still_down)
		cancel_delayed_work(&recovery_reboot_work);
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

static struct input_handler recovery_key_handler = {
	.event = recovery_key_event,
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
			": recovery boot detected, key combination disabled\n");
		return 0;
	}

	recovery_boot_deadline =
		jiffies + msecs_to_jiffies(RECOVERY_BOOT_WINDOW_MS);
	error = input_register_handler(&recovery_key_handler);
	if (error) {
		pr_err(DRIVER_NAME ": input handler registration failed: %d\n",
		       error);
		return error;
	}

	pr_info(DRIVER_NAME
		": enabled for %u ms, hold physical Volume Up + Power for %u ms\n",
		RECOVERY_BOOT_WINDOW_MS, RECOVERY_HOLD_MS);
	return 0;
}

device_initcall(recovery_keycombo_init);

MODULE_DESCRIPTION("A52 boot-time physical key recovery reboot");
MODULE_LICENSE("GPL v2");
