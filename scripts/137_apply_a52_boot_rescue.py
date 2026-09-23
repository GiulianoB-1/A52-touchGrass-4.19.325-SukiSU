#!/usr/bin/env python3
from pathlib import Path
import sys

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    input_c = root / "drivers/input/input.c"
    ram_c = root / "fs/pstore/ram.c"

    if not input_c.is_file() or not ram_c.is_file():
        raise SystemExit("kernel tree is missing drivers/input/input.c or fs/pstore/ram.c")

    replace_once(
        input_c,
        '#include <linux/rcupdate.h>\n#include "input-compat.h"\n',
        '#include <linux/rcupdate.h>\n'
        '#include <linux/reboot.h>\n'
        '#include <linux/kmsg_dump.h>\n'
        '#include <linux/ktime.h>\n'
        '#include <linux/workqueue.h>\n'
        '#include "input-compat.h"\n',
        "boot-rescue includes",
    )

    rescue_code = r'''
/*
 * A52 BOOT RESCUE P137
 *
 * During the first five seconds of kernel uptime, pressing Power +
 * Volume Up schedules a process-context rescue. The rescue first asks
 * the kmsg dumpers to persist the current printk ring as an OOPS record,
 * then performs a direct machine restart with the "recovery" command.
 *
 * machine_restart() is deliberate here: this is an escape hatch for a
 * possibly broken test kernel, so it avoids the normal device_shutdown()
 * path while still passing "recovery" to the Qualcomm/Samsung restart
 * implementation.
 */
#define A52_BOOT_RESCUE_WINDOW_NS	(5ULL * NSEC_PER_SEC)

enum {
	A52_RESCUE_POWER_BIT = 0,
	A52_RESCUE_VOLUP_BIT,
	A52_RESCUE_TRIGGERED_BIT,
};

static unsigned long a52_boot_rescue_state;

static void a52_boot_rescue_recovery_work(struct work_struct *work)
{
	pr_emerg("A52_BOOT_RESCUE: Power + Volume Up detected inside 5s window\n");
	pr_emerg("A52_BOOT_RESCUE: saving current printk ring to ramoops\n");

	/*
	 * Ramoops accepts OOPS/PANIC dmesg records, but deliberately rejects
	 * normal RESTART dumps. KMSG_DUMP_OOPS therefore creates a collector-
	 * readable snapshot without actually panicking the kernel.
	 */
	kmsg_dump(KMSG_DUMP_OOPS);

	pr_emerg("A52_BOOT_RESCUE: direct restart to recovery\n");
	machine_restart("recovery");

	pr_emerg("A52_BOOT_RESCUE: machine_restart returned unexpectedly\n");
}

static DECLARE_WORK(a52_boot_rescue_work, a52_boot_rescue_recovery_work);

static void a52_boot_rescue_handle_key(unsigned int type,
				       unsigned int code, int value)
{
	unsigned int bit;

	if (type != EV_KEY)
		return;

	if (code == KEY_POWER)
		bit = A52_RESCUE_POWER_BIT;
	else if (code == KEY_VOLUMEUP)
		bit = A52_RESCUE_VOLUP_BIT;
	else
		return;

	if (ktime_get_boot_ns() > A52_BOOT_RESCUE_WINDOW_NS)
		return;

	if (value)
		set_bit(bit, &a52_boot_rescue_state);
	else
		clear_bit(bit, &a52_boot_rescue_state);

	if (test_bit(A52_RESCUE_POWER_BIT, &a52_boot_rescue_state) &&
	    test_bit(A52_RESCUE_VOLUP_BIT, &a52_boot_rescue_state) &&
	    !test_and_set_bit(A52_RESCUE_TRIGGERED_BIT,
			      &a52_boot_rescue_state))
		schedule_work(&a52_boot_rescue_work);
}

'''
    replace_once(
        input_c,
        '\n#ifdef CONFIG_KSU\nextern bool ksu_input_hook __read_mostly;\n',
        '\n' + rescue_code + '#ifdef CONFIG_KSU\nextern bool ksu_input_hook __read_mostly;\n',
        "boot-rescue key state machine",
    )

    replace_once(
        input_c,
        '\tint disposition = input_get_disposition(dev, type, code, &value);\n#ifdef CONFIG_KSU\n',
        '\tint disposition = input_get_disposition(dev, type, code, &value);\n\n'
        '\ta52_boot_rescue_handle_key(type, code, value);\n'
        '#ifdef CONFIG_KSU\n',
        "boot-rescue input hook",
    )

    replace_once(
        ram_c,
        '#include <linux/of_address.h>\n',
        '#include <linux/of_address.h>\n#include <linux/ktime.h>\n',
        "ramoops ktime include",
    )

    context_anchor = '''struct ramoops_context {
	struct persistent_ram_zone **dprzs;	/* Oops dump zones */
'''
    context_new = '''#define A52_RAMOOPS_BOOT_HOLD_NS	(5ULL * NSEC_PER_SEC)

/*
 * A52 BOOT RESCUE P137
 *
 * Keep the previous boot's continuous ramoops zones physically intact during
 * the rescue window. The dmesg crash zone is intentionally not gated, so an
 * actual OOPS/PANIC or the manual rescue snapshot can persist the current
 * failed boot for the recovery collector.
 */
static atomic_t a52_console_zone_released = ATOMIC_INIT(0);
static atomic_t a52_ftrace_zone_released = ATOMIC_INIT(0);
static atomic_t a52_pmsg_zone_released = ATOMIC_INIT(0);

static bool a52_ramoops_boot_hold_active(void)
{
	return ktime_get_boot_ns() < A52_RAMOOPS_BOOT_HOLD_NS;
}

struct ramoops_context {
	struct persistent_ram_zone **dprzs;	/* Oops dump zones */
'''
    replace_once(ram_c, context_anchor, context_new, "ramoops boot-hold state")

    helper_anchor = '''static struct platform_device *dummy;
static struct ramoops_platform_data *dummy_data;

'''
    helper_new = r'''static struct platform_device *dummy;
static struct ramoops_platform_data *dummy_data;

static bool a52_ramoops_release_one(struct persistent_ram_zone *prz,
				   atomic_t *state, const char *name)
{
	int old;

	if (!prz)
		return true;

	if (atomic_read(state) == 2)
		return true;

	if (a52_ramoops_boot_hold_active())
		return false;

	old = atomic_cmpxchg(state, 0, 1);
	if (old == 0) {
		persistent_ram_zap(prz);
		smp_wmb();
		atomic_set(state, 2);
		pr_info("A52 BOOT RESCUE P137: released %s ramoops writer after 5s\n",
			name);
		return true;
	}

	return atomic_read(state) == 2;
}

static bool a52_ramoops_release_ftrace(struct ramoops_context *cxt)
{
	int i;
	int old;

	if (!cxt->fprzs || !cxt->max_ftrace_cnt)
		return true;

	if (atomic_read(&a52_ftrace_zone_released) == 2)
		return true;

	if (a52_ramoops_boot_hold_active())
		return false;

	old = atomic_cmpxchg(&a52_ftrace_zone_released, 0, 1);
	if (old == 0) {
		for (i = 0; i < cxt->max_ftrace_cnt; i++)
			if (cxt->fprzs[i])
				persistent_ram_zap(cxt->fprzs[i]);
		smp_wmb();
		atomic_set(&a52_ftrace_zone_released, 2);
		pr_info("A52 BOOT RESCUE P137: released ftrace ramoops writer after 5s\n");
		return true;
	}

	return atomic_read(&a52_ftrace_zone_released) == 2;
}

'''
    replace_once(ram_c, helper_anchor, helper_new, "ramoops release helpers")

    replace_once(
        ram_c,
        '''	if (record->type == PSTORE_TYPE_CONSOLE) {
		if (!cxt->cprz)
			return -ENOMEM;
		persistent_ram_write(cxt->cprz, record->buf, record->size);
		return 0;
	} else if (record->type == PSTORE_TYPE_FTRACE) {
		int zonenum;

		if (!cxt->fprzs)
			return -ENOMEM;
''',
        '''	if (record->type == PSTORE_TYPE_CONSOLE) {
		if (!cxt->cprz)
			return -ENOMEM;
		if (!a52_ramoops_release_one(cxt->cprz,
					    &a52_console_zone_released,
					    "console"))
			return 0;
		persistent_ram_write(cxt->cprz, record->buf, record->size);
		return 0;
	} else if (record->type == PSTORE_TYPE_FTRACE) {
		int zonenum;

		if (!cxt->fprzs)
			return -ENOMEM;
		if (!a52_ramoops_release_ftrace(cxt))
			return 0;
''',
        "gate console and ftrace writers",
    )

    replace_once(
        ram_c,
        '''		if (!header_length) {
			persistent_ram_free_old(prz);
			persistent_ram_zap(prz);
			prz = NULL;
		}
''',
        '''		if (!header_length) {
			persistent_ram_free_old(prz);
			if (!a52_ramoops_boot_hold_active())
				persistent_ram_zap(prz);
			prz = NULL;
		}
''',
        "protect invalid dmesg zone during rescue window",
    )

    replace_once(
        ram_c,
        '''	if (record->type == PSTORE_TYPE_PMSG) {
		struct ramoops_context *cxt = record->psi->data;

		if (!cxt->mprz)
			return -ENOMEM;
		return persistent_ram_write_user(cxt->mprz, buf, record->size);
	}
''',
        '''	if (record->type == PSTORE_TYPE_PMSG) {
		struct ramoops_context *cxt = record->psi->data;

		if (!cxt->mprz)
			return -ENOMEM;
		if (!a52_ramoops_release_one(cxt->mprz,
					    &a52_pmsg_zone_released,
					    "pmsg"))
			return record->size;
		return persistent_ram_write_user(cxt->mprz, buf, record->size);
	}
''',
        "gate pmsg writer",
    )

    replace_once(
        ram_c,
        '''static int ramoops_pstore_erase(struct pstore_record *record)
{
	struct ramoops_context *cxt = record->psi->data;
	struct persistent_ram_zone *prz;
''',
        '''static int ramoops_pstore_erase(struct pstore_record *record)
{
	struct ramoops_context *cxt = record->psi->data;
	struct persistent_ram_zone *prz;

	/* Never let pstore cleanup erase retained evidence inside the 5s window. */
	if (a52_ramoops_boot_hold_active())
		return 0;
''',
        "gate pstore erases during rescue window",
    )

    replace_once(
        ram_c,
        '''	persistent_ram_zap(*prz);

	*paddr += sz;
''',
        '''	/*
	 * A52 BOOT RESCUE P137: do not zap console/pmsg at probe time.
	 * Their first write at or after five seconds performs a one-time zap.
	 * This keeps the previous physical record intact while the recovery
	 * escape chord is still available.
	 */

	*paddr += sz;
''',
        "defer console/pmsg zap",
    )

    replace_once(
        ram_c,
        '''	paddr = cxt->phys_addr;

	dump_mem_sz = cxt->size - cxt->console_size - cxt->ftrace_size
''',
        '''	paddr = cxt->phys_addr;

	atomic_set(&a52_console_zone_released, 0);
	atomic_set(&a52_ftrace_zone_released, 0);
	atomic_set(&a52_pmsg_zone_released, 0);

	dump_mem_sz = cxt->size - cxt->console_size - cxt->ftrace_size
''',
        "reset ramoops gate state",
    )

    input_text = input_c.read_text()
    ram_text = ram_c.read_text()
    for token in (
        "A52 BOOT RESCUE P137",
        "A52_BOOT_RESCUE_WINDOW_NS",
        "a52_boot_rescue_handle_key(type, code, value);",
        'machine_restart("recovery");',
        "kmsg_dump(KMSG_DUMP_OOPS);",
    ):
        if token not in input_text:
            raise SystemExit(f"input.c missing expected token: {token}")
    for token in (
        "A52_RAMOOPS_BOOT_HOLD_NS",
        "a52_ramoops_release_one",
        "a52_ramoops_release_ftrace",
        "do not zap console/pmsg at probe time",
        "Never let pstore cleanup erase retained evidence",
    ):
        if token not in ram_text:
            raise SystemExit(f"ram.c missing expected token: {token}")

    print("A52 BOOT RESCUE P137 applied successfully")

if __name__ == "__main__":
    main()
