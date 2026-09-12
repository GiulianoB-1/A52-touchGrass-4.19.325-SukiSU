#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1"
REC_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase341 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    inc_old = """#include <linux/io.h>
#include <linux/kthread.h>
#include <linux/suspend.h>
"""
    inc_new = """#include <linux/io.h>
#include <linux/kthread.h>
#include <linux/hrtimer.h>
#include <linux/percpu.h>
#include <linux/smp.h>
#include <linux/suspend.h>
"""
    text = one(text, inc_old, inc_new, "include block")

    anchor = """static unsigned int a52_r339_checkpoint_index;
"""
    block = r'''/* A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1
 *
 * Hard-IRQ timer discriminator for the repeatable ~11.9 s runtime frontier.
 * Three pinned hrtimers (CPU0, CPU5, CPU7) write only to an independent raw
 * sideband every 500 ms.  This path does not depend on system_wq, kthreads or
 * the R48 recorder.  Each event is mirrored into two halves of a 14 KiB pmsg
 * tail reservation and both copies are cache-cleaned to PoC.
 *
 * Interpretation:
 *   - tick > 20 survives: generic timer IRQs still run beyond the scheduler
 *     frontier (~10 s after late_init).
 *   - all CPUs stop at tick 20/21: timer/IRQ/global CPU progress is implicated.
 *   - only one cluster stops: local CPU/cluster hotplug or interrupt state.
 */
#define A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL
#define A52_R341_SIDEBAND_BYTES 0x3800U
#define A52_R341_COPY_BYTES (A52_R341_SIDEBAND_BYTES / 2U)
#define A52_R341_SLOT_BYTES 64U
#define A52_R341_SLOTS_PER_COPY (A52_R341_COPY_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
#define A52_R341_COMMIT 0x341c0de5U
#define A52_R341_INTERVAL_MS 500U
#define A52_R341_LIMIT 30U

enum a52_r341_event {
	A52_R341_EVT_ARM = 1,
	A52_R341_EVT_CPU_START = 2,
	A52_R341_EVT_HRTIMER = 3,
};

struct a52_r341_slot {
	u64 magic;
	u64 monotonic_ns;
	u64 sequence;
	u64 jiffies64;
	u64 reserved64;
	u32 version;
	u32 index;
	u32 event;
	u32 cpu;
	u32 tick;
	u32 commit;
};

struct a52_r341_cpu_timer {
	struct hrtimer timer;
	u32 ticks;
	u32 cpu;
};

static void *a52_r341_sideband;
static atomic_t a52_r341_sideband_index = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_r341_cpu_timer, a52_r341_timers);

static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)
{
	struct a52_r341_slot slot;
	unsigned int index;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r341_sideband))
		return;

	memset(&slot, 0, sizeof(slot));
	index = (unsigned int)atomic_inc_return(&a52_r341_sideband_index) - 1U;
	slot.magic = A52_R341_MAGIC;
	slot.monotonic_ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.jiffies64 = get_jiffies_64();
	slot.version = 1U;
	slot.index = index;
	slot.event = event;
	slot.cpu = cpu;
	slot.tick = tick;
	slot.commit = A52_R341_COMMIT;

	pos = (index % A52_R341_SLOTS_PER_COPY) * A52_R341_SLOT_BYTES;
	dst0 = (u8 *)a52_r341_sideband + pos;
	dst1 = (u8 *)a52_r341_sideband + A52_R341_COPY_BYTES + pos;

	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static enum hrtimer_restart a52_r341_hrtimer_fn(struct hrtimer *timer)
{
	struct a52_r341_cpu_timer *ct =
		container_of(timer, struct a52_r341_cpu_timer, timer);

	ct->ticks++;
	a52_r341_sideband_write(A52_R341_EVT_HRTIMER,
		(u32)smp_processor_id(), ct->ticks);

	if (ct->ticks >= A52_R341_LIMIT)
		return HRTIMER_NORESTART;

	hrtimer_forward_now(timer, ms_to_ktime(A52_R341_INTERVAL_MS));
	return HRTIMER_RESTART;
}

static void a52_r341_start_cpu(void *unused)
{
	struct a52_r341_cpu_timer *ct = this_cpu_ptr(&a52_r341_timers);
	unsigned int cpu = (unsigned int)smp_processor_id();

	memset(ct, 0, sizeof(*ct));
	ct->cpu = cpu;
	hrtimer_init(&ct->timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED);
	ct->timer.function = a52_r341_hrtimer_fn;
	a52_r341_sideband_write(A52_R341_EVT_CPU_START, cpu, 0U);
	hrtimer_start(&ct->timer, ms_to_ktime(A52_R341_INTERVAL_MS),
		      HRTIMER_MODE_REL_PINNED);
}

static void a52_r341_start(void)
{
	static const unsigned int targets[] = { 0U, 5U, 7U };
	unsigned int mask = 0;
	unsigned int i;
	int rc;

	BUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
	BUILD_BUG_ON(A52_R341_COPY_BYTES % A52_R341_SLOT_BYTES);

	a52_r341_sideband = memremap(A52_R341_SIDEBAND_PHYS,
		A52_R341_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r341_sideband) {
		a52_ackfr_record("P276 341A map=0 mask=0");
		return;
	}

	for (i = 0; i < ARRAY_SIZE(targets); i++) {
		unsigned int cpu = targets[i];

		if (cpu >= nr_cpu_ids || !cpu_online(cpu))
			continue;
		rc = smp_call_function_single(cpu, a52_r341_start_cpu, NULL, 1);
		if (!rc)
			mask |= BIT(cpu);
	}

	a52_r341_sideband_write(A52_R341_EVT_ARM,
		(u32)smp_processor_id(), mask);
	a52_ackfr_record("P276 341A map=1 mask=%x int=%u lim=%u",
		mask, A52_R341_INTERVAL_MS, A52_R341_LIMIT);
}

'''
    text = one(text, anchor, block + anchor, "hardirq discriminator insertion")

    late_old = """\ta52_r340_start();
\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",
"""
    late_new = """\ta52_r340_start();
\ta52_r341_start();
\tpr_info("phase199 triple-copy RS+CRC32C recorder enabled stored=%llu dropped=%llu\\n",
"""
    text = one(text, late_old, late_new, "late-init start")
    return text


def validate(before: str, after: str) -> None:
    for token in (
        MARK,
        "A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL",
        "A52_R341_INTERVAL_MS 500U",
        "A52_R341_LIMIT 30U",
        "P276 341A map=1 mask=%x int=%u lim=%u",
        "hrtimer_forward_now(timer, ms_to_ktime(A52_R341_INTERVAL_MS));",
        "smp_call_function_single(cpu, a52_r341_start_cpu, NULL, 1);",
        "__flush_dcache_area(dst0, sizeof(slot));",
        "__flush_dcache_area(dst1, sizeof(slot));",
    ):
        if token not in after:
            raise SystemExit("Phase341 required token missing: " + token)

    for token in (
        "A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1",
        "P276 340A map=%u pm=%d kt=%ld",
        "P276 340K t=%u q=%llu r=%u",
        "P276 340P e=%lu q=%llu r=%u",
        "P276 340R t=60 warm=1",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase341 changed Phase340 token count: " + token)

    if after.count("hrtimer_start(") != before.count("hrtimer_start(") + 1:
        raise SystemExit("Phase341 expected one hrtimer start call site")
    if after.count("smp_call_function_single(") != before.count("smp_call_function_single(") + 1:
        raise SystemExit("Phase341 expected one SMP call site")
    if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 2:
        raise SystemExit("Phase341 expected two new arm recorder call sites")
    if after.count("\\t") != before.count("\\t"):
        raise SystemExit("Phase341 introduced literal backslash-t text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.root) / REC_REL
    if not path.is_file():
        raise SystemExit("Phase341 recorder source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        for token in (
            "P276 341A map=1 mask=%x int=%u lim=%u",
            "A52_R341_SIDEBAND_PHYS",
            "a52_r341_hrtimer_fn",
            "a52_r341_start();",
        ):
            if token not in before:
                raise SystemExit("Phase341 check-only token missing: " + token)
        print("Phase341 hardirq timer frontier audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase341 marker missing in check-only mode")
    if "A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1" not in before:
        raise SystemExit("Phase341 requires Phase340 recorder lineage")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase341 hardirq timer frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
