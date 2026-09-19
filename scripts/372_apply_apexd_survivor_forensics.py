#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE372_APEXD_SURVIVOR_FORENSICS_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase372 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


INCLUDES = r'''#include <linux/delay.h>
#include <linux/fs.h>
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
/* A52_PHASE372_APEXD_SURVIVOR_FORENSICS_V1
 *
 * Phase371 corrected an earlier interpretation:
 * libc++ std::async launches detached std::threads, and Bionic intentionally
 * calls set_tid_address(NULL) when a detached pthread exits. Therefore
 * clear_child_tid == NULL at mm_release is normal for those async workers.
 *
 * The useful Phase371 hardware result is instead the survivor cohort:
 * second apexd TGID 488 creates workers through at least TID 535.
 * TIDs 492..508 exit by ~19.04 s, while 509..535 reach userspace but do not
 * reach detached-thread teardown/mm_release before the ~48 s diagnostic reboot.
 *
 * Phase372 snapshots every surviving thread in the latest apexd process at
 * ~30 s, sequentially rather than by pid-tgid indexing. Up to 32 threads fit
 * in each 16 KiB mirror.
 *
 * For each survivor record:
 *   - latest syscall number, ENTRY/RETURN state, x0..x5 and return value
 *   - user PC/LR/SP from syscall entry
 *   - task state, context-switch counts, kernel wchan and bounded kernel stack
 *   - VMA/file-offset identity for BOTH PC and LR
 *   - clear_child_tid
 *   - current futex word when the last syscall is futex
 *   - 64 bytes from the first readable user-pointer syscall argument
 *
 * Mapping LR is deliberate: PC is usually libc's generic syscall() wrapper,
 * while LR identifies the actual caller (pthread/futex/libbinder/etc).
 *
 * Observation only. Snapshot at ~30 s, reboot to recovery at ~45 s.
 */
#define A52_R372_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R372_SIDEBAND_BYTES     0x8000U
#define A52_R372_COPY_BYTES         0x4000U
#define A52_R372_SLOT_BYTES         512U
#define A52_R372_SLOTS              32U
#define A52_R372_TRACK_SLOTS        128U
#define A52_R372_STACK_ENTRIES      12U
#define A52_R372_MAGIC              0x3237335652555341ULL
#define A52_R372_COMMIT             0x372c0de5U
#define A52_R372_VERSION            1U

#define A52_R372_EVT_ENTRY          1U
#define A52_R372_EVT_RETURN         2U

struct a52_r372_live {
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

struct a52_r372_record {
	u64 magic;
	u64 snapshot_ns;
	u64 last_ns;
	u64 sequence;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 wchan;
	u64 clear_child_tid;
	u64 mm;
	u64 x[6];
	s64 ret;
	u64 nvcsw;
	u64 nivcsw;
	unsigned long kstack[A52_R372_STACK_ENTRIES];

	u64 pc_map_start;
	u64 pc_map_end;
	u64 pc_map_pgoff;
	u64 pc_map_inode;
	u64 lr_map_start;
	u64 lr_map_end;
	u64 lr_map_pgoff;
	u64 lr_map_inode;

	u32 syscallno;
	u32 event;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 state;
	u32 exit_state;
	u32 flags;
	u32 slot;
	u32 total_threads;
	u32 futex_word;
	s32 futex_read_rc;

	char comm[TASK_COMM_LEN];
	char pc_mapname[32];
	char lr_mapname[32];
	u8 user_arg[64];
	u32 user_arg_reg;
	s32 user_arg_rc;
};

static const char a52_r372_marker[] __used =
	"A52_PHASE372_APEXD_SURVIVOR_FORENSICS_V1";
static void *a52_r372_sideband;
static struct a52_r372_live a52_r372_live[A52_R372_TRACK_SLOTS];
static DEFINE_SPINLOCK(a52_r372_lock);
static atomic64_t a52_r372_sequence = ATOMIC64_INIT(0);
static atomic_t a52_r372_latest_tgid = ATOMIC_INIT(-1);

static bool a52_r372_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static struct a52_r372_live *a52_r372_live_slot_locked(pid_t pid, pid_t tgid)
{
	unsigned int start = ((unsigned int)pid * 2654435761U) %
		A52_R372_TRACK_SLOTS;
	unsigned int i;
	struct a52_r372_live *r;

	for (i = 0; i < A52_R372_TRACK_SLOTS; i++) {
		r = &a52_r372_live[(start + i) % A52_R372_TRACK_SLOTS];
		if (r->pid == (u32)pid && r->tgid == (u32)tgid)
			return r;
		if (!r->pid)
			return r;
	}
	return &a52_r372_live[start];
}

static bool a52_r372_live_lookup(pid_t pid, pid_t tgid,
				 struct a52_r372_live *out)
{
	unsigned long irqflags;
	unsigned int i;
	bool found = false;

	spin_lock_irqsave(&a52_r372_lock, irqflags);
	for (i = 0; i < A52_R372_TRACK_SLOTS; i++) {
		if (a52_r372_live[i].pid == (u32)pid &&
		    a52_r372_live[i].tgid == (u32)tgid) {
			memcpy(out, &a52_r372_live[i], sizeof(*out));
			found = true;
			break;
		}
	}
	spin_unlock_irqrestore(&a52_r372_lock, irqflags);
	return found;
}

static u64 a52_r372_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r372_live *r;
	unsigned long irqflags;
	u64 sequence;

	if (!a52_r372_apexd_thread())
		return 0;

	atomic_set(&a52_r372_latest_tgid, current->tgid);
	sequence = (u64)atomic64_inc_return(&a52_r372_sequence);

	spin_lock_irqsave(&a52_r372_lock, irqflags);
	r = a52_r372_live_slot_locked(current->pid, current->tgid);
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
	r->event = A52_R372_EVT_ENTRY;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
	spin_unlock_irqrestore(&a52_r372_lock, irqflags);

	return sequence;
}

static void a52_r372_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r372_live *r;
	unsigned long irqflags;
	unsigned int i;

	if (!sequence || !a52_r372_apexd_thread())
		return;

	spin_lock_irqsave(&a52_r372_lock, irqflags);
	for (i = 0; i < A52_R372_TRACK_SLOTS; i++) {
		r = &a52_r372_live[i];
		if (r->pid == (u32)current->pid &&
		    r->tgid == (u32)current->tgid &&
		    r->sequence == sequence) {
			r->ns = ktime_get_ns();
			r->ret = (s64)regs->regs[0];
			r->event = A52_R372_EVT_RETURN;
			r->cpu = (u32)raw_smp_processor_id();
			break;
		}
	}
	spin_unlock_irqrestore(&a52_r372_lock, irqflags);
}

static void a52_r372_map_addr(struct task_struct *task, u64 addr,
			      u64 *start, u64 *end, u64 *pgoff,
			      u64 *inode, char *name, size_t name_size)
{
	struct mm_struct *mm;
	struct vm_area_struct *vma;

	if (!task || !addr)
		return;

	mm = get_task_mm(task);
	if (!mm)
		return;

	mmap_read_lock(mm);
	vma = find_vma(mm, addr);
	if (vma && addr >= vma->vm_start) {
		*start = vma->vm_start;
		*end = vma->vm_end;
		*pgoff = vma->vm_pgoff;
		if (vma->vm_file) {
			*inode = file_inode(vma->vm_file)->i_ino;
			strscpy(name,
				vma->vm_file->f_path.dentry->d_name.name,
				name_size);
		}
	}
	mmap_read_unlock(mm);
	mmput(mm);
}

static void a52_r372_capture_user_arg(struct task_struct *task,
				      struct a52_r372_record *r)
{
	unsigned int i;
	int rc;

	r->user_arg_reg = 0xffffffffU;
	r->user_arg_rc = -1;

	if (!task)
		return;

	for (i = 0; i < 6; i++) {
		unsigned long p = (unsigned long)r->x[i];

		if (p < 0x10000UL)
			continue;
		memset(r->user_arg, 0, sizeof(r->user_arg));
		rc = access_process_vm(task, p, r->user_arg,
				       sizeof(r->user_arg), 0);
		if (rc > 0) {
			r->user_arg_reg = i;
			r->user_arg_rc = rc;
			return;
		}
	}
}

static void a52_r372_fill_task(struct a52_r372_record *r,
			       struct task_struct *task,
			       unsigned int slot,
			       unsigned int total_threads)
{
	struct a52_r372_live live;
	unsigned int n;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R372_MAGIC;
	r->snapshot_ns = ktime_get_ns();
	r->slot = slot;
	r->total_threads = total_threads;

	if (!task)
		return;

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

	if (a52_r372_live_lookup(task->pid, task->tgid, &live)) {
		r->last_ns = live.ns;
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
				 A52_R372_STACK_ENTRIES, 0);
	while (n < A52_R372_STACK_ENTRIES)
		r->kstack[n++] = 0;
#endif

	a52_r372_map_addr(task, r->pc,
			  &r->pc_map_start, &r->pc_map_end,
			  &r->pc_map_pgoff, &r->pc_map_inode,
			  r->pc_mapname, sizeof(r->pc_mapname));
	a52_r372_map_addr(task, r->lr,
			  &r->lr_map_start, &r->lr_map_end,
			  &r->lr_map_pgoff, &r->lr_map_inode,
			  r->lr_mapname, sizeof(r->lr_mapname));

	if (r->syscallno == __NR_futex && r->event == A52_R372_EVT_ENTRY) {
		int frc = access_process_vm(task, (unsigned long)r->x[0],
					    &r->futex_word,
					    sizeof(r->futex_word), 0);
		r->futex_read_rc = frc;
		if (frc != sizeof(r->futex_word))
			r->futex_word = 0xffffffffU;
	} else {
		r->futex_word = 0xffffffffU;
		r->futex_read_rc = -1;
	}

	a52_r372_capture_user_arg(task, r);
}

static void a52_r372_write_slot(unsigned int slot,
				const struct a52_r372_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r372_sideband) || !r || slot >= A52_R372_SLOTS)
		return;

	pos = slot * A52_R372_SLOT_BYTES;
	dst0 = (u8 *)a52_r372_sideband + pos;
	dst1 = (u8 *)a52_r372_sideband + A52_R372_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
}

static struct task_struct *a52_r372_get_task(pid_t pid)
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

static void a52_r372_snapshot(void)
{
	struct a52_r372_record r;
	struct task_struct *leader;
	struct task_struct *task;
	struct task_struct *threads[A52_R372_SLOTS];
	unsigned int count = 0;
	unsigned int total = 0;
	unsigned int i;
	int tgid = atomic_read(&a52_r372_latest_tgid);

	if (!READ_ONCE(a52_r372_sideband) || tgid <= 0)
		return;

	leader = a52_r372_get_task((pid_t)tgid);
	if (!leader)
		return;

	threads[count++] = leader;
	get_task_struct(leader);

	rcu_read_lock();
	for_each_thread(leader, task) {
		total++;
		if (count < A52_R372_SLOTS) {
			get_task_struct(task);
			threads[count++] = task;
		}
	}
	rcu_read_unlock();

	/* total_threads includes leader. */
	total++;

	for (i = 0; i < count; i++) {
		a52_r372_fill_task(&r, threads[i], i, total);
		a52_r372_write_slot(i, &r);
		put_task_struct(threads[i]);
	}
	put_task_struct(leader);

	wmb();
	__flush_dcache_area(a52_r372_sideband, A52_R372_SIDEBAND_BYTES);
}

static int a52_r372_thread(void *unused)
{
	msleep(30000);
	a52_r372_snapshot();
	msleep(15000);
	wmb();
	__flush_dcache_area(a52_r372_sideband, A52_R372_SIDEBAND_BYTES);
	kernel_restart("recovery");
	return 0;
}

static int __init a52_r372_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r372_record) != A52_R372_SLOT_BYTES);
	BUILD_BUG_ON(A52_R372_SLOTS * A52_R372_SLOT_BYTES !=
		     A52_R372_COPY_BYTES);

	a52_r372_sideband = memremap(A52_R372_SIDEBAND_PHYS,
		A52_R372_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r372_sideband)
		return 0;

	memset(a52_r372_sideband, 0, A52_R372_SIDEBAND_BYTES);
	memset(a52_r372_live, 0, sizeof(a52_r372_live));
	atomic64_set(&a52_r372_sequence, 0);
	atomic_set(&a52_r372_latest_tgid, -1);
	wmb();
	__flush_dcache_area(a52_r372_sideband, A52_R372_SIDEBAND_BYTES);

	if (IS_ERR(kthread_run(a52_r372_thread, NULL, "a52_r372")))
		return 0;
	return 0;
}
late_initcall(a52_r372_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE369_APEXD_JOIN_TARGET_V1" not in text:
        raise SystemExit("Phase372 requires Phase369 syscall lineage")

    # Headers already exist from Phase368/369 in normal lineage.
    for inc in INCLUDES.strip().splitlines():
        if inc + "\n" not in text:
            text = inc + "\n" + text

    anchor = (
        'static const char a52_r369_marker[] __used =\n'
        '\t"A52_PHASE369_APEXD_JOIN_TARGET_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase372 block insertion")

    text = one(
        text,
        "static int __init a52_r369_init(void)\n",
        "static int __init __used a52_r369_init(void)\n",
        "mark Phase369 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r369_init);\n",
        "/* Phase372 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase369 mapper",
    )

    text = one(
        text,
        "\tu64 a52_r369_sequence_token = 0;\n",
        "\tu64 a52_r369_sequence_token = 0;\n"
        "\tu64 a52_r372_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r369_sequence_token = a52_r369_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r369_sys_return(regs, scno, a52_r369_sequence_token);\n"
    )
    new = (
        "\ta52_r369_sequence_token = a52_r369_sys_enter(regs, scno);\n"
        "\ta52_r372_sequence_token = a52_r372_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r372_sys_return(regs, scno, a52_r372_sequence_token);\n"
        "\ta52_r369_sys_return(regs, scno, a52_r369_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R372_SLOT_BYTES         512U",
        "A52_R372_SLOTS              32U",
        "A52_R372_TRACK_SLOTS        128U",
        "A52_R372_STACK_ENTRIES      12U",
        "lr_map_start",
        "user_arg[64]",
        "a52_r372_sys_enter(regs, scno);",
        "a52_r372_sys_return(regs, scno, a52_r372_sequence_token);",
        "stack_trace_save_tsk(task, r->kstack",
        "access_process_vm(task, (unsigned long)r->x[0]",
        "late_initcall(a52_r372_init);",
    ):
        if token not in text:
            raise SystemExit("Phase372 required token missing: " + token)

    if "late_initcall(a52_r369_init);" in text:
        raise SystemExit("Phase372 must disable Phase369 mapper")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYS
    if not path.is_file():
        raise SystemExit("Phase372 syscall source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        validate(before)
        print("Phase372 apexd survivor forensic audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase372 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase372 apexd survivor forensics applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
