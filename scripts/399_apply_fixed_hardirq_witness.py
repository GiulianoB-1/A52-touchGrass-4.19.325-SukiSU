#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE399_FIXED_HARDIRQ_WITNESS_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase399 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE398_DENSE_AP_CLIFF_V1" not in text:
        raise SystemExit("Phase399 requires Phase398")

    old = """#define A52_R341_COPY_BYTES (A52_R341_SIDEBAND_BYTES / 2U)
#define A52_R341_SLOT_BYTES 64U
#define A52_R341_SLOTS_PER_COPY (A52_R341_COPY_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
"""
    new = """#define A52_R341_COPY_BYTES (A52_R341_SIDEBAND_BYTES / 2U)
#define A52_R341_SLOT_BYTES 64U
/* A52_PHASE399_FIXED_HARDIRQ_WITNESS_V1
 * Reserve the final three 64-byte slots in each physical copy for fixed
 * per-CPU last-tick witnesses.  The circular R341 ring uses the bytes before
 * them.  copy1 witness ends exactly at 0xB1BFF800, where N396 begins.
 */
#define A52_P399_WITNESS_SLOT_BYTES 64U
#define A52_P399_WITNESS_SLOTS 3U
#define A52_P399_WITNESS_BYTES (A52_P399_WITNESS_SLOT_BYTES * A52_P399_WITNESS_SLOTS)
#define A52_R341_RING_BYTES (A52_R341_COPY_BYTES - A52_P399_WITNESS_BYTES)
#define A52_R341_SLOTS_PER_COPY (A52_R341_RING_BYTES / A52_R341_SLOT_BYTES)
#define A52_R341_MAGIC 0x3134335244353241ULL
#define A52_P399_WITNESS_MAGIC 0x3939335749525148ULL
#define A52_P399_WITNESS_COMMIT 0x399c0de5U
"""
    text = one(text, old, new, "geometry")

    anchor = """struct a52_r341_cpu_timer {
	struct hrtimer timer;
	u32 ticks;
	u32 cpu;
};

static void *a52_r341_sideband;
"""
    block = """struct a52_r341_cpu_timer {
	struct hrtimer timer;
	u32 ticks;
	u32 cpu;
};

struct a52_p399_witness {
	u64 magic;
	u64 monotonic_ns;
	u64 jiffies64;
	u64 r48_sequence;
	u32 cpu;
	u32 tick;
	u32 tick_inv;
	u32 slot;
	u32 version;
	u32 commit;
	u32 preempt_count;
	u32 irqs_disabled;
} __packed;

static void *a52_r341_sideband;
"""
    text = one(text, anchor, block, "witness struct")

    anchor = """static atomic_t a52_r341_sideband_index = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_r341_cpu_timer, a52_r341_timers);

static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)
"""
    helper = """static atomic_t a52_r341_sideband_index = ATOMIC_INIT(0);
static DEFINE_PER_CPU(struct a52_r341_cpu_timer, a52_r341_timers);

static int a52_p399_witness_slot(u32 cpu)
{
	if (cpu == 0U)
		return 0;
	if (cpu == 5U)
		return 1;
	if (cpu == 7U)
		return 2;
	return -1;
}

static void a52_p399_witness_write(u32 cpu, u32 tick)
{
	struct a52_p399_witness w;
	int slot = a52_p399_witness_slot(cpu);
	void *dst0;
	void *dst1;

	if (slot < 0 || !READ_ONCE(a52_r341_sideband))
		return;

	memset(&w, 0, sizeof(w));
	w.magic = A52_P399_WITNESS_MAGIC;
	w.monotonic_ns = ktime_get_ns();
	w.jiffies64 = get_jiffies_64();
	w.r48_sequence = (u64)atomic64_read(&a52_r179_sequence);
	w.cpu = cpu;
	w.tick = tick;
	w.tick_inv = ~tick;
	w.slot = (u32)slot;
	w.version = 1U;
	w.commit = A52_P399_WITNESS_COMMIT;
	w.preempt_count = (u32)preempt_count();
	w.irqs_disabled = irqs_disabled() ? 1U : 0U;

	BUILD_BUG_ON(sizeof(struct a52_p399_witness) != A52_P399_WITNESS_SLOT_BYTES);
	dst0 = (u8 *)a52_r341_sideband + A52_R341_RING_BYTES +
		(unsigned int)slot * A52_P399_WITNESS_SLOT_BYTES;
	dst1 = (u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
		A52_R341_RING_BYTES +
		(unsigned int)slot * A52_P399_WITNESS_SLOT_BYTES;
	memcpy(dst0, &w, sizeof(w));
	memcpy(dst1, &w, sizeof(w));
	wmb();
	__flush_dcache_area(dst0, sizeof(w));
	__flush_dcache_area(dst1, sizeof(w));
}

static void a52_r341_sideband_write(u32 event, u32 cpu, u32 tick)
"""
    text = one(text, anchor, helper, "witness helper")

    old = """	ct->ticks++;
	a52_r341_sideband_write(A52_R341_EVT_HRTIMER,
		(u32)smp_processor_id(), ct->ticks);

	if (ct->ticks >= A52_R341_LIMIT)
"""
    new = """	ct->ticks++;
	a52_r341_sideband_write(A52_R341_EVT_HRTIMER,
		(u32)smp_processor_id(), ct->ticks);
	a52_p399_witness_write((u32)smp_processor_id(), ct->ticks);

	if (ct->ticks >= A52_R341_LIMIT)
"""
    text = one(text, old, new, "tick witness")

    old = """	ct->timer.function = a52_r341_hrtimer_fn;
	a52_r341_sideband_write(A52_R341_EVT_CPU_START, cpu, 0U);
	hrtimer_start(&ct->timer, ms_to_ktime(A52_R341_INTERVAL_MS),
"""
    new = """	ct->timer.function = a52_r341_hrtimer_fn;
	a52_r341_sideband_write(A52_R341_EVT_CPU_START, cpu, 0U);
	a52_p399_witness_write(cpu, 0U);
	hrtimer_start(&ct->timer, ms_to_ktime(A52_R341_INTERVAL_MS),
"""
    text = one(text, old, new, "start witness")

    old = """	BUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
	BUILD_BUG_ON(A52_R341_COPY_BYTES % A52_R341_SLOT_BYTES);

	a52_r341_sideband = memremap(A52_R341_SIDEBAND_PHYS,
"""
    new = """	BUILD_BUG_ON(sizeof(struct a52_r341_slot) != A52_R341_SLOT_BYTES);
	BUILD_BUG_ON(A52_R341_RING_BYTES % A52_R341_SLOT_BYTES);
	BUILD_BUG_ON(A52_R341_RING_BYTES + A52_P399_WITNESS_BYTES !=
		     A52_R341_COPY_BYTES);

	a52_r341_sideband = memremap(A52_R341_SIDEBAND_PHYS,
"""
    text = one(text, old, new, "geometry assertions")

    old = """	if (!a52_r341_sideband) {
		a52_ackfr_record("P276 341A map=0 mask=0");
		return;
	}

	for (i = 0; i < ARRAY_SIZE(targets); i++) {
"""
    new = """	if (!a52_r341_sideband) {
		a52_ackfr_record("P276 341A map=0 mask=0");
		return;
	}

	memset((u8 *)a52_r341_sideband + A52_R341_RING_BYTES, 0,
	       A52_P399_WITNESS_BYTES);
	memset((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
	       A52_R341_RING_BYTES, 0, A52_P399_WITNESS_BYTES);
	wmb();
	__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_RING_BYTES,
			    A52_P399_WITNESS_BYTES);
	__flush_dcache_area((u8 *)a52_r341_sideband + A52_R341_COPY_BYTES +
			    A52_R341_RING_BYTES, A52_P399_WITNESS_BYTES);

	for (i = 0; i < ARRAY_SIZE(targets); i++) {
"""
    text = one(text, old, new, "clear witnesses")

    text = one(text,
        '"BOOT rs=ready phase=398 focus=ap-cliff roots=%u copies=2 crc=crc32c"',
        '"BOOT rs=ready phase=399 focus=irq-witness roots=%u copies=2 crc=crc32c"',
        "boot identity")
    return text


def validate(root: Path) -> None:
    t = (root / REC).read_text(errors="replace")
    for token in (
        MARK,
        "A52_R341_RING_BYTES",
        "A52_P399_WITNESS_BYTES",
        "A52_P399_WITNESS_MAGIC",
        "a52_p399_witness_write",
        "a52_p399_witness_write((u32)smp_processor_id(), ct->ticks);",
        "a52_p399_witness_write(cpu, 0U);",
        "BOOT rs=ready phase=399 focus=irq-witness roots=%u copies=2 crc=crc32c",
    ):
        if token not in t:
            raise SystemExit("Phase399 missing token: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    p = ns.root / REC
    if not p.is_file():
        raise SystemExit("Phase399 recorder missing")
    if not ns.check_only:
        p.write_text(patch(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase399 fixed hardirq witness: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
