#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
TIMER = Path("drivers/clocksource/arm_arch_timer.c")
MARK = "A52_PHASE355_TIMER_PC_SAMPLER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase355 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE355_TIMER_PC_SAMPLER_V1
 *
 * Phase354 proved that after 12.150 s one CPU (CPU6 in that capture) remains
 * alive and services the ARM architected timer at ~250 Hz for hundreds of
 * seconds, while it never reaches arch_cpu_idle().  The clockevent callback
 * itself returns.  This strongly suggests that the surviving CPU is spinning
 * or blocked in an IRQ-enabled execution context.
 *
 * Phase355 turns the timer IRQ into a low-overhead statistical PC sampler.
 * The first CPU to deliver an architected timer IRQ after 12.150 s becomes the
 * owner.  We persist:
 *   - first 64 consecutive timer samples (~256 ms at 250 Hz), then
 *   - one sample/second for 62 s, then
 *   - one rolling latest sample once/second thereafter.
 *
 * Each sample captures the interrupted pt_regs PC/LR/SP/PSTATE, current task,
 * PID/TGID/comm, preempt count, task flags, user/kernel context, runtime _text
 * and the flight-recorder sequence.  Runtime _text makes PC symbolication
 * robust even if the kernel image is relocated.
 *
 * Reclaims two concluded diagnostic regions (Phase350 + Phase351/354):
 *   0xB1BF0000..0xB1BF7FFF (32 KiB)
 *   copy A = +0x0000..+0x3fff
 *   copy B = +0x4000..+0x7fff
 */
#define A52_R355_SIDEBAND_PHYS   0xB1BF0000ULL
#define A52_R355_SIDEBAND_BYTES  0x8000U
#define A52_R355_COPY_BYTES      0x4000U
#define A52_R355_SLOT_BYTES      128U
#define A52_R355_SAMPLE_SLOTS    127U
#define A52_R355_META_OFF        0x3f80U
#define A52_R355_MAGIC           0x353533504d415354ULL /* "TSAMP355" */
#define A52_R355_META_MAGIC      0x213535334154454dULL /* "META355!" */
#define A52_R355_COMMIT          0x355c0de5U
#define A52_R355_ARM_NS          12150000000ULL
#define A52_R355_BURST_SAMPLES   64U
#define A52_R355_SPARSE_SAMPLES  62U
#define A52_R355_SPARSE_DIV      250U

#define A52_R355_F_REGS          BIT(0)
#define A52_R355_F_USER          BIT(1)
#define A52_R355_F_HAS_MM        BIT(2)

struct a52_r355_sample {
	u64 magic;
	u64 ns;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 pstate;
	u64 runtime_text;
	u64 sequence;
	u64 tick_index;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 preempt;
	u32 task_flags;
	u32 sample_flags;
	u32 sample_class;
	u32 slot_index;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
};

struct a52_r355_meta {
	u64 magic;
	u64 init_ns;
	u64 phys;
	u64 arm_ns;
	u64 runtime_text;
	u64 sequence;
	u32 commit;
	u32 version;
	u32 sample_slots;
	u32 slot_bytes;
	u32 owner_cpu;
	u32 reserved32;
	u64 reserved[7];
};

extern char _text[];
static void *a52_r355_sideband;
static atomic_t a52_r355_owner = ATOMIC_INIT(-1);
static u32 a52_r355_tick_count;

static void a52_r355_store(unsigned int pos, const void *src, size_t bytes)
{
	void *dst0;
	void *dst1;

	dst0 = (u8 *)a52_r355_sideband + pos;
	dst1 = (u8 *)a52_r355_sideband + A52_R355_COPY_BYTES + pos;
	memcpy(dst0, src, bytes);
	memcpy(dst1, src, bytes);
	wmb();
	__flush_dcache_area(dst0, bytes);
	__flush_dcache_area(dst1, bytes);
}

static void a52_r355_publish_owner(u32 cpu)
{
	void *dst0;
	void *dst1;
	unsigned int off;

	off = A52_R355_META_OFF + offsetof(struct a52_r355_meta, owner_cpu);
	dst0 = (u8 *)a52_r355_sideband + off;
	dst1 = (u8 *)a52_r355_sideband + A52_R355_COPY_BYTES + off;
	WRITE_ONCE(*(u32 *)dst0, cpu);
	WRITE_ONCE(*(u32 *)dst1, cpu);
	wmb();
	__flush_dcache_area(dst0, sizeof(cpu));
	__flush_dcache_area(dst1, sizeof(cpu));
}

void a52_p355_timer_sample(void)
{
	struct a52_r355_sample s;
	struct pt_regs *regs;
	u64 now;
	u32 cpu;
	u32 tick;
	u32 slot;
	u32 sample_class;
	u32 second;
	int owner;

	if (!READ_ONCE(a52_r355_sideband))
		return;
	now = ktime_get_ns();
	if (now < A52_R355_ARM_NS)
		return;
	cpu = (u32)raw_smp_processor_id();
	if (cpu >= 8U)
		return;

	owner = atomic_read(&a52_r355_owner);
	if (owner < 0) {
		if (atomic_cmpxchg(&a52_r355_owner, -1, (int)cpu) == -1) {
			owner = (int)cpu;
			a52_r355_publish_owner(cpu);
		} else {
			owner = atomic_read(&a52_r355_owner);
		}
	}
	if (owner != (int)cpu)
		return;

	tick = ++a52_r355_tick_count;
	if (tick <= A52_R355_BURST_SAMPLES) {
		slot = tick - 1U;
		sample_class = 0U;
	} else if ((tick % A52_R355_SPARSE_DIV) == 0U) {
		second = tick / A52_R355_SPARSE_DIV;
		if (second <= A52_R355_SPARSE_SAMPLES) {
			slot = 63U + second;
			sample_class = 1U;
		} else {
			slot = 126U;
			sample_class = 2U;
		}
	} else {
		return;
	}

	BUILD_BUG_ON(sizeof(struct a52_r355_sample) != A52_R355_SLOT_BYTES);
	memset(&s, 0, sizeof(s));
	s.magic = A52_R355_MAGIC;
	s.ns = now;
	s.runtime_text = (u64)(unsigned long)_text;
	s.sequence = (u64)atomic64_read(&a52_r179_sequence);
	s.tick_index = tick;
	s.cpu = cpu;
	s.pid = (u32)current->pid;
	s.tgid = (u32)current->tgid;
	s.preempt = (u32)preempt_count();
	s.task_flags = (u32)current->flags;
	s.sample_class = sample_class;
	s.slot_index = slot;
	s.commit = A52_R355_COMMIT;
	s.version = 1U;
	memcpy(s.comm, current->comm, TASK_COMM_LEN);
	if (current->mm)
		s.sample_flags |= A52_R355_F_HAS_MM;

	regs = get_irq_regs();
	if (regs) {
		s.sample_flags |= A52_R355_F_REGS;
		s.pc = regs->pc;
		s.lr = regs->regs[30];
		s.sp = regs->sp;
		s.pstate = regs->pstate;
		if (user_mode(regs))
			s.sample_flags |= A52_R355_F_USER;
	}

	a52_r355_store(slot * A52_R355_SLOT_BYTES, &s, sizeof(s));
}

static void a52_r355_start(void)
{
	struct a52_r355_meta meta;
	void *dst0;
	void *dst1;

	BUILD_BUG_ON(sizeof(struct a52_r355_meta) != A52_R355_SLOT_BYTES);
	BUILD_BUG_ON(A52_R355_META_OFF + A52_R355_SLOT_BYTES != A52_R355_COPY_BYTES);
	BUILD_BUG_ON(A52_R355_SAMPLE_SLOTS * A52_R355_SLOT_BYTES != A52_R355_META_OFF);

	a52_r355_sideband = memremap(A52_R355_SIDEBAND_PHYS,
		A52_R355_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r355_sideband) {
		a52_ackfr_record("P276 355A map=0");
		return;
	}

	memset(a52_r355_sideband, 0, A52_R355_SIDEBAND_BYTES);
	atomic_set(&a52_r355_owner, -1);
	a52_r355_tick_count = 0U;

	memset(&meta, 0, sizeof(meta));
	meta.magic = A52_R355_META_MAGIC;
	meta.init_ns = ktime_get_ns();
	meta.phys = A52_R355_SIDEBAND_PHYS;
	meta.arm_ns = A52_R355_ARM_NS;
	meta.runtime_text = (u64)(unsigned long)_text;
	meta.sequence = (u64)atomic64_read(&a52_r179_sequence);
	meta.commit = A52_R355_COMMIT;
	meta.version = 1U;
	meta.sample_slots = A52_R355_SAMPLE_SLOTS;
	meta.slot_bytes = A52_R355_SLOT_BYTES;
	meta.owner_cpu = ~0U;
	dst0 = (u8 *)a52_r355_sideband + A52_R355_META_OFF;
	dst1 = (u8 *)a52_r355_sideband + A52_R355_COPY_BYTES + A52_R355_META_OFF;
	memcpy(dst0, &meta, sizeof(meta));
	memcpy(dst1, &meta, sizeof(meta));
	wmb();
	__flush_dcache_area(a52_r355_sideband, A52_R355_SIDEBAND_BYTES);
	a52_ackfr_record("P276 355A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase355 requires Phase343 recorder lineage")

    text = one(text,
               "#include <linux/io.h>\n",
               "#include <linux/io.h>\n#include <asm/irq_regs.h>\n#include <asm/ptrace.h>\n",
               "recorder irq-reg includes")
    anchor = "static void *a52_r343_sideband;\n"
    text = one(text, anchor, RAW_BLOCK + anchor, "sampler insertion")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r355_start();\n\ta52_r343_start();\n",
               "late-init mapping")
    return text


def patch_timer(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <linux/interrupt.h>\n"
    text = one(text, inc,
               inc + "\nextern void a52_p355_timer_sample(void); /* " + MARK + " */\n",
               "timer extern")
    text = one(text,
               "\t\tevt->event_handler(evt);\n",
               "\t\ta52_p355_timer_sample();\n\t\tevt->event_handler(evt);\n",
               "timer sample boundary")
    return text


def validate(br: str, ar: str, bt: str, at: str) -> None:
    joined = ar + at
    for token in (
        MARK,
        "A52_R355_SIDEBAND_PHYS   0xB1BF0000ULL",
        "A52_R355_SIDEBAND_BYTES  0x8000U",
        "A52_R355_COMMIT          0x355c0de5U",
        "A52_R355_ARM_NS          12150000000ULL",
        "A52_R355_BURST_SAMPLES   64U",
        "A52_R355_SPARSE_DIV      250U",
        "a52_r355_start();",
        "a52_p355_timer_sample();",
        "get_irq_regs();",
        "regs->pc",
        "current->comm",
    ):
        if token not in joined:
            raise SystemExit("Phase355 required token missing: " + token)

    if at.count("evt->event_handler(evt);") != bt.count("evt->event_handler(evt);"):
        raise SystemExit("Phase355 changed protected timer callback count")
    if ar.count("a52_r355_start();") != 1:
        raise SystemExit("Phase355 recorder start count mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    files = [ns.root / REC, ns.root / TIMER]
    for p in files:
        if not p.is_file():
            raise SystemExit("Phase355 missing source: " + str(p))

    br, bt = (p.read_text() for p in files)
    ar = patch_rec(br)
    at = patch_timer(bt)
    validate(br, ar, bt, at)

    if ns.check_only:
        print("Phase355 timer PC sampler audit: PASS")
        return 0

    files[0].write_text(ar)
    files[1].write_text(at)
    print("Phase355 timer PC sampler applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
