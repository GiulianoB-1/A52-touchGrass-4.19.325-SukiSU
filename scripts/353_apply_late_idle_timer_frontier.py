#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
IDLE = Path("kernel/sched/idle.c")
TIMER = Path("drivers/clocksource/arm_arch_timer.c")
MARK = "A52_PHASE353_LATE_IDLE_TIMER_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase353 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE353_LATE_IDLE_TIMER_FRONTIER_V1
 *
 * Phase352 proved that generic cpuidle is unavailable on every observed CPU
 * and the real fallback is default_idle_call() -> arch_cpu_idle().  Phase353
 * asks the time-critical question: near the ~12 s global freeze, does a CPU
 * enter architectural idle and fail to return, and does the architected timer
 * IRQ still reach/return from its event handler?
 *
 * To keep perturbation tiny, each CPU records only the FIRST matching event
 * after eight fixed boot-time thresholds.  Each field is written at most once.
 *
 * Dedicated sideband, previously unused in this diagnostic lineage:
 *   0xB1BF4000..0xB1BF7FFF (16 KiB)
 *   copy A = +0x0000..+0x1fff
 *   copy B = +0x2000..+0x3fff
 *
 * In each 8 KiB copy:
 *   +0x0000..+0x0fff : 8 buckets x 8 CPUs x 64-byte timing slots
 *   +0x1000..+0x11ff : 8 x 64-byte cpuidle identity slots
 *   +0x1800           : 64-byte INIT/meta slot
 */
#define A52_R353_SIDEBAND_PHYS   0xB1BF4000ULL
#define A52_R353_SIDEBAND_BYTES  0x4000U
#define A52_R353_COPY_BYTES      0x2000U
#define A52_R353_SLOT_BYTES      64U
#define A52_R353_BUCKETS         8U
#define A52_R353_CPU_COUNT       8U
#define A52_R353_IDENTITY_OFF    0x1000U
#define A52_R353_META_OFF        0x1800U
#define A52_R353_MAGIC           0x3335334c44495441ULL
#define A52_R353_META_MAGIC      0x3335334154454d50ULL
#define A52_R353_COMMIT          0x353c0de5U

#define A52_R353_ARCH_ENTER      0U
#define A52_R353_ARCH_RETURN     1U
#define A52_R353_TIMER_ENTER     2U
#define A52_R353_TIMER_RETURN    3U

struct a52_r353_slot {
	u64 magic;
	u64 threshold_ns;
	u64 arch_enter_ns;
	u64 arch_return_ns;
	u64 timer_enter_ns;
	u64 timer_return_ns;
	u32 cpu;
	u16 bucket;
	u16 reserved;
	u32 commit;
	u32 version;
};

struct a52_r353_identity {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 reserved0;
	u32 cpu;
	u32 reason;
	u32 state_count;
	u32 dev_enabled;
	u32 commit;
	u32 version;
	u64 reserved1;
};

struct a52_r353_meta {
	u64 magic;
	u64 ns;
	u64 phys;
	u64 sequence;
	u32 commit;
	u32 version;
	u32 buckets;
	u32 cpus;
	u64 reserved0;
	u64 reserved1;
};

static const u64 a52_r353_threshold_ns[A52_R353_BUCKETS] = {
	10500000000ULL,
	11000000000ULL,
	11500000000ULL,
	11700000000ULL,
	11850000000ULL,
	12000000000ULL,
	12150000000ULL,
	12200000000ULL,
};

static void *a52_r353_sideband;
static unsigned long a52_r353_seen[A52_R353_CPU_COUNT][4];
static unsigned long a52_r353_identity_seen;

static __always_inline void a52_r353_write_u64(unsigned int pos, unsigned int field_off, u64 value)
{
	void *dst0;
	void *dst1;

	dst0 = (u8 *)a52_r353_sideband + pos + field_off;
	dst1 = (u8 *)a52_r353_sideband + A52_R353_COPY_BYTES + pos + field_off;
	WRITE_ONCE(*(u64 *)dst0, value);
	WRITE_ONCE(*(u64 *)dst1, value);
	wmb();
	__flush_dcache_area(dst0, sizeof(value));
	__flush_dcache_area(dst1, sizeof(value));
}

void a52_p353_mark_late(u32 kind)
{
	u64 now;
	unsigned int cpu;
	unsigned int bucket;
	unsigned int pos;
	unsigned int field_off;

	if (!READ_ONCE(a52_r353_sideband) || kind > A52_R353_TIMER_RETURN)
		return;
	cpu = (unsigned int)raw_smp_processor_id();
	if (cpu >= A52_R353_CPU_COUNT)
		return;
	now = ktime_get_ns();
	if (now < a52_r353_threshold_ns[0])
		return;

	if (kind == A52_R353_ARCH_ENTER)
		field_off = offsetof(struct a52_r353_slot, arch_enter_ns);
	else if (kind == A52_R353_ARCH_RETURN)
		field_off = offsetof(struct a52_r353_slot, arch_return_ns);
	else if (kind == A52_R353_TIMER_ENTER)
		field_off = offsetof(struct a52_r353_slot, timer_enter_ns);
	else
		field_off = offsetof(struct a52_r353_slot, timer_return_ns);

	for (bucket = 0; bucket < A52_R353_BUCKETS; bucket++) {
		if (now < a52_r353_threshold_ns[bucket])
			break;
		if (test_and_set_bit(bucket, &a52_r353_seen[cpu][kind]))
			continue;
		pos = (bucket * A52_R353_CPU_COUNT + cpu) * A52_R353_SLOT_BYTES;
		a52_r353_write_u64(pos, field_off, now);
	}
}

void a52_p353_cpuidle_identity(u32 reason, u32 state_count, u32 dev_enabled)
{
	struct a52_r353_identity slot;
	unsigned int cpu;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r353_sideband))
		return;
	if (ktime_get_ns() < a52_r353_threshold_ns[0])
		return;
	cpu = (unsigned int)raw_smp_processor_id();
	if (cpu >= A52_R353_CPU_COUNT)
		return;
	if (test_and_set_bit(cpu, &a52_r353_identity_seen))
		return;

	BUILD_BUG_ON(sizeof(struct a52_r353_identity) != A52_R353_SLOT_BYTES);
	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_R353_MAGIC;
	slot.ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.cpu = cpu;
	slot.reason = reason;
	slot.state_count = state_count;
	slot.dev_enabled = dev_enabled;
	slot.commit = A52_R353_COMMIT;
	slot.version = 1U;

	pos = A52_R353_IDENTITY_OFF + cpu * A52_R353_SLOT_BYTES;
	dst0 = (u8 *)a52_r353_sideband + pos;
	dst1 = (u8 *)a52_r353_sideband + A52_R353_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static void a52_r353_start(void)
{
	struct a52_r353_slot slot;
	struct a52_r353_meta meta;
	unsigned int cpu;
	unsigned int bucket;
	unsigned int pos;
	void *dst0;
	void *dst1;

	BUILD_BUG_ON(sizeof(struct a52_r353_slot) != A52_R353_SLOT_BYTES);
	BUILD_BUG_ON(sizeof(struct a52_r353_meta) != A52_R353_SLOT_BYTES);
	BUILD_BUG_ON(A52_R353_BUCKETS * A52_R353_CPU_COUNT *
		A52_R353_SLOT_BYTES != 0x1000U);

	a52_r353_sideband = memremap(A52_R353_SIDEBAND_PHYS,
		A52_R353_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r353_sideband) {
		a52_ackfr_record("P276 353A map=0");
		return;
	}

	memset(a52_r353_sideband, 0, A52_R353_SIDEBAND_BYTES);
	memset(a52_r353_seen, 0, sizeof(a52_r353_seen));
	a52_r353_identity_seen = 0;

	for (bucket = 0; bucket < A52_R353_BUCKETS; bucket++) {
		for (cpu = 0; cpu < A52_R353_CPU_COUNT; cpu++) {
			memset(&slot, 0, sizeof(slot));
			slot.magic = A52_R353_MAGIC;
			slot.threshold_ns = a52_r353_threshold_ns[bucket];
			slot.cpu = cpu;
			slot.bucket = (u16)bucket;
			slot.commit = A52_R353_COMMIT;
			slot.version = 1U;
			pos = (bucket * A52_R353_CPU_COUNT + cpu) * A52_R353_SLOT_BYTES;
			dst0 = (u8 *)a52_r353_sideband + pos;
			dst1 = (u8 *)a52_r353_sideband + A52_R353_COPY_BYTES + pos;
			memcpy(dst0, &slot, sizeof(slot));
			memcpy(dst1, &slot, sizeof(slot));
		}
	}

	memset(&meta, 0, sizeof(meta));
	meta.magic = A52_R353_META_MAGIC;
	meta.ns = ktime_get_ns();
	meta.phys = A52_R353_SIDEBAND_PHYS;
	meta.sequence = (u64)atomic64_read(&a52_r179_sequence);
	meta.commit = A52_R353_COMMIT;
	meta.version = 1U;
	meta.buckets = A52_R353_BUCKETS;
	meta.cpus = A52_R353_CPU_COUNT;
	dst0 = (u8 *)a52_r353_sideband + A52_R353_META_OFF;
	dst1 = (u8 *)a52_r353_sideband + A52_R353_COPY_BYTES + A52_R353_META_OFF;
	memcpy(dst0, &meta, sizeof(meta));
	memcpy(dst1, &meta, sizeof(meta));
	wmb();
	__flush_dcache_area(a52_r353_sideband, A52_R353_SIDEBAND_BYTES);
	a52_ackfr_record("P276 353A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase353 requires Phase343 recorder lineage")
    anchor = "static void *a52_r343_sideband;\n"
    text = one(text, anchor, RAW_BLOCK + anchor, "raw helper insertion")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r353_start();\n\ta52_r343_start();\n",
               "late-init mapping")
    return text


def patch_idle(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <trace/hooks/sched.h>\n"
    text = one(text, inc,
               inc + "\nextern void a52_p353_mark_late(u32 kind); /* " + MARK + " */\n"
                     "extern void a52_p353_cpuidle_identity(u32 reason, u32 state_count, u32 dev_enabled);\n",
               "idle extern")

    old = "\tif (cpuidle_not_available(drv, dev)) {\n"
    new = (
        "\ta52_p353_cpuidle_identity((drv ? 0U : 1U) | (dev ? 0U : 2U) |\n"
        "\t\t((dev && !dev->enabled) ? 4U : 0U),\n"
        "\t\tdrv ? (u32)drv->state_count : 0U,\n"
        "\t\tdev ? (dev->enabled ? 1U : 0U) : 0U);\n"
        "\tif (cpuidle_not_available(drv, dev)) {\n"
    )
    text = one(text, old, new, "cpuidle identity")

    old = "\t\tarch_cpu_idle();\n"
    new = (
        "\t\ta52_p353_mark_late(0U);\n"
        "\t\tarch_cpu_idle();\n"
        "\t\ta52_p353_mark_late(1U);\n"
    )
    text = one(text, old, new, "arch idle boundary")
    return text


def patch_timer(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <linux/interrupt.h>\n"
    text = one(text, inc,
               inc + "\nextern void a52_p353_mark_late(u32 kind); /* " + MARK + " */\n",
               "timer extern")

    old = "\t\tevt->event_handler(evt);\n"
    new = (
        "\t\ta52_p353_mark_late(2U);\n"
        "\t\tevt->event_handler(evt);\n"
        "\t\ta52_p353_mark_late(3U);\n"
    )
    text = one(text, old, new, "architected timer event boundary")
    return text


def validate(br: str, ar: str, bi: str, ai: str, bt: str, at: str) -> None:
    joined = ar + ai + at
    for token in (
        MARK,
        "A52_R353_SIDEBAND_PHYS   0xB1BF4000ULL",
        "A52_R353_COMMIT          0x353c0de5U",
        "10500000000ULL",
        "11700000000ULL",
        "12200000000ULL",
        "a52_r353_start();",
        "a52_p353_mark_late(0U);",
        "a52_p353_mark_late(1U);",
        "a52_p353_mark_late(2U);",
        "a52_p353_mark_late(3U);",
        "a52_p353_cpuidle_identity",
    ):
        if token not in joined:
            raise SystemExit("Phase353 required token missing: " + token)

    for token in (
        "arch_cpu_idle();",
        "cpuidle_not_available(drv, dev)",
        "evt->event_handler(evt);",
    ):
        before = bi + bt
        after = ai + at
        if before.count(token) != after.count(token):
            raise SystemExit("Phase353 changed protected call count: " + token)

    if ar.count("a52_r353_start();") != 1:
        raise SystemExit("Phase353 recorder start count mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    files = {p: ns.root / p for p in (REC, IDLE, TIMER)}
    for p, f in files.items():
        if not f.is_file():
            raise SystemExit("Phase353 source missing: " + str(p))
    src = {p: files[p].read_text() for p in files}

    if all(MARK in src[p] for p in files):
        validate(src[REC], src[REC], src[IDLE], src[IDLE], src[TIMER], src[TIMER])
        print("Phase353 late idle/timer frontier audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase353 marker missing in check-only mode")

    out = dict(src)
    out[REC] = patch_rec(src[REC])
    out[IDLE] = patch_idle(src[IDLE])
    out[TIMER] = patch_timer(src[TIMER])
    validate(src[REC], out[REC], src[IDLE], out[IDLE], src[TIMER], out[TIMER])
    for p in files:
        files[p].write_text(out[p])

    print("Phase353 late idle/timer frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
