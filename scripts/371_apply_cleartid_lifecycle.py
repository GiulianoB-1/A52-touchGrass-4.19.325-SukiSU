#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
FORK = Path("kernel/fork.c")
MARK = "A52_PHASE371_CLEARTID_LIFECYCLE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase371 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE371_CLEARTID_LIFECYCLE_V1
 *
 * Phase369 proved apexd can pthread_join a dead TID whose userspace tid word
 * remains nonzero. Phase370 then proved a stronger invariant violation:
 *
 *   parent clone(): CLONE_CHILD_CLEARTID set, child_tid pointer nonzero
 *   child mm_release(): task->clear_child_tid == NULL
 *
 * Therefore put_user(0) and FUTEX_WAKE are never even attempted for those
 * pthreads. Phase371 locates exactly where task->clear_child_tid disappears.
 *
 * One 128-byte record is allocated per apexd CLONE_CHILD_CLEARTID task and
 * updated in-place across the task lifecycle:
 *
 *   after copy_process assignment
 *   after arch copy_thread()
 *   immediately before wake_up_new_task()
 *   first syscall executed by the child
 *   set_tid_address() if called
 *   mm_release()
 *
 * 128 records fit in each 16 KiB mirror. Creation-stage values are accumulated
 * in RAM; records are flushed at publication/first-syscall/set_tid/mm_release.
 * This avoids Phase370's high-frequency event ring while preserving the exact
 * transition that clears the pointer.
 */
#define A52_R371_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R371_SIDEBAND_BYTES     0x8000U
#define A52_R371_COPY_BYTES         0x4000U
#define A52_R371_SLOT_BYTES         128U
#define A52_R371_SLOTS              128U
#define A52_R371_MAGIC              0x3137334546494c41ULL
#define A52_R371_COMMIT             0x371c0de5U
#define A52_R371_VERSION            1U

#define A52_R371_ST_ASSIGN          (1U << 0)
#define A52_R371_ST_AFTER_COPY      (1U << 1)
#define A52_R371_ST_BEFORE_WAKE     (1U << 2)
#define A52_R371_ST_FIRST_SYSCALL   (1U << 3)
#define A52_R371_ST_SET_TID         (1U << 4)
#define A52_R371_ST_MM_RELEASE      (1U << 5)

struct a52_r371_record {
	u64 magic;
	u64 task_ptr;
	u64 clone_flags;
	u64 arg_child_tid;
	u64 after_assign;
	u64 after_copy;
	u64 before_wake;
	u64 first_syscall_clear;
	u64 set_tid_arg;
	u64 mm_clear_tid;
	u32 pid;
	u32 tgid;
	u32 first_syscallno;
	u32 stage_mask;
	u32 set_tid_seen;
	u32 cpu;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
};

static const char a52_r371_marker[] __used =
	"A52_PHASE371_CLEARTID_LIFECYCLE_V1";
static void *a52_r371_sideband;
static struct a52_r371_record a52_r371_live[A52_R371_SLOTS];
static DEFINE_SPINLOCK(a52_r371_lock);
static atomic_t a52_r371_next = ATOMIC_INIT(0);

static bool a52_r371_apexd_task(struct task_struct *tsk)
{
	char comm[TASK_COMM_LEN];

	if (!tsk || !tsk->group_leader)
		return false;
	get_task_comm(comm, tsk->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static int a52_r371_find_locked(struct task_struct *tsk)
{
	u64 ptr = (u64)(unsigned long)tsk;
	unsigned int i;

	for (i = 0; i < A52_R371_SLOTS; i++)
		if (a52_r371_live[i].task_ptr == ptr)
			return (int)i;
	return -1;
}

static void a52_r371_write_slot(unsigned int slot,
				const struct a52_r371_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r371_sideband) || !r || slot >= A52_R371_SLOTS)
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

static void a52_r371_persist_locked(unsigned int slot)
{
	a52_r371_live[slot].cpu = (u32)raw_smp_processor_id();
	a52_r371_live[slot].commit = A52_R371_COMMIT;
	a52_r371_live[slot].version = A52_R371_VERSION;
	a52_r371_write_slot(slot, &a52_r371_live[slot]);
}

void a52_r371_fork_stage(struct task_struct *child, u32 stage,
			 u64 clone_flags, u64 arg_child_tid);
void a52_r371_fork_stage(struct task_struct *child, u32 stage,
			 u64 clone_flags, u64 arg_child_tid)
{
	struct a52_r371_record *r;
	unsigned long irqflags;
	int slot;
	int next;

	if (!READ_ONCE(a52_r371_sideband) || !child)
		return;

	spin_lock_irqsave(&a52_r371_lock, irqflags);
	slot = a52_r371_find_locked(child);

	if (stage == A52_R371_ST_ASSIGN && slot < 0) {
		if (!a52_r371_apexd_task(current) ||
		    !(clone_flags & CLONE_CHILD_CLEARTID)) {
			spin_unlock_irqrestore(&a52_r371_lock, irqflags);
			return;
		}

		next = atomic_read(&a52_r371_next);
		if (next < 0 || next >= A52_R371_SLOTS) {
			spin_unlock_irqrestore(&a52_r371_lock, irqflags);
			return;
		}
		atomic_inc(&a52_r371_next);
		slot = next;
		memset(&a52_r371_live[slot], 0,
		       sizeof(a52_r371_live[slot]));
		r = &a52_r371_live[slot];
		r->magic = A52_R371_MAGIC;
		r->task_ptr = (u64)(unsigned long)child;
		r->clone_flags = clone_flags;
		r->arg_child_tid = arg_child_tid;
	} else if (slot < 0) {
		spin_unlock_irqrestore(&a52_r371_lock, irqflags);
		return;
	}

	r = &a52_r371_live[slot];
	r->stage_mask |= stage;

	if (stage == A52_R371_ST_ASSIGN)
		r->after_assign =
			(u64)(unsigned long)READ_ONCE(child->clear_child_tid);
	else if (stage == A52_R371_ST_AFTER_COPY)
		r->after_copy =
			(u64)(unsigned long)READ_ONCE(child->clear_child_tid);
	else if (stage == A52_R371_ST_BEFORE_WAKE) {
		r->before_wake =
			(u64)(unsigned long)READ_ONCE(child->clear_child_tid);
		r->pid = (u32)child->pid;
		r->tgid = (u32)child->tgid;
		get_task_comm(r->comm, child);
		a52_r371_persist_locked((unsigned int)slot);
	}

	spin_unlock_irqrestore(&a52_r371_lock, irqflags);
}

void a52_r371_set_tid_event(struct task_struct *tsk, u64 tidptr);
void a52_r371_set_tid_event(struct task_struct *tsk, u64 tidptr)
{
	unsigned long irqflags;
	int slot;

	if (!READ_ONCE(a52_r371_sideband) || !tsk)
		return;

	spin_lock_irqsave(&a52_r371_lock, irqflags);
	slot = a52_r371_find_locked(tsk);
	if (slot >= 0) {
		a52_r371_live[slot].set_tid_seen++;
		a52_r371_live[slot].set_tid_arg = tidptr;
		a52_r371_live[slot].stage_mask |= A52_R371_ST_SET_TID;
		a52_r371_persist_locked((unsigned int)slot);
	}
	spin_unlock_irqrestore(&a52_r371_lock, irqflags);
}

void a52_r371_mm_event(struct task_struct *tsk);
void a52_r371_mm_event(struct task_struct *tsk)
{
	unsigned long irqflags;
	int slot;

	if (!READ_ONCE(a52_r371_sideband) || !tsk)
		return;

	spin_lock_irqsave(&a52_r371_lock, irqflags);
	slot = a52_r371_find_locked(tsk);
	if (slot >= 0) {
		a52_r371_live[slot].mm_clear_tid =
			(u64)(unsigned long)READ_ONCE(tsk->clear_child_tid);
		a52_r371_live[slot].stage_mask |= A52_R371_ST_MM_RELEASE;
		a52_r371_persist_locked((unsigned int)slot);
	}
	spin_unlock_irqrestore(&a52_r371_lock, irqflags);
}

static void a52_r371_first_syscall(int scno)
{
	unsigned long irqflags;
	int slot;

	if (!READ_ONCE(a52_r371_sideband))
		return;

	spin_lock_irqsave(&a52_r371_lock, irqflags);
	slot = a52_r371_find_locked(current);
	if (slot >= 0 &&
	    !(a52_r371_live[slot].stage_mask & A52_R371_ST_FIRST_SYSCALL)) {
		a52_r371_live[slot].first_syscall_clear =
			(u64)(unsigned long)READ_ONCE(current->clear_child_tid);
		a52_r371_live[slot].first_syscallno = (u32)scno;
		a52_r371_live[slot].pid = (u32)current->pid;
		a52_r371_live[slot].tgid = (u32)current->tgid;
		get_task_comm(a52_r371_live[slot].comm, current);
		a52_r371_live[slot].stage_mask |= A52_R371_ST_FIRST_SYSCALL;
		a52_r371_persist_locked((unsigned int)slot);
	}
	spin_unlock_irqrestore(&a52_r371_lock, irqflags);
}

static int a52_r371_thread(void *unused)
{
	msleep(45000);
	wmb();
	__flush_dcache_area(a52_r371_sideband, A52_R371_SIDEBAND_BYTES);
	kernel_restart("recovery");
	return 0;
}

static int __init a52_r371_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r371_record) != A52_R371_SLOT_BYTES);
	BUILD_BUG_ON(A52_R371_SLOTS * A52_R371_SLOT_BYTES !=
		     A52_R371_COPY_BYTES);

	a52_r371_sideband = memremap(A52_R371_SIDEBAND_PHYS,
		A52_R371_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r371_sideband)
		return 0;

	memset(a52_r371_sideband, 0, A52_R371_SIDEBAND_BYTES);
	memset(a52_r371_live, 0, sizeof(a52_r371_live));
	atomic_set(&a52_r371_next, 0);
	wmb();
	__flush_dcache_area(a52_r371_sideband, A52_R371_SIDEBAND_BYTES);

	if (IS_ERR(kthread_run(a52_r371_thread, NULL, "a52_r371")))
		return 0;
	return 0;
}
late_initcall(a52_r371_init);

'''


DECL = r'''
extern void a52_r371_fork_stage(struct task_struct *child, u32 stage,
				u64 clone_flags, u64 arg_child_tid);
extern void a52_r371_set_tid_event(struct task_struct *tsk, u64 tidptr);
extern void a52_r371_mm_event(struct task_struct *tsk);

#define A52_R371_ST_ASSIGN          (1U << 0)
#define A52_R371_ST_AFTER_COPY      (1U << 1)
#define A52_R371_ST_BEFORE_WAKE     (1U << 2)

'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE369_APEXD_JOIN_TARGET_V1" not in text:
        raise SystemExit("Phase371 requires Phase369 syscall lineage")

    anchor = (
        'static const char a52_r369_marker[] __used =\n'
        '\t"A52_PHASE369_APEXD_JOIN_TARGET_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "lifecycle block insertion")

    text = one(
        text,
        "static int __init a52_r369_init(void)\n",
        "static int __init __used a52_r369_init(void)\n",
        "mark Phase369 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r369_init);\n",
        "/* Phase371 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase369 mapper",
    )

    # Call before every syscall; only tracked child tasks are touched.
    invoke = "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
    text = one(
        text,
        invoke,
        "\ta52_r371_first_syscall(scno);\n" + invoke,
        "first syscall hook",
    )
    return text


def patch_fork(text: str) -> str:
    if MARK in text:
        return text

    futex_inc = "#include <linux/futex.h>\n"
    if futex_inc not in text:
        raise SystemExit("Phase371 fork.c futex include missing")
    text = one(text, futex_inc, futex_inc + DECL, "declarations")

    assign = (
        "\tp->clear_child_tid = (clone_flags & CLONE_CHILD_CLEARTID) ? "
        "args->child_tid : NULL;\n"
    )
    text = one(
        text,
        assign,
        assign +
        "\ta52_r371_fork_stage(p, A52_R371_ST_ASSIGN, clone_flags,\n"
        "\t\t\t     (u64)(unsigned long)args->child_tid);\n",
        "post assignment",
    )

    copy = (
        "\tretval = copy_thread(clone_flags, args->stack, args->stack_size, p, args->tls);\n"
        "\tif (retval)\n"
        "\t\tgoto bad_fork_cleanup_io;\n"
    )
    text = one(
        text,
        copy,
        copy +
        "\ta52_r371_fork_stage(p, A52_R371_ST_AFTER_COPY, clone_flags,\n"
        "\t\t\t     (u64)(unsigned long)args->child_tid);\n",
        "post copy_thread",
    )

    wake = "\twake_up_new_task(p);\n"
    text = one(
        text,
        wake,
        "\ta52_r371_fork_stage(p, A52_R371_ST_BEFORE_WAKE, clone_flags,\n"
        "\t\t\t     (u64)(unsigned long)args->child_tid);\n" +
        wake,
        "pre wake",
    )

    set_tid = (
        "SYSCALL_DEFINE1(set_tid_address, int __user *, tidptr)\n"
        "{\n"
        "\tcurrent->clear_child_tid = tidptr;\n"
    )
    text = one(
        text,
        set_tid,
        "SYSCALL_DEFINE1(set_tid_address, int __user *, tidptr)\n"
        "{\n"
        "\ta52_r371_set_tid_event(current, (u64)(unsigned long)tidptr);\n"
        "\tcurrent->clear_child_tid = tidptr;\n",
        "set_tid_address",
    )

    mm = "static void mm_release(struct task_struct *tsk, struct mm_struct *mm)\n{\n"
    text = one(
        text,
        mm,
        mm + "\ta52_r371_mm_event(tsk);\n",
        "mm_release entry",
    )

    marker = "static void mm_release(struct task_struct *tsk, struct mm_struct *mm)\n"
    if MARK not in text:
        text = text.replace(
            marker,
            "static const char a52_r371_fork_marker[] __used =\n"
            '\t"A52_PHASE371_CLEARTID_LIFECYCLE_V1";\n' + marker,
            1,
        )
    return text


def validate(syscall: str, fork: str) -> None:
    for token in (
        MARK,
        "A52_R371_SLOT_BYTES         128U",
        "A52_R371_SLOTS              128U",
        "after_assign",
        "after_copy",
        "before_wake",
        "first_syscall_clear",
        "set_tid_arg",
        "mm_clear_tid",
        "a52_r371_first_syscall(scno);",
        "late_initcall(a52_r371_init);",
    ):
        if token not in syscall:
            raise SystemExit("Phase371 syscall token missing: " + token)

    for token in (
        MARK,
        "a52_r371_fork_stage(p, A52_R371_ST_ASSIGN",
        "a52_r371_fork_stage(p, A52_R371_ST_AFTER_COPY",
        "a52_r371_fork_stage(p, A52_R371_ST_BEFORE_WAKE",
        "a52_r371_set_tid_event(current",
        "a52_r371_mm_event(tsk);",
    ):
        if token not in fork:
            raise SystemExit("Phase371 fork token missing: " + token)

    if "late_initcall(a52_r369_init);" in syscall:
        raise SystemExit("Phase371 must disable Phase369 sideband owner")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    syscall_path = ns.root / SYS
    fork_path = ns.root / FORK
    if not syscall_path.is_file() or not fork_path.is_file():
        raise SystemExit("Phase371 required source missing")

    before_sys = syscall_path.read_text(encoding="utf-8")
    before_fork = fork_path.read_text(encoding="utf-8")

    if MARK in before_sys and MARK in before_fork:
        validate(before_sys, before_fork)
        print("Phase371 clear_child_tid lifecycle audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase371 marker missing in check-only mode")

    after_sys = patch_syscall(before_sys)
    after_fork = patch_fork(before_fork)
    validate(after_sys, after_fork)

    syscall_path.write_text(after_sys, encoding="utf-8")
    fork_path.write_text(after_fork, encoding="utf-8")
    print("Phase371 clear_child_tid lifecycle applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
