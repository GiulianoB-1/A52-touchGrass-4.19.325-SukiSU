#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1"
REC_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase339 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    inc_old = """#include <linux/kernel.h>
#include <linux/ktime.h>
"""
    inc_new = """#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/delay.h>
#include <linux/reboot.h>
"""
    text = one(text, inc_old, inc_new, "include block")

    gate_old = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
\t      fmt[3] == '6' && fmt[4] == ' ' && fmt[5] == '3' &&
\t      fmt[6] == '3' && (fmt[7] == '1' || fmt[7] == '2')))
\t\treturn;
"""
    gate_new = """\t/* A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1
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
    text = one(text, gate_old, gate_new, "post-retention admission")

    anchor = """static atomic_t a52_r179_heartbeat_count = ATOMIC_INIT(0);
"""
    worker = r'''/* A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1
 * This is a preservation-only experiment.  It does not touch DRM, DSI, clocks,
 * MMIO, regulators, resets or atomic return values.  Checkpoints straddle the
 * observed ~11.932 s recovery frontier and continue to five minutes.  The final
 * worker forces a warm reboot directly into recovery so no manual hard-reset
 * path participates in the handoff.
 */
static const unsigned int a52_r339_checkpoints_s[] = {
	10U, 12U, 15U, 30U, 60U, 120U, 180U, 240U, 300U, 330U,
};
static unsigned int a52_r339_checkpoint_index;
static void a52_r339_preservation_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_r339_preservation_work,
			    a52_r339_preservation_fn);

static void a52_r339_preservation_fn(struct work_struct *work)
{
	unsigned int index = a52_r339_checkpoint_index;
	unsigned int now_s;
	unsigned int next_s;
	u64 seq;
	unsigned int retained;

	if (index >= ARRAY_SIZE(a52_r339_checkpoints_s))
		return;

	now_s = a52_r339_checkpoints_s[index];
	seq = (u64)atomic64_read(&a52_r179_sequence);
	retained = (unsigned int)atomic_read(&a52_r280_retained);

	if (now_s == 330U)
		a52_ackfr_record("P276 339R t=%u q=%llu r=%u warm=1",
			now_s, (unsigned long long)seq, retained);
	else
		a52_ackfr_record("P276 339S t=%u q=%llu r=%u j=%lu",
			now_s, (unsigned long long)seq, retained, jiffies);

	/* Order the persistent transport writes before any following checkpoint
	 * and, at 330 s, before the restart path begins.
	 */
	wmb();

	a52_r339_checkpoint_index = index + 1;
	if (now_s == 330U) {
		pr_emerg("Phase339 warm reboot into recovery after preservation checkpoint
");
		msleep(250);
		kernel_restart("recovery");
		a52_ackfr_record("P276 339E reboot_returned=1");
		wmb();
		return;
	}

	next_s = a52_r339_checkpoints_s[index + 1];
	schedule_delayed_work(&a52_r339_preservation_work,
		msecs_to_jiffies((next_s - now_s) * 1000U));
}

'''
    text = one(text, anchor, worker + anchor, "preservation worker insertion")

    late_old = """\ta52_ackfr_record("P273 START h=%u q=%u/%u s=%u",
\t\tA52_R273_FRONTIER_END_S, A52_R273_SCAN_FAST_MS,
\t\tA52_R273_SCAN_SLOW_MS, A52_R273_SUMMARY_S);
\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",
"""
    late_new = """\ta52_ackfr_record("P273 START h=%u q=%u/%u s=%u",
\t\tA52_R273_FRONTIER_END_S, A52_R273_SCAN_FAST_MS,
\t\tA52_R273_SCAN_SLOW_MS, A52_R273_SUMMARY_S);
\ta52_ackfr_record("P276 339A first=10 final=330 warm=1");
\ta52_r339_checkpoint_index = 0;
\tschedule_delayed_work(&a52_r339_preservation_work,
\t\tmsecs_to_jiffies(a52_r339_checkpoints_s[0] * 1000U));
\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",
"""
    text = one(text, late_old, late_new, "late-init scheduling")
    return text


def validate(before: str, after: str) -> None:
    required = (
        MARK,
        '#include <linux/delay.h>',
        '#include <linux/reboot.h>',
        "P276 339A first=10 final=330 warm=1",
        "P276 339S t=%u q=%llu r=%u j=%lu",
        "P276 339R t=%u q=%llu r=%u warm=1",
        'P276 339E reboot_returned=1',
        'kernel_restart("recovery");',
        "fmt[7] == '9'",
    )
    for token in required:
        if token not in after:
            raise SystemExit("Phase339 required token missing: " + token)

    protected = (
        "a52_ackfr_retain_timeout_snapshot(void)",
        "atomic_set(&a52_r280_retained, 1);",
        "P276 280Z q=2",
        "P276 332A q=2 g=1 d=%u st=%x m=%x",
        "P276 332B q=2 retained=1",
    )
    for token in protected:
        if after.count(token) != before.count(token):
            raise SystemExit("Phase339 changed inherited retention token count: " + token)

    if after.count("kernel_restart(") != before.count("kernel_restart(") + 1:
        raise SystemExit("Phase339 expected exactly one warm restart call")
    if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 4:
        raise SystemExit("Phase339 expected four new recorder calls")
    if after.count("\\t") != before.count("\\t"):
        raise SystemExit("Phase339 introduced literal backslash-t text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.root) / REC_REL
    if not path.is_file():
        raise SystemExit("Phase339 recorder source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        for token in (
            "P276 339A first=10 final=330 warm=1",
            "P276 339S t=%u q=%llu r=%u j=%lu",
            "P276 339R t=%u q=%llu r=%u warm=1",
            'kernel_restart("recovery");',
        ):
            if token not in before:
                raise SystemExit("Phase339 check-only token missing: " + token)
        print("Phase339 warm-reboot preservation probe audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase339 marker missing in check-only mode")
    if "A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1" not in before:
        raise SystemExit("Phase339 requires Phase332+ recorder lineage")
    if "A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1" not in before:
        raise SystemExit("Phase339 requires Phase280 retention latch")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase339 warm-reboot preservation probe applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
