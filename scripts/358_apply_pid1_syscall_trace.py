#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MARK = "A52_PHASE358_PID1_SYSCALL_TRACE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase358 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def one_of(text: str, choices: list[tuple[str, str]], label: str) -> str:
    hits = [(old, new) for old, new in choices if text.count(old) == 1]
    if len(hits) != 1:
        counts = ", ".join(str(text.count(old)) for old, _ in choices)
        raise SystemExit(f"Phase358 {label}: expected exactly one usable anchor, counts=[{counts}]")
    old, new = hits[0]
    return text.replace(old, new, 1)


INCLUDES = r'''#include <linux/syscalls.h>
#include <linux/build_bug.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/mm.h>
#include <linux/sched.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <asm/cacheflush.h>
'''


BLOCK = r'''
/* A52_PHASE358_PID1_SYSCALL_TRACE_V1
 *
 * Phase357 proved that PID1 reaches do_group_exit()/do_exit() with 0x7f00,
 * then hits the global-init panic. Phase358 moves one layer earlier and keeps
 * the last 32 arm64 syscalls issued by PID1 in ordinary RAM. No persistent
 * cache maintenance is done during normal operation.
 *
 * When PID1 enters exit(2) or exit_group(2), the 32-entry history is written
 * in chronological order to the Phase357 32 KiB sideband and the final syscall
 * snapshot is replicated 32 times. This minimizes perturbation before failure
 * while retaining strong resistance to pmsg clear-bit damage.
 *
 * Phase358 owns 0xB1BF0000..0xB1BF7FFF. Phase357's mapper is deliberately
 * disabled by this phase, while its source and panic instrumentation remain
 * compiled for lineage.
 */
#define A52_R358_SIDEBAND_PHYS    0xB1BF0000ULL
#define A52_R358_SIDEBAND_BYTES   0x8000U
#define A52_R358_COPY_BYTES       0x4000U
#define A52_R358_SLOT_BYTES       256U
#define A52_R358_HISTORY_SLOTS    32U
#define A52_R358_FINAL_FIRST      32U
#define A52_R358_SLOTS_PER_COPY   64U
#define A52_R358_MAGIC            0x3835335449584550ULL /* "PEXIT358" */
#define A52_R358_COMMIT           0x358c0de5U
#define A52_R358_VERSION          1U

#define A52_R358_EVT_ENTRY        1U
#define A52_R358_EVT_RETURN       2U
#define A52_R358_EVT_FINAL_EXIT   3U

struct a52_r358_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 pc;
	u64 sp;
	u64 pstate;
	u64 fp;
	u64 lr;
	u64 x[9];
	u64 mm;
	u64 start_code;
	u64 end_code;
	u64 start_data;
	u64 end_data;
	u64 start_stack;
	s64 ret;
	u32 event;
	u32 syscallno;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ring_index;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[16];
};

static const char a52_r358_marker[] __used = "A52_PHASE358_PID1_SYSCALL_TRACE_V1";
static void *a52_r358_sideband;
static struct a52_r358_record a52_r358_ring[A52_R358_HISTORY_SLOTS];
static u64 a52_r358_sequence;
static u32 a52_r358_count;

static void a52_r358_fill(struct a52_r358_record *r,
			  struct pt_regs *regs, int scno, u32 event,
			  u64 sequence, s64 ret)
{
	struct mm_struct *mm = current->mm;
	unsigned int i;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R358_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->pc = regs->pc;
	r->sp = regs->sp;
	r->pstate = regs->pstate;
	r->fp = regs->regs[29];
	r->lr = regs->regs[30];
	r->x[0] = regs->orig_x0;
	for (i = 1; i <= 8; i++)
		r->x[i] = regs->regs[i];
	r->mm = (u64)(unsigned long)mm;
	if (mm) {
		r->start_code = READ_ONCE(mm->start_code);
		r->end_code = READ_ONCE(mm->end_code);
		r->start_data = READ_ONCE(mm->start_data);
		r->end_data = READ_ONCE(mm->end_data);
		r->start_stack = READ_ONCE(mm->start_stack);
	}
	r->ret = ret;
	r->event = event;
	r->syscallno = (u32)scno;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->ring_index = (u32)((sequence - 1ULL) % A52_R358_HISTORY_SLOTS);
	r->commit = A52_R358_COMMIT;
	r->version = A52_R358_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
}

static void a52_r358_write_slot(unsigned int slot,
				const struct a52_r358_record *r)
{
	unsigned int pos = slot * A52_R358_SLOT_BYTES;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r358_sideband) || !r ||
	    slot >= A52_R358_SLOTS_PER_COPY)
		return;

	dst0 = (u8 *)a52_r358_sideband + pos;
	dst1 = (u8 *)a52_r358_sideband + A52_R358_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
}

static void a52_r358_persist(const struct a52_r358_record *final)
{
	u64 first_sequence;
	u64 sequence;
	u32 count;
	unsigned int i;
	unsigned int pos;
	const struct a52_r358_record *src;

	if (!READ_ONCE(a52_r358_sideband) || !final)
		return;

	count = READ_ONCE(a52_r358_count);
	if (count > A52_R358_HISTORY_SLOTS)
		count = A52_R358_HISTORY_SLOTS;
	first_sequence = final->sequence - (u64)count + 1ULL;

	/* Slots 0..31: chronological syscall history, oldest to newest. */
	for (i = 0; i < count; i++) {
		sequence = first_sequence + (u64)i;
		pos = (unsigned int)((sequence - 1ULL) % A52_R358_HISTORY_SLOTS);
		src = &a52_r358_ring[pos];
		if (READ_ONCE(src->sequence) == sequence &&
		    READ_ONCE(src->commit) == A52_R358_COMMIT)
			a52_r358_write_slot(i, src);
	}

	/* Slots 32..63: replicated non-returning exit/exit_group entry. */
	for (i = A52_R358_FINAL_FIRST; i < A52_R358_SLOTS_PER_COPY; i++)
		a52_r358_write_slot(i, final);

	wmb();
	__flush_dcache_area(a52_r358_sideband, A52_R358_SIDEBAND_BYTES);
}

static u64 a52_r358_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r358_record r;
	u64 sequence;
	unsigned int pos;
	u32 event = A52_R358_EVT_ENTRY;

	if (current->pid != 1 || !READ_ONCE(a52_r358_sideband))
		return 0;

	sequence = ++a52_r358_sequence;
	pos = (unsigned int)((sequence - 1ULL) % A52_R358_HISTORY_SLOTS);
	if (scno == __NR_exit_group || scno == __NR_exit)
		event = A52_R358_EVT_FINAL_EXIT;

	a52_r358_fill(&r, regs, scno, event, sequence,
			(s64)(1ULL << 63));
	memcpy(&a52_r358_ring[pos], &r, sizeof(r));
	if (a52_r358_count < A52_R358_HISTORY_SLOTS)
		a52_r358_count++;

	if (event == A52_R358_EVT_FINAL_EXIT)
		a52_r358_persist(&r);

	return sequence;
}

static void a52_r358_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r358_record r;
	unsigned int pos;

	if (!sequence || current->pid != 1)
		return;

	pos = (unsigned int)((sequence - 1ULL) % A52_R358_HISTORY_SLOTS);
	if (READ_ONCE(a52_r358_ring[pos].sequence) != sequence)
		return;

	a52_r358_fill(&r, regs, scno, A52_R358_EVT_RETURN, sequence,
			(s64)regs->regs[0]);
	memcpy(&a52_r358_ring[pos], &r, sizeof(r));
}

static int __init a52_r358_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r358_record) != A52_R358_SLOT_BYTES);
	BUILD_BUG_ON(A52_R358_SLOTS_PER_COPY * A52_R358_SLOT_BYTES !=
		     A52_R358_COPY_BYTES);
	BUILD_BUG_ON(A52_R358_HISTORY_SLOTS != A52_R358_FINAL_FIRST);

	a52_r358_sideband = memremap(A52_R358_SIDEBAND_PHYS,
		A52_R358_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r358_sideband)
		return 0;

	memset(a52_r358_sideband, 0, A52_R358_SIDEBAND_BYTES);
	memset(a52_r358_ring, 0, sizeof(a52_r358_ring));
	a52_r358_sequence = 0;
	a52_r358_count = 0;
	wmb();
	__flush_dcache_area(a52_r358_sideband, A52_R358_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r358_init);

'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text

    text = one(text, "#include <linux/syscalls.h>\n", INCLUDES, "syscall includes")
    text = one(text,
               "long sys_ni_syscall(void);\n",
               "long sys_ni_syscall(void);\n" + BLOCK,
               "recorder insertion")

    text = one_of(
        text,
        [
            (
                "\tunsigned long flags = read_thread_flags();\n",
                "\tunsigned long flags = read_thread_flags();\n"
                "\tu64 a52_r358_sequence_token = 0;\n",
            ),
            (
                "\tunsigned long flags = current_thread_info()->flags;\n",
                "\tunsigned long flags = current_thread_info()->flags;\n"
                "\tu64 a52_r358_sequence_token = 0;\n",
            ),
        ],
        "el0_svc flags",
    )

    invoke = "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
    wrapped = (
        "\ta52_r358_sequence_token = a52_r358_sys_enter(regs, scno);\n"
        + invoke +
        "\ta52_r358_sys_return(regs, scno, a52_r358_sequence_token);\n"
    )
    text = one(text, invoke, wrapped, "invoke_syscall wrapper")
    return text


def patch_recorder(text: str) -> str:
    if "A52_PHASE357_INIT_EXIT_CAUSE_V1" not in text:
        raise SystemExit("Phase358 requires Phase357 recorder lineage")

    if "static void __used a52_r357_start(void)" not in text:
        text = one(text,
                   "static void a52_r357_start(void)\n",
                   "static void __used a52_r357_start(void)\n",
                   "mark Phase357 mapper dormant")

    if "a52_r357_start();" in text:
        text = one(text,
                   "\ta52_r357_start();\n",
                   "\t/* Phase358 owns the Phase357 sideband at runtime. */\n",
                   "disable Phase357 sideband start")
    return text


def validate(before_sys: str, after_sys: str,
             before_rec: str, after_rec: str) -> None:
    for token in (
        MARK,
        "A52_R358_SIDEBAND_PHYS    0xB1BF0000ULL",
        "A52_R358_HISTORY_SLOTS    32U",
        "A52_R358_FINAL_FIRST      32U",
        "A52_R358_COMMIT           0x358c0de5U",
        "a52_r358_sys_enter(regs, scno);",
        "a52_r358_sys_return(regs, scno, a52_r358_sequence_token);",
        "late_initcall(a52_r358_init);",
        "scno == __NR_exit_group || scno == __NR_exit",
    ):
        if token not in after_sys:
            raise SystemExit("Phase358 required token missing: " + token)

    if after_sys.count("invoke_syscall(regs, scno, sc_nr, syscall_table);") != \
            before_sys.count("invoke_syscall(regs, scno, sc_nr, syscall_table);"):
        raise SystemExit("Phase358 changed invoke_syscall call count")
    if after_sys.count("a52_r358_sys_enter(regs, scno);") != 1:
        raise SystemExit("Phase358 syscall-enter hook count mismatch")
    if after_sys.count("a52_r358_sys_return(regs, scno, a52_r358_sequence_token);") != 1:
        raise SystemExit("Phase358 syscall-return hook count mismatch")

    if after_rec.count("a52_r357_start();") != before_rec.count("a52_r357_start();") - 1:
        raise SystemExit("Phase358 must disable exactly one Phase357 runtime mapper")
    if "static void __used a52_r357_start(void)" not in after_rec:
        raise SystemExit("Phase358 must retain dormant Phase357 mapper for lineage")

    for token in (
        "A52_PHASE357_INIT_EXIT_CAUSE_V1",
        "a52_p357_persist_init_panic",
        "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1",
        "A52_PHASE346_DMA_RAW_SIDEBAND_V1",
    ):
        if after_rec.count(token) != before_rec.count(token):
            raise SystemExit("Phase358 changed inherited recorder token count: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    sys_path = ns.root / SYS
    rec_path = ns.root / REC
    for p in (sys_path, rec_path):
        if not p.is_file():
            raise SystemExit("Phase358 missing source: " + str(p))

    before_sys = sys_path.read_text(encoding="utf-8")
    before_rec = rec_path.read_text(encoding="utf-8")

    if MARK in before_sys:
        for token in (
            "A52_R358_SIDEBAND_PHYS    0xB1BF0000ULL",
            "A52_R358_COMMIT           0x358c0de5U",
            "a52_r358_sys_enter(regs, scno);",
            "a52_r358_sys_return(regs, scno, a52_r358_sequence_token);",
            "late_initcall(a52_r358_init);",
        ):
            if token not in before_sys:
                raise SystemExit("Phase358 check-only token missing: " + token)
        if "a52_r357_start();" in before_rec:
            raise SystemExit("Phase358 check-only: Phase357 runtime mapper still enabled")
        if "static void __used a52_r357_start(void)" not in before_rec:
            raise SystemExit("Phase358 check-only: dormant Phase357 mapper not retained")
        print("Phase358 PID1 syscall trace audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase358 marker missing in check-only mode")

    after_sys = patch_syscall(before_sys)
    after_rec = patch_recorder(before_rec)
    validate(before_sys, after_sys, before_rec, after_rec)

    sys_path.write_text(after_sys, encoding="utf-8")
    rec_path.write_text(after_rec, encoding="utf-8")
    print("Phase358 PID1 syscall trace applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
