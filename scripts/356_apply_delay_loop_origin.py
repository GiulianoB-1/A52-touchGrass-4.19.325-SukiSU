#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
TIMER = Path("drivers/clocksource/arm_arch_timer.c")
MARK = "A52_PHASE356_DELAY_LOOP_ORIGIN_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase356 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE356_DELAY_LOOP_ORIGIN_V1
 *
 * Phase355 sampled the surviving CPU's interrupted execution context and
 * repeatedly found PID1/init at runtime PC __const_udelay+0x74. Exact Image
 * disassembly shows this is the counter read immediately after WFE in the
 * event-stream delay loop. Phase356 samples the live registers needed to
 * distinguish two failures:
 *   (A) get_cycles()/CNTVCT advances but x21 is a huge delay target; identify
 *       the caller from the PAC-signed LR saved at [sp+8].
 *   (B) x0 does not advance while timer IRQs continue; the counter/read path
 *       itself is broken.
 *
 * Reuses concluded diagnostic storage:
 *   0xB1BF0000..0xB1BF7FFF (32 KiB)
 *   copy A = +0x0000..+0x3fff
 *   copy B = +0x4000..+0x7fff
 */
#define A52_R356_SIDEBAND_PHYS   0xB1BF0000ULL
#define A52_R356_SIDEBAND_BYTES  0x8000U
#define A52_R356_COPY_BYTES      0x4000U
#define A52_R356_SLOT_BYTES      256U
#define A52_R356_SAMPLE_SLOTS    63U
#define A52_R356_META_OFF        0x3f00U
#define A52_R356_MAGIC           0x3635334749524f44ULL /* "DORIG356" */
#define A52_R356_META_MAGIC      0x213635334154454dULL /* "META356!" */
#define A52_R356_COMMIT          0x356c0de5U
#define A52_R356_ARM_NS          11500000000ULL
#define A52_R356_BURST_SAMPLES   32U
#define A52_R356_SPARSE_SAMPLES  30U
#define A52_R356_SPARSE_DIV      250U

#define A52_R356_F_REGS          BIT(0)
#define A52_R356_F_KERNEL        BIT(1)
#define A52_R356_F_DELAY_PC      BIT(2)
#define A52_R356_F_STACK_OK      BIT(3)

struct a52_r356_sample {
	u64 magic;
	u64 ns;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 pstate;
	u64 runtime_text;
	u64 sequence;
	u64 tick_index;
	u64 x0;
	u64 x19;
	u64 x20;
	u64 x21;
	u64 x22;
	u64 x29;
	u64 stack_qword[4];
	u64 loops_per_jiffy;
	u64 delay_symbol;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 preempt;
	u32 task_flags;
	u32 sample_flags;
	u32 sample_class;
	u32 slot_index;
	s32 stack_rc;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[28];
};

struct a52_r356_meta {
	u64 magic;
	u64 init_ns;
	u64 phys;
	u64 arm_ns;
	u64 runtime_text;
	u64 delay_symbol;
	u64 udelay_symbol;
	u64 sequence;
	u32 commit;
	u32 version;
	u32 sample_slots;
	u32 slot_bytes;
	u32 owner_cpu;
	u32 reserved32;
	u64 reserved[21];
};

extern char _text[];
static void *a52_r356_sideband;
static atomic_t a52_r356_owner = ATOMIC_INIT(-1);
static u32 a52_r356_tick_count;

static void a52_r356_store(unsigned int pos, const void *src, size_t bytes)
{
	void *dst0;
	void *dst1;

	dst0 = (u8 *)a52_r356_sideband + pos;
	dst1 = (u8 *)a52_r356_sideband + A52_R356_COPY_BYTES + pos;
	memcpy(dst0, src, bytes);
	memcpy(dst1, src, bytes);
	wmb();
	__flush_dcache_area(dst0, bytes);
	__flush_dcache_area(dst1, bytes);
}

static void a52_r356_publish_owner(u32 cpu)
{
	void *dst0;
	void *dst1;
	unsigned int off;

	off = A52_R356_META_OFF + offsetof(struct a52_r356_meta, owner_cpu);
	dst0 = (u8 *)a52_r356_sideband + off;
	dst1 = (u8 *)a52_r356_sideband + A52_R356_COPY_BYTES + off;
	WRITE_ONCE(*(u32 *)dst0, cpu);
	WRITE_ONCE(*(u32 *)dst1, cpu);
	wmb();
	__flush_dcache_area(dst0, sizeof(cpu));
	__flush_dcache_area(dst1, sizeof(cpu));
}

void a52_p356_delay_loop_sample(void)
{
	struct a52_r356_sample s;
	struct pt_regs *regs;
	u64 now;
	u64 delay_lo;
	u64 delay_hi;
	u32 cpu;
	u32 tick;
	u32 slot;
	u32 sample_class;
	u32 second;
	int owner;

	if (!READ_ONCE(a52_r356_sideband))
		return;
	now = ktime_get_ns();
	if (now < A52_R356_ARM_NS)
		return;
	if (current->pid != 1)
		return;

	regs = get_irq_regs();
	if (!regs || user_mode(regs))
		return;

	delay_lo = (u64)(unsigned long)__const_udelay;
	delay_hi = (u64)(unsigned long)__udelay;
	if (regs->pc < delay_lo || regs->pc >= delay_hi)
		return;

	cpu = (u32)raw_smp_processor_id();
	if (cpu >= 8U)
		return;

	owner = atomic_read(&a52_r356_owner);
	if (owner < 0) {
		if (atomic_cmpxchg(&a52_r356_owner, -1, (int)cpu) == -1) {
			owner = (int)cpu;
			a52_r356_publish_owner(cpu);
		} else {
			owner = atomic_read(&a52_r356_owner);
		}
	}
	if (owner != (int)cpu)
		return;

	tick = ++a52_r356_tick_count;
	if (tick <= A52_R356_BURST_SAMPLES) {
		slot = tick - 1U;
		sample_class = 0U;
	} else if ((tick % A52_R356_SPARSE_DIV) == 0U) {
		second = tick / A52_R356_SPARSE_DIV;
		if (second <= A52_R356_SPARSE_SAMPLES) {
			slot = 31U + second;
			sample_class = 1U;
		} else {
			slot = 62U;
			sample_class = 2U;
		}
	} else {
		return;
	}

	BUILD_BUG_ON(sizeof(struct a52_r356_sample) != A52_R356_SLOT_BYTES);
	memset(&s, 0, sizeof(s));
	s.magic = A52_R356_MAGIC;
	s.ns = now;
	s.pc = regs->pc;
	s.lr = regs->regs[30];
	s.sp = regs->sp;
	s.pstate = regs->pstate;
	s.runtime_text = (u64)(unsigned long)_text;
	s.sequence = (u64)atomic64_read(&a52_r179_sequence);
	s.tick_index = tick;
	s.x0 = regs->regs[0];
	s.x19 = regs->regs[19];
	s.x20 = regs->regs[20];
	s.x21 = regs->regs[21];
	s.x22 = regs->regs[22];
	s.x29 = regs->regs[29];
	s.loops_per_jiffy = READ_ONCE(loops_per_jiffy);
	s.delay_symbol = delay_lo;
	s.cpu = cpu;
	s.pid = (u32)current->pid;
	s.tgid = (u32)current->tgid;
	s.preempt = (u32)preempt_count();
	s.task_flags = (u32)current->flags;
	s.sample_flags = A52_R356_F_REGS | A52_R356_F_KERNEL | A52_R356_F_DELAY_PC;
	s.sample_class = sample_class;
	s.slot_index = slot;
	s.stack_rc = (s32)copy_from_kernel_nofault(s.stack_qword,
		(const void *)(unsigned long)regs->sp, sizeof(s.stack_qword));
	if (!s.stack_rc)
		s.sample_flags |= A52_R356_F_STACK_OK;
	s.commit = A52_R356_COMMIT;
	s.version = 1U;
	memcpy(s.comm, current->comm, TASK_COMM_LEN);

	a52_r356_store(slot * A52_R356_SLOT_BYTES, &s, sizeof(s));
}

static void a52_r356_start(void)
{
	struct a52_r356_meta meta;
	void *dst0;
	void *dst1;

	BUILD_BUG_ON(sizeof(struct a52_r356_sample) != A52_R356_SLOT_BYTES);
	BUILD_BUG_ON(sizeof(struct a52_r356_meta) != A52_R356_SLOT_BYTES);
	BUILD_BUG_ON(A52_R356_META_OFF + A52_R356_SLOT_BYTES != A52_R356_COPY_BYTES);
	BUILD_BUG_ON(A52_R356_SAMPLE_SLOTS * A52_R356_SLOT_BYTES != A52_R356_META_OFF);

	a52_r356_sideband = memremap(A52_R356_SIDEBAND_PHYS,
		A52_R356_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r356_sideband) {
		a52_ackfr_record("P276 356A map=0");
		return;
	}

	memset(a52_r356_sideband, 0, A52_R356_SIDEBAND_BYTES);
	atomic_set(&a52_r356_owner, -1);
	a52_r356_tick_count = 0U;

	memset(&meta, 0, sizeof(meta));
	meta.magic = A52_R356_META_MAGIC;
	meta.init_ns = ktime_get_ns();
	meta.phys = A52_R356_SIDEBAND_PHYS;
	meta.arm_ns = A52_R356_ARM_NS;
	meta.runtime_text = (u64)(unsigned long)_text;
	meta.delay_symbol = (u64)(unsigned long)__const_udelay;
	meta.udelay_symbol = (u64)(unsigned long)__udelay;
	meta.sequence = (u64)atomic64_read(&a52_r179_sequence);
	meta.commit = A52_R356_COMMIT;
	meta.version = 1U;
	meta.sample_slots = A52_R356_SAMPLE_SLOTS;
	meta.slot_bytes = A52_R356_SLOT_BYTES;
	meta.owner_cpu = ~0U;
	dst0 = (u8 *)a52_r356_sideband + A52_R356_META_OFF;
	dst1 = (u8 *)a52_r356_sideband + A52_R356_COPY_BYTES + A52_R356_META_OFF;
	memcpy(dst0, &meta, sizeof(meta));
	memcpy(dst1, &meta, sizeof(meta));
	wmb();
	__flush_dcache_area(a52_r356_sideband, A52_R356_SIDEBAND_BYTES);
	a52_ackfr_record("P276 356A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase356 requires Phase343 recorder lineage")
    text = one(text,
               "#include <linux/io.h>\n",
               "#include <linux/io.h>\n#include <linux/delay.h>\n#include <linux/uaccess.h>\n#include <asm/irq_regs.h>\n#include <asm/ptrace.h>\n",
               "recorder includes")
    anchor = "static void *a52_r343_sideband;\n"
    text = one(text, anchor, RAW_BLOCK + anchor, "probe insertion")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r356_start();\n\ta52_r343_start();\n",
               "late-init mapping")
    return text


def patch_timer(text: str) -> str:
    if MARK in text:
        return text
    inc = "#include <linux/interrupt.h>\n"
    text = one(text, inc,
               inc + "\nextern void a52_p356_delay_loop_sample(void); /* " + MARK + " */\n",
               "timer extern")
    text = one(text,
               "\t\tevt->event_handler(evt);\n",
               "\t\ta52_p356_delay_loop_sample();\n\t\tevt->event_handler(evt);\n",
               "timer sample boundary")
    return text


def validate(br: str, ar: str, bt: str, at: str) -> None:
    joined = ar + at
    for token in (
        MARK,
        "A52_R356_SIDEBAND_PHYS   0xB1BF0000ULL",
        "A52_R356_SLOT_BYTES      256U",
        "A52_R356_COMMIT          0x356c0de5U",
        "A52_R356_ARM_NS          11500000000ULL",
        "a52_r356_start();",
        "a52_p356_delay_loop_sample();",
        "regs->regs[0]",
        "regs->regs[19]",
        "regs->regs[21]",
        "copy_from_kernel_nofault",
        "loops_per_jiffy",
        "__const_udelay",
        "__udelay",
    ):
        if token not in joined:
            raise SystemExit("Phase356 required token missing: " + token)
    if at.count("evt->event_handler(evt);") != bt.count("evt->event_handler(evt);"):
        raise SystemExit("Phase356 changed protected timer callback count")
    if ar.count("a52_r356_start();") != 1:
        raise SystemExit("Phase356 recorder start count mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    files = [ns.root / REC, ns.root / TIMER]
    for p in files:
        if not p.is_file():
            raise SystemExit("Phase356 missing source: " + str(p))
    br, bt = (p.read_text() for p in files)
    ar = patch_rec(br)
    at = patch_timer(bt)
    validate(br, ar, bt, at)
    if ns.check_only:
        print("Phase356 delay-loop origin audit: PASS")
        return 0
    files[0].write_text(ar)
    files[1].write_text(at)
    print("Phase356 delay-loop origin probe applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
