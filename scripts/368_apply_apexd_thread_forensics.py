#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE368_APEXD_THREAD_FORENSICS_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase368 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def locate_futex(root: Path) -> Path:
    hits = []
    for rel in (Path("kernel/futex/core.c"), Path("kernel/futex.c")):
        p = root / rel
        if p.is_file() and "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1" in p.read_text(encoding="utf-8"):
            hits.append(p)
    if len(hits) != 1:
        raise SystemExit(f"Phase368 expected one Phase367 futex source, found {hits}")
    return hits[0]


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
'''

BLOCK = r'''
/* A52_PHASE368_APEXD_THREAD_FORENSICS_V1
 *
 * Phase367 proved the final apexd group-leader condition is never targeted by
 * any userspace or kernel-level futex wake. The remaining question is which
 * async apexd worker never completes and what that worker is blocked inside.
 *
 * Track the latest syscall for every apexd thread in ordinary RAM, indexed by
 * pid - tgid (0..63). At ~30 seconds, snapshot all surviving threads into the
 * 32 KiB sideband as two mirrored 16 KiB copies, one fixed 256-byte slot per
 * relative PID. Each snapshot includes:
 *   - latest syscall number, x0..x5, entry/return state and return value
 *   - user PC/LR/SP from the syscall entry
 *   - task state, exit_state, flags, context-switch counters
 *   - kernel get_wchan()
 *   - clear_child_tid
 *   - VMA start/end/pgoff/inode + basename for the last user PC
 *
 * This is intentionally broad enough to identify the surviving worker and its
 * blocking primitive in one hardware boot. No syscall, task or userspace state
 * is modified. After the 30 s snapshot has been flushed, the diagnostic boot
 * reboots to recovery at ~45 s; Phase339's 330 s safety reboot remains compiled
 * but should not be reached.
 */
#define A52_R368_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R368_SIDEBAND_BYTES     0x8000U
#define A52_R368_COPY_BYTES         0x4000U
#define A52_R368_SLOT_BYTES         256U
#define A52_R368_SLOTS              64U
#define A52_R368_MAGIC              0x3836335846504141ULL
#define A52_R368_COMMIT             0x368c0de5U
#define A52_R368_VERSION            1U

#define A52_R368_EVT_ENTRY          1U
#define A52_R368_EVT_RETURN         2U

struct a52_r368_record {
	u64 magic;
	u64 snapshot_ns;
	u64 last_ns;
	u64 sequence;
	u64 pc;
	u64 lr;
	u64 sp;
	u64 wchan;
	u64 clear_child_tid;
	u64 map_start;
	u64 map_end;
	u64 map_pgoff;
	u64 map_inode;
	u64 x[6];
	s64 ret;
	u64 nvcsw;
	u64 nivcsw;
	u32 syscallno;
	u32 event;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 rel;
	u32 alive;
	u32 state;
	u32 exit_state;
	u32 flags;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	char mapname[16];
};

static const char a52_r368_marker[] __used =
	"A52_PHASE368_APEXD_THREAD_FORENSICS_V1";
static void *a52_r368_sideband;
static struct a52_r368_record a52_r368_live[A52_R368_SLOTS];
static DEFINE_SPINLOCK(a52_r368_lock);
static atomic64_t a52_r368_sequence = ATOMIC64_INIT(0);
static atomic_t a52_r368_latest_tgid = ATOMIC_INIT(-1);

static bool a52_r368_apexd_thread(void)
{
	char comm[TASK_COMM_LEN];

	if (!current->group_leader)
		return false;
	get_task_comm(comm, current->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static int a52_r368_rel(void)
{
	int rel = current->pid - current->tgid;

	if (rel < 0 || rel >= A52_R368_SLOTS)
		return -1;
	return rel;
}

static u64 a52_r368_sys_enter(struct pt_regs *regs, int scno)
{
	struct a52_r368_record *r;
	unsigned long irqflags;
	u64 sequence;
	int rel;
	unsigned int i;

	if (!a52_r368_apexd_thread())
		return 0;

	rel = a52_r368_rel();
	if (rel < 0)
		return 0;

	atomic_set(&a52_r368_latest_tgid, current->tgid);
	sequence = (u64)atomic64_inc_return(&a52_r368_sequence);

	spin_lock_irqsave(&a52_r368_lock, irqflags);
	r = &a52_r368_live[rel];
	memset(r, 0, sizeof(*r));
	r->magic = A52_R368_MAGIC;
	r->last_ns = ktime_get_ns();
	r->sequence = sequence;
	r->pc = regs->pc;
	r->lr = regs->regs[30];
	r->sp = regs->sp;
	r->x[0] = regs->orig_x0;
	for (i = 1; i < ARRAY_SIZE(r->x); i++)
		r->x[i] = regs->regs[i];
	r->ret = (s64)(1ULL << 63);
	r->syscallno = (u32)scno;
	r->event = A52_R368_EVT_ENTRY;
	r->cpu = (u32)raw_smp_processor_id();
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->rel = (u32)rel;
	r->commit = A52_R368_COMMIT;
	r->version = A52_R368_VERSION;
	memcpy(r->comm, current->comm, TASK_COMM_LEN);
	spin_unlock_irqrestore(&a52_r368_lock, irqflags);

	return sequence;
}

static void a52_r368_sys_return(struct pt_regs *regs, int scno, u64 sequence)
{
	struct a52_r368_record *r;
	unsigned long irqflags;
	int rel;

	if (!sequence || !a52_r368_apexd_thread())
		return;

	rel = a52_r368_rel();
	if (rel < 0)
		return;

	spin_lock_irqsave(&a52_r368_lock, irqflags);
	r = &a52_r368_live[rel];
	if (r->sequence == sequence && r->pid == (u32)current->pid &&
	    r->tgid == (u32)current->tgid) {
		r->last_ns = ktime_get_ns();
		r->ret = (s64)regs->regs[0];
		r->event = A52_R368_EVT_RETURN;
		r->cpu = (u32)raw_smp_processor_id();
	}
	spin_unlock_irqrestore(&a52_r368_lock, irqflags);
}

static void a52_r368_map_pc(struct task_struct *task,
			    struct a52_r368_record *r)
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

static void a52_r368_write_slot(unsigned int slot,
				const struct a52_r368_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r368_sideband) || !r || slot >= A52_R368_SLOTS)
		return;

	pos = slot * A52_R368_SLOT_BYTES;
	dst0 = (u8 *)a52_r368_sideband + pos;
	dst1 = (u8 *)a52_r368_sideband + A52_R368_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
}

static void a52_r368_snapshot(void)
{
	struct a52_r368_record r;
	struct task_struct *task;
	unsigned long irqflags;
	u64 now = ktime_get_ns();
	int tgid = atomic_read(&a52_r368_latest_tgid);
	unsigned int rel;
	pid_t pid;

	if (!READ_ONCE(a52_r368_sideband) || tgid <= 0)
		return;

	for (rel = 0; rel < A52_R368_SLOTS; rel++) {
		spin_lock_irqsave(&a52_r368_lock, irqflags);
		memcpy(&r, &a52_r368_live[rel], sizeof(r));
		spin_unlock_irqrestore(&a52_r368_lock, irqflags);

		/* Ignore stale slots from the earlier bootstrap apexd instance. */
		if (r.magic != A52_R368_MAGIC || r.tgid != (u32)tgid)
			memset(&r, 0, sizeof(r));

		r.magic = A52_R368_MAGIC;
		r.snapshot_ns = now;
		r.rel = rel;
		r.commit = A52_R368_COMMIT;
		r.version = A52_R368_VERSION;
		pid = (pid_t)(tgid + (int)rel);

		task = NULL;
		rcu_read_lock();
		task = find_task_by_vpid(pid);
		if (task && task->tgid == tgid)
			get_task_struct(task);
		else
			task = NULL;
		rcu_read_unlock();

		if (task) {
			r.alive = 1;
			r.pid = (u32)task->pid;
			r.tgid = (u32)task->tgid;
			r.state = (u32)READ_ONCE(task->state);
			r.exit_state = (u32)READ_ONCE(task->exit_state);
			r.flags = READ_ONCE(task->flags);
			r.wchan = get_wchan(task);
			r.clear_child_tid =
				(u64)(unsigned long)READ_ONCE(task->clear_child_tid);
			r.nvcsw = READ_ONCE(task->nvcsw);
			r.nivcsw = READ_ONCE(task->nivcsw);
			get_task_comm(r.comm, task);
			a52_r368_map_pc(task, &r);
			put_task_struct(task);
		}

		a52_r368_write_slot(rel, &r);
	}

	wmb();
	__flush_dcache_area(a52_r368_sideband, A52_R368_SIDEBAND_BYTES);
}

static int a52_r368_thread(void *unused)
{
	msleep(30000);
	a52_r368_snapshot();
	msleep(15000);
	wmb();
	__flush_dcache_area(a52_r368_sideband, A52_R368_SIDEBAND_BYTES);
	kernel_restart("recovery");
	return 0;
}

static int __init a52_r368_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r368_record) != A52_R368_SLOT_BYTES);
	BUILD_BUG_ON(A52_R368_SLOTS * A52_R368_SLOT_BYTES !=
		     A52_R368_COPY_BYTES);

	a52_r368_sideband = memremap(A52_R368_SIDEBAND_PHYS,
		A52_R368_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r368_sideband)
		return 0;

	memset(a52_r368_sideband, 0, A52_R368_SIDEBAND_BYTES);
	memset(a52_r368_live, 0, sizeof(a52_r368_live));
	atomic64_set(&a52_r368_sequence, 0);
	atomic_set(&a52_r368_latest_tgid, -1);
	wmb();
	__flush_dcache_area(a52_r368_sideband, A52_R368_SIDEBAND_BYTES);

	if (IS_ERR(kthread_run(a52_r368_thread, NULL, "a52_r368")))
		return 0;
	return 0;
}
late_initcall(a52_r368_init);

'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE366_APEXD_FUTEX_THREADS_V1" not in text:
        raise SystemExit("Phase368 requires Phase366 syscall lineage")

    if "#include <linux/kthread.h>" not in text:
        text = INCLUDES + text

    anchor = (
        'static const char a52_r366_marker[] __used =\n'
        '\t"A52_PHASE366_APEXD_FUTEX_THREADS_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "forensic block insertion")

    text = one(
        text,
        "\tu64 a52_r366_sequence_token = 0;\n",
        "\tu64 a52_r366_sequence_token = 0;\n"
        "\tu64 a52_r368_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r366_sequence_token = a52_r366_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r366_sys_return(regs, scno, a52_r366_sequence_token);\n"
    )
    new = (
        "\ta52_r366_sequence_token = a52_r366_sys_enter(regs, scno);\n"
        "\ta52_r368_sequence_token = a52_r368_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r368_sys_return(regs, scno, a52_r368_sequence_token);\n"
        "\ta52_r366_sys_return(regs, scno, a52_r366_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


def patch_futex(text: str) -> str:
    if "A52_PHASE367_APEXD_KERNEL_FUTEX_WAKE_V1" not in text:
        raise SystemExit("Phase368 requires Phase367 futex lineage")

    if "static int __init __used a52_r367_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r367_init(void)\n",
            "static int __init __used a52_r367_init(void)\n",
            "mark Phase367 init dormant",
        )
    if "late_initcall(a52_r367_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r367_init);\n",
            "/* Phase368 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase367 mapper",
        )
    return text


def validate(syscall: str, futex: str) -> None:
    for token in (
        MARK,
        "A52_R368_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R368_SLOT_BYTES         256U",
        "A52_R368_SLOTS              64U",
        "A52_R368_COMMIT             0x368c0de5U",
        "a52_r368_sys_enter(regs, scno);",
        "a52_r368_sys_return(regs, scno, a52_r368_sequence_token);",
        "r.wchan = get_wchan(task);",
        "READ_ONCE(task->clear_child_tid)",
        "vma->vm_file->f_path.dentry->d_name.name",
        'kernel_restart("recovery");',
        "late_initcall(a52_r368_init);",
    ):
        if token not in syscall:
            raise SystemExit("Phase368 syscall token missing: " + token)

    if "late_initcall(a52_r367_init);" in futex:
        raise SystemExit("Phase368 must disable Phase367 sideband owner")
    if "static int __init __used a52_r367_init(void)" not in futex:
        raise SystemExit("Phase368 dormant Phase367 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    syscall_path = ns.root / SYSCALL
    futex_path = locate_futex(ns.root)
    if not syscall_path.is_file():
        raise SystemExit("Phase368 syscall source missing")

    before_sys = syscall_path.read_text(encoding="utf-8")
    before_futex = futex_path.read_text(encoding="utf-8")

    if MARK in before_sys:
        validate(before_sys, before_futex)
        print("Phase368 apexd thread forensic snapshot audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase368 marker missing in check-only mode")

    after_sys = patch_syscall(before_sys)
    after_futex = patch_futex(before_futex)
    validate(after_sys, after_futex)

    syscall_path.write_text(after_sys, encoding="utf-8")
    futex_path.write_text(after_futex, encoding="utf-8")
    print("Phase368 apexd thread forensic snapshot applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
