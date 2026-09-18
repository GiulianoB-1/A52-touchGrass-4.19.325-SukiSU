#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase367 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def locate_futex(root: Path) -> Path:
    hits = []
    for rel in (Path("kernel/futex/core.c"), Path("kernel/futex.c")):
        p = root / rel
        if p.is_file():
            t = p.read_text(encoding="utf-8")
            if "long do_futex(" in t:
                hits.append(p)
    if len(hits) != 1:
        raise SystemExit(f"Phase367 expected one do_futex source, found {hits}")
    return hits[0]


BLOCK = r'''
/* A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1
 *
 * Phase366 proved the apexd group leader blocks indefinitely in
 * FUTEX_WAIT_BITSET_PRIVATE while its worker threads continue futex activity.
 * Syscall-only tracing cannot see exit-generated clear_child_tid wakes because
 * those call do_futex() directly from the kernel.
 *
 * Phase367 therefore observes do_futex() itself. The latest apexd group-leader
 * WAIT/WAIT_BITSET address is latched. Every later apexd-process futex wake is
 * recorded with uaddr, op/flags, clear_child_tid, caller identity and the
 * number of waiters actually woken. The target wait is replicated eight times.
 *
 * Observation only: no futex arguments, keys, queueing, wake counts, scheduling
 * or userspace memory are modified.
 */
#define A52_R367_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R367_SIDEBAND_BYTES     0x8000U
#define A52_R367_COPY_BYTES         0x4000U
#define A52_R367_SLOT_BYTES         256U
#define A52_R367_RING_SLOTS         56U
#define A52_R367_LATCH_FIRST        56U
#define A52_R367_LATCH_REPLICAS     8U
#define A52_R367_SLOTS_PER_COPY     64U
#define A52_R367_MAGIC              0x3736335854554641ULL
#define A52_R367_COMMIT             0x367c0de5U
#define A52_R367_VERSION            1U

#define A52_R367_EVT_WAIT_ENTRY     1U
#define A52_R367_EVT_WAIT_RETURN    2U
#define A52_R367_EVT_WAKE_RETURN    3U

struct a52_r367_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 target;
	u64 uaddr;
	u64 clear_child_tid;
	u64 mm;
	s64 ret;
	u32 event;
	u32 cmd;
	u32 op;
	u32 flags;
	u32 val;
	u32 bitset;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ring_index;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[128];
};

static const char a52_r367_marker[] __used =
	"A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1";
static void *a52_r367_sideband;
static atomic64_t a52_r367_sequence = ATOMIC64_INIT(0);
static u64 a52_r367_target;

static bool a52_r367_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static void a52_r367_fill(struct a52_r367_record *r,
			  u32 event, u32 cmd, int op, unsigned int flags,
			  u32 __user *uaddr, u32 val, u32 bitset, long ret,
			  u64 sequence)
{
	memset(r, 0, sizeof(*r));
	r->magic = A52_R367_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->target = READ_ONCE(a52_r367_target);
	r->uaddr = (u64)(unsigned long)uaddr;
	r->clear_child_tid = (u64)(unsigned long)READ_ONCE(current->clear_child_tid);
	r->mm = (u64)(unsigned long)current->mm;
	r->ret = ret;
	r->event = event;
	r->cmd = cmd;
	r->op = (u32)op;
	r->flags = flags;
	r->val = val;
	r->bitset = bitset;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->ring_index = (u32)((sequence - 1ULL) % A52_R367_RING_SLOTS);
	r->commit = A52_R367_COMMIT;
	r->version = A52_R367_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
}

static void a52_r367_write_slot(unsigned int slot,
				const struct a52_r367_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r367_sideband) || !r ||
	    slot >= A52_R367_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R367_SLOT_BYTES;
	dst0 = (u8 *)a52_r367_sideband + pos;
	dst1 = (u8 *)a52_r367_sideband + A52_R367_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r367_record_event(u32 event, u32 cmd, int op,
				  unsigned int flags, u32 __user *uaddr,
				  u32 val, u32 bitset, long ret, bool latch)
{
	struct a52_r367_record r;
	u64 sequence;
	unsigned int slot;
	unsigned int i;

	if (!READ_ONCE(a52_r367_sideband))
		return;

	sequence = (u64)atomic64_inc_return(&a52_r367_sequence);
	slot = (unsigned int)((sequence - 1ULL) % A52_R367_RING_SLOTS);
	a52_r367_fill(&r, event, cmd, op, flags, uaddr, val, bitset,
		      ret, sequence);
	a52_r367_write_slot(slot, &r);

	if (latch)
		for (i = 0; i < A52_R367_LATCH_REPLICAS; i++)
			a52_r367_write_slot(A52_R367_LATCH_FIRST + i, &r);
}

static bool a52_r367_wait_cmd(u32 cmd)
{
	return cmd == FUTEX_WAIT || cmd == FUTEX_WAIT_BITSET;
}

static bool a52_r367_wake_cmd(u32 cmd)
{
	return cmd == FUTEX_WAKE || cmd == FUTEX_WAKE_BITSET;
}

static void a52_r367_wait_enter(u32 __user *uaddr, int op, u32 cmd,
				unsigned int flags, u32 val, u32 bitset)
{
	if (!a52_r367_apexd_thread() || current->pid != current->tgid ||
	    !a52_r367_wait_cmd(cmd))
		return;

	WRITE_ONCE(a52_r367_target, (u64)(unsigned long)uaddr);
	a52_r367_record_event(A52_R367_EVT_WAIT_ENTRY, cmd, op, flags,
			      uaddr, val, bitset, LONG_MIN, true);
}

static void a52_r367_wait_return(u32 __user *uaddr, int op, u32 cmd,
				 unsigned int flags, u32 val, u32 bitset,
				 long ret)
{
	if (!a52_r367_apexd_thread() || current->pid != current->tgid ||
	    !a52_r367_wait_cmd(cmd))
		return;

	a52_r367_record_event(A52_R367_EVT_WAIT_RETURN, cmd, op, flags,
			      uaddr, val, bitset, ret, true);
}

static void a52_r367_wake_return(u32 __user *uaddr, int op, u32 cmd,
				 unsigned int flags, u32 val, u32 bitset,
				 long ret)
{
	if (!a52_r367_apexd_thread() || !a52_r367_wake_cmd(cmd) ||
	    !READ_ONCE(a52_r367_target))
		return;

	a52_r367_record_event(A52_R367_EVT_WAKE_RETURN, cmd, op, flags,
			      uaddr, val, bitset, ret, false);
}

static int __init a52_r367_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r367_record) != A52_R367_SLOT_BYTES);
	BUILD_BUG_ON(A52_R367_SLOTS_PER_COPY * A52_R367_SLOT_BYTES !=
		     A52_R367_COPY_BYTES);
	BUILD_BUG_ON(A52_R367_RING_SLOTS + A52_R367_LATCH_REPLICAS !=
		     A52_R367_SLOTS_PER_COPY);

	a52_r367_sideband = memremap(A52_R367_SIDEBAND_PHYS,
		A52_R367_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r367_sideband)
		return 0;

	memset(a52_r367_sideband, 0, A52_R367_SIDEBAND_BYTES);
	atomic64_set(&a52_r367_sequence, 0);
	WRITE_ONCE(a52_r367_target, 0);
	wmb();
	__flush_dcache_area(a52_r367_sideband, A52_R367_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r367_init);

'''


def patch_futex(text: str) -> str:
    if MARK in text:
        return text

    includes = (
        "#include <linux/init.h>\n"
        "#include <linux/io.h>\n"
        "#include <linux/ktime.h>\n"
        "#include <linux/smp.h>\n"
        "#include <linux/string.h>\n"
        "#include <asm/cacheflush.h>\n"
    )
    text = includes + text

    anchor = "long do_futex(u32 __user *uaddr, int op, u32 val, ktime_t *timeout,\n"
    if anchor not in text:
        raise SystemExit("Phase367 do_futex anchor missing")
    text = text.replace(anchor, BLOCK + anchor, 1)

    # Learn every apexd group-leader wait target after flags are finalized.
    trace_anchor = "\ttrace_android_vh_do_futex(cmd, &flags, uaddr2);\n"
    if trace_anchor in text:
        text = one(
            text,
            trace_anchor,
            trace_anchor +
            "\ta52_r367_wait_enter(uaddr, op, cmd, flags, val, val3);\n",
            "wait target hook",
        )
    else:
        switch_anchor = "\tswitch (cmd) {\n"
        text = one(
            text,
            switch_anchor,
            "\ta52_r367_wait_enter(uaddr, op, cmd, flags, val, val3);\n" +
            switch_anchor,
            "wait target hook fallback",
        )

    old_wait = (
        "\tcase FUTEX_WAIT:\n"
        "\t\tval3 = FUTEX_BITSET_MATCH_ANY;\n"
        "\t\tfallthrough;\n"
        "\tcase FUTEX_WAIT_BITSET:\n"
        "\t\treturn futex_wait(uaddr, flags, val, timeout, val3);\n"
    )
    new_wait = (
        "\tcase FUTEX_WAIT:\n"
        "\t\tval3 = FUTEX_BITSET_MATCH_ANY;\n"
        "\t\tfallthrough;\n"
        "\tcase FUTEX_WAIT_BITSET: {\n"
        "\t\tlong a52_r367_ret = futex_wait(uaddr, flags, val, timeout, val3);\n"
        "\t\ta52_r367_wait_return(uaddr, op, cmd, flags, val, val3,\n"
        "\t\t\t\t      a52_r367_ret);\n"
        "\t\treturn a52_r367_ret;\n"
        "\t}\n"
    )
    text = one(text, old_wait, new_wait, "wait return wrapper")

    old_wake = (
        "\tcase FUTEX_WAKE:\n"
        "\t\tval3 = FUTEX_BITSET_MATCH_ANY;\n"
        "\t\tfallthrough;\n"
        "\tcase FUTEX_WAKE_BITSET:\n"
        "\t\treturn futex_wake(uaddr, flags, val, val3);\n"
    )
    new_wake = (
        "\tcase FUTEX_WAKE:\n"
        "\t\tval3 = FUTEX_BITSET_MATCH_ANY;\n"
        "\t\tfallthrough;\n"
        "\tcase FUTEX_WAKE_BITSET: {\n"
        "\t\tlong a52_r367_ret = futex_wake(uaddr, flags, val, val3);\n"
        "\t\ta52_r367_wake_return(uaddr, op, cmd, flags, val, val3,\n"
        "\t\t\t\t      a52_r367_ret);\n"
        "\t\treturn a52_r367_ret;\n"
        "\t}\n"
    )
    text = one(text, old_wake, new_wake, "wake return wrapper")
    return text


def patch_syscall(text: str) -> str:
    if "A52_PHASE366_APEXD_FUTEX_THREADS_V1" not in text:
        raise SystemExit("Phase367 requires Phase366 syscall lineage")

    if "static int __init __used a52_r366_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r366_init(void)\n",
            "static int __init __used a52_r366_init(void)\n",
            "mark Phase366 init dormant",
        )
    if "late_initcall(a52_r366_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r366_init);\n",
            "/* Phase367 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase366 mapper",
        )
    return text


def validate(futex: str, syscall: str) -> None:
    for token in (
        MARK,
        "A52_R367_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R367_RING_SLOTS         56U",
        "A52_R367_LATCH_REPLICAS     8U",
        "A52_R367_COMMIT             0x367c0de5U",
        "a52_r367_wait_enter(uaddr, op, cmd, flags, val, val3);",
        "a52_r367_wait_return(uaddr, op, cmd, flags, val, val3,",
        "a52_r367_wake_return(uaddr, op, cmd, flags, val, val3,",
        "READ_ONCE(current->clear_child_tid)",
        "late_initcall(a52_r367_init);",
    ):
        if token not in futex:
            raise SystemExit("Phase367 futex token missing: " + token)

    if "late_initcall(a52_r366_init);" in syscall:
        raise SystemExit("Phase367 must disable Phase366 sideband owner")
    if "static int __init __used a52_r366_init(void)" not in syscall:
        raise SystemExit("Phase367 dormant Phase366 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    futex_path = locate_futex(ns.root)
    syscall_path = ns.root / SYSCALL
    if not syscall_path.is_file():
        raise SystemExit("Phase367 syscall source missing")

    before_futex = futex_path.read_text(encoding="utf-8")
    before_sys = syscall_path.read_text(encoding="utf-8")

    if MARK in before_futex:
        validate(before_futex, before_sys)
        print(f"Phase367 kernel futex wake audit: PASS ({futex_path})")
        return 0
    if ns.check_only:
        raise SystemExit("Phase367 marker missing in check-only mode")

    after_futex = patch_futex(before_futex)
    after_sys = patch_syscall(before_sys)
    validate(after_futex, after_sys)

    futex_path.write_text(after_futex, encoding="utf-8")
    syscall_path.write_text(after_sys, encoding="utf-8")
    print(f"Phase367 kernel futex wake trace applied: {futex_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
