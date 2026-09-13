#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1"
REC_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase343 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    anchor = """static unsigned int a52_r339_checkpoint_index;
"""
    block = r'''#include <asm/sysreg.h>

/* A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1
 *
 * Resolve the remaining Phase342 ambiguity without using any clock as the
 * progress condition.  CPU5 executes a tight SCHED_FIFO loop.  Progress is
 * measured only by a monotonically increasing software iteration counter.
 * Every 2^28 iterations a mirrored cache-cleaned raw sideband checkpoint is
 * written.  ktime, jiffies and CNTVCT are sampled only as payload data after
 * the iteration threshold has already been reached.
 *
 * Interpretation:
 *   - iteration checkpoints stop near the Phase342 frontier:
 *       CPU/global execution really stopped (or CPU5 was forcibly removed).
 *   - iterations continue while ktime and CNTVCT stop:
 *       architectural/kernel clock progression stopped, CPU execution lived.
 *   - iterations and CNTVCT continue while ktime/jiffies stop:
 *       kernel timekeeping/tick progression failed, not CPU execution.
 *
 * No display, clock, regulator, reset, PM, IRQ or timer behavior is changed.
 */
#define A52_R343_SIDEBAND_PHYS 0xB1BF8000ULL
#define A52_R343_SIDEBAND_BYTES 0x2000U
#define A52_R343_COPY_BYTES (A52_R343_SIDEBAND_BYTES / 2U)
#define A52_R343_SLOT_BYTES 64U
#define A52_R343_SLOTS_PER_COPY (A52_R343_COPY_BYTES / A52_R343_SLOT_BYTES)
#define A52_R343_MAGIC 0x3334335244353241ULL
#define A52_R343_COMMIT 0x343c0de5U
#define A52_R343_STRIDE_SHIFT 28U
#define A52_R343_STRIDE (1ULL << A52_R343_STRIDE_SHIFT)
#define A52_R343_LIMIT 128U
#define A52_R343_CPU 5U

enum a52_r343_event {
	A52_R343_EVT_ARM = 1,
	A52_R343_EVT_THREAD_START = 2,
	A52_R343_EVT_ITERATION = 3,
	A52_R343_EVT_DONE = 4,
};

struct a52_r343_slot {
	u64 magic;
	u64 iteration;
	u64 monotonic_ns;
	u64 cntvct;
	u64 jiffies64;
	u32 index;
	u32 event;
	u32 cpu;
	u32 checkpoint;
	u32 preempt;
	u32 commit;
};

static void *a52_r343_sideband;
static atomic_t a52_r343_sideband_index = ATOMIC_INIT(0);
static struct task_struct *a52_r343_task;

static void a52_r343_sideband_write(u32 event, u32 checkpoint, u64 iteration)
{
	struct a52_r343_slot slot;
	unsigned int index;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r343_sideband))
		return;

	memset(&slot, 0, sizeof(slot));
	index = (unsigned int)atomic_inc_return(&a52_r343_sideband_index) - 1U;
	slot.magic = A52_R343_MAGIC;
	slot.iteration = iteration;
	slot.monotonic_ns = ktime_get_ns();
	slot.cntvct = read_sysreg(cntvct_el0);
	slot.jiffies64 = get_jiffies_64();
	slot.index = index;
	slot.event = event;
	slot.cpu = (u32)raw_smp_processor_id();
	slot.checkpoint = checkpoint;
	slot.preempt = (u32)preempt_count();
	slot.commit = A52_R343_COMMIT;

	pos = (index % A52_R343_SLOTS_PER_COPY) * A52_R343_SLOT_BYTES;
	dst0 = (u8 *)a52_r343_sideband + pos;
	dst1 = (u8 *)a52_r343_sideband + A52_R343_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static int a52_r343_thread_fn(void *unused)
{
	struct sched_param sp = { .sched_priority = 1 };
	u64 iteration = 0;
	u64 next = A52_R343_STRIDE;
	unsigned int checkpoint = 0;

	sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);
	a52_r343_sideband_write(A52_R343_EVT_THREAD_START, 0U, 0ULL);

	while (!kthread_should_stop() && checkpoint < A52_R343_LIMIT) {
		iteration++;

		if (unlikely(iteration == next)) {
			checkpoint++;
			a52_r343_sideband_write(A52_R343_EVT_ITERATION,
				checkpoint, iteration);
			next += A52_R343_STRIDE;
		}
		cpu_relax();
	}

	a52_r343_sideband_write(A52_R343_EVT_DONE, checkpoint, iteration);
	return 0;
}

static void a52_r343_start(void)
{
	long kt_rc = 0;

	BUILD_BUG_ON(sizeof(struct a52_r343_slot) != A52_R343_SLOT_BYTES);
	BUILD_BUG_ON(A52_R343_COPY_BYTES % A52_R343_SLOT_BYTES);

	a52_r343_sideband = memremap(A52_R343_SIDEBAND_PHYS,
		A52_R343_SIDEBAND_BYTES, MEMREMAP_WB);
	a52_r343_task = kthread_create(a52_r343_thread_fn, NULL, "a52-p343");
	if (IS_ERR(a52_r343_task)) {
		kt_rc = PTR_ERR(a52_r343_task);
		a52_r343_task = NULL;
	} else {
		if (A52_R343_CPU < nr_cpu_ids && cpu_online(A52_R343_CPU))
			kthread_bind(a52_r343_task, A52_R343_CPU);
		wake_up_process(a52_r343_task);
	}

	a52_r343_sideband_write(A52_R343_EVT_ARM, 0U, 0ULL);
	a52_ackfr_record("P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u",
		a52_r343_sideband ? 1U : 0U, kt_rc,
		A52_R343_CPU, A52_R343_STRIDE_SHIFT, A52_R343_LIMIT);
}

'''
    text = one(text, anchor, block + anchor, "instruction-counter insertion")

    late_old = "	a52_r342_start();
"
    late_new = "	a52_r342_start();
	a52_r343_start();
"
    text = one(text, late_old, late_new, "late-init start")
    return text


def validate(before: str, after: str) -> None:
    for token in (
        MARK,
        "A52_R343_SIDEBAND_PHYS 0xB1BF8000ULL",
        "A52_R343_STRIDE_SHIFT 28U",
        "A52_R343_LIMIT 128U",
        "A52_R343_CPU 5U",
        "#include <asm/sysreg.h>",
        "P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u",
        "iteration++",
        "iteration == next",
        "read_sysreg(cntvct_el0)",
        "sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);",
        "kthread_bind(a52_r343_task, A52_R343_CPU);",
        "__flush_dcache_area(dst0, sizeof(slot));",
        "__flush_dcache_area(dst1, sizeof(slot));",
    ):
        if token not in after:
            raise SystemExit("Phase343 required token missing: " + token)

    for token in (
        "A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1",
        "P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u",
        "A52_R342_SIDEBAND_PHYS 0xB1BFA000ULL",
        "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1",
        "A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase343 changed inherited token count: " + token)

    if after.count("a52_r342_start();") != before.count("a52_r342_start();") - 1:
        raise SystemExit("Phase343 must disable exactly one Phase342 runtime start")
    if after.count("a52_r343_start();") != before.count("a52_r343_start();") + 1:
        raise SystemExit("Phase343 must add exactly one Phase343 runtime start")
    if after.count("kthread_create(") != before.count("kthread_create(") + 1:
        raise SystemExit("Phase343 expected one kthread_create call")
    if after.count("kthread_bind(") != before.count("kthread_bind(") + 1:
        raise SystemExit("Phase343 expected one kthread_bind call")
    if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 1:
        raise SystemExit("Phase343 expected one new R48 arm call")
    if after.count("__flush_dcache_area(") != before.count("__flush_dcache_area(") + 2:
        raise SystemExit("Phase343 expected two mirrored cache-clean sites")
    if after.count("\\t") != before.count("\\t"):
        raise SystemExit("Phase343 introduced literal backslash-t text")

    protected = (
        "writel_relaxed(", "writel(", "writeq_relaxed(", "readl_relaxed(",
        "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(",
        "pm_runtime_get_sync(", "pm_runtime_put_sync(",
        "sde_power_resource_enable(",
    )
    for token in protected:
        if after.count(token) != before.count(token):
            raise SystemExit("Phase343 changed protected behavior: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.root) / REC_REL
    if not path.is_file():
        raise SystemExit("Phase343 recorder source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        for token in (
            "P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u",
            "A52_R343_SIDEBAND_PHYS",
            "a52_r343_thread_fn",
            "a52_r343_start();",
        ):
            if token not in before:
                raise SystemExit("Phase343 check-only token missing: " + token)
        print("Phase343 instruction-counter frontier audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase343 marker missing in check-only mode")
    if "A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1" not in before:
        raise SystemExit("Phase343 requires Phase342 recorder lineage")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase343 instruction-counter frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
