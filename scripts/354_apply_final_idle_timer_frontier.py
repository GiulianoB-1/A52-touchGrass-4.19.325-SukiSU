#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
IDLE = Path("kernel/sched/idle.c")
TIMER = Path("drivers/clocksource/arm_arch_timer.c")
MARK = "A52_PHASE354_FINAL_IDLE_TIMER_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase354 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE354_FINAL_IDLE_TIMER_FRONTIER_V1
 *
 * Phase353 showed that an architected timer callback still ENTERS and RETURNS
 * at ~12.200009 s, so a simple "WFI never wakes" explanation is insufficient.
 * Phase353 recorded only the FIRST event after each threshold, though, and
 * therefore could miss a later non-returning transition in the same bucket.
 *
 * Phase354 arms only at 12.150 s and continuously overwrites a tiny per-CPU
 * record with the LAST observed architectural-idle and architected-timer
 * boundaries.  Counts make unmatched ENTER/RETURN pairs explicit.  The short
 * arm window keeps perturbation bounded to the final ~75 ms of the known
 * collapse interval.
 *
 * Reuses the concluded Phase351/353 diagnostic region:
 *   0xB1BF4000..0xB1BF7FFF (16 KiB)
 *   copy A = +0x0000..+0x1fff
 *   copy B = +0x2000..+0x3fff
 */
#define A52_R354_SIDEBAND_PHYS   0xB1BF4000ULL
#define A52_R354_SIDEBAND_BYTES  0x4000U
#define A52_R354_COPY_BYTES      0x2000U
#define A52_R354_SLOT_BYTES      128U
#define A52_R354_CPU_COUNT       8U
#define A52_R354_META_OFF        0x1000U
#define A52_R354_MAGIC           0x3435334c414e4946ULL /* "FINAL354" */
#define A52_R354_META_MAGIC      0x213435334154454dULL /* "META354!" */
#define A52_R354_COMMIT          0x354c0de5U
#define A52_R354_ARM_NS          12150000000ULL

#define A52_R354_ARCH_ENTER      0U
#define A52_R354_ARCH_RETURN     1U
#define A52_R354_TIMER_ENTER     2U
#define A52_R354_TIMER_RETURN    3U

struct a52_r354_slot {
	u64 magic;
	u64 armed_ns;
	u64 last_event_ns;
	u64 arch_enter_ns;
	u64 arch_return_ns;
	u64 timer_enter_ns;
	u64 timer_return_ns;
	u64 sequence;
	u32 arch_enter_count;
	u32 arch_return_count;
	u32 timer_enter_count;
	u32 timer_return_count;
	u32 last_kind;
	u32 cpu;
	u32 commit;
	u32 version;
	u64 reserved[4];
};

struct a52_r354_meta {
	u64 magic;
	u64 init_ns;
	u64 phys;
	u64 arm_ns;
	u64 sequence;
	u32 commit;
	u32 version;
	u32 cpu_count;
	u32 slot_bytes;
	u64 reserved[8];
};

static void *a52_r354_sideband;
static u32 a52_r354_counts[A52_R354_CPU_COUNT][4];

static __always_inline void a52_r354_write_u64(unsigned int pos,
		unsigned int field_off, u64 value)
{
	void *dst0;
	void *dst1;

	dst0 = (u8 *)a52_r354_sideband + pos + field_off;
	dst1 = (u8 *)a52_r354_sideband + A52_R354_COPY_BYTES + pos + field_off;
	WRITE_ONCE(*(u64 *)dst0, value);
	WRITE_ONCE(*(u64 *)dst1, value);
	wmb();
	__flush_dcache_area(dst0, sizeof(value));
	__flush_dcache_area(dst1, sizeof(value));
}

static __always_inline void a52_r354_write_u32(unsigned int pos,
		unsigned int field_off, u32 value)
{
	void *dst0;
	void *dst1;

	dst0 = (u8 *)a52_r354_sideband + pos + field_off;
	dst1 = (u8 *)a52_r354_sideband + A52_R354_COPY_BYTES + pos + field_off;
	WRITE_ONCE(*(u32 *)dst0, value);
	WRITE_ONCE(*(u32 *)dst1, value);
	wmb();
	__flush_dcache_area(dst0, sizeof(value));
	__flush_dcache_area(dst1, sizeof(value));
}

void a52_p354_mark_final(u32 kind)
{
	u64 now;
	u64 sequence;
	unsigned int cpu;
	unsigned int pos;
	unsigned int ts_off;
	unsigned int count_off;
	u32 count;

	if (!READ_ONCE(a52_r354_sideband) || kind > A52_R354_TIMER_RETURN)
		return;
	now = ktime_get_ns();
	if (now < A52_R354_ARM_NS)
		return;
	cpu = (unsigned int)raw_smp_processor_id();
	if (cpu >= A52_R354_CPU_COUNT)
		return;

	if (kind == A52_R354_ARCH_ENTER) {
		ts_off = offsetof(struct a52_r354_slot, arch_enter_ns);
		count_off = offsetof(struct a52_r354_slot, arch_enter_count);
	} else if (kind == A52_R354_ARCH_RETURN) {
		ts_off = offsetof(struct a52_r354_slot, arch_return_ns);
		count_off = offsetof(struct a52_r354_slot, arch_return_count);
	} else if (kind == A52_R354_TIMER_ENTER) {
		ts_off = offsetof(struct a52_r354_slot, timer_enter_ns);
		count_off = offsetof(struct a52_r354_slot, timer_enter_count);
	} else {
		ts_off = offsetof(struct a52_r354_slot, timer_return_ns);
		count_off = offsetof(struct a52_r354_slot, timer_return_count);
	}

	pos = cpu * A52_R354_SLOT_BYTES;
	count = ++a52_r354_counts[cpu][kind];
	sequence = (u64)atomic64_read(&a52_r179_sequence);

	/* Publish event-specific state first; last_kind is the commit marker. */
	a52_r354_write_u64(pos, ts_off, now);
	a52_r354_write_u32(pos, count_off, count);
	a52_r354_write_u64(pos, offsetof(struct a52_r354_slot, sequence), sequence);
	a52_r354_write_u64(pos, offsetof(struct a52_r354_slot, last_event_ns), now);
	a52_r354_write_u32(pos, offsetof(struct a52_r354_slot, last_kind), kind);
}

static void a52_r354_start(void)
{
	struct a52_r354_slot slot;
	struct a52_r354_meta meta;
	unsigned int cpu;
	unsigned int pos;
	void *dst0;
	void *dst1;

	BUILD_BUG_ON(sizeof(struct a52_r354_slot) != A52_R354_SLOT_BYTES);
	BUILD_BUG_ON(sizeof(struct a52_r354_meta) != A52_R354_SLOT_BYTES);

	a52_r354_sideband = memremap(A52_R354_SIDEBAND_PHYS,
		A52_R354_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r354_sideband) {
		a52_ackfr_record("P276 354A map=0");
		return;
	}

	memset(a52_r354_sideband, 0, A52_R354_SIDEBAND_BYTES);
	memset(a52_r354_counts, 0, sizeof(a52_r354_counts));

	for (cpu = 0; cpu < A52_R354_CPU_COUNT; cpu++) {
		memset(&slot, 0, sizeof(slot));
		slot.magic = A52_R354_MAGIC;
		slot.armed_ns = A52_R354_ARM_NS;
		slot.last_kind = ~0U;
		slot.cpu = cpu;
		slot.commit = A52_R354_COMMIT;
		slot.version = 1U;
		pos = cpu * A52_R354_SLOT_BYTES;
		dst0 = (u8 *)a52_r354_sideband + pos;
		dst1 = (u8 *)a52_r354_sideband + A52_R354_COPY_BYTES + pos;
		memcpy(dst0, &slot, sizeof(slot));
		memcpy(dst1, &slot, sizeof(slot));
	}

	memset(&meta, 0, sizeof(meta));
	meta.magic = A52_R354_META_MAGIC;
	meta.init_ns = ktime_get_ns();
	meta.phys = A52_R354_SIDEBAND_PHYS;
	meta.arm_ns = A52_R354_ARM_NS;
	meta.sequence = (u64)atomic64_read(&a52_r179_sequence);
	meta.commit = A52_R354_COMMIT;
	meta.version = 1U;
	meta.cpu_count = A52_R354_CPU_COUNT;
	meta.slot_bytes = A52_R354_SLOT_BYTES;
	dst0 = (u8 *)a52_r354_sideband + A52_R354_META_OFF;
	dst1 = (u8 *)a52_r354_sideband + A52_R354_COPY_BYTES + A52_R354_META_OFF;
	memcpy(dst0, &meta, sizeof(meta));
	memcpy(dst1, &meta, sizeof(meta));
	wmb();
	__flush_dcache_area(a52_r354_sideband, A52_R354_SIDEBAND_BYTES);
	a52_ackfr_record("P276 354A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase354 requires Phase343 recorder lineage")
    anchor = "static void *a52_r343_sideband;\n"
    text = one(text, anchor, RAW_BLOCK + anchor, "raw helper insertion")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r354_start();\n\ta52_r343_start();\n",
               "late-init mapping")
    return text


def patch_idle(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <trace/hooks/sched.h>\n"
    text = one(text, inc,
               inc + "\nextern void a52_p354_mark_final(u32 kind); /* " + MARK + " */\n",
               "idle extern")
    text = one(text,
               "\t\tarch_cpu_idle();\n",
               "\t\ta52_p354_mark_final(0U);\n\t\tarch_cpu_idle();\n\t\ta52_p354_mark_final(1U);\n",
               "arch idle boundary")
    return text


def patch_timer(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <linux/interrupt.h>\n"
    text = one(text, inc,
               inc + "\nextern void a52_p354_mark_final(u32 kind); /* " + MARK + " */\n",
               "timer extern")
    text = one(text,
               "\t\tevt->event_handler(evt);\n",
               "\t\ta52_p354_mark_final(2U);\n\t\tevt->event_handler(evt);\n\t\ta52_p354_mark_final(3U);\n",
               "architected timer event boundary")
    return text


def validate(br: str, ar: str, bi: str, ai: str, bt: str, at: str) -> None:
    joined = ar + ai + at
    for token in (
        MARK,
        "A52_R354_SIDEBAND_PHYS   0xB1BF4000ULL",
        "A52_R354_COMMIT          0x354c0de5U",
        "A52_R354_ARM_NS          12150000000ULL",
        "a52_r354_start();",
        "a52_p354_mark_final(0U);",
        "a52_p354_mark_final(1U);",
        "a52_p354_mark_final(2U);",
        "a52_p354_mark_final(3U);",
    ):
        if token not in joined:
            raise SystemExit("Phase354 required token missing: " + token)

    for token in ("arch_cpu_idle();", "evt->event_handler(evt);"):
        before = bi + bt
        after = ai + at
        if before.count(token) != after.count(token):
            raise SystemExit("Phase354 changed protected call count: " + token)

    if ar.count("a52_r354_start();") != 1:
        raise SystemExit("Phase354 recorder start count mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    files = [ns.root / REC, ns.root / IDLE, ns.root / TIMER]
    for p in files:
        if not p.is_file():
            raise SystemExit("Phase354 missing source: " + str(p))

    br, bi, bt = (p.read_text() for p in files)
    ar = patch_rec(br)
    ai = patch_idle(bi)
    at = patch_timer(bt)
    validate(br, ar, bi, ai, bt, at)

    if ns.check_only:
        print("Phase354 final idle/timer frontier audit: PASS")
        return 0

    for p, text in zip(files, (ar, ai, at)):
        p.write_text(text)
    print("Phase354 final idle/timer frontier applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
