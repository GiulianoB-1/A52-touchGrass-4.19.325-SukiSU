#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
FORK = Path("kernel/fork.c")
MARK = "A52_PHASE370_CLEARTID_FORENSICS_V1"

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase370 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE370_CLEARTID_FORENSICS_V1
 *
 * Phase369 hardware result:
 *   apexd main waits in plain FUTEX_WAIT with expected TID 1285;
 *   target TID 1285 no longer exists at snapshot time;
 *   the actual userspace futex word still contains 1285 (0x505).
 *
 * Bionic pthread_create uses CLONE_CHILD_CLEARTID and pthread_join waits on
 * thread->tid. Linux mm_release() must put_user(0, clear_child_tid) and wake
 * that address on thread exit.
 *
 * Trace all relevant pieces in one boot:
 *   - apexd clone() return: child TID, clone flags, child_tid pointer
 *   - apexd group-leader plain FUTEX_WAIT: candidate joined TID/address
 *   - every apexd thread mm_release(): clear_child_tid, mm_users, signal flags
 *   - userspace word before put_user, after put_user, after futex wake
 *   - put_user return and do_futex(FUTEX_WAKE) return
 *
 * 64 x 256-byte records are mirrored into two 16 KiB copies and flushed on
 * every event. The boot returns to recovery at ~45 s.
 */
#define A52_R370_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R370_SIDEBAND_BYTES     0x8000U
#define A52_R370_COPY_BYTES         0x4000U
#define A52_R370_SLOT_BYTES         256U
#define A52_R370_SLOTS              64U
#define A52_R370_MAGIC              0x3037334449544341ULL
#define A52_R370_COMMIT             0x370c0de5U
#define A52_R370_VERSION            1U

#define A52_R370_EVT_JOIN_WAIT      1U
#define A52_R370_EVT_CLONE_RETURN   2U
#define A52_R370_EVT_MM_ENTER       3U
#define A52_R370_EVT_MM_SKIP        4U
#define A52_R370_EVT_AFTER_PUT      5U
#define A52_R370_EVT_AFTER_WAKE     6U

struct a52_r370_record {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 uaddr;
	u64 mm;
	u64 clear_child_tid;
	u64 aux;
	s64 wake_ret;
	u32 event;
	u32 pid;
	u32 tgid;
	u32 target_tid;
	u32 mm_users;
	u32 signal_flags;
	u32 word_before;
	u32 word_after_put;
	u32 word_after_wake;
	s32 before_rc;
	s32 put_rc;
	s32 after_put_rc;
	s32 after_wake_rc;
	u32 cpu;
	u32 task_flags;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	u8 reserved[108];
};

static const char a52_r370_marker[] __used =
	"A52_PHASE370_CLEARTID_FORENSICS_V1";
static void *a52_r370_sideband;
static atomic64_t a52_r370_sequence = ATOMIC64_INIT(0);

static bool a52_r370_apexd_task(struct task_struct *tsk)
{
	char comm[TASK_COMM_LEN];

	if (!tsk || !tsk->group_leader)
		return false;
	get_task_comm(comm, tsk->group_leader);
	return !strncmp(comm, "apexd", TASK_COMM_LEN);
}

static void a52_r370_write(const struct a52_r370_record *r)
{
	unsigned int slot;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r370_sideband) || !r)
		return;

	slot = (unsigned int)((r->sequence - 1ULL) % A52_R370_SLOTS);
	pos = slot * A52_R370_SLOT_BYTES;
	dst0 = (u8 *)a52_r370_sideband + pos;
	dst1 = (u8 *)a52_r370_sideband + A52_R370_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r370_record_basic(u32 event, struct task_struct *tsk,
				  u64 uaddr, u32 target_tid, u64 aux)
{
	struct a52_r370_record r;

	if (!READ_ONCE(a52_r370_sideband) || !a52_r370_apexd_task(tsk))
		return;

	memset(&r, 0, sizeof(r));
	r.magic = A52_R370_MAGIC;
	r.ns = ktime_get_ns();
	r.sequence = (u64)atomic64_inc_return(&a52_r370_sequence);
	r.uaddr = uaddr;
	r.aux = aux;
	r.wake_ret = LONG_MIN;
	r.event = event;
	r.pid = (u32)tsk->pid;
	r.tgid = (u32)tsk->tgid;
	r.target_tid = target_tid;
	r.cpu = (u32)raw_smp_processor_id();
	r.task_flags = READ_ONCE(tsk->flags);
	r.commit = A52_R370_COMMIT;
	r.version = A52_R370_VERSION;
	get_task_comm(r.comm, tsk);
	a52_r370_write(&r);
}

void a52_r370_record_mm_release(struct task_struct *tsk, struct mm_struct *mm,
				u32 event, u32 __user *clear_tid,
				u32 word_before, int before_rc,
				int put_rc, u32 word_after_put,
				int after_put_rc, long wake_ret,
				u32 word_after_wake, int after_wake_rc);
void a52_r370_record_mm_release(struct task_struct *tsk, struct mm_struct *mm,
				u32 event, u32 __user *clear_tid,
				u32 word_before, int before_rc,
				int put_rc, u32 word_after_put,
				int after_put_rc, long wake_ret,
				u32 word_after_wake, int after_wake_rc)
{
	struct a52_r370_record r;

	if (!READ_ONCE(a52_r370_sideband) || !a52_r370_apexd_task(tsk))
		return;

	memset(&r, 0, sizeof(r));
	r.magic = A52_R370_MAGIC;
	r.ns = ktime_get_ns();
	r.sequence = (u64)atomic64_inc_return(&a52_r370_sequence);
	r.uaddr = (u64)(unsigned long)clear_tid;
	r.mm = (u64)(unsigned long)mm;
	r.clear_child_tid = (u64)(unsigned long)READ_ONCE(tsk->clear_child_tid);
	r.aux = 0;
	r.wake_ret = wake_ret;
	r.event = event;
	r.pid = (u32)tsk->pid;
	r.tgid = (u32)tsk->tgid;
	r.mm_users = mm ? (u32)atomic_read(&mm->mm_users) : 0;
	r.signal_flags = tsk->signal ? READ_ONCE(tsk->signal->flags) : 0;
	r.word_before = word_before;
	r.word_after_put = word_after_put;
	r.word_after_wake = word_after_wake;
	r.before_rc = before_rc;
	r.put_rc = put_rc;
	r.after_put_rc = after_put_rc;
	r.after_wake_rc = after_wake_rc;
	r.cpu = (u32)raw_smp_processor_id();
	r.task_flags = READ_ONCE(tsk->flags);
	r.commit = A52_R370_COMMIT;
	r.version = A52_R370_VERSION;
	get_task_comm(r.comm, tsk);
	a52_r370_write(&r);
}

static u64 a52_r370_sys_enter(struct pt_regs *regs, int scno)
{
	if (!a52_r370_apexd_task(current))
		return 0;

	if (current->pid == current->tgid && scno == __NR_futex) {
		u32 op = (u32)regs->regs[1];
		u32 expected = (u32)regs->regs[2];

		if ((op & FUTEX_CMD_MASK) == FUTEX_WAIT &&
		    !(op & FUTEX_PRIVATE_FLAG) && expected > 1U)
			a52_r370_record_basic(A52_R370_EVT_JOIN_WAIT, current,
				regs->orig_x0, expected, (u64)op);
	}

	/* Token is only needed so return handling knows this was an apexd call. */
	return 1;
}

static void a52_r370_sys_return(struct pt_regs *regs, int scno, u64 token)
{
	s64 ret;

	if (!token || !a52_r370_apexd_task(current))
		return;

	ret = (s64)regs->regs[0];
	if (scno == __NR_clone && ret > 0) {
		/*
		 * arm64 clone(flags, newsp, parent_tid, tls, child_tid).
		 * Bionic pthread_create passes &thread->tid as child_tid.
		 */
		a52_r370_record_basic(A52_R370_EVT_CLONE_RETURN, current,
			regs->regs[4], (u32)ret, regs->orig_x0);
	}
}

static int a52_r370_thread(void *unused)
{
	msleep(45000);
	wmb();
	__flush_dcache_area(a52_r370_sideband, A52_R370_SIDEBAND_BYTES);
	kernel_restart("recovery");
	return 0;
}

static int __init a52_r370_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r370_record) != A52_R370_SLOT_BYTES);
	BUILD_BUG_ON(A52_R370_SLOTS * A52_R370_SLOT_BYTES !=
		     A52_R370_COPY_BYTES);

	a52_r370_sideband = memremap(A52_R370_SIDEBAND_PHYS,
		A52_R370_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r370_sideband)
		return 0;

	memset(a52_r370_sideband, 0, A52_R370_SIDEBAND_BYTES);
	atomic64_set(&a52_r370_sequence, 0);
	wmb();
	__flush_dcache_area(a52_r370_sideband, A52_R370_SIDEBAND_BYTES);

	if (IS_ERR(kthread_run(a52_r370_thread, NULL, "a52_r370")))
		return 0;
	return 0;
}
late_initcall(a52_r370_init);

'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE369_APEXD_JOIN_TARGET_V1" not in text:
        raise SystemExit("Phase370 requires Phase369 syscall lineage")

    anchor = (
        'static const char a52_r369_marker[] __used =\n'
        '\t"A52_PHASE369_APEXD_JOIN_TARGET_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase370 block insertion")

    text = one(
        text,
        "static int __init a52_r369_init(void)\n",
        "static int __init __used a52_r369_init(void)\n",
        "mark Phase369 init dormant",
    )
    text = one(
        text,
        "late_initcall(a52_r369_init);\n",
        "/* Phase370 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
        "disable Phase369 mapper",
    )

    text = one(
        text,
        "\tu64 a52_r369_sequence_token = 0;\n",
        "\tu64 a52_r369_sequence_token = 0;\n"
        "\tu64 a52_r370_sequence_token = 0;\n",
        "sequence token",
    )

    old = (
        "\ta52_r369_sequence_token = a52_r369_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r369_sys_return(regs, scno, a52_r369_sequence_token);\n"
    )
    new = (
        "\ta52_r369_sequence_token = a52_r369_sys_enter(regs, scno);\n"
        "\ta52_r370_sequence_token = a52_r370_sys_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_sys_return(regs, scno, a52_r370_sequence_token);\n"
        "\ta52_r369_sys_return(regs, scno, a52_r369_sequence_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text


DECL = r'''
extern void a52_r370_record_mm_release(struct task_struct *tsk,
				       struct mm_struct *mm, u32 event,
				       u32 __user *clear_tid,
				       u32 word_before, int before_rc,
				       int put_rc, u32 word_after_put,
				       int after_put_rc, long wake_ret,
				       u32 word_after_wake, int after_wake_rc);

#define A52_R370_EVT_MM_ENTER       3U
#define A52_R370_EVT_MM_SKIP        4U
#define A52_R370_EVT_AFTER_PUT      5U
#define A52_R370_EVT_AFTER_WAKE     6U

'''

OLD_MM = r'''	if (tsk->clear_child_tid) {
		if (!(tsk->signal->flags & SIGNAL_GROUP_COREDUMP) &&
		    atomic_read(&mm->mm_users) > 1) {
			/*
			 * We don't check the error code - if userspace has
			 * not set up a proper pointer then tough luck.
			 */
			put_user(0, tsk->clear_child_tid);
			do_futex(tsk->clear_child_tid, FUTEX_WAKE,
					1, NULL, NULL, 0, 0);
		}
		tsk->clear_child_tid = NULL;
	}
'''

NEW_MM = r'''	{
		u32 __user *a52_r370_ctid = READ_ONCE(tsk->clear_child_tid);
		u32 a52_r370_before = 0xffffffffU;
		u32 a52_r370_after_put = 0xffffffffU;
		u32 a52_r370_after_wake = 0xffffffffU;
		int a52_r370_before_rc = -1;
		int a52_r370_put_rc = -1;
		int a52_r370_after_put_rc = -1;
		int a52_r370_after_wake_rc = -1;
		long a52_r370_wake_ret = LONG_MIN;

		if (a52_r370_ctid)
			a52_r370_before_rc =
				get_user(a52_r370_before, a52_r370_ctid);

		a52_r370_record_mm_release(tsk, mm, A52_R370_EVT_MM_ENTER,
			a52_r370_ctid, a52_r370_before, a52_r370_before_rc,
			a52_r370_put_rc, a52_r370_after_put,
			a52_r370_after_put_rc, a52_r370_wake_ret,
			a52_r370_after_wake, a52_r370_after_wake_rc);

		if (a52_r370_ctid) {
			if (!(tsk->signal->flags & SIGNAL_GROUP_COREDUMP) &&
			    atomic_read(&mm->mm_users) > 1) {
				/*
				 * Preserve the original mm_release semantics:
				 * ignore put_user() failure and issue FUTEX_WAKE.
				 */
				a52_r370_put_rc = put_user(0, a52_r370_ctid);
				a52_r370_after_put_rc =
					get_user(a52_r370_after_put,
						 a52_r370_ctid);
				a52_r370_record_mm_release(tsk, mm,
					A52_R370_EVT_AFTER_PUT,
					a52_r370_ctid, a52_r370_before,
					a52_r370_before_rc, a52_r370_put_rc,
					a52_r370_after_put,
					a52_r370_after_put_rc,
					a52_r370_wake_ret,
					a52_r370_after_wake,
					a52_r370_after_wake_rc);

				a52_r370_wake_ret =
					do_futex(a52_r370_ctid, FUTEX_WAKE,
						 1, NULL, NULL, 0, 0);
				a52_r370_after_wake_rc =
					get_user(a52_r370_after_wake,
						 a52_r370_ctid);
				a52_r370_record_mm_release(tsk, mm,
					A52_R370_EVT_AFTER_WAKE,
					a52_r370_ctid, a52_r370_before,
					a52_r370_before_rc, a52_r370_put_rc,
					a52_r370_after_put,
					a52_r370_after_put_rc,
					a52_r370_wake_ret,
					a52_r370_after_wake,
					a52_r370_after_wake_rc);
			} else {
				a52_r370_record_mm_release(tsk, mm,
					A52_R370_EVT_MM_SKIP,
					a52_r370_ctid, a52_r370_before,
					a52_r370_before_rc, a52_r370_put_rc,
					a52_r370_after_put,
					a52_r370_after_put_rc,
					a52_r370_wake_ret,
					a52_r370_after_wake,
					a52_r370_after_wake_rc);
			}
			tsk->clear_child_tid = NULL;
		} else {
			a52_r370_record_mm_release(tsk, mm,
				A52_R370_EVT_MM_SKIP,
				a52_r370_ctid, a52_r370_before,
				a52_r370_before_rc, a52_r370_put_rc,
				a52_r370_after_put,
				a52_r370_after_put_rc,
				a52_r370_wake_ret,
				a52_r370_after_wake,
				a52_r370_after_wake_rc);
		}
	}
'''


def patch_fork(text: str) -> str:
    if MARK in text:
        return text

    # Put the declaration after the include block, before source functions.
    marker = "#include <linux/futex.h>\n"
    if marker not in text:
        raise SystemExit("Phase370 fork.c futex include missing")
    text = one(text, marker, marker + DECL, "fork declaration")

    if OLD_MM not in text:
        raise SystemExit("Phase370 exact mm_release clear_child_tid block missing")
    text = text.replace(OLD_MM, NEW_MM, 1)

    # Source-level marker for workflow/image audits.
    text = text.replace(
        "static void mm_release(struct task_struct *tsk, struct mm_struct *mm)\n",
        "static const char a52_r370_fork_marker[] __used =\n"
        '\t"A52_PHASE370_CLEARTID_FORENSICS_V1";\n'
        "static void mm_release(struct task_struct *tsk, struct mm_struct *mm)\n",
        1,
    )
    return text


def validate(syscall: str, fork: str) -> None:
    for token in (
        MARK,
        "A52_R370_SIDEBAND_PHYS      0xB1BF0000ULL",
        "A52_R370_EVT_CLONE_RETURN",
        "A52_R370_EVT_JOIN_WAIT",
        "scno == __NR_clone",
        "regs->regs[4]",
        "a52_r370_record_mm_release",
        "late_initcall(a52_r370_init);",
    ):
        if token not in syscall:
            raise SystemExit("Phase370 syscall token missing: " + token)

    for token in (
        MARK,
        "A52_R370_EVT_MM_ENTER",
        "A52_R370_EVT_AFTER_PUT",
        "A52_R370_EVT_AFTER_WAKE",
        "get_user(a52_r370_before",
        "a52_r370_put_rc = put_user(0, a52_r370_ctid);",
        "do_futex(a52_r370_ctid, FUTEX_WAKE",
    ):
        if token not in fork:
            raise SystemExit("Phase370 fork token missing: " + token)

    if "late_initcall(a52_r369_init);" in syscall:
        raise SystemExit("Phase370 must disable Phase369 sideband owner")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    syscall_path = ns.root / SYS
    fork_path = ns.root / FORK
    if not syscall_path.is_file() or not fork_path.is_file():
        raise SystemExit("Phase370 required source missing")

    before_sys = syscall_path.read_text(encoding="utf-8")
    before_fork = fork_path.read_text(encoding="utf-8")

    if MARK in before_sys and MARK in before_fork:
        validate(before_sys, before_fork)
        print("Phase370 clear_child_tid forensic audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase370 marker missing in check-only mode")

    after_sys = patch_syscall(before_sys)
    after_fork = patch_fork(before_fork)
    validate(after_sys, after_fork)

    syscall_path.write_text(after_sys, encoding="utf-8")
    fork_path.write_text(after_fork, encoding="utf-8")
    print("Phase370 clear_child_tid forensics applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
