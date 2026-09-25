#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE372_FUTURE_WORKER_CORRELATION_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase372 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE372_FUTURE_WORKER_CORRELATION_V1
 *
 * Phase371 corrected Phase370's CLONE_THREAD targeting and proved that the
 * latest real apexd worker (TID 537 in TGID 489) executed substantial APEX
 * work and then exited normally about 202 ms after creation.
 *
 * That result also proves "latest worker" is the wrong correlation target:
 * apexd first launches all std::async workers, stores their futures, and only
 * then waits on futures in vector order. The main thread can therefore block
 * on an early worker while later-created workers have already completed.
 *
 * Phase372 correlates the three things we actually need:
 *   1. direct apexd-main CLONE_THREAD creation order
 *   2. apexd-main FUTEX_WAIT/FUTEX_WAIT_BITSET chronology
 *   3. per-worker terminal state and FUTEX_WAKE activity
 *
 * Sideband per 16 KiB mirror, 128-byte records:
 *   slots   0..15  main apexd futex-wait ring
 *   slots  16..71  56 persistent worker summaries by clone ordinal
 *   slots  72..127 56-entry worker clone/wake/exit event ring
 *
 * A worker summary survives later traffic and records its last syscall,
 * last futex uaddr/op/val, wake return/count, exit state, clear_child_tid,
 * and whether it ever successfully woke the exact main wait target active
 * at that moment. This lets us identify the worker/future ordering without
 * assuming PID adjacency or "latest clone == current future".
 *
 * Observation only.
 */
#define A52_R372_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R372_SIDEBAND_BYTES     0x8000U
#define A52_R372_COPY_BYTES         0x4000U
#define A52_R372_SLOT_BYTES         128U
#define A52_R372_SLOTS_PER_COPY     128U
#define A52_R372_MAIN_FIRST         0U
#define A52_R372_MAIN_SLOTS         16U
#define A52_R372_WORKER_FIRST       16U
#define A52_R372_WORKER_SLOTS       56U
#define A52_R372_EVENT_FIRST        72U
#define A52_R372_EVENT_SLOTS        56U
#define A52_R372_MAGIC              0x3237335857434141ULL
#define A52_R372_COMMIT             0x372c0de5U
#define A52_R372_VERSION            1U

#define A52_R372_F_CLONED           BIT(0)
#define A52_R372_F_SYSCALL_ENTRY    BIT(1)
#define A52_R372_F_SYSCALL_RETURN   BIT(2)
#define A52_R372_F_WAKE             BIT(3)
#define A52_R372_F_WAKE_MATCH       BIT(4)
#define A52_R372_F_EXIT             BIT(5)
#define A52_R372_F_MAIN_WAIT_ENTRY  BIT(8)
#define A52_R372_F_MAIN_WAIT_RETURN BIT(9)

struct a52_r372_record {
	u64 magic;
	u64 first_ns;
	u64 last_ns;
	u64 uaddr;
	u64 clear_child_tid;
	s64 last_ret;
	u32 event_flags;
	u32 ordinal;
	u32 pid;
	u32 tgid;
	u32 clone_flags;
	u32 last_syscall;
	u32 futex_op;
	u32 futex_val;
	s32 last_wake_ret;
	u32 wake_calls;
	u32 matched_wait_seq;
	u32 exit_seen;
	u32 exit_code;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[4];
};

static const char a52_r372_marker[] __used =
	"A52_PHASE372_FUTURE_WORKER_CORRELATION_V1";
static void *a52_r372_sideband;
static atomic_t a52_r372_worker_count = ATOMIC_INIT(0);
static atomic64_t a52_r372_main_wait_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_r372_event_sequence = ATOMIC64_INIT(0);
static u32 a52_r372_worker_pid[A52_R372_WORKER_SLOTS];
static u32 a52_r372_worker_tgid[A52_R372_WORKER_SLOTS];
static struct a52_r372_record
	a52_r372_worker_summary[A52_R372_WORKER_SLOTS];
static u64 a52_r372_main_wait_uaddr;
static u32 a52_r372_main_wait_seq;

static bool a52_r372_apexd_group_leader(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static bool a52_r372_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static void a52_r372_write_slot(unsigned int slot,
				const struct a52_r372_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r372_sideband) || !r ||
	    slot >= A52_R372_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R372_SLOT_BYTES;
	dst0 = (u8 *)a52_r372_sideband + pos;
	dst1 = (u8 *)a52_r372_sideband + A52_R372_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r372_fill_identity(struct a52_r372_record *r)
{
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->clear_child_tid =
		(u64)(unsigned long)READ_ONCE(current->clear_child_tid);
	get_task_comm(r->comm, current);
}

static void a52_r372_emit_event(const struct a52_r372_record *src,
				u32 event_flags)
{
	struct a52_r372_record r;
	u64 sequence;
	unsigned int slot;

	if (!src || !READ_ONCE(a52_r372_sideband))
		return;

	sequence = (u64)atomic64_inc_return(&a52_r372_event_sequence);
	slot = A52_R372_EVENT_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R372_EVENT_SLOTS);
	r = *src;
	r.event_flags = event_flags;
	r.last_ns = ktime_get_ns();
	r.ordinal = src->ordinal;
	a52_r372_write_slot(slot, &r);
}

static int a52_r372_find_worker(u32 pid, u32 tgid)
{
	unsigned int i;
	unsigned int count = min_t(unsigned int,
				   (unsigned int)atomic_read(&a52_r372_worker_count),
				   A52_R372_WORKER_SLOTS);

	for (i = 0; i < count; i++) {
		if (READ_ONCE(a52_r372_worker_pid[i]) == pid &&
		    READ_ONCE(a52_r372_worker_tgid[i]) == tgid)
			return (int)i;
	}
	return -1;
}

static void a52_r372_note_clone_return(struct pt_regs *regs, int scno)
{
	struct a52_r372_record *r;
	s64 child = (s64)regs->regs[0];
	u64 flags;
	int ordinal;

	if (!READ_ONCE(a52_r372_sideband) || !a52_r372_apexd_group_leader())
		return;
	if (scno != __NR_clone)
		return;

	flags = regs->orig_x0;
	if (!(flags & CLONE_THREAD))
		return;
	if (child <= 0 || child > INT_MAX)
		return;

	ordinal = atomic_inc_return(&a52_r372_worker_count) - 1;
	if (ordinal < 0 || ordinal >= A52_R372_WORKER_SLOTS)
		return;

	WRITE_ONCE(a52_r372_worker_pid[ordinal], (u32)child);
	WRITE_ONCE(a52_r372_worker_tgid[ordinal], (u32)current->tgid);

	r = &a52_r372_worker_summary[ordinal];
	memset(r, 0, sizeof(*r));
	r->magic = A52_R372_MAGIC;
	r->first_ns = ktime_get_ns();
	r->last_ns = r->first_ns;
	r->last_ret = child;
	r->event_flags = A52_R372_F_CLONED;
	r->ordinal = (u32)ordinal;
	r->pid = (u32)child;
	r->tgid = (u32)current->tgid;
	r->clone_flags = (u32)flags;
	r->last_syscall = (u32)scno;
	r->commit = A52_R372_COMMIT;
	r->version = A52_R372_VERSION;
	memcpy(r->comm, "apexd", 6);

	a52_r372_write_slot(A52_R372_WORKER_FIRST + (unsigned int)ordinal, r);
	a52_r372_emit_event(r, A52_R372_F_CLONED);
}

static u64 a52_r372_main_wait_enter(struct pt_regs *regs, int scno)
{
	struct a52_r372_record r;
	u64 sequence;
	u32 op;
	u32 cmd;
	unsigned int slot;

	if (!READ_ONCE(a52_r372_sideband) || !a52_r372_apexd_group_leader() ||
	    scno != __NR_futex)
		return 0;

	op = (u32)regs->regs[1];
	cmd = op & FUTEX_CMD_MASK;
	if (cmd != FUTEX_WAIT && cmd != FUTEX_WAIT_BITSET)
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r372_main_wait_sequence);
	slot = A52_R372_MAIN_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R372_MAIN_SLOTS);

	memset(&r, 0, sizeof(r));
	r.magic = A52_R372_MAGIC;
	r.first_ns = ktime_get_ns();
	r.last_ns = r.first_ns;
	r.uaddr = regs->orig_x0;
	r.clear_child_tid =
		(u64)(unsigned long)READ_ONCE(current->clear_child_tid);
	r.last_ret = (s64)(1ULL << 63);
	r.event_flags = A52_R372_F_MAIN_WAIT_ENTRY;
	r.ordinal = (u32)sequence;
	r.pid = (u32)current->pid;
	r.tgid = (u32)current->tgid;
	r.last_syscall = (u32)scno;
	r.futex_op = op;
	r.futex_val = (u32)regs->regs[2];
	r.commit = A52_R372_COMMIT;
	r.version = A52_R372_VERSION;
	get_task_comm(r.comm, current);
	a52_r372_write_slot(slot, &r);

	WRITE_ONCE(a52_r372_main_wait_uaddr, r.uaddr);
	WRITE_ONCE(a52_r372_main_wait_seq, (u32)sequence);
	return sequence;
}

static void a52_r372_main_wait_return(struct pt_regs *regs, int scno,
				      u64 sequence)
{
	struct a52_r372_record r;
	unsigned int slot;

	if (!sequence || !READ_ONCE(a52_r372_sideband) ||
	    !a52_r372_apexd_group_leader() || scno != __NR_futex)
		return;

	slot = A52_R372_MAIN_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R372_MAIN_SLOTS);

	memset(&r, 0, sizeof(r));
	r.magic = A52_R372_MAGIC;
	r.first_ns = 0;
	r.last_ns = ktime_get_ns();
	r.uaddr = regs->orig_x0;
	r.clear_child_tid =
		(u64)(unsigned long)READ_ONCE(current->clear_child_tid);
	r.last_ret = (s64)regs->regs[0];
	r.event_flags = A52_R372_F_MAIN_WAIT_RETURN;
	r.ordinal = (u32)sequence;
	r.pid = (u32)current->pid;
	r.tgid = (u32)current->tgid;
	r.last_syscall = (u32)scno;
	r.futex_op = (u32)regs->regs[1];
	r.futex_val = (u32)regs->regs[2];
	r.commit = A52_R372_COMMIT;
	r.version = A52_R372_VERSION;
	get_task_comm(r.comm, current);
	a52_r372_write_slot(slot, &r);

	if (READ_ONCE(a52_r372_main_wait_seq) == (u32)sequence) {
		WRITE_ONCE(a52_r372_main_wait_uaddr, 0);
		WRITE_ONCE(a52_r372_main_wait_seq, 0);
	}
}

static void a52_r372_worker_enter(struct pt_regs *regs, int scno)
{
	struct a52_r372_record *r;
	int ordinal;

	if (!READ_ONCE(a52_r372_sideband) || !a52_r372_apexd_thread() ||
	    current->pid == current->tgid)
		return;

	ordinal = a52_r372_find_worker((u32)current->pid, (u32)current->tgid);
	if (ordinal < 0)
		return;

	r = &a52_r372_worker_summary[ordinal];
	r->last_ns = ktime_get_ns();
	r->last_syscall = (u32)scno;
	r->last_ret = (s64)(1ULL << 63);
	r->event_flags |= A52_R372_F_SYSCALL_ENTRY;
	a52_r372_fill_identity(r);

	if (scno == __NR_futex) {
		r->uaddr = regs->orig_x0;
		r->futex_op = (u32)regs->regs[1];
		r->futex_val = (u32)regs->regs[2];
	}

	if (scno == __NR_exit || scno == __NR_exit_group) {
		r->event_flags |= A52_R372_F_EXIT;
		r->exit_seen = 1;
		r->exit_code = (u32)regs->orig_x0;
		a52_r372_write_slot(A52_R372_WORKER_FIRST +
				    (unsigned int)ordinal, r);
		a52_r372_emit_event(r, A52_R372_F_EXIT);
		return;
	}

	a52_r372_write_slot(A52_R372_WORKER_FIRST + (unsigned int)ordinal, r);
}

static void a52_r372_worker_return(struct pt_regs *regs, int scno)
{
	struct a52_r372_record *r;
	u32 cmd = ~0U;
	s32 wake_ret;
	int ordinal;

	if (!READ_ONCE(a52_r372_sideband) || !a52_r372_apexd_thread() ||
	    current->pid == current->tgid)
		return;

	ordinal = a52_r372_find_worker((u32)current->pid, (u32)current->tgid);
	if (ordinal < 0)
		return;

	r = &a52_r372_worker_summary[ordinal];
	r->last_ns = ktime_get_ns();
	r->last_syscall = (u32)scno;
	r->last_ret = (s64)regs->regs[0];
	r->event_flags |= A52_R372_F_SYSCALL_RETURN;
	a52_r372_fill_identity(r);

	if (scno == __NR_futex) {
		r->uaddr = regs->orig_x0;
		r->futex_op = (u32)regs->regs[1];
		r->futex_val = (u32)regs->regs[2];
		cmd = r->futex_op & FUTEX_CMD_MASK;
	}

	if (cmd == FUTEX_WAKE || cmd == FUTEX_WAKE_BITSET) {
		wake_ret = (s32)regs->regs[0];
		r->event_flags |= A52_R372_F_WAKE;
		r->last_wake_ret = wake_ret;
		r->wake_calls++;

		if (wake_ret > 0 &&
		    r->uaddr == READ_ONCE(a52_r372_main_wait_uaddr) &&
		    READ_ONCE(a52_r372_main_wait_seq)) {
			r->event_flags |= A52_R372_F_WAKE_MATCH;
			if (!r->matched_wait_seq)
				r->matched_wait_seq =
					READ_ONCE(a52_r372_main_wait_seq);
		}

		a52_r372_write_slot(A52_R372_WORKER_FIRST +
				    (unsigned int)ordinal, r);
		a52_r372_emit_event(r,
			(r->event_flags & A52_R372_F_WAKE_MATCH) ?
			A52_R372_F_WAKE | A52_R372_F_WAKE_MATCH :
			A52_R372_F_WAKE);
		return;
	}

	a52_r372_write_slot(A52_R372_WORKER_FIRST + (unsigned int)ordinal, r);
}

static int __init a52_r372_init(void)
{
	unsigned int i;

	BUILD_BUG_ON(sizeof(struct a52_r372_record) != A52_R372_SLOT_BYTES);
	BUILD_BUG_ON(A52_R372_SLOTS_PER_COPY * A52_R372_SLOT_BYTES !=
		     A52_R372_COPY_BYTES);
	BUILD_BUG_ON(A52_R372_MAIN_SLOTS + A52_R372_WORKER_SLOTS +
		     A52_R372_EVENT_SLOTS != A52_R372_SLOTS_PER_COPY);

	a52_r372_sideband = memremap(A52_R372_SIDEBAND_PHYS,
		A52_R372_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r372_sideband)
		return 0;

	memset(a52_r372_sideband, 0, A52_R372_SIDEBAND_BYTES);
	memset(a52_r372_worker_pid, 0, sizeof(a52_r372_worker_pid));
	memset(a52_r372_worker_tgid, 0, sizeof(a52_r372_worker_tgid));
	memset(a52_r372_worker_summary, 0, sizeof(a52_r372_worker_summary));
	atomic_set(&a52_r372_worker_count, 0);
	atomic64_set(&a52_r372_main_wait_sequence, 0);
	atomic64_set(&a52_r372_event_sequence, 0);
	WRITE_ONCE(a52_r372_main_wait_uaddr, 0);
	WRITE_ONCE(a52_r372_main_wait_seq, 0);
	wmb();
	__flush_dcache_area(a52_r372_sideband, A52_R372_SIDEBAND_BYTES);
	return 0;
}
late_initcall(a52_r372_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE371_THREAD_WORKER_STATE_V1" not in text:
        raise SystemExit("Phase372 requires Phase371 syscall lineage")

    if "static int __init __used a52_r371_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r371_init(void)\n",
            "static int __init __used a52_r371_init(void)\n",
            "mark Phase371 init dormant",
        )
    if "late_initcall(a52_r371_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r371_init);\n",
            "/* Phase372 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase371 mapper/sampler",
        )

    anchor = (
        'static const char a52_r371_marker[] __used =\n'
        '\t"A52_PHASE371_THREAD_WORKER_STATE_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase372 block insertion")

    text = one(
        text,
        "\tu64 a52_r371_sequence_token = 0;\n",
        "\tu64 a52_r371_sequence_token = 0;\n"
        "\tu64 a52_r372_main_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r371_sequence_token = a52_r371_worker_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r371_note_clone_return(regs, scno);\n"
        "\ta52_r371_worker_return(regs, scno, a52_r371_sequence_token);\n"
    )
    new = (
        "\ta52_r371_sequence_token = a52_r371_worker_enter(regs, scno);\n"
        "\ta52_r372_main_sequence_token = a52_r372_main_wait_enter(regs, scno);\n"
        "\ta52_r372_worker_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r371_note_clone_return(regs, scno);\n"
        "\ta52_r372_note_clone_return(regs, scno);\n"
        "\ta52_r372_worker_return(regs, scno);\n"
        "\ta52_r372_main_wait_return(regs, scno, a52_r372_main_sequence_token);\n"
        "\ta52_r371_worker_return(regs, scno, a52_r371_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R372_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R372_SLOT_BYTES         128U",
        "A52_R372_WORKER_SLOTS       56U",
        "A52_R372_EVENT_SLOTS        56U",
        "A52_R372_COMMIT             0x372c0de5U",
        "if (!(flags & CLONE_THREAD))",
        "a52_r372_main_wait_enter(regs, scno);",
        "a52_r372_worker_enter(regs, scno);",
        "a52_r372_note_clone_return(regs, scno);",
        "a52_r372_worker_return(regs, scno);",
        "a52_r372_main_wait_return(regs, scno, a52_r372_main_sequence_token);",
        "A52_R372_F_WAKE_MATCH",
        "late_initcall(a52_r372_init);",
    ):
        if token not in text:
            raise SystemExit("Phase372 required token missing: " + token)

    if "late_initcall(a52_r371_init);" in text:
        raise SystemExit("Phase372 must disable Phase371 sideband owner/sampler")
    if "static int __init __used a52_r371_init(void)" not in text:
        raise SystemExit("Phase372 dormant Phase371 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYSCALL
    if not path.is_file():
        raise SystemExit("Phase372 syscall source missing")

    before = path.read_text(encoding="utf-8")
    if MARK in before:
        validate(before)
        print("Phase372 future-worker correlation audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase372 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase372 future-worker correlation recorder applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
