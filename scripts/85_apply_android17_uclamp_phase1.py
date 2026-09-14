#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 85_apply_android17_uclamp_phase1.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
core = root / "kernel/sched/core.c"
sched_h = root / "kernel/sched/sched.h"
sugov = root / "kernel/sched/cpufreq_schedutil.c"
kconfig = root / "init/Kconfig"
defconfig = root / "arch/arm64/configs/a52xq_defconfig"

for p in (core, sched_h, sugov, kconfig, defconfig):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

cc = core.read_text()
sh = sched_h.read_text()
sg = sugov.read_text()
kc = kconfig.read_text()
dc = defconfig.read_text()

def replace_once(text, old, new, name):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{name}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

# ---------------------------------------------------------------------------
# Product compatibility: Samsung intentionally made SCHED_TUNE and UCLAMP
# mutually exclusive. Android userspace on this device still expects /dev/stune,
# so keep the legacy controller compiled while modern uclamp is introduced.
# This is a compatibility adaptation around the Android17 uclamp semantics.
# ---------------------------------------------------------------------------
kc = replace_once(
    kc,
    'config SCHED_TUNE\n\tbool "Boosting for CFS tasks (EXPERIMENTAL)"\n\tdepends on !UCLAMP_TASK\n\tdepends on SMP\n',
    'config SCHED_TUNE\n\tbool "Boosting for CFS tasks (EXPERIMENTAL)"\n\tdepends on SMP\n',
    "sched_tune_uclamp_coexist_kconfig",
)
kconfig.write_text(kc)

if "# CONFIG_UCLAMP_TASK is not set" in dc:
    dc = dc.replace("# CONFIG_UCLAMP_TASK is not set", "CONFIG_UCLAMP_TASK=y", 1)
elif "CONFIG_UCLAMP_TASK=y" not in dc:
    raise SystemExit("UCLAMP_TASK defconfig anchor missing")
if "CONFIG_SCHED_TUNE=y" not in dc:
    raise SystemExit("SCHED_TUNE vendor compatibility must remain enabled")
defconfig.write_text(dc)

# ---------------------------------------------------------------------------
# Android17/Linux 6.18 fast-path gating helpers.
# ---------------------------------------------------------------------------
old_sh = '''#ifdef CONFIG_UCLAMP_TASK
unsigned long uclamp_eff_value(struct task_struct *p, enum uclamp_id clamp_id);

static __always_inline
unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,
'''
new_sh = '''#ifdef CONFIG_UCLAMP_TASK
extern struct static_key_false sched_uclamp_used;
extern unsigned int sysctl_sched_uclamp_util_min_rt_default;

unsigned long uclamp_eff_value(struct task_struct *p, enum uclamp_id clamp_id);

static inline bool uclamp_is_used(void)
{
	return static_branch_likely(&sched_uclamp_used);
}

static inline void sched_uclamp_enable(void)
{
	if (!uclamp_is_used())
		static_branch_enable(&sched_uclamp_used);
}

static __always_inline
unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,
'''
sh = replace_once(sh, old_sh, new_sh, "sched_h_fastpath_helpers")

old_sh_else = '''#else /* CONFIG_UCLAMP_TASK */
static inline
unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,
'''
new_sh_else = '''#else /* CONFIG_UCLAMP_TASK */
static inline bool uclamp_is_used(void)
{
	return false;
}

static inline void sched_uclamp_enable(void) { }

static inline
unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,
'''
sh = replace_once(sh, old_sh_else, new_sh_else, "sched_h_disabled_helpers")
sched_h.write_text(sh)

# ---------------------------------------------------------------------------
# Android17 core state: RT default clamp and static-key fast path.
# ---------------------------------------------------------------------------
old_globals = '''/* Max allowed maximum utilization */
unsigned int sysctl_sched_uclamp_util_max = SCHED_CAPACITY_SCALE;

/* All clamps are required to be less or equal than these values */
static struct uclamp_se uclamp_default[UCLAMP_CNT];
'''
new_globals = '''/* Max allowed maximum utilization */
unsigned int sysctl_sched_uclamp_util_max = SCHED_CAPACITY_SCALE;

/*
 * Android17 behavior: RT tasks default to full-capacity UCLAMP_MIN unless
 * userspace supplied an explicit clamp.
 */
unsigned int sysctl_sched_uclamp_util_min_rt_default = SCHED_CAPACITY_SCALE;

/* All clamps are required to be less or equal than these values */
static struct uclamp_se uclamp_default[UCLAMP_CNT];

/*
 * Android17 fast path: rq clamp aggregation is dormant until userspace
 * actually opts in through sched_setattr() or a clamp sysctl.
 */
DEFINE_STATIC_KEY_FALSE(sched_uclamp_used);
'''
cc = replace_once(cc, old_globals, new_globals, "uclamp_globals")

old_bucket = '''static inline unsigned int uclamp_bucket_id(unsigned int clamp_value)
{
	return clamp_value / UCLAMP_BUCKET_DELTA;
}
'''
new_bucket = '''static inline unsigned int uclamp_bucket_id(unsigned int clamp_value)
{
	return min_t(unsigned int, clamp_value / UCLAMP_BUCKET_DELTA,
		     UCLAMP_BUCKETS - 1);
}
'''
cc = replace_once(cc, old_bucket, new_bucket, "bucket_bounds")

# Gate rq aggregation like modern uclamp.
old_inc = '''static inline void uclamp_rq_inc(struct rq *rq, struct task_struct *p)
{
	enum uclamp_id clamp_id;

	if (unlikely(!p->sched_class->uclamp_enabled))
		return;
'''
new_inc = '''static inline void uclamp_rq_inc(struct rq *rq, struct task_struct *p)
{
	enum uclamp_id clamp_id;

	if (!uclamp_is_used())
		return;

	if (unlikely(!p->sched_class->uclamp_enabled))
		return;
'''
cc = replace_once(cc, old_inc, new_inc, "uclamp_rq_inc_static_key")

old_dec = '''static inline void uclamp_rq_dec(struct rq *rq, struct task_struct *p)
{
	enum uclamp_id clamp_id;

	if (unlikely(!p->sched_class->uclamp_enabled))
		return;
'''
new_dec = '''static inline void uclamp_rq_dec(struct rq *rq, struct task_struct *p)
{
	enum uclamp_id clamp_id;

	if (!uclamp_is_used())
		return;

	if (unlikely(!p->sched_class->uclamp_enabled))
		return;
'''
cc = replace_once(cc, old_dec, new_dec, "uclamp_rq_dec_static_key")

# Enable the static key when sysctl clamp defaults are changed.
old_root_update = '''	if (update_root_tg)
		uclamp_update_root_tg();
'''
new_root_update = '''	if (update_root_tg) {
		sched_uclamp_enable();
		uclamp_update_root_tg();
	}
'''
cc = replace_once(cc, old_root_update, new_root_update, "sysctl_static_key")

# Android17 sched_setattr validation/reset semantics, including -1 reset.
start = cc.find("static int uclamp_validate(struct task_struct *p,")
end = cc.find("static void uclamp_fork(struct task_struct *p)", start)
if start < 0 or end < 0:
    raise SystemExit("uclamp validate/set block anchors missing")

modern_set_block = r'''static int uclamp_validate(struct task_struct *p,
			   const struct sched_attr *attr)
{
	int util_min = p->uclamp_req[UCLAMP_MIN].value;
	int util_max = p->uclamp_req[UCLAMP_MAX].value;

	if (attr->sched_flags & SCHED_FLAG_UTIL_CLAMP_MIN) {
		util_min = attr->sched_util_min;
		if (util_min + 1 > SCHED_CAPACITY_SCALE + 1)
			return -EINVAL;
	}

	if (attr->sched_flags & SCHED_FLAG_UTIL_CLAMP_MAX) {
		util_max = attr->sched_util_max;
		if (util_max + 1 > SCHED_CAPACITY_SCALE + 1)
			return -EINVAL;
	}

	if (util_min != -1 && util_max != -1 && util_min > util_max)
		return -EINVAL;

	/*
	 * Android17: only pay rq aggregation cost after userspace actually uses
	 * uclamp. static_branch_enable() is deliberately done before rq locks.
	 */
	sched_uclamp_enable();
	return 0;
}

static bool uclamp_reset(const struct sched_attr *attr,
			 enum uclamp_id clamp_id,
			 struct uclamp_se *uc_se)
{
	if (likely(!(attr->sched_flags & SCHED_FLAG_UTIL_CLAMP)) &&
	    !uc_se->user_defined)
		return true;

	if (clamp_id == UCLAMP_MIN &&
	    (attr->sched_flags & SCHED_FLAG_UTIL_CLAMP_MIN) &&
	    attr->sched_util_min == -1)
		return true;

	if (clamp_id == UCLAMP_MAX &&
	    (attr->sched_flags & SCHED_FLAG_UTIL_CLAMP_MAX) &&
	    attr->sched_util_max == -1)
		return true;

	return false;
}

static void __setscheduler_uclamp(struct task_struct *p,
				  const struct sched_attr *attr)
{
	enum uclamp_id clamp_id;

	for_each_clamp_id(clamp_id) {
		struct uclamp_se *uc_se = &p->uclamp_req[clamp_id];
		unsigned int value;

		if (!uclamp_reset(attr, clamp_id, uc_se))
			continue;

		if (unlikely(rt_task(p) && clamp_id == UCLAMP_MIN))
			value = sysctl_sched_uclamp_util_min_rt_default;
		else
			value = uclamp_none(clamp_id);

		uclamp_se_set(uc_se, value, false);
	}

	if (likely(!(attr->sched_flags & SCHED_FLAG_UTIL_CLAMP)))
		return;

	if ((attr->sched_flags & SCHED_FLAG_UTIL_CLAMP_MIN) &&
	    attr->sched_util_min != -1)
		uclamp_se_set(&p->uclamp_req[UCLAMP_MIN],
			      attr->sched_util_min, true);

	if ((attr->sched_flags & SCHED_FLAG_UTIL_CLAMP_MAX) &&
	    attr->sched_util_max != -1)
		uclamp_se_set(&p->uclamp_req[UCLAMP_MAX],
			      attr->sched_util_max, true);
}

'''
cc = cc[:start] + modern_set_block + cc[end:]

# Add post-fork RT default synchronization and modern rq initialization.
old_fork_tail = '''static void uclamp_fork(struct task_struct *p)
{
	enum uclamp_id clamp_id;

	for_each_clamp_id(clamp_id)
		p->uclamp[clamp_id].active = false;

	if (likely(!p->sched_reset_on_fork))
		return;

	for_each_clamp_id(clamp_id) {
		uclamp_se_set(&p->uclamp_req[clamp_id],
			      uclamp_none(clamp_id), false);
	}
}

#ifdef CONFIG_SMP
'''
new_fork_tail = '''static void uclamp_fork(struct task_struct *p)
{
	enum uclamp_id clamp_id;

	for_each_clamp_id(clamp_id)
		p->uclamp[clamp_id].active = false;

	if (likely(!p->sched_reset_on_fork))
		return;

	for_each_clamp_id(clamp_id) {
		uclamp_se_set(&p->uclamp_req[clamp_id],
			      uclamp_none(clamp_id), false);
	}
}

static void uclamp_post_fork(struct task_struct *p)
{
	struct uclamp_se *uc_se;

	if (!rt_task(p))
		return;

	uc_se = &p->uclamp_req[UCLAMP_MIN];
	if (!uc_se->user_defined)
		uclamp_se_set(uc_se,
			      sysctl_sched_uclamp_util_min_rt_default, false);
}

#ifdef CONFIG_SMP
'''
cc = replace_once(cc, old_fork_tail, new_fork_tail, "uclamp_post_fork")

# Preserve Samsung stune semantics at compatibility call sites while layering
# explicit Android17 task clamps above them.
old_task_helpers = r'''#ifdef CONFIG_SMP
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

new_task_helpers = r'''#ifdef CONFIG_SMP
#ifdef CONFIG_SCHED_TUNE
long schedtune_task_margin(struct task_struct *task);
int schedtune_task_boost(struct task_struct *p);
int schedtune_prefer_idle(struct task_struct *p);
#endif

unsigned int uclamp_task(struct task_struct *p)
{
	unsigned long base_util = task_util_est(p);
	unsigned long util = base_util;

#ifdef CONFIG_SCHED_TUNE
	{
		long margin = schedtune_task_margin(p);
		util += margin;
#ifdef CONFIG_SEC_PERF_MANAGER
		if (p->drawing_flag)
			util = max(util, get_max_fps_util(p->drawing_flag));
#endif
		trace_sched_boost_task(p, base_util, margin);
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
cc = replace_once(cc, old_task_helpers, new_task_helpers, "schedtune_compat_helpers")

old_init = '''static void __init init_uclamp(void)
{
	struct uclamp_se uc_max = {};
	enum uclamp_id clamp_id;
	int cpu;

	mutex_init(&uclamp_mutex);

	for_each_possible_cpu(cpu) {
		memset(&cpu_rq(cpu)->uclamp, 0,
				sizeof(struct uclamp_rq)*UCLAMP_CNT);
		cpu_rq(cpu)->uclamp_flags = 0;
	}
'''
new_init = '''static void __init init_uclamp_rq(struct rq *rq)
{
	enum uclamp_id clamp_id;

	for_each_clamp_id(clamp_id) {
		memset(&rq->uclamp[clamp_id], 0, sizeof(struct uclamp_rq));
		rq->uclamp[clamp_id].value = uclamp_none(clamp_id);
	}
	rq->uclamp_flags = UCLAMP_FLAG_IDLE;
}

static void __init init_uclamp(void)
{
	struct uclamp_se uc_max = {};
	enum uclamp_id clamp_id;
	int cpu;

	mutex_init(&uclamp_mutex);

	for_each_possible_cpu(cpu)
		init_uclamp_rq(cpu_rq(cpu));
'''
cc = replace_once(cc, old_init, new_init, "modern_rq_init")

# Add disabled post-fork stub.
old_disabled = '''static void __setscheduler_uclamp(struct task_struct *p,
				  const struct sched_attr *attr) { }
static inline void uclamp_fork(struct task_struct *p) { }

long schedtune_task_margin'''
new_disabled = '''static void __setscheduler_uclamp(struct task_struct *p,
				  const struct sched_attr *attr) { }
static inline void uclamp_fork(struct task_struct *p) { }
static inline void uclamp_post_fork(struct task_struct *p) { }

long schedtune_task_margin'''
cc = replace_once(cc, old_disabled, new_disabled, "disabled_post_fork_stub")

# Call post-fork after policy/class has been finalized.
old_class = '''	if (dl_prio(p->prio))
		return -EAGAIN;
	else if (rt_prio(p->prio))
		p->sched_class = &rt_sched_class;
	else
		p->sched_class = &fair_sched_class;

	init_entity_runnable_average(&p->se);
'''
new_class = '''	if (dl_prio(p->prio))
		return -EAGAIN;
	else if (rt_prio(p->prio))
		p->sched_class = &rt_sched_class;
	else
		p->sched_class = &fair_sched_class;

	uclamp_post_fork(p);
	init_entity_runnable_average(&p->se);
'''
cc = replace_once(cc, old_class, new_class, "sched_fork_post_uclamp")
core.write_text(cc)

# ---------------------------------------------------------------------------
# Samsung WALT schedutil compatibility: retain stune as the vendor source, but
# apply Android17 UCLAMP_MIN/MAX as the final frequency floor/cap.
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

	util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);
	if (uclamp_is_used())
		util = uclamp_rq_util_with(rq, util, NULL);

	pr_info_once("U17 uclamp + WALT schedutil compatibility path active\n");
	return min(util, max);
}
#else'''
sg = replace_once(sg, old_sugov, new_sugov, "walt_schedutil_uclamp")
sugov.write_text(sg)

# Final structural checks.
checks = {
    kconfig: [
        'config SCHED_TUNE',
        'depends on SMP',
    ],
    defconfig: [
        'CONFIG_UCLAMP_TASK=y',
        'CONFIG_SCHED_TUNE=y',
        'CONFIG_SCHED_WALT=y',
    ],
    sched_h: [
        'extern struct static_key_false sched_uclamp_used;',
        'static inline bool uclamp_is_used(void)',
        'static inline void sched_uclamp_enable(void)',
    ],
    core: [
        'DEFINE_STATIC_KEY_FALSE(sched_uclamp_used);',
        'sysctl_sched_uclamp_util_min_rt_default',
        'static bool uclamp_reset(',
        'sched_uclamp_enable();',
        'static void uclamp_post_fork(struct task_struct *p)',
        'static void __init init_uclamp_rq(struct rq *rq)',
        'uclamp_post_fork(p);',
    ],
    sugov: [
        'U17 uclamp + WALT schedutil compatibility path active',
        'if (uclamp_is_used())',
        'uclamp_rq_util_with(rq, util, NULL)',
    ],
}
for path, needles in checks.items():
    text = path.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing {needle!r}")

if 'depends on !UCLAMP_TASK' in kconfig.read_text():
    raise SystemExit("SCHED_TUNE/UCLAMP mutual exclusion still present")

print("Android17 uclamp Phase1 semantic backport applied")
print("source_semantics=android17-linux6.18")
print("static_key=sched_uclamp_used")
print("sched_setattr_reset=-1-supported")
print("rt_default_min=ported")
print("rq_init=modernized")
print("schedtune=retained-compatibility")
print("walt_schedutil=uclamp-aware")
print("cass=unchanged")
print("eevdf=unchanged")
