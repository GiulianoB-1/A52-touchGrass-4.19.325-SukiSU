#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1"
REC_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase342 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    anchor = """static unsigned int a52_r339_checkpoint_index;
"""
    block = r'''/* A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1
 *
 * Keep one CPU continuously executing across the repeatable ~12 s frontier.
 * The thread is pinned to CPU5, promoted to a low RT FIFO priority and never
 * sleeps after startup.  It polls ktime_get_ns() directly and writes a mirrored
 * raw sideband every 250 ms.  This makes each checkpoint independent of
 * hrtimer delivery, scheduler ticks, system_wq and the R48 backend.
 *
 * If ktime checkpoints continue after Phase341 hrtimers stop, CPU execution and
 * the architectural clocksource survive while interrupt/timer delivery fails.
 * If this stream also stops at the same instant, CPU/global execution itself is
 * collapsing.  If keeping CPU5 busy prevents the collapse, cpuidle/power-entry
 * becomes the leading suspect.
 */
#define A52_R342_SIDEBAND_PHYS 0xB1BFA000ULL
#define A52_R342_SIDEBAND_BYTES 0x2000U
#define A52_R342_COPY_BYTES (A52_R342_SIDEBAND_BYTES / 2U)
#define A52_R342_SLOT_BYTES 64U
#define A52_R342_SLOTS_PER_COPY (A52_R342_COPY_BYTES / A52_R342_SLOT_BYTES)
#define A52_R342_MAGIC 0x3234335244353241ULL
#define A52_R342_COMMIT 0x342c0de5U
#define A52_R342_INTERVAL_NS 250000000ULL
#define A52_R342_LIMIT 60U
#define A52_R342_CPU 5U

enum a52_r342_event {
	A52_R342_EVT_ARM = 1,
	A52_R342_EVT_THREAD_START = 2,
	A52_R342_EVT_POLL = 3,
	A52_R342_EVT_DONE = 4,
};

struct a52_r342_slot {
	u64 magic;
	u64 monotonic_ns;
	u64 sequence;
	u64 jiffies64;
	u32 version;
	u32 index;
	u32 event;
	u32 cpu;
	u32 tick;
	u32 irqs_off;
	u32 preempt;
	u32 commit;
};

static void *a52_r342_sideband;
static atomic_t a52_r342_sideband_index = ATOMIC_INIT(0);
static struct task_struct *a52_r342_task;

static void a52_r342_sideband_write(u32 event, u32 tick)
{
	struct a52_r342_slot slot;
	unsigned int index;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r342_sideband))
		return;

	memset(&slot, 0, sizeof(slot));
	index = (unsigned int)atomic_inc_return(&a52_r342_sideband_index) - 1U;
	slot.magic = A52_R342_MAGIC;
	slot.monotonic_ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.jiffies64 = get_jiffies_64();
	slot.version = 1U;
	slot.index = index;
	slot.event = event;
	slot.cpu = (u32)raw_smp_processor_id();
	slot.tick = tick;
	slot.irqs_off = irqs_disabled() ? 1U : 0U;
	slot.preempt = (u32)preempt_count();
	slot.commit = A52_R342_COMMIT;

	pos = (index % A52_R342_SLOTS_PER_COPY) * A52_R342_SLOT_BYTES;
	dst0 = (u8 *)a52_r342_sideband + pos;
	dst1 = (u8 *)a52_r342_sideband + A52_R342_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static int a52_r342_thread_fn(void *unused)
{
	struct sched_param sp = { .sched_priority = 1 };
	u64 next;
	unsigned int tick = 0;

	sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);
	a52_r342_sideband_write(A52_R342_EVT_THREAD_START, 0U);
	next = ktime_get_ns() + A52_R342_INTERVAL_NS;

	while (!kthread_should_stop() && tick < A52_R342_LIMIT) {
		u64 now = ktime_get_ns();

		if ((s64)(now - next) >= 0) {
			tick++;
			a52_r342_sideband_write(A52_R342_EVT_POLL, tick);
			next += A52_R342_INTERVAL_NS;
		}
		cpu_relax();
	}

	a52_r342_sideband_write(A52_R342_EVT_DONE, tick);
	return 0;
}

static void a52_r342_start(void)
{
	long kt_rc = 0;

	BUILD_BUG_ON(sizeof(struct a52_r342_slot) != A52_R342_SLOT_BYTES);
	BUILD_BUG_ON(A52_R342_COPY_BYTES % A52_R342_SLOT_BYTES);

	a52_r342_sideband = memremap(A52_R342_SIDEBAND_PHYS,
		A52_R342_SIDEBAND_BYTES, MEMREMAP_WB);
	a52_r342_task = kthread_create(a52_r342_thread_fn, NULL, "a52-p342");
	if (IS_ERR(a52_r342_task)) {
		kt_rc = PTR_ERR(a52_r342_task);
		a52_r342_task = NULL;
	} else {
		if (A52_R342_CPU < nr_cpu_ids && cpu_online(A52_R342_CPU))
			kthread_bind(a52_r342_task, A52_R342_CPU);
		wake_up_process(a52_r342_task);
	}

	a52_r342_sideband_write(A52_R342_EVT_ARM, 0U);
	a52_ackfr_record("P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u",
		a52_r342_sideband ? 1U : 0U, kt_rc,
		A52_R342_CPU, A52_R342_LIMIT);
}

'''
    text = one(text, anchor, block + anchor, "busyloop discriminator insertion")

    late_old = "\ta52_r341_start();\n"
    late_new = "\ta52_r341_start();\n\ta52_r342_start();\n"
    text = one(text, late_old, late_new, "late-init start")
    return text


def validate(before: str, after: str) -> None:
    for token in (
        MARK,
        "A52_R342_SIDEBAND_PHYS 0xB1BFA000ULL",
        "A52_R342_INTERVAL_NS 250000000ULL",
        "A52_R342_LIMIT 60U",
        "A52_R342_CPU 5U",
        "P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u",
        "sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);",
        "kthread_bind(a52_r342_task, A52_R342_CPU);",
        "__flush_dcache_area(dst0, sizeof(slot));",
        "__flush_dcache_area(dst1, sizeof(slot));",
    ):
        if token not in after:
            raise SystemExit("Phase342 required token missing: " + token)

    for token in (
        "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1",
        "P276 341A map=1 mask=%x int=%u lim=%u",
        "A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase342 changed Phase341 token count: " + token)

    if after.count("kthread_create(") != before.count("kthread_create(") + 1:
        raise SystemExit("Phase342 expected one kthread_create call")
    if after.count("kthread_bind(") != before.count("kthread_bind(") + 1:
        raise SystemExit("Phase342 expected one kthread_bind call")
    if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 1:
        raise SystemExit("Phase342 expected one new R48 arm call")
    if after.count("__flush_dcache_area(") != before.count("__flush_dcache_area(") + 2:
        raise SystemExit("Phase342 expected two mirrored cache-clean sites")
    if after.count("\\t") != before.count("\\t"):
        raise SystemExit("Phase342 introduced literal backslash-t text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.root) / REC_REL
    if not path.is_file():
        raise SystemExit("Phase342 recorder source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        for token in (
            "P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u",
            "A52_R342_SIDEBAND_PHYS",
            "a52_r342_thread_fn",
            "a52_r342_start();",
        ):
            if token not in before:
                raise SystemExit("Phase342 check-only token missing: " + token)
        print("Phase342 busyloop counter frontier audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase342 marker missing in check-only mode")
    if "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1" not in before:
        raise SystemExit("Phase342 requires Phase341 recorder lineage")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase342 busyloop counter frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
