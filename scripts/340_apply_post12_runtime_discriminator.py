#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1"
REC_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase340 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    inc_old = """#include <linux/delay.h>
#include <linux/reboot.h>
#include <linux/math64.h>
"""
    inc_new = """#include <linux/delay.h>
#include <linux/reboot.h>
#include <linux/io.h>
#include <linux/kthread.h>
#include <linux/suspend.h>
#include <linux/math64.h>
#include <asm/cacheflush.h>
"""
    text = one(text, inc_old, inc_new, "include block")

    gate_old = """\t/* A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1
\t * Preserve the Phase280 freeze. Admit only P276 339* in addition to the
\t * existing 331/332 timeout breadcrumbs so late preservation checkpoints can
\t * survive if exact-F0 freezes ordinary traffic before the warm reboot.
\t */
\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
\t      fmt[3] == '6' && fmt[4] == ' ' && fmt[5] == '3' &&
\t      fmt[6] == '3' &&
\t      (fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '9')))
\t\treturn;
"""
    gate_new = """\t/* A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1
\t * Preserve the Phase280 freeze. Admit only P276 339* in addition to the
\t * existing 331/332 timeout breadcrumbs so late preservation checkpoints can
\t * survive if exact-F0 freezes ordinary traffic before the warm reboot.
\t */
\t/* A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1
\t * Phase340 additionally admits only P276 340*. The 340 stream comes from a
\t * dedicated kthread/PM notifier and is independent of system delayed_work.
\t */
\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
\t      fmt[3] == '6' && fmt[4] == ' ' &&
\t      ((fmt[5] == '3' && fmt[6] == '3' &&
\t        (fmt[7] == '1' || fmt[7] == '2' || fmt[7] == '9')) ||
\t       (fmt[5] == '3' && fmt[6] == '4' && fmt[7] == '0'))))
\t\treturn;
"""
    text = one(text, gate_old, gate_new, "post-retention admission")

    anchor = """static const unsigned int a52_r339_checkpoints_s[] = {
\t10U, 12U, 15U, 30U, 60U, 120U, 180U, 240U, 300U, 330U,
};
"""
    addition = r'''
/* A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1
 *
 * Independent sideband + dedicated-kthread discriminator for the repeatable
 * ~11.932 s frontier. The sideband occupies the final 2 KiB of the pmsg zone;
 * current recovery captures show the pmsg zone using only ~1 KiB, while R48
 * itself never targets pmsg. Each 64-byte slot is cache-cleaned to PoC after
 * commit so recovery can inspect this path independently of the R48 backend.
 */
#define A52_R340_SIDEBAND_PHYS 0xB1BFF800ULL
#define A52_R340_SIDEBAND_BYTES 0x800U
#define A52_R340_SLOT_BYTES 64U
#define A52_R340_SLOT_COUNT (A52_R340_SIDEBAND_BYTES / A52_R340_SLOT_BYTES)
#define A52_R340_MAGIC 0x3034335244353241ULL
#define A52_R340_COMMIT 0x340c0de5U

enum a52_r340_event {
	A52_R340_EVT_ARM = 1,
	A52_R340_EVT_KTHREAD = 2,
	A52_R340_EVT_PM = 3,
	A52_R340_EVT_REBOOT = 4,
};

struct a52_r340_slot {
	u64 magic;
	u64 monotonic_ns;
	u64 sequence;
	u64 jiffies64;
	u32 version;
	u32 index;
	u32 event;
	u32 arg;
	u32 retained;
	u32 cpu;
	u32 commit;
	u32 reserved;
};

static void *a52_r340_sideband;
static atomic_t a52_r340_sideband_index = ATOMIC_INIT(0);
static struct task_struct *a52_r340_task;

static void a52_r340_sideband_write(u32 event, u32 arg)
{
	struct a52_r340_slot slot;
	void *dst;
	unsigned int index;

	if (!READ_ONCE(a52_r340_sideband))
		return;

	memset(&slot, 0, sizeof(slot));
	index = (unsigned int)atomic_inc_return(&a52_r340_sideband_index) - 1U;
	slot.magic = A52_R340_MAGIC;
	slot.monotonic_ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.jiffies64 = get_jiffies_64();
	slot.version = 1U;
	slot.index = index;
	slot.event = event;
	slot.arg = arg;
	slot.retained = (u32)atomic_read(&a52_r280_retained);
	slot.cpu = (u32)task_cpu(current);
	slot.commit = A52_R340_COMMIT;

	dst = (u8 *)a52_r340_sideband +
		(index % A52_R340_SLOT_COUNT) * A52_R340_SLOT_BYTES;
	memcpy(dst, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst, sizeof(slot));
}

static int a52_r340_pm_notify(struct notifier_block *nb,
			      unsigned long action, void *unused)
{
	switch (action) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		a52_r340_sideband_write(A52_R340_EVT_PM, (u32)action);
		a52_ackfr_record("P276 340P e=%lu q=%llu r=%u",
			action,
			(unsigned long long)atomic64_read(&a52_r179_sequence),
			(unsigned int)atomic_read(&a52_r280_retained));
		break;
	default:
		break;
	}
	return NOTIFY_DONE;
}

static struct notifier_block a52_r340_pm_nb = {
	.notifier_call = a52_r340_pm_notify,
};

static int a52_r340_thread_fn(void *unused)
{
	static const unsigned int checkpoints_s[] = {
		10U, 12U, 15U, 30U, 60U,
	};
	unsigned int i;
	unsigned int previous = 0;

	for (i = 0; i < ARRAY_SIZE(checkpoints_s); i++) {
		unsigned int now = checkpoints_s[i];

		if (kthread_should_stop())
			return 0;
		msleep((now - previous) * 1000U);
		a52_r340_sideband_write(A52_R340_EVT_KTHREAD, now);
		a52_ackfr_record("P276 340K t=%u q=%llu r=%u",
			now,
			(unsigned long long)atomic64_read(&a52_r179_sequence),
			(unsigned int)atomic_read(&a52_r280_retained));
		previous = now;
	}

	a52_r340_sideband_write(A52_R340_EVT_REBOOT, 60U);
	a52_ackfr_record("P276 340R t=60 warm=1");
	wmb();
	msleep(250);
	kernel_restart("recovery");
	return 0;
}

static void a52_r340_start(void)
{
	long kt_rc = 0;
	int pm_rc;

	BUILD_BUG_ON(sizeof(struct a52_r340_slot) != A52_R340_SLOT_BYTES);
	a52_r340_sideband = memremap(A52_R340_SIDEBAND_PHYS,
		A52_R340_SIDEBAND_BYTES, MEMREMAP_WB);
	pm_rc = register_pm_notifier(&a52_r340_pm_nb);
	a52_r340_task = kthread_run(a52_r340_thread_fn, NULL,
				    "a52-p340");
	if (IS_ERR(a52_r340_task)) {
		kt_rc = PTR_ERR(a52_r340_task);
		a52_r340_task = NULL;
	}

	a52_r340_sideband_write(A52_R340_EVT_ARM, 0U);
	a52_ackfr_record("P276 340A map=%u pm=%d kt=%ld",
		a52_r340_sideband ? 1U : 0U, pm_rc, kt_rc);
}

'''
    text = one(text, anchor, anchor + addition, "runtime discriminator insertion")

    late_old = """\ta52_r339_checkpoint_index = 0;
\tschedule_delayed_work(&a52_r339_preservation_work,
\t\tmsecs_to_jiffies(a52_r339_checkpoints_s[0] * 1000U));
\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",
"""
    late_new = """\ta52_r339_checkpoint_index = 0;
\tschedule_delayed_work(&a52_r339_preservation_work,
\t\tmsecs_to_jiffies(a52_r339_checkpoints_s[0] * 1000U));
\ta52_r340_start();
\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",
"""
    text = one(text, late_old, late_new, "late-init start")
    return text


def validate(before: str, after: str) -> None:
    for token in (
        MARK,
        "A52_R340_SIDEBAND_PHYS 0xB1BFF800ULL",
        "P276 340A map=%u pm=%d kt=%ld",
        "P276 340K t=%u q=%llu r=%u",
        "P276 340P e=%lu q=%llu r=%u",
        "P276 340R t=60 warm=1",
        "__flush_dcache_area(dst, sizeof(slot));",
        'kthread_run(a52_r340_thread_fn, NULL,',
        'kernel_restart("recovery");',
    ):
        if token not in after:
            raise SystemExit("Phase340 required token missing: " + token)

    for token in (
        "A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1",
        "P276 339A first=10 final=330 warm=1",
        "P276 339S t=%u q=%llu r=%u j=%lu",
        "P276 339R t=%u q=%llu r=%u warm=1",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase340 changed Phase339 token count: " + token)

    if after.count('kernel_restart("recovery");') != before.count('kernel_restart("recovery");') + 1:
        raise SystemExit("Phase340 expected exactly one additional warm restart")
    if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 4:
        raise SystemExit("Phase340 expected four new R48 call sites")
    if after.count("\\t") != before.count("\\t"):
        raise SystemExit("Phase340 introduced literal backslash-t text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.root) / REC_REL
    if not path.is_file():
        raise SystemExit("Phase340 recorder source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        for token in (
            "P276 340A map=%u pm=%d kt=%ld",
            "P276 340K t=%u q=%llu r=%u",
            "P276 340P e=%lu q=%llu r=%u",
            "A52_R340_SIDEBAND_PHYS",
        ):
            if token not in before:
                raise SystemExit("Phase340 check-only token missing: " + token)
        print("Phase340 post-12s runtime discriminator audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase340 marker missing in check-only mode")
    if "A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1" not in before:
        raise SystemExit("Phase340 requires Phase339 recorder lineage")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase340 post-12s runtime discriminator applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
