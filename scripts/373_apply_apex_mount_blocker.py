#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE373_APEX_MOUNT_BLOCKER_V1"

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase373 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)

BLOCK = r'''
/* A52_PHASE373_APEX_MOUNT_BLOCKER_V1
 *
 * Phase372 hardware result:
 * - second apexd process TGID 493 begins at worker ordinal 49 because the first
 *   apexd consumed ordinals 0..48.
 * - worker 49/PID 496 successfully wakes main futex wait #5.
 * - workers 50..54 have all exited before main advances.
 * - worker 55/PID 502 enters syscall 40 (__NR_mount) at ~18.972 s and never
 *   returns before the ~330 s safety reboot.
 * - main returns from wait #5 at ~19.065165 s and only ~47.9 us later enters
 *   wait #6 forever. The blocked mount had already been outstanding ~92.8 ms.
 *
 * Phase373 resets worker ordinals for each new apexd TGID, so the problematic
 * worker is expected near local ordinal 6 instead of global ordinal 55.
 *
 * Each worker gets one persistent 512-byte record containing its latest mount:
 * source, target, filesystem type, flags, return state, and (after 100 ms
 * blocked) a kernel task stack snapshot.  The top stack address is also
 * symbolized in-kernel using sprint_symbol().
 *
 * 32 records per 16 KiB mirror; worker ordinal maps directly to slot.
 * Observation only.
 */
#define A52_R373_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R373_SIDEBAND_BYTES     0x8000U
#define A52_R373_COPY_BYTES         0x4000U
#define A52_R373_SLOT_BYTES         512U
#define A52_R373_SLOTS_PER_COPY     32U
#define A52_R373_MAX_WORKERS        32U
#define A52_R373_STACK_ENTRIES      12U
#define A52_R373_MAGIC              0x333733584d414141ULL
#define A52_R373_COMMIT             0x373c0de5U
#define A52_R373_VERSION            1U

#define A52_R373_EVT_MOUNT_ENTRY    1U
#define A52_R373_EVT_MOUNT_RETURN   2U
#define A52_R373_EVT_STACK_SAMPLE   3U

struct a52_r373_record {
	u64 magic;
	u64 entry_ns;
	u64 sample_ns;
	u64 return_ns;
	s64 ret;
	u64 mount_flags;
	u32 generation;
	u32 ordinal;
	u32 pid;
	u32 tgid;
	u32 event;
	u32 task_state;
	u32 stack_nr;
	u32 cpu;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	char source[80];
	char target[120];
	char fstype[32];
	unsigned long stack[A52_R373_STACK_ENTRIES];
	char top_symbol[64];
	u8 reserved[16];
};

static const char a52_r373_marker[] __used =
	"A52_PHASE373_APEX_MOUNT_BLOCKER_V1";
static void *a52_r373_sideband;
static u32 a52_r373_generation;
static u32 a52_r373_tgid;
static u32 a52_r373_worker_count;
static u32 a52_r373_worker_pid[A52_R373_MAX_WORKERS];
static struct a52_r373_record a52_r373_records[A52_R373_MAX_WORKERS];
static struct task_struct *a52_r373_sampler_task;

static bool a52_r373_apexd_group_leader(void)
{
	return current->pid == current->tgid &&
	       !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static void a52_r373_write_slot(unsigned int slot,
				const struct a52_r373_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r373_sideband) || !r ||
	    slot >= A52_R373_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R373_SLOT_BYTES;
	dst0 = (u8 *)a52_r373_sideband + pos;
	dst1 = (u8 *)a52_r373_sideband + A52_R373_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r373_copy_string(char *dst, size_t cap,
				 const char __user *src)
{
	long n;

	if (!dst || !cap || !src)
		return;
	n = strncpy_from_user(dst, src, cap);
	if (n < 0)
		return;
	dst[cap - 1] = '\0';
}

static void a52_r373_reset_generation(u32 tgid)
{
	WRITE_ONCE(a52_r373_tgid, tgid);
	WRITE_ONCE(a52_r373_worker_count, 0);
	WRITE_ONCE(a52_r373_generation, READ_ONCE(a52_r373_generation) + 1U);
	memset(a52_r373_worker_pid, 0, sizeof(a52_r373_worker_pid));
	memset(a52_r373_records, 0, sizeof(a52_r373_records));
	memset(a52_r373_sideband, 0, A52_R373_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_r373_sideband, A52_R373_SIDEBAND_BYTES);
}

static void a52_r373_note_clone_return(struct pt_regs *regs, int scno)
{
	u64 flags;
	s64 child;
	u32 ordinal;

	if (!READ_ONCE(a52_r373_sideband) || !a52_r373_apexd_group_leader() ||
	    scno != __NR_clone)
		return;

	flags = regs->orig_x0;
	if (!(flags & CLONE_THREAD))
		return;

	child = (s64)regs->regs[0];
	if (child <= 0 || child > INT_MAX)
		return;

	if (READ_ONCE(a52_r373_tgid) != (u32)current->tgid)
		a52_r373_reset_generation((u32)current->tgid);

	ordinal = READ_ONCE(a52_r373_worker_count);
	if (ordinal >= A52_R373_MAX_WORKERS)
		return;

	WRITE_ONCE(a52_r373_worker_pid[ordinal], (u32)child);
	WRITE_ONCE(a52_r373_worker_count, ordinal + 1U);
}

static int a52_r373_find_ordinal(void)
{
	u32 i;
	u32 count;

	if ((u32)current->tgid != READ_ONCE(a52_r373_tgid))
		return -1;

	count = min_t(u32, READ_ONCE(a52_r373_worker_count),
		      A52_R373_MAX_WORKERS);
	for (i = 0; i < count; i++)
		if (READ_ONCE(a52_r373_worker_pid[i]) == (u32)current->pid)
			return (int)i;
	return -1;
}

static u64 a52_r373_mount_enter(struct pt_regs *regs, int scno)
{
	struct a52_r373_record *r;
	int ordinal;

	if (!READ_ONCE(a52_r373_sideband) || scno != __NR_mount ||
	    current->pid == current->tgid)
		return 0;

	ordinal = a52_r373_find_ordinal();
	if (ordinal < 0)
		return 0;

	r = &a52_r373_records[ordinal];
	memset(r, 0, sizeof(*r));
	r->magic = A52_R373_MAGIC;
	r->entry_ns = ktime_get_ns();
	r->ret = (s64)(1ULL << 63);
	r->mount_flags = regs->regs[3];
	r->generation = READ_ONCE(a52_r373_generation);
	r->ordinal = (u32)ordinal;
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->event = A52_R373_EVT_MOUNT_ENTRY;
	r->cpu = (u32)raw_smp_processor_id();
	r->commit = A52_R373_COMMIT;
	r->version = A52_R373_VERSION;
	get_task_comm(r->comm, current);

	a52_r373_copy_string(r->source, sizeof(r->source),
		(const char __user *)(unsigned long)regs->orig_x0);
	a52_r373_copy_string(r->target, sizeof(r->target),
		(const char __user *)(unsigned long)regs->regs[1]);
	a52_r373_copy_string(r->fstype, sizeof(r->fstype),
		(const char __user *)(unsigned long)regs->regs[2]);

	a52_r373_write_slot((unsigned int)ordinal, r);
	return (u64)ordinal + 1ULL;
}

static void a52_r373_mount_return(struct pt_regs *regs, int scno, u64 token)
{
	struct a52_r373_record *r;
	unsigned int ordinal;

	if (!token || !READ_ONCE(a52_r373_sideband) || scno != __NR_mount)
		return;

	ordinal = (unsigned int)(token - 1ULL);
	if (ordinal >= A52_R373_MAX_WORKERS)
		return;

	r = &a52_r373_records[ordinal];
	if (r->pid != (u32)current->pid || r->tgid != (u32)current->tgid)
		return;

	r->return_ns = ktime_get_ns();
	r->ret = (s64)regs->regs[0];
	r->event = A52_R373_EVT_MOUNT_RETURN;
	r->cpu = (u32)raw_smp_processor_id();
	a52_r373_write_slot(ordinal, r);
}

static void a52_r373_sample_blocked_mounts(void)
{
	struct a52_r373_record *r;
	struct task_struct *p;
	u64 now = ktime_get_ns();
	u32 i;
	u32 count;

	count = min_t(u32, READ_ONCE(a52_r373_worker_count),
		      A52_R373_MAX_WORKERS);
	for (i = 0; i < count; i++) {
		r = &a52_r373_records[i];
		if (READ_ONCE(r->magic) != A52_R373_MAGIC ||
		    !READ_ONCE(r->entry_ns) || READ_ONCE(r->return_ns))
			continue;
		if (now - READ_ONCE(r->entry_ns) < 100000000ULL)
			continue;

		p = find_get_task_by_vpid((pid_t)READ_ONCE(r->pid));
		if (!p)
			continue;
		if ((u32)p->tgid != READ_ONCE(r->tgid)) {
			put_task_struct(p);
			continue;
		}

		r->sample_ns = now;
		r->event = A52_R373_EVT_STACK_SAMPLE;
		r->task_state = (u32)READ_ONCE(p->state);
		r->cpu = (u32)task_cpu(p);
		memset(r->stack, 0, sizeof(r->stack));
		memset(r->top_symbol, 0, sizeof(r->top_symbol));
		r->stack_nr = stack_trace_save_tsk(p, r->stack,
			A52_R373_STACK_ENTRIES, 0);
		if (r->stack_nr)
			sprint_symbol(r->top_symbol, r->stack[0]);
		put_task_struct(p);

		a52_r373_write_slot(i, r);
	}
}

static int a52_r373_sampler_fn(void *unused)
{
	while (!kthread_should_stop()) {
		a52_r373_sample_blocked_mounts();
		if (msleep_interruptible(250) && kthread_should_stop())
			break;
	}
	return 0;
}

static int __init a52_r373_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r373_record) != A52_R373_SLOT_BYTES);
	BUILD_BUG_ON(A52_R373_SLOTS_PER_COPY * A52_R373_SLOT_BYTES !=
		     A52_R373_COPY_BYTES);

	a52_r373_sideband = memremap(A52_R373_SIDEBAND_PHYS,
		A52_R373_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r373_sideband)
		return 0;

	memset(a52_r373_sideband, 0, A52_R373_SIDEBAND_BYTES);
	memset(a52_r373_worker_pid, 0, sizeof(a52_r373_worker_pid));
	memset(a52_r373_records, 0, sizeof(a52_r373_records));
	WRITE_ONCE(a52_r373_generation, 0);
	WRITE_ONCE(a52_r373_tgid, 0);
	WRITE_ONCE(a52_r373_worker_count, 0);
	wmb();
	__flush_dcache_area(a52_r373_sideband, A52_R373_SIDEBAND_BYTES);

	a52_r373_sampler_task =
		kthread_run(a52_r373_sampler_fn, NULL, "a52_r373_sampler");
	if (IS_ERR(a52_r373_sampler_task))
		a52_r373_sampler_task = NULL;
	return 0;
}
late_initcall(a52_r373_init);

'''

def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE372_FUTURE_WORKER_CORRELATION_V1" not in text:
        raise SystemExit("Phase373 requires Phase372 syscall lineage")

    for inc in ("#include <linux/stacktrace.h>\n", "#include <linux/kallsyms.h>\n"):
        if inc not in text:
            text = inc + text

    if "static int __init __used a52_r372_init(void)" not in text:
        text = one(text,
            "static int __init a52_r372_init(void)\n",
            "static int __init __used a52_r372_init(void)\n",
            "mark Phase372 init dormant")
    if "late_initcall(a52_r372_init);" in text:
        text = one(text,
            "late_initcall(a52_r372_init);\n",
            "/* Phase373 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase372 mapper")

    anchor = (
        'static const char a52_r372_marker[] __used =\n'
        '\t"A52_PHASE372_FUTURE_WORKER_CORRELATION_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase373 block insertion")

    text = one(text,
        "\tu64 a52_r372_main_sequence_token = 0;\n",
        "\tu64 a52_r372_main_sequence_token = 0;\n"
        "\tu64 a52_r373_mount_token = 0;\n",
        "mount token")

    old = (
        "\ta52_r372_worker_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r371_note_clone_return(regs, scno);\n"
        "\ta52_r372_note_clone_return(regs, scno);\n"
    )
    new = (
        "\ta52_r372_worker_enter(regs, scno);\n"
        "\ta52_r373_mount_token = a52_r373_mount_enter(regs, scno);\n"
        "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
        "\ta52_r370_note_clone_return(regs, scno);\n"
        "\ta52_r371_note_clone_return(regs, scno);\n"
        "\ta52_r372_note_clone_return(regs, scno);\n"
        "\ta52_r373_note_clone_return(regs, scno);\n"
        "\ta52_r373_mount_return(regs, scno, a52_r373_mount_token);\n"
    )
    text = one(text, old, new, "syscall wrapper")
    return text

def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R373_SLOT_BYTES         512U",
        "A52_R373_MAX_WORKERS        32U",
        "A52_R373_COMMIT             0x373c0de5U",
        "if (!(flags & CLONE_THREAD))",
        "a52_r373_reset_generation((u32)current->tgid);",
        "a52_r373_mount_enter(regs, scno);",
        "a52_r373_mount_return(regs, scno, a52_r373_mount_token);",
        "stack_trace_save_tsk(p, r->stack",
        "sprint_symbol(r->top_symbol, r->stack[0]);",
        "late_initcall(a52_r373_init);",
    ):
        if token not in text:
            raise SystemExit("Phase373 required token missing: " + token)
    if "late_initcall(a52_r372_init);" in text:
        raise SystemExit("Phase373 must disable Phase372 sideband owner")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    path = ns.root / SYSCALL
    if not path.is_file():
        raise SystemExit("Phase373 syscall source missing")
    before = path.read_text(encoding="utf-8")
    if MARK in before:
        validate(before)
        print("Phase373 apex mount blocker audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase373 marker missing in check-only mode")
    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase373 apex mount blocker recorder applied")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
