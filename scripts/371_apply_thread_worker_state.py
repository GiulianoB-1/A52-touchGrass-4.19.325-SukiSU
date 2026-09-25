#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE371_THREAD_WORKER_STATE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase371 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE371_THREAD_WORKER_STATE_V1
 *
 * Phase370 hardware result exposed a targeting bug in the recorder itself.
 * The final latched clone returned PID 1062 with flags 0x01200011:
 *   CLONE_CHILD_SETTID | CLONE_CHILD_CLEARTID | SIGCHLD
 * and therefore was a separate child process, not an apexd pthread worker.
 * Phase370 assigned current->tgid to every clone target and required that TGID
 * when matching, guaranteeing zero records for such a process child.
 *
 * Phase371 fixes target selection by latching only legacy clone() calls with
 * CLONE_THREAD set. This matches the Phase369 async-worker pattern
 * (clone flags around 0x003d0f00).
 *
 * In addition to the target worker's syscall chronology, a sampler records the
 * target task's scheduler/runtime state once per second. This distinguishes:
 *   - runnable but never scheduled
 *   - actively consuming CPU in userspace
 *   - sleeping/blocked task
 *   - exited/disappeared task
 *
 * Sideband layout per 16 KiB mirror:
 * slots  0..3  : replicated latest CLONE_THREAD target header
 * slots  4..31 : 28-entry target-worker syscall ring
 * slots 32..63 : 32-entry target-task snapshot ring
 *
 * Observation only.
 */
#define A52_R371_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R371_SIDEBAND_BYTES     0x8000U
#define A52_R371_COPY_BYTES         0x4000U
#define A52_R371_SLOT_BYTES         256U
#define A52_R371_SLOTS_PER_COPY     64U
#define A52_R371_HEADER_SLOTS       4U
#define A52_R371_SYSCALL_FIRST      4U
#define A52_R371_SYSCALL_SLOTS      28U
#define A52_R371_SNAPSHOT_FIRST     32U
#define A52_R371_SNAPSHOT_SLOTS     32U
#define A52_R371_MAGIC              0x3137335857504141ULL
#define A52_R371_COMMIT             0x371c0de5U
#define A52_R371_VERSION            1U

#define A52_R371_EVT_TARGET         1U
#define A52_R371_EVT_ENTRY          2U
#define A52_R371_EVT_RETURN         3U
#define A52_R371_EVT_EXIT_ENTRY     4U
#define A52_R371_EVT_SNAPSHOT       5U
#define A52_R371_EVT_MISSING        6U

struct a52_r371_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 target_pid;
	u64 target_tgid;
	u64 clone_flags;
	u64 task_state;
	u64 task_flags;
	u64 sum_exec_runtime;
	u64 nvcsw;
	u64 nivcsw;
	u64 clear_child_tid;
	u64 mm;
	u64 x[6];
	s64 ret;
	u32 event;
	u32 syscallno;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 ppid;
	u32 exit_state;
	u32 on_cpu;
	u32 on_rq;
	u32 task_cpu;
	u32 ring_index;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[28];
};

static const char a52_r371_marker[] __used =
	"A52_PHASE371_THREAD_WORKER_STATE_V1";
static void *a52_r371_sideband;
static atomic64_t a52_r371_sys_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_r371_snap_sequence = ATOMIC64_INIT(0);
static u32 a52_r371_target_pid;
static u32 a52_r371_target_tgid;
static u64 a52_r371_clone_flags;
static struct task_struct *a52_r371_sampler_task;

static bool a52_r371_apexd_group_leader(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static bool a52_r371_is_target(void)
{
	u32 pid = READ_ONCE(a52_r371_target_pid);
	u32 tgid = READ_ONCE(a52_r371_target_tgid);

	return pid && current->pid == pid && current->tgid == tgid;
}

static void a52_r371_fill_task(struct a52_r371_record *r,
			       struct task_struct *p)
{
	if (!p)
		return;

	r->task_state = (u64)READ_ONCE(p->state);
	r->task_flags = (u64)READ_ONCE(p->flags);
	r->sum_exec_runtime = READ_ONCE(p->se.sum_exec_runtime);
	r->nvcsw = (u64)READ_ONCE(p->nvcsw);
	r->nivcsw = (u64)READ_ONCE(p->nivcsw);
	r->clear_child_tid =
		(u64)(unsigned long)READ_ONCE(p->clear_child_tid);
	r->mm = (u64)(unsigned long)READ_ONCE(p->mm);
	r->exit_state = (u32)READ_ONCE(p->exit_state);
#ifdef CONFIG_SMP
	r->on_cpu = (u32)READ_ONCE(p->on_cpu);
#else
	r->on_cpu = 0;
#endif
	r->on_rq = (u32)READ_ONCE(p->on_rq);
	r->task_cpu = (u32)task_cpu(p);
	r->pid = (u32)p->pid;
	r->tgid = (u32)p->tgid;
	r->ppid = (u32)task_ppid_nr(p);
	get_task_comm(r->comm, p);
}

static void a52_r371_fill_current(struct a52_r371_record *r,
				  struct pt_regs *regs, int scno,
				  u32 event, u64 sequence, s64 ret,
				  unsigned int slot)
{
	unsigned int i;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R371_MAGIC;
	r->ns = ktime_get_ns();
	r->sequence = sequence;
	r->target_pid = READ_ONCE(a52_r371_target_pid);
	r->target_tgid = READ_ONCE(a52_r371_target_tgid);
	r->clone_flags = READ_ONCE(a52_r371_clone_flags);
	if (regs) {
		r->x[0] = regs->orig_x0;
		for (i = 1; i < ARRAY_SIZE(r->x); i++)
			r->x[i] = regs->regs[i];
	}
	r->ret = ret;
	r->event = event;
	r->syscallno = (u32)scno;
	r->cpu = (u32)raw_smp_processor_id();
	r->ring_index = slot;
	r->commit = A52_R371_COMMIT;
	r->version = A52_R371_VERSION;
	a52_r371_fill_task(r, current);
}

static void a52_r371_write_slot(unsigned int slot,
				const struct a52_r371_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r371_sideband) || !r ||
	    slot >= A52_R371_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R371_SLOT_BYTES;
	dst0 = (u8 *)a52_r371_sideband + pos;
	dst1 = (u8 *)a52_r371_sideband + A52_R371_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r371_write_target_header(struct pt_regs *regs, int scno,
					 s64 child, u64 flags)
{
	struct a52_r371_record r;
	unsigned int i;

	a52_r371_fill_current(&r, regs, scno, A52_R371_EVT_TARGET,
			      0, child, 0);
	r.target_pid = (u64)(u32)child;
	r.target_tgid = (u64)(u32)current->tgid;
	r.clone_flags = flags;

	for (i = 0; i < A52_R371_HEADER_SLOTS; i++) {
		r.ring_index = i;
		a52_r371_write_slot(i, &r);
	}
}

static void a52_r371_note_clone_return(struct pt_regs *regs, int scno)
{
	s64 child = (s64)regs->regs[0];
	u64 flags;

	if (!READ_ONCE(a52_r371_sideband) || !a52_r371_apexd_group_leader())
		return;
	if (scno != __NR_clone)
		return;

	flags = regs->orig_x0;
	if (!(flags & CLONE_THREAD))
		return;
	if (child <= 0 || child > INT_MAX)
		return;

	WRITE_ONCE(a52_r371_target_tgid, (u32)current->tgid);
	WRITE_ONCE(a52_r371_target_pid, (u32)child);
	WRITE_ONCE(a52_r371_clone_flags, flags);
	atomic64_set(&a52_r371_sys_sequence, 0);
	atomic64_set(&a52_r371_snap_sequence, 0);
	a52_r371_write_target_header(regs, scno, child, flags);
}

static u64 a52_r371_worker_enter(struct pt_regs *regs, int scno)
{
	struct a52_r371_record r;
	u64 sequence;
	unsigned int slot;
	u32 event = A52_R371_EVT_ENTRY;

	if (!READ_ONCE(a52_r371_sideband) || !a52_r371_is_target())
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r371_sys_sequence);
	slot = A52_R371_SYSCALL_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R371_SYSCALL_SLOTS);
	if (scno == __NR_exit || scno == __NR_exit_group)
		event = A52_R371_EVT_EXIT_ENTRY;

	a52_r371_fill_current(&r, regs, scno, event, sequence,
			      (s64)(1ULL << 63), slot);
	a52_r371_write_slot(slot, &r);
	return sequence;
}

static void a52_r371_worker_return(struct pt_regs *regs, int scno, u64 token)
{
	struct a52_r371_record r;
	unsigned int slot;

	if (!token || !READ_ONCE(a52_r371_sideband) || !a52_r371_is_target())
		return;

	slot = A52_R371_SYSCALL_FIRST +
	       (unsigned int)((token - 1ULL) % A52_R371_SYSCALL_SLOTS);
	a52_r371_fill_current(&r, regs, scno, A52_R371_EVT_RETURN, token,
			      (s64)regs->regs[0], slot);
	a52_r371_write_slot(slot, &r);
}

static void a52_r371_snapshot_target(void)
{
	struct a52_r371_record r;
	struct task_struct *p;
	u64 sequence;
	unsigned int slot;
	u32 pid = READ_ONCE(a52_r371_target_pid);

	if (!READ_ONCE(a52_r371_sideband) || !pid)
		return;

	sequence = (u64)atomic64_inc_return(&a52_r371_snap_sequence);
	slot = A52_R371_SNAPSHOT_FIRST +
	       (unsigned int)((sequence - 1ULL) % A52_R371_SNAPSHOT_SLOTS);

	memset(&r, 0, sizeof(r));
	r.magic = A52_R371_MAGIC;
	r.ns = ktime_get_ns();
	r.sequence = sequence;
	r.target_pid = pid;
	r.target_tgid = READ_ONCE(a52_r371_target_tgid);
	r.clone_flags = READ_ONCE(a52_r371_clone_flags);
	r.event = A52_R371_EVT_MISSING;
	r.syscallno = ~0U;
	r.cpu = (u32)raw_smp_processor_id();
	r.ring_index = slot;
	r.commit = A52_R371_COMMIT;
	r.version = A52_R371_VERSION;

	p = find_get_task_by_vpid((pid_t)pid);
	if (p) {
		r.event = A52_R371_EVT_SNAPSHOT;
		a52_r371_fill_task(&r, p);
		put_task_struct(p);
	}
	a52_r371_write_slot(slot, &r);
}

static int a52_r371_sampler_fn(void *unused)
{
	while (!kthread_should_stop()) {
		a52_r371_snapshot_target();
		if (msleep_interruptible(1000) && kthread_should_stop())
			break;
	}
	return 0;
}

static int __init a52_r371_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r371_record) != A52_R371_SLOT_BYTES);
	BUILD_BUG_ON(A52_R371_SLOTS_PER_COPY * A52_R371_SLOT_BYTES !=
		     A52_R371_COPY_BYTES);
	BUILD_BUG_ON(A52_R371_HEADER_SLOTS + A52_R371_SYSCALL_SLOTS +
		     A52_R371_SNAPSHOT_SLOTS != A52_R371_SLOTS_PER_COPY);

	a52_r371_sideband = memremap(A52_R371_SIDEBAND_PHYS,
		A52_R371_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r371_sideband)
		return 0;

	memset(a52_r371_sideband, 0, A52_R371_SIDEBAND_BYTES);
	atomic64_set(&a52_r371_sys_sequence, 0);
	atomic64_set(&a52_r371_snap_sequence, 0);
	WRITE_ONCE(a52_r371_target_pid, 0);
	WRITE_ONCE(a52_r371_target_tgid, 0);
	WRITE_ONCE(a52_r371_clone_flags, 0);
	wmb();
	__flush_dcache_area(a52_r371_sideband, A52_R371_SIDEBAND_BYTES);

	a52_r371_sampler_task =
		kthread_run(a52_r371_sampler_fn, NULL, "a52_r371_sampler");
	if (IS_ERR(a52_r371_sampler_task))
		a52_r371_sampler_task = NULL;

	return 0;
}
late_initcall(a52_r371_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE370_LATEST_APEX_WORKER_V1" not in text:
        raise SystemExit("Phase371 requires Phase370 syscall lineage")

    includes = (
        "#include <linux/kthread.h>\n"
        "#include <linux/delay.h>\n"
        "#include <linux/sched/signal.h>\n"
    )
    if "#include <linux/kthread.h>\n" not in text:
        text = includes + text

    if "static int __init __used a52_r370_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r370_init(void)\n",
            "static int __init __used a52_r370_init(void)\n",
            "mark Phase370 init dormant",
        )
    if "late_initcall(a52_r370_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r370_init);\n",
            "/* Phase371 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase370 mapper",
        )

    anchor = (
        'static const char a52_r370_marker[] __used =\n'
        '\t"A52_PHASE370_LATEST_APEX_WORKER_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase371 block insertion")

    text = one(
        text,
        "\tu64 a52_r370_sequence_token = 0;\n",
        "\tu64 a52_r370_sequence_token = 0;\n"
        "\tu64 a52_r371_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r370_sequence_token = a52_r370_worker_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r370_worker_return(regs, scno, a52_r370_sequence_token);\n"
    )
    new = (
        "\ta52_r370_sequence_token = a52_r370_worker_enter(regs, scno);\n"
        "\ta52_r371_sequence_token = a52_r371_worker_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r371_note_clone_return(regs, scno);\n"
        "\ta52_r371_worker_return(regs, scno, a52_r371_sequence_token);\n"
        "\ta52_r370_worker_return(regs, scno, a52_r370_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R371_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R371_SYSCALL_SLOTS      28U",
        "A52_R371_SNAPSHOT_SLOTS     32U",
        "A52_R371_COMMIT             0x371c0de5U",
        "if (!(flags & CLONE_THREAD))",
        "a52_r371_note_clone_return(regs, scno);",
        "a52_r371_worker_enter(regs, scno);",
        "a52_r371_worker_return(regs, scno, a52_r371_sequence_token);",
        "find_get_task_by_vpid((pid_t)pid)",
        "kthread_run(a52_r371_sampler_fn",
        "late_initcall(a52_r371_init);",
    ):
        if token not in text:
            raise SystemExit("Phase371 required token missing: " + token)

    if "late_initcall(a52_r370_init);" in text:
        raise SystemExit("Phase371 must disable Phase370 sideband owner")
    if "static int __init __used a52_r370_init(void)" not in text:
        raise SystemExit("Phase371 dormant Phase370 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYSCALL
    if not path.is_file():
        raise SystemExit("Phase371 syscall source missing")

    before = path.read_text(encoding="utf-8")
    if MARK in before:
        validate(before)
        print("Phase371 thread-worker state audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase371 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase371 thread-worker state recorder applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
