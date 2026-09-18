#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE366_APEXD_FUTEX_THREADS_V1"

BLOCK = r'''
/* A52_PHASE366_APEXD_FUTEX_THREADS_V1
 *
 * Phase365 recovered the apexd group leader's property-service writev:
 * PROP_MSG_SETPROP2("apexd.status", "starting"). The same process later enters
 * a permanent FUTEX_WAIT_BITSET_PRIVATE before apexd.status reaches activated
 * or ready.
 *
 * Trace futex activity from every thread whose group leader is "apexd".
 * Each call occupies one 256-byte slot and is rewritten in-place on return.
 * The latest group-leader FUTEX_WAIT* call is additionally replicated into
 * eight reserved slots, so the final blocking wait survives worker traffic.
 *
 * No futex arguments, scheduling, return values, userspace memory or Android
 * behavior are modified.
 */
#define A52_R366_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R366_SIDEBAND_BYTES     0x8000U
#define A52_R366_COPY_BYTES         0x4000U
#define A52_R366_SLOT_BYTES         256U
#define A52_R366_RING_SLOTS         56U
#define A52_R366_LATCH_FIRST        56U
#define A52_R366_LATCH_REPLICAS     8U
#define A52_R366_SLOTS_PER_COPY     64U
#define A52_R366_MAGIC              0x3636335854554641ULL
#define A52_R366_COMMIT             0x366c0de5U
#define A52_R366_VERSION            1U

#define A52_R366_EVT_ENTRY          1U
#define A52_R366_EVT_RETURN         2U

struct a52_r366_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 uaddr;
	u64 val;
	u64 timeout;
	u64 uaddr2;
	u64 val3;
	u64 target_uaddr;
	s64 ret;
	u32 event;
	u32 syscallno;
	u32 op;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ring_index;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[136];
};

static const char a52_r366_marker[] __used =
	"A52_PHASE366_APEXD_FUTEX_THREADS_V1";
static void *a52_r366_sideband;
static atomic64_t a52_r366_sequence = ATOMIC64_INIT(0);
static u64 a52_r366_main_target;

static bool a52_r366_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static bool a52_r366_wait_op(u32 op)
{
	u32 cmd = op & FUTEX_CMD_MASK;

	return cmd == FUTEX_WAIT || cmd == FUTEX_WAIT_BITSET;
}

static void a52_r366_fill(struct a52_r366_record *r,
			  struct pt_regs *regs, u32 event,
			  u64 sequence, s64 ret)
{
	memset(r, 0, sizeof(*r));
	r->magic = A52_R366_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->uaddr = regs->orig_x0;
	r->op = (u32)regs->regs[1];
	r->val = regs->regs[2];
	r->timeout = regs->regs[3];
	r->uaddr2 = regs->regs[4];
	r->val3 = regs->regs[5];
	r->target_uaddr = READ_ONCE(a52_r366_main_target);
	r->ret = ret;
	r->event = event;
	r->syscallno = __NR_futex;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->ring_index = (u32)((sequence - 1ULL) % A52_R366_RING_SLOTS);
	r->commit = A52_R366_COMMIT;
	r->version = A52_R366_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
}

static void a52_r366_write_slot(unsigned int slot,
				const struct a52_r366_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r366_sideband) || !r ||
	    slot >= A52_R366_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R366_SLOT_BYTES;
	dst0 = (u8 *)a52_r366_sideband + pos;
	dst1 = (u8 *)a52_r366_sideband + A52_R366_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r366_write_latch(const struct a52_r366_record *r)
{
	unsigned int i;

	for (i = 0; i < A52_R366_LATCH_REPLICAS; i++)
		a52_r366_write_slot(A52_R366_LATCH_FIRST + i, r);
}

static u64 a52_r366_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r366_record r;
	u64 sequence;
	unsigned int slot;
	u32 op;

	if (scno != __NR_futex || !READ_ONCE(a52_r366_sideband) ||
	    !a52_r366_apexd_thread())
		return 0;

	op = (u32)regs->regs[1];
	if (current->pid == current->tgid && a52_r366_wait_op(op))
		WRITE_ONCE(a52_r366_main_target, regs->orig_x0);

	sequence = (u64)atomic64_inc_return(&a52_r366_sequence);
	slot = (unsigned int)((sequence - 1ULL) % A52_R366_RING_SLOTS);
	a52_r366_fill(&r, regs, A52_R366_EVT_ENTRY, sequence,
		      (s64)(1ULL << 63));
	a52_r366_write_slot(slot, &r);

	if (current->pid == current->tgid && a52_r366_wait_op(op))
		a52_r366_write_latch(&r);

	return sequence;
}

static void a52_r366_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r366_record r;
	unsigned int slot;
	u32 op;

	if (!sequence || scno != __NR_futex ||
	    !READ_ONCE(a52_r366_sideband) || !a52_r366_apexd_thread())
		return;

	op = (u32)regs->regs[1];
	slot = (unsigned int)((sequence - 1ULL) % A52_R366_RING_SLOTS);
	a52_r366_fill(&r, regs, A52_R366_EVT_RETURN, sequence,
		      (s64)regs->regs[0]);
	a52_r366_write_slot(slot, &r);

	if (current->pid == current->tgid && a52_r366_wait_op(op))
		a52_r366_write_latch(&r);
}

static int __init a52_r366_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r366_record) != A52_R366_SLOT_BYTES);
	BUILD_BUG_ON(A52_R366_SLOTS_PER_COPY * A52_R366_SLOT_BYTES !=
		     A52_R366_COPY_BYTES);
	BUILD_BUG_ON(A52_R366_RING_SLOTS + A52_R366_LATCH_REPLICAS !=
		     A52_R366_SLOTS_PER_COPY);

	a52_r366_sideband = memremap(A52_R366_SIDEBAND_PHYS,
		A52_R366_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r366_sideband)
		return 0;

	memset(a52_r366_sideband, 0, A52_R366_SIDEBAND_BYTES);
	atomic64_set(&a52_r366_sequence, 0);
	WRITE_ONCE(a52_r366_main_target, 0);
	wmb();
	__flush_dcache_area(a52_r366_sideband, A52_R366_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r366_init);

'''


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase366 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R366_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R366_RING_SLOTS         56U",
        "A52_R366_LATCH_REPLICAS     8U",
        "A52_R366_COMMIT             0x366c0de5U",
        "#include <linux/futex.h>",
        "get_task_comm(comm, current->group_leader);",
        "a52_r366_sys_enter(regs, scno);",
        "a52_r366_sys_return(regs, scno, a52_r366_sequence_token);",
        "late_initcall(a52_r366_init);",
    ):
        if token not in text:
            raise SystemExit("Phase366 required token missing: " + token)
    if "late_initcall(a52_r365_init);" in text:
        raise SystemExit("Phase366 must disable the Phase365 sideband owner")
    if text.count("invoke_syscall(regs, scno, sc_nr, syscall_table);") != 1:
        raise SystemExit("Phase366 invoke_syscall count mismatch")


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE365_APEXD_WRITEV_FUTEX_V1" not in text:
        raise SystemExit("Phase366 requires Phase365 syscall lineage")

    if "#include <linux/futex.h>\n" not in text:
        text = one(
            text,
            "#include <linux/uio.h>\n",
            "#include <linux/uio.h>\n#include <linux/futex.h>\n",
            "futex constants include",
        )

    text = one(
        text,
        "static int __init a52_r365_init(void)\n",
        "static int __init __used a52_r365_init(void)\n",
        "mark Phase365 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r365_init);\n",
        "/* Phase366 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase365 mapper",
    )

    anchor = (
        'static const char a52_r365_marker[] __used =\n'
        '\t"A52_PHASE365_APEXD_WRITEV_FUTEX_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase366 block insertion")

    text = one(
        text,
        "\tu64 a52_r365_sequence_token = 0;\n",
        "\tu64 a52_r365_sequence_token = 0;\n"
        "\tu64 a52_r366_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r365_sequence_token = a52_r365_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r365_sys_return(regs, scno, a52_r365_sequence_token);\n"
    )
    new = (
        "\ta52_r365_sequence_token = a52_r365_sys_enter(regs, scno);\n"
        "\ta52_r366_sequence_token = a52_r366_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r366_sys_return(regs, scno, a52_r366_sequence_token);\n"
        "\ta52_r365_sys_return(regs, scno, a52_r365_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYS
    if not path.is_file():
        raise SystemExit("Phase366 syscall source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        validate(before)
        print("Phase366 apexd futex thread audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase366 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase366 apexd futex thread trace applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
