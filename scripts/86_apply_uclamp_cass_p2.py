#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 86_apply_uclamp_cass_p2.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cass = root / "kernel/sched/cass.c"
core = root / "kernel/sched/core.c"
sysctl_c = root / "kernel/sysctl.c"
sysctl_h = root / "include/linux/sched/sysctl.h"

for p in (cass, core, sysctl_c, sysctl_h):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

ca = cass.read_text()
cc = core.read_text()
sc = sysctl_c.read_text()
sh = sysctl_h.read_text()

def replace_once(text, old, new, name):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{name}: expected exactly 1 anchor, found {n}")
    return text.replace(old, new, 1)

# ---------------------------------------------------------------------------
# CASS + modern uclamp:
# CASS used raw task_util_est(), so a task's modern UCLAMP_MIN/UCLAMP_MAX
# request did not influence CPU placement.  P1's uclamp_task() already
# composes WALT task demand + Samsung schedtune compatibility + explicit
# Android17 task clamps.  Make that the task demand CASS places.
# ---------------------------------------------------------------------------
old_cass = """    unsigned long p_util = task_util_est(p);
    bool has_idle = false;
"""
new_cass = """    /*
     * P2: CASS placement consumes the same task demand policy that feeds
     * modern uclamp-aware scheduling.  uclamp_task() preserves Samsung
     * schedtune/WALT compatibility and then applies task UCLAMP_MIN/MAX.
     */
    unsigned long p_util = uclamp_task(p);
    bool has_idle = false;
"""
ca = replace_once(ca, old_cass, new_cass, "cass_task_util")

marker_old = """    rcu_read_lock();

    for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask) {
"""
marker_new = r"""    pr_info_once("CASS uclamp-aware task demand path is active\n");
    rcu_read_lock();

    for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask) {
"""
ca = replace_once(ca, marker_old, marker_new, "cass_runtime_marker")
cass.write_text(ca)

# ---------------------------------------------------------------------------
# Android17 RT default clamp live-update support.
# The P1 backport introduced sysctl_sched_uclamp_util_min_rt_default and uses
# it for new/class-changing RT tasks.  P2 exposes the runtime sysctl and
# synchronizes existing RT tasks when it changes, matching modern semantics.
# ---------------------------------------------------------------------------
insert_anchor = """#ifdef CONFIG_UCLAMP_TASK_GROUP
static inline void
uclamp_update_active_tasks(struct cgroup_subsys_state *css,
"""
if insert_anchor not in cc:
    raise SystemExit("uclamp active-task anchor missing")

rt_helpers = r'''static void uclamp_update_util_min_rt_default(struct task_struct *p)
{
	struct uclamp_se *uc_se;
	struct rq_flags rf;
	struct rq *rq;

	if (!rt_task(p))
		return;

	rq = task_rq_lock(p, &rf);
	uc_se = &p->uclamp_req[UCLAMP_MIN];

	if (!uc_se->user_defined) {
		uclamp_se_set(uc_se,
			      sysctl_sched_uclamp_util_min_rt_default, false);

		/*
		 * If this task currently contributes to the rq aggregation,
		 * re-account its effective minimum immediately.
		 */
		if (p->uclamp[UCLAMP_MIN].active) {
			uclamp_rq_dec_id(rq, p, UCLAMP_MIN);
			uclamp_rq_inc_id(rq, p, UCLAMP_MIN);
		}
	}

	task_rq_unlock(rq, p, &rf);
}

static void uclamp_sync_util_min_rt_default(void)
{
	struct task_struct *g, *p;

	/*
	 * Mirror modern uclamp's fork-vs-sysctl ordering guarantee, then walk
	 * the process list under RCU and refresh existing RT tasks.
	 */
	read_lock(&tasklist_lock);
	smp_mb__after_spinlock();
	read_unlock(&tasklist_lock);

	rcu_read_lock();
	for_each_process_thread(g, p)
		uclamp_update_util_min_rt_default(p);
	rcu_read_unlock();
}

'''
cc = cc.replace(insert_anchor, rt_helpers + insert_anchor, 1)

old_handler_head = """int sysctl_sched_uclamp_handler(struct ctl_table *table, int write,
				void __user *buffer, size_t *lenp,
				loff_t *ppos)
{
	bool update_root_tg = false;
	int old_min, old_max;
	int result;

	mutex_lock(&uclamp_mutex);
	old_min = sysctl_sched_uclamp_util_min;
	old_max = sysctl_sched_uclamp_util_max;
"""
new_handler_head = """int sysctl_sched_uclamp_handler(struct ctl_table *table, int write,
				void __user *buffer, size_t *lenp,
				loff_t *ppos)
{
	bool update_root_tg = false;
	int old_min, old_max, old_min_rt;
	int result;

	mutex_lock(&uclamp_mutex);
	old_min = sysctl_sched_uclamp_util_min;
	old_max = sysctl_sched_uclamp_util_max;
	old_min_rt = sysctl_sched_uclamp_util_min_rt_default;
"""
cc = replace_once(cc, old_handler_head, new_handler_head, "uclamp_handler_head")

old_validate = """	if (sysctl_sched_uclamp_util_min > sysctl_sched_uclamp_util_max ||
	    sysctl_sched_uclamp_util_max > SCHED_CAPACITY_SCALE) {
		result = -EINVAL;
		goto undo;
	}
"""
new_validate = """	if (sysctl_sched_uclamp_util_min > sysctl_sched_uclamp_util_max ||
	    sysctl_sched_uclamp_util_max > SCHED_CAPACITY_SCALE ||
	    sysctl_sched_uclamp_util_min_rt_default > SCHED_CAPACITY_SCALE) {
		result = -EINVAL;
		goto undo;
	}
"""
cc = replace_once(cc, old_validate, new_validate, "uclamp_handler_validation")

old_after_root = """	if (update_root_tg) {
		sched_uclamp_enable();
		uclamp_update_root_tg();
	}

	/*
	 * We update all RUNNABLE tasks only when task groups are in use.
"""
new_after_root = """	if (update_root_tg) {
		sched_uclamp_enable();
		uclamp_update_root_tg();
	}

	if (old_min_rt != sysctl_sched_uclamp_util_min_rt_default) {
		sched_uclamp_enable();
		uclamp_sync_util_min_rt_default();
	}

	/*
	 * We update all RUNNABLE tasks only when task groups are in use.
"""
cc = replace_once(cc, old_after_root, new_after_root, "uclamp_rt_sync")

old_undo = """undo:
	sysctl_sched_uclamp_util_min = old_min;
	sysctl_sched_uclamp_util_max = old_max;
done:
"""
new_undo = """undo:
	sysctl_sched_uclamp_util_min = old_min;
	sysctl_sched_uclamp_util_max = old_max;
	sysctl_sched_uclamp_util_min_rt_default = old_min_rt;
done:
"""
cc = replace_once(cc, old_undo, new_undo, "uclamp_handler_undo")
core.write_text(cc)

# ---------------------------------------------------------------------------
# Expose /proc/sys/kernel/sched_util_clamp_min_rt_default.
# ---------------------------------------------------------------------------
old_extern = """extern unsigned int sysctl_sched_uclamp_util_min;
extern unsigned int sysctl_sched_uclamp_util_max;
"""
new_extern = """extern unsigned int sysctl_sched_uclamp_util_min;
extern unsigned int sysctl_sched_uclamp_util_max;
extern unsigned int sysctl_sched_uclamp_util_min_rt_default;
"""
sh = replace_once(sh, old_extern, new_extern, "sysctl_h_rt_extern")
sysctl_h.write_text(sh)

old_sysctl_entry = """	{
		.procname	= "sched_util_clamp_max",
		.data		= &sysctl_sched_uclamp_util_max,
		.maxlen		= sizeof(unsigned int),
		.mode		= 0644,
		.proc_handler	= sysctl_sched_uclamp_handler,
	},
#endif
"""
new_sysctl_entry = """	{
		.procname	= "sched_util_clamp_max",
		.data		= &sysctl_sched_uclamp_util_max,
		.maxlen		= sizeof(unsigned int),
		.mode		= 0644,
		.proc_handler	= sysctl_sched_uclamp_handler,
	},
	{
		.procname	= "sched_util_clamp_min_rt_default",
		.data		= &sysctl_sched_uclamp_util_min_rt_default,
		.maxlen		= sizeof(unsigned int),
		.mode		= 0644,
		.proc_handler	= sysctl_sched_uclamp_handler,
	},
#endif
"""
sc = replace_once(sc, old_sysctl_entry, new_sysctl_entry, "rt_sysctl_entry")
sysctl_c.write_text(sc)

checks = {
    cass: [
        "unsigned long p_util = uclamp_task(p);",
        "CASS uclamp-aware task demand path is active",
    ],
    core: [
        "static void uclamp_update_util_min_rt_default(struct task_struct *p)",
        "static void uclamp_sync_util_min_rt_default(void)",
        "old_min_rt = sysctl_sched_uclamp_util_min_rt_default;",
        "uclamp_sync_util_min_rt_default();",
    ],
    sysctl_h: [
        "extern unsigned int sysctl_sched_uclamp_util_min_rt_default;",
    ],
    sysctl_c: [
        '"sched_util_clamp_min_rt_default"',
        "&sysctl_sched_uclamp_util_min_rt_default",
    ],
}
for path, needles in checks.items():
    text = path.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing expected P2 element: {needle!r}")

if "unsigned long p_util = task_util_est(p);" in cass.read_text():
    raise SystemExit("CASS still uses raw task_util_est() for waking task")

print("Android17 uclamp P2 applied")
print("cass_task_demand=uclamp_task")
print("cass_walt_cpu_demand=unchanged")
print("rt_default_sysctl=enabled")
print("rt_default_live_sync=enabled")
print("schedtune=retained")
print("walt=retained")
print("eevdf=unchanged")
