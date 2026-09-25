#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE377_APEXD_LIVE_THREAD_CENSUS_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase377 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE377_APEXD_LIVE_THREAD_CENSUS_V1
 *
 * Phase373 hardware result:
 * - the Phase372 suspect maps to local worker ordinal 6 in the later apexd
 * - its final mount is the APEX active-path bind mount for
 *   com.samsung.android.camera.unihal
 * - mount flags are 0x1000 (MS_BIND), source is the versioned /apex path,
 *   target is /apex/com.samsung.android.camera.unihal
 * - in this boot the mount has a RETURN record with ret=0 in both mirrors
 * - every surviving Phase373 mount record is a RETURN; no persistent
 *   MOUNT_ENTRY/STACK_SAMPLE remains without a return
 *
 * Therefore the previous mount stall is timing-sensitive or the blocker moved.
 * Phase377 takes direct live thread-group censuses at 19, 20, 25 and 40 seconds
 * rather than assuming the same syscall will stall again.
 *
 * Per 16 KiB mirror:
 *   4 snapshots x 16 records x 256 bytes = 16 KiB
 * The apexd group leader is always record 0 of each snapshot; up to 15 other
 * live threads follow.  Each record captures task state, wchan + symbol,
 * 6 kernel stack PCs, scheduler/runtime counters and saved userspace PC/LR/SP.
 *
 * Observation only.
 */
#define A52_R377_SIDEBAND_PHYS      0xB1BF0000ULL
#define A52_R377_SIDEBAND_BYTES     0x8000U
#define A52_R377_COPY_BYTES         0x4000U
#define A52_R377_SLOT_BYTES         256U
#define A52_R377_SLOTS_PER_COPY     64U
#define A52_R377_SNAPSHOT_COUNT     4U
#define A52_R377_THREADS_PER_SNAP   16U
#define A52_R377_STACK_ENTRIES      6U
#define A52_R377_MAGIC              0x37373358534e4543ULL
#define A52_R377_COMMIT             0x377c0de5U
#define A52_R377_VERSION            1U

struct a52_r377_record {
	u64 magic;
	u64 ns;
	u64 wchan;
	u64 user_pc;
	u64 user_lr;
	u64 user_sp;
	u64 sum_exec_runtime;
	u64 clear_child_tid;
	unsigned long stack[A52_R377_STACK_ENTRIES];
	u32 snapshot_id;
	u32 pid;
	u32 tgid;
	u32 ppid;
	u32 task_state;
	u32 exit_state;
	u32 task_flags;
	u32 on_cpu;
	u32 on_rq;
	u32 task_cpu;
	u32 nr_threads;
	u32 live_threads;
	u32 stack_nr;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	char wchan_symbol[64];
	u8 reserved[4];
};

static const char a52_r377_marker[] __used =
	"A52_PHASE377_APEXD_LIVE_THREAD_CENSUS_V1";
static void *a52_r377_sideband;
static struct task_struct *a52_r377_sampler_task;

static void a52_r377_write_slot(unsigned int slot,
				const struct a52_r377_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r377_sideband) || !r ||
	    slot >= A52_R377_SLOTS_PER_COPY)
		return;

	pos = slot * A52_R377_SLOT_BYTES;
	dst0 = (u8 *)a52_r377_sideband + pos;
	dst1 = (u8 *)a52_r377_sideband + A52_R377_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
}

static struct task_struct *a52_r377_get_latest_apexd_leader(void)
{
	struct task_struct *g;
	struct task_struct *best = NULL;
	pid_t best_pid = 0;
	char comm[TASK_COMM_LEN];

	rcu_read_lock();
	for_each_process(g) {
		if (g->pid != g->tgid)
			continue;
		get_task_comm(comm, g);
		if (strncmp(comm, "apexd", TASK_COMM_LEN))
			continue;
		if (g->pid <= best_pid)
			continue;
		best = g;
		best_pid = g->pid;
	}
	if (best)
		get_task_struct(best);
	rcu_read_unlock();
	return best;
}

static unsigned int
a52_r377_collect_thread_refs(struct task_struct *leader,
			     struct task_struct **tasks,
			     unsigned int cap)
{
	struct task_struct *t;
	unsigned int nr = 0;

	if (!leader || !tasks || !cap)
		return 0;

	tasks[nr++] = leader;
	get_task_struct(leader);

	rcu_read_lock();
	for_each_thread(leader, t) {
		if (t == leader)
			continue;
		if (nr >= cap)
			break;
		get_task_struct(t);
		tasks[nr++] = t;
	}
	rcu_read_unlock();
	return nr;
}

static void a52_r377_fill_task(struct a52_r377_record *r,
			       struct task_struct *p,
			       unsigned int snapshot_id)
{
	struct pt_regs *regs;
	unsigned int nr_threads = 0;
	unsigned int live_threads = 0;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R377_MAGIC;
	r->ns = ktime_get_ns();
	r->snapshot_id = snapshot_id;
	r->pid = (u32)p->pid;
	r->tgid = (u32)p->tgid;
	r->ppid = (u32)task_ppid_nr(p);
	r->task_state = (u32)READ_ONCE(p->state);
	r->exit_state = (u32)READ_ONCE(p->exit_state);
	r->task_flags = (u32)READ_ONCE(p->flags);
#ifdef CONFIG_SMP
	r->on_cpu = (u32)READ_ONCE(p->on_cpu);
#else
	r->on_cpu = 0;
#endif
	r->on_rq = (u32)READ_ONCE(p->on_rq);
	r->task_cpu = (u32)task_cpu(p);
	r->sum_exec_runtime = READ_ONCE(p->se.sum_exec_runtime);
	r->clear_child_tid =
		(u64)(unsigned long)READ_ONCE(p->clear_child_tid);
	if (p->signal) {
		nr_threads = (unsigned int)READ_ONCE(p->signal->nr_threads);
		live_threads = (unsigned int)atomic_read(&p->signal->live);
	}
	r->nr_threads = nr_threads;
	r->live_threads = live_threads;
	get_task_comm(r->comm, p);

	r->wchan = (u64)get_wchan(p);
	if (r->wchan)
		sprint_symbol(r->wchan_symbol, (unsigned long)r->wchan);

	if (try_get_task_stack(p)) {
		r->stack_nr = stack_trace_save_tsk(p, r->stack,
			A52_R377_STACK_ENTRIES, 0);
		if (p->mm) {
			regs = task_pt_regs(p);
			r->user_pc = READ_ONCE(regs->pc);
			r->user_lr = READ_ONCE(regs->regs[30]);
			r->user_sp = READ_ONCE(regs->sp);
		}
		put_task_stack(p);
	}

	r->commit = A52_R377_COMMIT;
	r->version = A52_R377_VERSION;
}

static void a52_r377_take_snapshot(unsigned int snapshot_id)
{
	struct task_struct *leader;
	struct task_struct *tasks[A52_R377_THREADS_PER_SNAP];
	struct a52_r377_record r;
	unsigned int i;
	unsigned int nr;
	unsigned int base;

	memset(tasks, 0, sizeof(tasks));
	leader = a52_r377_get_latest_apexd_leader();
	if (!leader)
		return;

	/*
	 * collect_thread_refs takes its own reference for slot 0, so drop the
	 * discovery reference after collection.
	 */
	nr = a52_r377_collect_thread_refs(leader, tasks,
					 A52_R377_THREADS_PER_SNAP);
	put_task_struct(leader);

	base = snapshot_id * A52_R377_THREADS_PER_SNAP;
	for (i = 0; i < nr; i++) {
		a52_r377_fill_task(&r, tasks[i], snapshot_id);
		a52_r377_write_slot(base + i, &r);
		put_task_struct(tasks[i]);
	}
}

static int a52_r377_sampler_fn(void *unused)
{
	static const u32 seconds[A52_R377_SNAPSHOT_COUNT] = { 19U, 20U, 25U, 40U };
	unsigned int next = 0;
	u64 sec;

	while (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
		sec = div_u64(ktime_get_boottime_ns(), NSEC_PER_SEC);
		if (sec >= seconds[next]) {
			a52_r377_take_snapshot(next);
			next++;
			continue;
		}
		if (msleep_interruptible(100) && kthread_should_stop())
			break;
	}
	return 0;
}

static int __init a52_r377_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r377_record) != A52_R377_SLOT_BYTES);
	BUILD_BUG_ON(A52_R377_SLOTS_PER_COPY * A52_R377_SLOT_BYTES !=
		     A52_R377_COPY_BYTES);
	BUILD_BUG_ON(A52_R377_SNAPSHOT_COUNT * A52_R377_THREADS_PER_SNAP !=
		     A52_R377_SLOTS_PER_COPY);

	a52_r377_sideband = memremap(A52_R377_SIDEBAND_PHYS,
		A52_R377_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r377_sideband)
		return 0;

	memset(a52_r377_sideband, 0, A52_R377_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_r377_sideband, A52_R377_SIDEBAND_BYTES);

	a52_r377_sampler_task =
		kthread_run(a52_r377_sampler_fn, NULL, "a52_r377_sampler");
	if (IS_ERR(a52_r377_sampler_task))
		a52_r377_sampler_task = NULL;
	return 0;
}
late_initcall(a52_r377_init);

'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE373_APEX_MOUNT_BLOCKER_V1" not in text:
        raise SystemExit("Phase377 requires Phase373 syscall lineage")

    for inc in (
        "#include <linux/sched/task_stack.h>\n",
        "#include <linux/rcupdate.h>\n",
    ):
        if inc not in text:
            text = inc + text

    if "static int __init __used a52_r373_init(void)" not in text:
        text = one(
            text,
            "static int __init a52_r373_init(void)\n",
            "static int __init __used a52_r373_init(void)\n",
            "mark Phase373 init dormant",
        )
    if "late_initcall(a52_r373_init);" in text:
        text = one(
            text,
            "late_initcall(a52_r373_init);\n",
            "/* Phase377 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase373 mapper/sampler",
        )

    anchor = (
        'static const char a52_r373_marker[] __used =\n'
        '\t"A52_PHASE373_APEX_MOUNT_BLOCKER_V1";\n'
    )
    text = one(text, anchor, BLOCK + anchor, "Phase377 block insertion")
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R377_SLOT_BYTES         256U",
        "A52_R377_SNAPSHOT_COUNT     4U",
        "A52_R377_THREADS_PER_SNAP   16U",
        "A52_R377_COMMIT             0x377c0de5U",
        "get_wchan(p)",
        "stack_trace_save_tsk(p, r->stack",
        "task_pt_regs(p)",
        "{ 19U, 20U, 25U, 40U }",
        "late_initcall(a52_r377_init);",
    ):
        if token not in text:
            raise SystemExit("Phase377 required token missing: " + token)

    if "late_initcall(a52_r373_init);" in text:
        raise SystemExit("Phase377 must disable Phase373 sideband owner/sampler")
    if "static int __init __used a52_r373_init(void)" not in text:
        raise SystemExit("Phase377 dormant Phase373 init marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYSCALL
    if not path.is_file():
        raise SystemExit("Phase377 syscall source missing")

    before = path.read_text(encoding="utf-8")
    if MARK in before:
        validate(before)
        print("Phase377 apexd live-thread census audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase377 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase377 apexd live-thread census applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
