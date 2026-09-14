#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 85_apply_uclamp_walt.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
core = root / "kernel/sched/core.c"
sugov = root / "kernel/sched/cpufreq_schedutil.c"
defconfig = root / "arch/arm64/configs/a52xq_defconfig"

for p in (core, sugov, defconfig):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

cc = core.read_text()
sg = sugov.read_text()
dc = defconfig.read_text()

# ---------------------------------------------------------------------------
# Enable the task-level uclamp machinery that Samsung already backported.
# Keep UCLAMP_TASK_GROUP disabled for U1: the ROM still depends on /dev/stune
# and we want task-level clamp + schedutil integration before cgroup migration.
# ---------------------------------------------------------------------------
if "# CONFIG_UCLAMP_TASK is not set" in dc:
    dc = dc.replace("# CONFIG_UCLAMP_TASK is not set", "CONFIG_UCLAMP_TASK=y", 1)
elif "CONFIG_UCLAMP_TASK=y" not in dc:
    raise SystemExit("UCLAMP_TASK defconfig anchor missing")

if "CONFIG_UCLAMP_TASK_GROUP=y" in dc:
    raise SystemExit("U1 refuses to enable UCLAMP_TASK_GROUP")

defconfig.write_text(dc)

# ---------------------------------------------------------------------------
# Samsung compatibility:
# CONFIG_UCLAMP_TASK=n used legacy wrappers that mapped the scheduler's
# uclamp_* helpers onto schedtune. Simply enabling uclamp would otherwise
# remove schedtune boost/prefer-idle behavior from those call sites.
#
# Preserve Samsung's boost signal first, then apply the explicit task clamp.
# This lets old /dev/stune policy and modern sched_setattr uclamp coexist.
# ---------------------------------------------------------------------------
old_task = r'''#ifdef CONFIG_SMP
unsigned int uclamp_task(struct task_struct *p)
{
	unsigned long util;

	util = task_util_est(p);
	util = max(util, uclamp_eff_value(p, UCLAMP_MIN));
	util = min(util, uclamp_eff_value(p, UCLAMP_MAX));

	return util;
}

bool uclamp_boosted(struct task_struct *p)
{
	return uclamp_eff_value(p, UCLAMP_MIN) > 0;
}

bool uclamp_latency_sensitive(struct task_struct *p)
{
#ifdef CONFIG_UCLAMP_TASK_GROUP
	struct cgroup_subsys_state *css = task_css(p, cpu_cgrp_id);
	struct task_group *tg;

	if (!css)
		return false;
	tg = container_of(css, struct task_group, css);

	return tg->latency_sensitive;
#else
	return false;
#endif
}
#endif /* CONFIG_SMP */'''

new_task = r'''#ifdef CONFIG_SMP
#ifdef CONFIG_SCHED_TUNE
long schedtune_task_margin(struct task_struct *task);
int schedtune_task_boost(struct task_struct *p);
int schedtune_prefer_idle(struct task_struct *p);
#endif

unsigned int uclamp_task(struct task_struct *p)
{
	unsigned long util = task_util_est(p);

#ifdef CONFIG_SCHED_TUNE
	/*
	 * U1 hybrid rule:
	 * preserve Samsung's legacy schedtune boost as the base utilization,
	 * then apply modern task uclamp MIN/MAX constraints.
	 */
	{
		long margin = schedtune_task_margin(p);

		util += margin;
#ifdef CONFIG_SEC_PERF_MANAGER
		if (p->drawing_flag)
			util = max(util, get_max_fps_util(p->drawing_flag));
#endif
		trace_sched_boost_task(p, task_util_est(p), margin);
	}
#endif

	util = max(util, uclamp_eff_value(p, UCLAMP_MIN));
	util = min(util, uclamp_eff_value(p, UCLAMP_MAX));

	return util;
}

bool uclamp_boosted(struct task_struct *p)
{
	if (uclamp_eff_value(p, UCLAMP_MIN) > 0)
		return true;
#ifdef CONFIG_SCHED_TUNE
	return schedtune_task_boost(p) > 0;
#else
	return false;
#endif
}

bool uclamp_latency_sensitive(struct task_struct *p)
{
#ifdef CONFIG_UCLAMP_TASK_GROUP
	struct cgroup_subsys_state *css = task_css(p, cpu_cgrp_id);
	struct task_group *tg;

	if (css) {
		tg = container_of(css, struct task_group, css);
		if (tg->latency_sensitive)
			return true;
	}
#endif
#ifdef CONFIG_SCHED_TUNE
	return schedtune_prefer_idle(p) != 0;
#else
	return false;
#endif
}
#endif /* CONFIG_SMP */'''

if old_task in cc:
    cc = cc.replace(old_task, new_task, 1)
elif "U1 hybrid rule:" not in cc:
    raise SystemExit("uclamp task compatibility block anchor mismatch")

core.write_text(cc)

# ---------------------------------------------------------------------------
# WALT schedutil currently returns stune_util() directly, bypassing
# uclamp_rq_util_with(). Feed the WALT/schedtune utilization through the
# runqueue clamp so MIN/MAX constraints affect real frequency selection.
# ---------------------------------------------------------------------------
old_sugov = r'''#ifdef CONFIG_SCHED_WALT
static unsigned long sugov_get_util(struct sugov_cpu *sg_cpu)
{
	struct rq *rq = cpu_rq(sg_cpu->cpu);
	unsigned long max = arch_scale_cpu_capacity(NULL, sg_cpu->cpu);

	sg_cpu->max = max;
	sg_cpu->bw_dl = cpu_bw_dl(rq);

	return stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);
}
#else'''

new_sugov = r'''#ifdef CONFIG_SCHED_WALT
static unsigned long sugov_get_util(struct sugov_cpu *sg_cpu)
{
	struct rq *rq = cpu_rq(sg_cpu->cpu);
	unsigned long max = arch_scale_cpu_capacity(NULL, sg_cpu->cpu);
	unsigned long util;

	sg_cpu->max = max;
	sg_cpu->bw_dl = cpu_bw_dl(rq);

	/*
	 * Samsung WALT remains the utilization source and schedtune remains
	 * compatible with vendor userspace. Modern uclamp is the final
	 * frequency floor/cap applied to that signal.
	 */
	util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);
	util = uclamp_rq_util_with(rq, util, NULL);
	pr_info_once("U1 uclamp+WALT schedutil path active\n");

	return min(util, max);
}
#else'''

if old_sugov in sg:
    sg = sg.replace(old_sugov, new_sugov, 1)
elif "U1 uclamp+WALT schedutil path active" not in sg:
    raise SystemExit("WALT schedutil anchor mismatch")

sugov.write_text(sg)

# Safety / intent checks.
checks = {
    defconfig: [
        "CONFIG_UCLAMP_TASK=y",
        "CONFIG_SCHED_TUNE=y",
        "CONFIG_SCHED_WALT=y",
        "CONFIG_CPU_FREQ_GOV_SCHEDUTIL=y",
    ],
    core: [
        "U1 hybrid rule:",
        "uclamp_eff_value(p, UCLAMP_MIN)",
        "schedtune_task_margin(p)",
        "schedtune_prefer_idle(p)",
    ],
    sugov: [
        "U1 uclamp+WALT schedutil path active",
        "util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);",
        "util = uclamp_rq_util_with(rq, util, NULL);",
    ],
}
for path, needles in checks.items():
    text = path.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing expected U1 marker: {needle}")

print("U1 modern uclamp + Samsung WALT/schedutil hybrid applied")
print("uclamp_task=enabled")
print("uclamp_task_group=disabled")
print("schedtune=retained")
print("walt=retained")
print("cass_placement=unchanged")
print("eevdf=unchanged")
