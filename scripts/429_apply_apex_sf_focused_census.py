#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE429_APEX_SF_FOCUSED_CENSUS_V1"
SYSCALL = Path("arch/arm64/kernel/syscall.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase429 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE429_APEX_SF_FOCUSED_CENSUS_V1
 *
 * Phase428 separated two pre-DSI delays from the later DMA_DONE failure:
 *   A) long-lived apexd / early userspace stall;
 *   B) ~55-60 s gap after SurfaceFlinger/display HAL exist but before the
 *      first real DRM atomic transaction.
 *
 * This phase deliberately does NOT add DSI/TCS probing.  It reuses the old
 * Phase377 0xB1BF0000..0xB1BF7fff sideband and takes only eight sparse
 * snapshots:
 *   30, 60, 90, 120 s: apexd (leader + up to five threads, D-state first)
 *   150, 170, 190, 200 s: SurfaceFlinger, composer, system_server
 *
 * Each 320-byte record contains task state, wchan + symbol, 12 kernel PCs,
 * saved userspace PC/LR/SP, scheduler placement and context-switch counters.
 * Two identical 0x3c00-byte copies are kept inside the 32 KiB sideband.
 */
#define A52_P429_PHYS              0xB1BF0000ULL
#define A52_P429_BYTES             0x8000U
#define A52_P429_COPY_STRIDE       0x4000U
#define A52_P429_SLOT_BYTES        320U
#define A52_P429_SNAPSHOTS         8U
#define A52_P429_PER_SNAPSHOT      6U
#define A52_P429_SLOTS             48U
#define A52_P429_STACK             12U
#define A52_P429_MAGIC             0x34323943454e5355ULL
#define A52_P429_COMMIT            0x429c0de5U
#define A52_P429_VERSION           1U

#define A52_P429_ROLE_APEXD        1U
#define A52_P429_ROLE_SF           2U
#define A52_P429_ROLE_COMPOSER     3U
#define A52_P429_ROLE_SYSTEM       4U

struct a52_p429_record {
	u64 magic;
	u64 ns;
	u64 wchan;
	u64 user_pc;
	u64 user_lr;
	u64 user_sp;
	u64 sum_exec_runtime;
	unsigned long stack[A52_P429_STACK];
	u32 snapshot_id;
	u32 role;
	u32 pid;
	u32 tgid;
	u32 ppid;
	u32 task_state;
	u32 exit_state;
	u32 task_flags;
	u32 on_cpu;
	u32 on_rq;
	u32 task_cpu;
	u32 stack_nr;
	u32 nr_threads;
	u32 live_threads;
	u32 nvcsw;
	u32 nivcsw;
	u32 slot;
	u32 commit;
	u32 version;
	char comm[TASK_COMM_LEN];
	char wchan_symbol[48];
	u8 reserved[28];
};

static void *a52_p429_sideband;
static struct task_struct *a52_p429_sampler;
extern void a52_p429_ufs_compact(unsigned int snapshot_id);

static bool a52_p429_comm_is(struct task_struct *p, const char *name)
{
	char comm[TASK_COMM_LEN];

	if (!p || !name)
		return false;
	get_task_comm(comm, p);
	return !strncmp(comm, name, TASK_COMM_LEN);
}

static bool a52_p429_comm_prefix(struct task_struct *p, const char *prefix,
				 unsigned int n)
{
	char comm[TASK_COMM_LEN];

	if (!p || !prefix)
		return false;
	get_task_comm(comm, p);
	return !strncmp(comm, prefix, n);
}

static struct task_struct *a52_p429_find_leader(unsigned int role)
{
	struct task_struct *g;
	struct task_struct *best = NULL;
	pid_t best_pid = 0;
	bool match;

	rcu_read_lock();
	for_each_process(g) {
		if (g->pid != g->tgid)
			continue;
		match = false;
		switch (role) {
		case A52_P429_ROLE_APEXD:
			match = a52_p429_comm_is(g, "apexd");
			break;
		case A52_P429_ROLE_SF:
			match = a52_p429_comm_is(g, "surfaceflinger");
			break;
		case A52_P429_ROLE_COMPOSER:
			match = a52_p429_comm_prefix(g, "composer", 8U);
			break;
		case A52_P429_ROLE_SYSTEM:
			match = a52_p429_comm_is(g, "system_server");
			break;
		default:
			break;
		}
		if (!match || g->pid <= best_pid)
			continue;
		best = g;
		best_pid = g->pid;
	}
	if (best)
		get_task_struct(best);
	rcu_read_unlock();
	return best;
}

static bool a52_p429_has_task(struct task_struct **tasks, unsigned int nr,
			      struct task_struct *p)
{
	unsigned int i;

	for (i = 0; i < nr; i++)
		if (tasks[i] == p)
			return true;
	return false;
}

static bool a52_p429_add(struct task_struct **tasks, u32 *roles,
			 unsigned int *nr, unsigned int cap,
			 struct task_struct *p, u32 role)
{
	if (!p || !nr || *nr >= cap || a52_p429_has_task(tasks, *nr, p))
		return false;
	get_task_struct(p);
	tasks[*nr] = p;
	roles[*nr] = role;
	(*nr)++;
	return true;
}

static bool a52_p429_preferred(struct task_struct *p, unsigned int role)
{
	char comm[TASK_COMM_LEN];

	if (!p)
		return false;
	get_task_comm(comm, p);
	if (role == A52_P429_ROLE_SYSTEM)
		return !strncmp(comm, "android.display", TASK_COMM_LEN);
	if (role == A52_P429_ROLE_SF)
		return strnstr(comm, "EventThread", TASK_COMM_LEN) ||
		       strnstr(comm, "RenderEngine", TASK_COMM_LEN) ||
		       strnstr(comm, "Binder", TASK_COMM_LEN) ||
		       strnstr(comm, "binder", TASK_COMM_LEN);
	if (role == A52_P429_ROLE_COMPOSER)
		return strnstr(comm, "Binder", TASK_COMM_LEN) ||
		       strnstr(comm, "binder", TASK_COMM_LEN);
	return false;
}

static void a52_p429_add_threads(struct task_struct *leader, u32 role,
				 struct task_struct **tasks, u32 *roles,
				 unsigned int *nr, unsigned int cap,
				 unsigned int want)
{
	struct task_struct *t;
	unsigned int before;

	if (!leader || !want || *nr >= cap)
		return;
	before = *nr;

	rcu_read_lock();
	for_each_thread(leader, t) {
		if (*nr - before >= want || *nr >= cap)
			break;
		if (!(READ_ONCE(t->state) & TASK_UNINTERRUPTIBLE))
			continue;
		a52_p429_add(tasks, roles, nr, cap, t, role);
	}
	rcu_read_unlock();

	rcu_read_lock();
	for_each_thread(leader, t) {
		if (*nr - before >= want || *nr >= cap)
			break;
		if (!a52_p429_preferred(t, role))
			continue;
		a52_p429_add(tasks, roles, nr, cap, t, role);
	}
	rcu_read_unlock();

	rcu_read_lock();
	for_each_thread(leader, t) {
		if (*nr - before >= want || *nr >= cap)
			break;
		a52_p429_add(tasks, roles, nr, cap, t, role);
	}
	rcu_read_unlock();
}

static void a52_p429_fill(struct a52_p429_record *r, struct task_struct *p,
			  unsigned int snapshot_id, unsigned int role,
			  unsigned int slot)
{
	struct pt_regs *regs;

	memset(r, 0, sizeof(*r));
	r->magic = A52_P429_MAGIC;
	r->ns = ktime_get_boottime_ns();
	r->snapshot_id = snapshot_id;
	r->role = role;
	r->slot = slot;
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
	r->nvcsw = (u32)READ_ONCE(p->nvcsw);
	r->nivcsw = (u32)READ_ONCE(p->nivcsw);
	if (p->signal) {
		r->nr_threads = (u32)READ_ONCE(p->signal->nr_threads);
		r->live_threads = (u32)atomic_read(&p->signal->live);
	}
	get_task_comm(r->comm, p);
	r->wchan = (u64)get_wchan(p);
	if (r->wchan)
		sprint_symbol(r->wchan_symbol, (unsigned long)r->wchan);

	if (try_get_task_stack(p)) {
		r->stack_nr = stack_trace_save_tsk(p, r->stack,
			A52_P429_STACK, 0);
		if (p->mm) {
			regs = task_pt_regs(p);
			r->user_pc = READ_ONCE(regs->pc);
			r->user_lr = READ_ONCE(regs->regs[30]);
			r->user_sp = READ_ONCE(regs->sp);
		}
		put_task_stack(p);
	}
	r->commit = A52_P429_COMMIT;
	r->version = A52_P429_VERSION;
}

static void a52_p429_write(unsigned int slot,
			   const struct a52_p429_record *r)
{
	unsigned int off;
	void *d0;
	void *d1;

	if (!a52_p429_sideband || !r || slot >= A52_P429_SLOTS)
		return;
	off = slot * A52_P429_SLOT_BYTES;
	d0 = (u8 *)a52_p429_sideband + off;
	d1 = (u8 *)a52_p429_sideband + A52_P429_COPY_STRIDE + off;
	memcpy(d0, r, sizeof(*r));
	memcpy(d1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(d0, sizeof(*r));
	__flush_dcache_area(d1, sizeof(*r));
}

static unsigned int a52_p429_apex_tasks(struct task_struct **tasks, u32 *roles,
					unsigned int cap, int *leader_pid)
{
	struct task_struct *leader;
	unsigned int nr = 0;

	leader = a52_p429_find_leader(A52_P429_ROLE_APEXD);
	if (!leader)
		return 0;
	if (leader_pid)
		*leader_pid = leader->pid;
	a52_p429_add(tasks, roles, &nr, cap, leader, A52_P429_ROLE_APEXD);
	a52_p429_add_threads(leader, A52_P429_ROLE_APEXD,
		tasks, roles, &nr, cap, cap - nr);
	put_task_struct(leader);
	return nr;
}

static unsigned int a52_p429_display_tasks(struct task_struct **tasks, u32 *roles,
					   unsigned int cap, int *sf_pid,
					   int *cp_pid, int *ss_pid)
{
	struct task_struct *sf;
	struct task_struct *cp;
	struct task_struct *ss;
	unsigned int nr = 0;

	sf = a52_p429_find_leader(A52_P429_ROLE_SF);
	cp = a52_p429_find_leader(A52_P429_ROLE_COMPOSER);
	ss = a52_p429_find_leader(A52_P429_ROLE_SYSTEM);
	if (sf) {
		if (sf_pid) *sf_pid = sf->pid;
		a52_p429_add(tasks, roles, &nr, cap, sf, A52_P429_ROLE_SF);
		a52_p429_add_threads(sf, A52_P429_ROLE_SF,
			tasks, roles, &nr, cap, 1U);
	}
	if (cp) {
		if (cp_pid) *cp_pid = cp->pid;
		a52_p429_add(tasks, roles, &nr, cap, cp, A52_P429_ROLE_COMPOSER);
		a52_p429_add_threads(cp, A52_P429_ROLE_COMPOSER,
			tasks, roles, &nr, cap, 1U);
	}
	if (ss) {
		if (ss_pid) *ss_pid = ss->pid;
		a52_p429_add(tasks, roles, &nr, cap, ss, A52_P429_ROLE_SYSTEM);
		a52_p429_add_threads(ss, A52_P429_ROLE_SYSTEM,
			tasks, roles, &nr, cap, 1U);
	}
	if (sf) put_task_struct(sf);
	if (cp) put_task_struct(cp);
	if (ss) put_task_struct(ss);
	return nr;
}

static void a52_p429_snapshot(unsigned int snapshot_id)
{
	struct task_struct *tasks[A52_P429_PER_SNAPSHOT];
	u32 roles[A52_P429_PER_SNAPSHOT];
	struct a52_p429_record r;
	unsigned int nr;
	unsigned int i;
	unsigned int base = snapshot_id * A52_P429_PER_SNAPSHOT;
	int ap = -1, sf = -1, cp = -1, ss = -1;

	memset(tasks, 0, sizeof(tasks));
	memset(roles, 0, sizeof(roles));
	if (snapshot_id < 4U) {
		nr = a52_p429_apex_tasks(tasks, roles,
			A52_P429_PER_SNAPSHOT, &ap);
		a52_p429_ufs_compact(snapshot_id);
		a52_ackfr_record("P429 S id=%u mode=A nr=%u ap=%d",
			snapshot_id, nr, ap);
	} else {
		nr = a52_p429_display_tasks(tasks, roles,
			A52_P429_PER_SNAPSHOT, &sf, &cp, &ss);
		a52_ackfr_record("P429 S id=%u mode=D nr=%u sf=%d cp=%d ss=%d",
			snapshot_id, nr, sf, cp, ss);
	}

	for (i = 0; i < nr; i++) {
		a52_p429_fill(&r, tasks[i], snapshot_id, roles[i], i);
		a52_p429_write(base + i, &r);
		put_task_struct(tasks[i]);
	}
}

static int a52_p429_sampler_fn(void *unused)
{
	static const u32 sec[A52_P429_SNAPSHOTS] = {
		30U, 60U, 90U, 120U, 150U, 170U, 190U, 200U,
	};
	unsigned int next = 0;
	u64 now;

	while (!kthread_should_stop() && next < A52_P429_SNAPSHOTS) {
		now = div_u64(ktime_get_boottime_ns(), NSEC_PER_SEC);
		if (now >= sec[next]) {
			a52_p429_snapshot(next);
			next++;
			continue;
		}
		if (msleep_interruptible(100) && kthread_should_stop())
			break;
	}
	return 0;
}

static int __init a52_p429_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_p429_record) != A52_P429_SLOT_BYTES);
	BUILD_BUG_ON(A52_P429_SNAPSHOTS * A52_P429_PER_SNAPSHOT != A52_P429_SLOTS);
	BUILD_BUG_ON(A52_P429_SLOTS * A52_P429_SLOT_BYTES > 0x3c00U);

	a52_p429_sideband = memremap(A52_P429_PHYS, A52_P429_BYTES, MEMREMAP_WB);
	if (!a52_p429_sideband)
		return 0;
	memset(a52_p429_sideband, 0, A52_P429_BYTES);
	wmb();
	__flush_dcache_area(a52_p429_sideband, A52_P429_BYTES);
	a52_p429_sampler = kthread_run(a52_p429_sampler_fn, NULL,
		"a52_p429_focus");
	if (IS_ERR(a52_p429_sampler))
		a52_p429_sampler = NULL;
	return 0;
}
late_initcall(a52_p429_init);
'''


UFS_BLOCK = r'''
/* A52_PHASE429_APEX_SF_FOCUSED_CENSUS_V1
 * One compact UFS health line at each apexd snapshot.  No per-tag text dump.
 */
void a52_p429_ufs_compact(unsigned int snapshot_id)
{
	struct ufs_hba *hba = READ_ONCE(a52_r378_hba);
	unsigned long out = 0;
	u32 db = 0;
	u32 state = ~0U, link = ~0U;
	s64 oldest = -1;
	u64 now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	unsigned int i;

	if (!hba) {
		a52_ackfr_record("P429 U id=%u h=0", snapshot_id);
		return;
	}
	if (pm_runtime_active(hba->dev))
		db = ufshcd_readl(hba, REG_UTP_TRANSFER_REQ_DOOR_BELL);
	out = READ_ONCE(hba->outstanding_reqs);
	state = READ_ONCE(hba->ufshcd_state);
	link = READ_ONCE(hba->uic_link_state);
	for (i = 0; i < min_t(unsigned int, hba->nutrs, A52_R378_MAX_TAGS); i++) {
		struct ufshcd_lrb *lrbp = &hba->lrb[i];
		struct scsi_cmnd *cmd = READ_ONCE(lrbp->cmd);
		s64 issue_ms;
		s64 age;

		if (!cmd && !(db & BIT(i)) && !test_bit(i, &out))
			continue;
		issue_ms = ktime_to_ms(READ_ONCE(lrbp->issue_time_stamp));
		age = issue_ms > 0 ? (s64)now_ms - issue_ms : -1;
		if (age > oldest)
			oldest = age;
	}
	a52_ackfr_record(
		"P429 U id=%u h=1 rpm=%u db=%x out=%lx old=%lld st=%u lk=%u d=%lld/%lld/%lld/%lld",
		snapshot_id, pm_runtime_active(hba->dev), db, out,
		(long long)oldest, state, link,
		(long long)atomic64_read(&a52_r378_trc_count),
		(long long)atomic64_read(&a52_r378_seen_count),
		(long long)atomic64_read(&a52_r378_hook_done_count),
		(long long)atomic64_read(&a52_r378_scsi_done_count));
}
EXPORT_SYMBOL_GPL(a52_p429_ufs_compact);
'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    for tok in (
        "A52_PHASE377_APEXD_LIVE_THREAD_CENSUS_V1",
        "static int __init __used a52_r377_init(void)",
        "/* Phase380 replaces the Phase377 apexd census at runtime. */",
        "late_initcall(a52_r380_vdc_init);",
    ):
        if tok not in text:
            raise SystemExit("Phase429 syscall prerequisite missing: " + tok)

    if "static int __init __used a52_r380_vdc_init(void)" not in text:
        text = one(text, "static int __init a52_r380_vdc_init(void)\n",
                   "static int __init __used a52_r380_vdc_init(void)\n",
                   "dormant Phase380 VDC init")
    text = one(text, "late_initcall(a52_r380_vdc_init);\n",
               "/* Phase429 retires the unrelated Phase380 VDC sampler. */\n",
               "retire Phase380 VDC sampler")
    anchor = "/* Phase380 replaces the Phase377 apexd census at runtime. */\n"
    text = one(text, anchor, anchor + "\n" + BLOCK + "\n",
               "focused census insertion")
    return text


def patch_ufs(text: str) -> str:
    if MARK in text:
        return text
    for tok in (
        "A52_PHASE378_UFS_COMPLETION_FLIGHT_RECORDER_V1",
        "static struct ufs_hba *a52_r378_hba;",
        "static unsigned long a52_r378_last_completed;",
    ):
        if tok not in text:
            raise SystemExit("Phase429 UFS prerequisite missing: " + tok)
    anchor = "static unsigned long a52_r378_last_completed;\n"
    text = one(text, anchor, anchor + "\n" + UFS_BLOCK + "\n",
               "compact UFS helper")
    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE427_CB2_HANDOFF_ROAD_PROBE_V1" not in text:
        raise SystemExit("Phase429 recorder requires Phase427 lineage")

    retained = '''\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "P427", 4) &&
'''
    text = one(text, retained,
               '''\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "P429", 4) &&
\t    strncmp(fmt, "P427", 4) &&
''', "retained P429 admission")

    normal = '''if (strncmp(fmt, "P427", 4) &&
'''
    text = one(text, normal,
               '''if (strncmp(fmt, "P429", 4) &&
    strncmp(fmt, "P427", 4) &&
''', "normal P429 admission")

    clean = '''\tif (!fmt || (
\t    strncmp(fmt, "P427", 4) &&
'''
    text = one(text, clean,
               '''\tif (!fmt || (
\t    strncmp(fmt, "P429", 4) &&
\t    strncmp(fmt, "P427", 4) &&
''', "clean P429 admission")

    critical = 'return !strncmp(message, "P427 ", 5) ||'
    text = one(text, critical,
               'return !strncmp(message, "P429 ", 5) ||\n\t       !strncmp(message, "P427 ", 5) ||',
               "critical P429 admission")

    if "a52_p418_apex_sample(boot_s);" in text:
        text = one(text, "\ta52_p418_apex_sample(boot_s);\n",
                   "\t/* Phase429: periodic P418 APX text sampler retired; binary census owns this. */\n",
                   "retire P418 APEX text sampler")
    text += "\n/* " + MARK + ": focused APEX/SF census admitted; DSI instrumentation unchanged. */\n"
    return text


def validate(root: Path) -> None:
    syscall = (root / SYSCALL).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")
    ufs = (root / UFS).read_text(errors="replace")

    for tok in (
        MARK,
        "A52_P429_SLOT_BYTES        320U",
        "A52_P429_SNAPSHOTS         8U",
        "A52_P429_PER_SNAPSHOT      6U",
        "A52_P429_STACK             12U",
        "30U, 60U, 90U, 120U, 150U, 170U, 190U, 200U",
        'a52_p429_comm_is(g, "surfaceflinger")',
        'a52_p429_comm_prefix(g, "composer", 8U)',
        'a52_p429_comm_is(g, "system_server")',
        'return !strncmp(comm, "android.display", TASK_COMM_LEN);',
        "stack_trace_save_tsk(p, r->stack",
        "sprint_symbol(r->wchan_symbol",
        "late_initcall(a52_p429_init);",
    ):
        if tok not in syscall:
            raise SystemExit("Phase429 syscall validation missing: " + tok)
    if "late_initcall(a52_r377_init);" in syscall:
        raise SystemExit("Phase429 old Phase377 sampler still active")
    if "late_initcall(a52_r380_vdc_init);" in syscall:
        raise SystemExit("Phase429 unrelated Phase380 VDC sampler still active")

    for tok in (
        MARK,
        "void a52_p429_ufs_compact(unsigned int snapshot_id)",
        '"P429 U id=%u h=1 rpm=%u db=%x out=%lx old=%lld',
        "A52_R378_MAX_TAGS",
    ):
        if tok not in ufs:
            raise SystemExit("Phase429 UFS validation missing: " + tok)

    for tok in (
        MARK,
        'strncmp(fmt, "P429", 4)',
        '!strncmp(message, "P429 ", 5)',
        "Phase429: periodic P418 APX text sampler retired",
    ):
        if tok not in rec:
            raise SystemExit("Phase429 recorder validation missing: " + tok)
    if "\ta52_p418_apex_sample(boot_s);\n" in rec:
        raise SystemExit("Phase429 P418 APEX periodic call remains")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    for rel in (SYSCALL, REC, UFS):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase429 source missing: " + str(rel))
    if not ns.check_only:
        for rel, fn in ((SYSCALL, patch_syscall), (UFS, patch_ufs), (REC, patch_rec)):
            p = ns.root / rel
            p.write_text(fn(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase429 APEX + SurfaceFlinger focused census: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
