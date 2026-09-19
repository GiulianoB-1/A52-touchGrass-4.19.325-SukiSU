#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE369_APEXD_JOIN_TARGET_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase369 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def locate_futex(root: Path) -> Path:
    hits = []
    for rel in (Path("kernel/futex/core.c"), Path("kernel/futex.c")):
        p = root / rel
        if p.is_file() and "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1" in p.read_text(encoding="utf-8"):
            hits.append(p)
    if len(hits) != 1:
        raise SystemExit(f"Phase369 expected one Phase367 futex source, found {hits}")
    return hits[0]


INCLUDES = r'''#include <linux/delay.h>
#include <linux/fs.h>
#include <linux/futex.h>
#include <linux/kthread.h>
#include <linux/mm.h>
#include <linux/pid.h>
#include <linux/rcupdate.h>
#include <linux/reboot.h>
#include <linux/sched/mm.h>
#include <linux/sched/signal.h>
#include <linux/spinlock.h>
#include <linux/stacktrace.h>
'''

BLOCK = r'''
/* A52_PHASE369_APEXD_JOIN_TARGET_V1
 *
 * Phase368 hardware showed the second apexd group leader at ~30 s in:
 *   futex(uaddr, FUTEX_WAIT, 0x505, timeout, ...)
 * where 0x505 == TID 1285. This exactly matches the modern Bionic pthread_join
 * primitive shape: wait on thread->tid until CLONE_CHILD_CLEARTID clears it.
 *
 * Phase368 indexed workers only by pid-tgid < 64, so the late TID 1285 escaped
 * that snapshot. Phase369 removes that blind spot.
 *
 * Every apexd thread's latest syscall is tracked in a 128-entry PID-keyed RAM
 * table. Whenever the apexd group leader enters a plain FUTEX_WAIT with a
 * positive expected value, latch:
 *   - main TGID
 *   - candidate target TID (expected futex value)
 *   - futex address
 *   - timeout pointer
 *   - timestamp
 *
 * At ~30 s, persist:
 *   slot 0: main thread
 *   slot 1: exact candidate target TID, even when pid-tgid is hundreds
 *   slots 2..17: every other surviving apexd thread (bounded)
 *
 * Each 512-byte record contains task state, clear_child_tid, latest syscall,
 * x0..x5, user PC/LR/SP, VMA identity, kernel wchan and a bounded kernel stack.
 * It also reads the actual 32-bit join futex word from the main process.
 *
 * Decision:
 *   target alive + clear_child_tid == wait_uaddr:
 *       identify exactly what the target is blocked doing.
 *   target gone + futex_word still == target_tid:
 *       CLONE_CHILD_CLEARTID / exit cleanup failed.
 *   target gone + futex_word == 0:
 *       waiter failed to observe/resume after a valid clear+wake.
 *
 * Observation only. No futex, task, syscall, scheduling or userspace state is
 * modified. Snapshot ~30 s, reboot recovery ~45 s.
 */
#define A52_R369_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R369_SIDEBAND_BYTES     0x8000U
#define A52_R369_COPY_BYTES         0x4000U
#define A52_R369_SLOT_BYTES         512U
#define A52_R369_SLOTS_PER_COPY     32U
#define A52_R369_SURVIVOR_LIMIT     16U
#define A52_R369_TRACK_SLOTS        128U
#define A52_R369_STACK_ENTRIES      12U
#define A52_R369_MAGIC              0x3936334e494f4a41ULL
#define A52_R369_COMMIT             0x369c0de5U
#define A52_R369_VERSION            1U

#define A52_R369_EVT_ENTRY          1U
#define A52_R369_EVT_RETURN         2U

struct a52_r369_live {
	u64 sequence;
	u64 ns;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 x[6];
	s64 ret;
	u32 syscallno;
	u32 event;
	u32 cpu;
	u32 pid;
	u32 tgid;
	char comm[TASK_COMM_LEN];
};

struct a52_r369_record {
	u64 magic;
	u64 snapshot_ns;
	u64 wait_ns;
	u64 sequence;
	u64 wait_uaddr;
	u64 wait_timeout;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 wchan;
	u64 clear_child_tid;
	u64 mm;
	u64 map_start;
	u64 map_end;
	u64 map_pgoff;
	u64 map_inode;
	u64 x[6];
	s64 ret;
	u64 nvcsw;
	u64 nivcsw;
	unsigned long kstack[A52_R369_STACK_ENTRIES];
	u32 role;
	u32 main_tgid;
	u32 candidate_tid;
	u32 candidate_live;
	u32 futex_word;
	s32 futex_read_rc;
	u32 syscallno;
	u32 event;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 state;
	u32 exit_state;
	u32 flags;
	u32 slot;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	char mapname[32];
	u8 reserved[100];
};

static const char a52_r369_marker[] __used =
	"A52_PHASE369_APEXD_JOIN_TARGET_V1";
static void *a52_r369_sideband;
static struct a52_r369_live a52_r369_live[A52_R369_TRACK_SLOTS];
static DEFINE_SPINLOCK(a52_r369_lock);
static atomic64_t a52_r369_sequence = ATOMIC64_INIT(0);
static atomic_t a52_r369_main_tgid = ATOMIC_INIT(-1);
static atomic_t a52_r369_candidate_tid = ATOMIC_INIT(-1);
static u64 a52_r369_wait_uaddr;
static u64 a52_r369_wait_timeout;
static u64 a52_r369_wait_ns;

static bool a52_r369_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static struct a52_r369_live *a52_r369_live_slot_locked(pid_t pid, pid_t tgid)
{
	unsigned int start = ((unsigned int)pid * 2654435761U) %
		A52_R369_TRACK_SLOTS;
	unsigned int i;
	struct a52_r369_live *r;

	for (i = 0; i < A52_R369_TRACK_SLOTS; i++) {
		r = &a52_r369_live[(start + i) % A52_R369_TRACK_SLOTS];
		if (r->pid == (u32)pid && r->tgid == (u32)tgid)
			return r;
		if (!r->pid)
			return r;
	}

	/* Bounded fallback: replace the hash home slot. */
	return &a52_r369_live[start];
}

static bool a52_r369_live_lookup(pid_t pid, pid_t tgid,
				 struct a52_r369_live *out)
{
	unsigned long irqflags;
	unsigned int i;
	bool found = false;

	spin_lock_irqsave(&a52_r369_lock, irqflags);
	for (i = 0; i < A52_R369_TRACK_SLOTS; i++) {
		if (a52_r369_live[i].pid == (u32)pid &&
		    a52_r369_live[i].tgid == (u32)tgid) {
			memcpy(out, &a52_r369_live[i], sizeof(*out));
			found = true;
			break;
		}
	}
	spin_unlock_irqrestore(&a52_r369_lock, irqflags);
	return found;
}

static u64 a52_r369_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r369_live *r;
	unsigned long irqflags;
	u64 sequence;
	u32 op;
	u32 expected;

	if (!a52_r369_apexd_thread())
		return 0;

	sequence = (u64)atomic64_inc_return(&a52_r369_sequence);

	spin_lock_irqsave(&a52_r369_lock, irqflags);
	r = a52_r369_live_slot_locked(current->pid, current->tgid);
	memset(r, 0, sizeof(*r));
	r->sequence = sequence;
	r->ns = ktime_get_ns();
	r->pc = regs->pc;
	r->lr = regs->regs[30];
	r->sp = regs->sp;
	r->x[0] = regs->orig_x0;
	r->x[1] = regs->regs[1];
	r->x[2] = regs->regs[2];
	r->x[3] = regs->regs[3];
	r->x[4] = regs->regs[4];
	r->x[5] = regs->regs[5];
	r->ret = (s64)(1ULL << 63);
	r->syscallno = (u32)scno;
	r->event = A52_R369_EVT_ENTRY;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
	spin_unlock_irqrestore(&a52_r369_lock, irqflags);

	/*
	 * Modern Bionic pthread_join uses plain FUTEX_WAIT on &thread->tid with
	 * expected value == target TID. Keep the latest group-leader instance.
	 */
	if (current->pid == current->tgid && scno == __NR_futex) {
		op = (u32)regs->regs[1];
		expected = (u32)regs->regs[2];
		if ((op & FUTEX_CMD_MASK) == FUTEX_WAIT &&
		    !(op & FUTEX_PRIVATE_FLAG) && expected > 1U) {
			atomic_set(&a52_r369_main_tgid, current->tgid);
			atomic_set(&a52_r369_candidate_tid, (int)expected);
			WRITE_ONCE(a52_r369_wait_uaddr, regs->orig_x0);
			WRITE_ONCE(a52_r369_wait_timeout, regs->regs[3]);
			WRITE_ONCE(a52_r369_wait_ns, ktime_get_ns());
		}
	}

	return sequence;
}

static void a52_r369_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r369_live *r;
	unsigned long irqflags;
	unsigned int i;

	if (!sequence || !a52_r369_apexd_thread())
		return;

	spin_lock_irqsave(&a52_r369_lock, irqflags);
	for (i = 0; i < A52_R369_TRACK_SLOTS; i++) {
		r = &a52_r369_live[i];
		if (r->pid == (u32)current->pid &&
		    r->tgid == (u32)current->tgid &&
		    r->sequence == sequence) {
			r->ns = ktime_get_ns();
			r->ret = (s64)regs->regs[0];
			r->event = A52_R369_EVT_RETURN;
			r->cpu = (u32)raw_smp_processor_id();
			break;
		}
	}
	spin_unlock_irqrestore(&a52_r369_lock, irqflags);
}

static void a52_r369_map_pc(struct task_struct *task,
			    struct a52_r369_record *r)
{
	struct mm_struct *mm;
	struct vm_area_struct *vma;

	if (!task || !r->pc)
		return;

	mm = get_task_mm(task);
	if (!mm)
		return;

	mmap_read_lock(mm);
	vma = find_vma(mm, r->pc);
	if (vma && r->pc >= vma->vm_start) {
		r->map_start = vma->vm_start;
		r->map_end = vma->vm_end;
		r->map_pgoff = vma->vm_pgoff;
		if (vma->vm_file) {
			r->map_inode = file_inode(vma->vm_file)->i_ino;
			strscpy(r->mapname,
				vma->vm_file->f_path.dentry->d_name.name,
				sizeof(r->mapname));
		}
	}
	mmap_read_unlock(mm);
	mmput(mm);
}

static void a52_r369_fill_task(struct a52_r369_record *r,
			       struct task_struct *task, u32 role,
			       unsigned int slot, u32 futex_word,
			       int futex_read_rc)
{
	struct a52_r369_live live;
	unsigned int n;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R369_MAGIC;
	r->snapshot_ns = ktime_get_ns();
	r->wait_ns = READ_ONCE(a52_r369_wait_ns);
	r->wait_uaddr = READ_ONCE(a52_r369_wait_uaddr);
	r->wait_timeout = READ_ONCE(a52_r369_wait_timeout);
	r->role = role;
	r->slot = slot;
	r->main_tgid = (u32)atomic_read(&a52_r369_main_tgid);
	r->candidate_tid = (u32)atomic_read(&a52_r369_candidate_tid);
	r->futex_word = futex_word;
	r->futex_read_rc = futex_read_rc;
	r->commit = A52_R369_COMMIT;
	r->version = A52_R369_VERSION;

	if (!task)
		return;

	r->candidate_live = task->pid == (pid_t)r->candidate_tid;
	r->pid = (u32)task->pid;
	r->tgid = (u32)task->tgid;
	r->state = (u32)READ_ONCE(task->state);
	r->exit_state = (u32)READ_ONCE(task->exit_state);
	r->flags = READ_ONCE(task->flags);
	r->wchan = get_wchan(task);
	r->clear_child_tid =
		(u64)(unsigned long)READ_ONCE(task->clear_child_tid);
	r->mm = (u64)(unsigned long)READ_ONCE(task->mm);
	r->nvcsw = READ_ONCE(task->nvcsw);
	r->nivcsw = READ_ONCE(task->nivcsw);
	get_task_comm(r->comm, task);

	if (a52_r369_live_lookup(task->pid, task->tgid, &live)) {
		r->sequence = live.sequence;
		r->pc = live.pc;
		r->lr = live.lr;
		r->sp = live.sp;
		memcpy(r->x, live.x, sizeof(r->x));
		r->ret = live.ret;
		r->syscallno = live.syscallno;
		r->event = live.event;
		r->cpu = live.cpu;
	}

#ifdef CONFIG_STACKTRACE
	n = stack_trace_save_tsk(task, r->kstack,
				 A52_R369_STACK_ENTRIES, 0);
	while (n < A52_R369_STACK_ENTRIES)
		r->kstack[n++] = 0;
#endif
	a52_r369_map_pc(task, r);
}

static void a52_r369_write_slot(unsigned int slot,
				const struct a52_r369_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r369_sideband) || !r ||
	    slot >= A52_R369_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R369_SLOT_BYTES;
	dst0 = (u8 *)a52_r369_sideband + pos;
	dst1 = (u8 *)a52_r369_sideband + A52_R369_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
}

static struct task_struct *a52_r369_get_task(pid_t pid)
{
	struct task_struct *task = NULL;

	if (pid <= 0)
		return NULL;
	rcu_read_lock();
	task = find_task_by_vpid(pid);
	if (task)
		get_task_struct(task);
	rcu_read_unlock();
	return task;
}

static void a52_r369_snapshot(void)
{
	struct a52_r369_record r;
	struct task_struct *leader;
	struct task_struct *target;
	struct task_struct *task;
	struct task_struct *survivors[A52_R369_SURVIVOR_LIMIT];
	unsigned int count = 0;
	unsigned int i;
	u32 futex_word = 0xffffffffU;
	int futex_rc = -1;
	int tgid = atomic_read(&a52_r369_main_tgid);
	int target_tid = atomic_read(&a52_r369_candidate_tid);
	u64 wait_uaddr = READ_ONCE(a52_r369_wait_uaddr);

	if (!READ_ONCE(a52_r369_sideband) || tgid <= 0)
		return;

	leader = a52_r369_get_task((pid_t)tgid);
	target = a52_r369_get_task((pid_t)target_tid);

	if (leader && wait_uaddr) {
		futex_rc = access_process_vm(leader, (unsigned long)wait_uaddr,
					     &futex_word, sizeof(futex_word), 0);
		if (futex_rc != sizeof(futex_word))
			futex_word = 0xffffffffU;
	}

	a52_r369_fill_task(&r, leader, 1U, 0U, futex_word, futex_rc);
	a52_r369_write_slot(0U, &r);

	/* Slot 1 always describes the exact TID encoded in the join futex. */
	if (target && target->tgid != tgid) {
		put_task_struct(target);
		target = NULL;
	}
	a52_r369_fill_task(&r, target, 2U, 1U, futex_word, futex_rc);
	a52_r369_write_slot(1U, &r);

	if (leader) {
		rcu_read_lock();
		for_each_thread(leader, task) {
			if (count >= A52_R369_SURVIVOR_LIMIT)
				break;
			if (target && task->pid == target->pid)
				continue;
			get_task_struct(task);
			survivors[count++] = task;
		}
		rcu_read_unlock();

		for (i = 0; i < count; i++) {
			a52_r369_fill_task(&r, survivors[i], 3U, 2U + i,
					   futex_word, futex_rc);
			a52_r369_write_slot(2U + i, &r);
			put_task_struct(survivors[i]);
		}
	}

	if (target)
		put_task_struct(target);
	if (leader)
		put_task_struct(leader);

	wmb();
	__flush_dcache_area(a52_r369_sideband, A52_R369_SIDEBAND_BYTES);
}

static int a52_r369_thread(void *unused)
{
	msleep(30000);
	a52_r369_snapshot();
	msleep(15000);
	wmb();
	__flush_dcache_area(a52_r369_sideband, A52_R369_SIDEBAND_BYTES);
	kernel_restart("recovery");
	return 0;
}

static int __init a52_r369_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r369_record) != A52_R369_SLOT_BYTES);
	BUILD_BUG_ON(A52_R369_SLOTS_PER_COPY * A52_R369_SLOT_BYTES !=
		     A52_R369_COPY_BYTES);

	a52_r369_sideband = memremap(A52_R369_SIDEBAND_PHYS,
		A52_R369_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r369_sideband)
		return 0;

	memset(a52_r369_sideband, 0, A52_R369_SIDEBAND_BYTES);
	memset(a52_r369_live, 0, sizeof(a52_r369_live));
	atomic64_set(&a52_r369_sequence, 0);
	atomic_set(&a52_r369_main_tgid, -1);
	atomic_set(&a52_r369_candidate_tid, -1);
	WRITE_ONCE(a52_r369_wait_uaddr, 0);
	WRITE_ONCE(a52_r369_wait_timeout, 0);
	WRITE_ONCE(a52_r369_wait_ns, 0);
	wmb();
	__flush_dcache_area(a52_r369_sideband, A52_R369_SIDEBAND_BYTES);

	if (IS_ERR(kthread_run(a52_r369_thread, NULL, "a52_r369")))
		return 0;
	return 0;
}
late_initcall(a52_r369_init);

'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE368_APEXD_THREAD_FORENSICS_V1" not in text:
        raise SystemExit("Phase369 requires Phase368 syscall lineage")

    # Phase368 already inserted most of these headers; add only missing ones.
    if "#include <linux/stacktrace.h>\n" not in text:
        text = one(
            text,
            "#include <linux/spinlock.h>\n",
            "#include <linux/spinlock.h>\n#include <linux/stacktrace.h>\n",
            "stacktrace include",
        )

    anchor = (
        'static const char a52_r368_marker[] __used =\n'
        '\t"A52_PHASE368_APEXD_THREAD_FORENSICS_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase369 block insertion")

    # Disable Phase368's mapper and 45-second reboot thread.
    text = one(
        text,
        "static int __init a52_r368_init(void)\n",
        "static int __init __used a52_r368_init(void)\n",
        "mark Phase368 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r368_init);\n",
        "/* Phase369 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase368 mapper",
    )

    text = one(
        text,
        "\tu64 a52_r368_sequence_token = 0;\n",
        "\tu64 a52_r368_sequence_token = 0;\n"
        "\tu64 a52_r369_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r368_sequence_token = a52_r368_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r368_sys_return(regs, scno, a52_r368_sequence_token);\n"
    )
    new = (
        "\ta52_r368_sequence_token = a52_r368_sys_enter(regs, scno);\n"
        "\ta52_r369_sequence_token = a52_r369_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r369_sys_return(regs, scno, a52_r369_sequence_token);\n"
        "\ta52_r368_sys_return(regs, scno, a52_r368_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def patch_futex(text: str) -> str:
    # Phase368 already disabled the Phase367 mapper; retain that exact state.
    if "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1" not in text:
        raise SystemExit("Phase369 requires Phase367 futex lineage")
    if "late_initcall(a52_r367_init);" in text:
        raise SystemExit("Phase369 expected Phase367 mapper already dormant")
    return text


def validate(syscall: str, futex: str) -> None:
    for token in (
        MARK,
        "A52_R369_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R369_SLOT_BYTES         512U",
        "A52_R369_TRACK_SLOTS        128U",
        "A52_R369_STACK_ENTRIES      12U",
        "a52_r369_candidate_tid",
        "(op & FUTEX_CMD_MASK) == FUTEX_WAIT",
        "access_process_vm(leader, (unsigned long)wait_uaddr",
        "READ_ONCE(task->clear_child_tid)",
        "stack_trace_save_tsk(task, r->kstack",
        "a52_r369_sys_enter(regs, scno);",
        "a52_r369_sys_return(regs, scno, a52_r369_sequence_token);",
        'kernel_restart("recovery");',
        "late_initcall(a52_r369_init);",
    ):
        if token not in syscall:
            raise SystemExit("Phase369 syscall token missing: " + token)

    if "late_initcall(a52_r368_init);" in syscall:
        raise SystemExit("Phase369 must disable Phase368 sideband owner")
    if "late_initcall(a52_r367_init);" in futex:
        raise SystemExit("Phase369 requires dormant Phase367 mapper")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    syscall_path = ns.root / SYS
    futex_path = locate_futex(ns.root)
    if not syscall_path.is_file():
        raise SystemExit("Phase369 syscall source missing")

    before_sys = syscall_path.read_text(encoding="utf-8")
    futex = futex_path.read_text(encoding="utf-8")

    if MARK in before_sys:
        validate(before_sys, futex)
        print("Phase369 apexd join-target audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase369 marker missing in check-only mode")

    after_sys = patch_syscall(before_sys)
    patch_futex(futex)
    validate(after_sys, futex)
    syscall_path.write_text(after_sys, encoding="utf-8")
    print("Phase369 apexd join-target forensics applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
