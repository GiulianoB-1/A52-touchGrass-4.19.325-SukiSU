#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 67_apply_cass_phase1.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
cass_c = kernel / "kernel/sched/cass.c"
kconfig = kernel / "init/Kconfig"
defconfig = kernel / "arch/arm64/configs/a52xq_defconfig"

for path in (fair_c, kconfig, defconfig):
    if not path.is_file():
        raise SystemExit(f"missing required file: {path}")

fc = fair_c.read_text()
kc = kconfig.read_text()
dc = defconfig.read_text()

# This experiment must be a clean CASS-only scheduler test.  Abort if one of
# our EEVDF integration markers somehow leaked into the prepared kernel tree.
eevdf_markers = (
    "sched_eevdf_enabled",
    "EEVDF phase 1 scaffold",
    "EEVDF phase 2 accounting",
    "EEVDF phase 3 deadline/lag bookkeeping",
    "EEVDF phase 4 augmented deadline tree",
    "EEVDF phase 5 picker integration",
    "EEVDF phase 6 wakeup/preemption integration",
    "EEVDF phase 7 placement consistency",
)
for marker in eevdf_markers:
    if marker in fc:
        raise SystemExit(f"refusing mixed EEVDF/CASS tree, found: {marker}")

cass_source = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Capacity Aware Superset Scheduler (CASS)
 *
 * Copyright (C) 2023-2024 Sultan Alsawaf <sultan@kerneltoast.com>.
 *
 * A52 Linux 4.19/WALT compatibility adaptation:
 * - Samsung 4.19 five-argument select_task_rq_fair() ABI
 * - task cpumask is task_struct::cpus_allowed
 * - raw WALT rq utilization is used so overload is not clipped
 * - capacity_orig_of() is used because this tree has no thermal_load_avg()
 * - sched_idle_cpu() is not available in this tree
 *
 * CASS changes CPU/runqueue placement for fair tasks.  It does not replace
 * CFS's per-runqueue task ordering.  EEVDF is intentionally absent.
 */

struct cass_cpu_cand {
    int cpu;
    unsigned int exit_lat;
    unsigned long cap;
    unsigned long cap_max;
    unsigned long eff_util;
    unsigned long hard_util;
    unsigned long util;
};

static bool sched_cass_enabled __read_mostly = true;

static int __init sched_cass_setup(char *str)
{
    bool enable;

    if (!str || kstrtobool(str, &enable))
        return 0;

    sched_cass_enabled = enable;
    pr_info("CASS phase 1 CPU placement %s\n",
            enable ? "enabled" : "disabled");
    return 1;
}
__setup("sched_cass=", sched_cass_setup);

static __always_inline unsigned long cass_uclamp_min(struct task_struct *p)
{
#ifdef CONFIG_UCLAMP_TASK
    return uclamp_eff_value(p, UCLAMP_MIN);
#else
    return 0;
#endif
}

static __always_inline bool
cass_fits_capacity(unsigned long util, unsigned long cap)
{
    /* Same ~20% headroom rule used by modern fair scheduling. */
    return util * 1280UL < cap * 1024UL;
}

static __always_inline
void cass_cpu_util(struct cass_cpu_cand *c, int this_cpu, bool sync)
{
    struct rq *rq = cpu_rq(c->cpu);

#ifdef CONFIG_SCHED_WALT
    /*
     * Do not use cpu_util() here: this Samsung 4.19 implementation clips
     * cpu_util() to capacity_orig_of().  CASS explicitly needs utilization
     * above capacity to distinguish overloaded CPUs.
     */
    c->util = READ_ONCE(rq->walt_stats.cumulative_runnable_avg_scaled);
#else
    {
        struct cfs_rq *cfs_rq = &rq->cfs;
        unsigned long est;

        c->util = READ_ONCE(cfs_rq->avg.util_avg);
        if (sched_feat(UTIL_EST)) {
            est = READ_ONCE(cfs_rq->avg.util_est.enqueued);
            if (est > c->util) {
                sync = false;
                c->util = est;
            }
        }
    }
#endif

    /*
     * Deduct the sync waker from this CPU.  RT tasks do not have the same
     * per-entity fair utilization signal, so never subtract them here.
     */
    if (sync && c->cpu == this_cpu && !rt_task(current))
        c->util -= min(c->util, task_util(current));

    c->hard_util = cpu_util_rt(rq) + cpu_util_dl(rq) + cpu_util_irq(rq);

    /*
     * Keep at least one unit of usable capacity so relative-utilization
     * division cannot hit zero even under extreme RT/DL/IRQ pressure.
     */
    c->cap = c->cap_max - min(c->hard_util, c->cap_max - 1);
}

/* Returns true if @a is a better destination CPU than @b. */
static __always_inline
bool cass_cpu_better(const struct cass_cpu_cand *a,
                     const struct cass_cpu_cand *b,
                     unsigned long p_util,
                     int this_cpu, int prev_cpu, bool sync)
{
#define cass_cmp(a, b) ({ res = (long)(a) - (long)(b); })
#define cass_eq(a, b)  ({ res = (long)((a) == (b)); })
    long res;

    /* Prefer a CPU that will not be overloaded. */
    if (cass_cmp(b->eff_util / b->cap_max,
                 a->eff_util / a->cap_max))
        goto done;

    /* If both are overloaded, prefer the less-overloaded CPU. */
    if (b->eff_util > b->cap_max && a->eff_util > a->cap_max &&
        cass_cmp(b->eff_util * SCHED_CAPACITY_SCALE / b->cap_max,
                 a->eff_util * SCHED_CAPACITY_SCALE / a->cap_max))
        goto done;

    /* Avoid fighting the idle load balancer with a misfit placement. */
    if (cass_cmp(cass_fits_capacity(p_util, a->cap_max),
                 cass_fits_capacity(p_util, b->cap_max)))
        goto done;

    /* Fundamental CASS rule: lower relative utilization wins. */
    if (cass_cmp(b->util, a->util))
        goto done;

    /* Relevant for uclamp-boosted tasks where non-idle CPUs are considered. */
    if (cass_cmp(!!a->exit_lat, !!b->exit_lat))
        goto done;

    /* Keep synchronous wakeups local when otherwise tied. */
    if (sync &&
        (cass_eq(a->cpu, this_cpu) || !cass_cmp(b->cpu, this_cpu)))
        goto done;

    /* Higher remaining capacity wins the next tie. */
    if (cass_cmp(a->cap, b->cap))
        goto done;

    /* Prefer the shallower idle state. */
    if (cass_cmp(b->exit_lat, a->exit_lat))
        goto done;

    /* Preserve cache locality where possible. */
    if (cass_eq(a->cpu, prev_cpu) || !cass_cmp(b->cpu, prev_cpu))
        goto done;

    if (cass_cmp(cpus_share_cache(a->cpu, prev_cpu),
                 cpus_share_cache(b->cpu, prev_cpu)))
        goto done;

done:
    return res > 0;
#undef cass_cmp
#undef cass_eq
}

static int cass_best_cpu(struct task_struct *p, int prev_cpu, bool sync)
{
    struct cass_cpu_cand cands[2], *best = cands;
    int this_cpu = raw_smp_processor_id();
    unsigned long p_util = task_util_est(p);
    unsigned long uc_min = cass_uclamp_min(p);
    bool has_idle = false;
    int cidx = 0, cpu;

    /*
     * idle_get_state() requires an RCU read-side critical section on this
     * Samsung scheduler.  Use one explicitly rather than relying on implicit
     * non-preemptible RCU-sched behavior.
     */
    rcu_read_lock();

    for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask) {
        struct cass_cpu_cand *curr = &cands[cidx];
        struct cpuidle_state *idle_state;
        struct rq *rq = cpu_rq(cpu);

        curr->cpu = cpu;
        curr->cap_max = capacity_orig_of(cpu);

        /*
         * If no CPU can fully satisfy uclamp, still walk toward the CPU that
         * comes closest instead of effectively ignoring the request.
         */
        if (curr->cap_max < uc_min && curr->cap_max < best->cap_max)
            continue;

        /*
         * This 4.19 tree has no sched_idle_cpu() helper.  Treat genuinely
         * idle CPUs as idle, plus the sync-waker CPU when the waker is the
         * only runnable task.
         */
        if ((sync && cpu == this_cpu && rq->nr_running == 1) ||
            available_idle_cpu(cpu)) {
            if (!uc_min) {
                if (!has_idle)
                    best = curr;
                has_idle = true;
            }

            curr->exit_lat = 1;
            idle_state = idle_get_state(rq);
            if (idle_state)
                curr->exit_lat += idle_state->exit_latency;
        } else {
            if (has_idle)
                continue;
            curr->exit_lat = 0;
        }

        cass_cpu_util(curr, this_cpu, sync);

        if (cpu != task_cpu(p))
            curr->util += p_util;

        curr->eff_util = max(curr->util + curr->hard_util, uc_min);

        if (curr->util < uc_min)
            curr->util = uc_min;

        curr->util =
            curr->util * SCHED_CAPACITY_SCALE / curr->cap;

        if (best == curr ||
            cass_cpu_better(curr, best, p_util, this_cpu, prev_cpu, sync)) {
            best = curr;
            cidx ^= 1;
        }
    }

    rcu_read_unlock();
    return best->cpu;
}

static int cass_select_task_rq_fair(struct task_struct *p, int prev_cpu,
                                    int sd_flag, int wake_flags,
                                    int sibling_count_hint)
{
    bool sync;

    if (unlikely(!sched_cass_enabled))
        return select_task_rq_fair(p, prev_cpu, sd_flag, wake_flags,
                                   sibling_count_hint);

    pr_info_once("CASS phase 1 fair CPU placement path is active\n");

    /* Placement on exec is intentionally left unchanged. */
    if (sd_flag & SD_BALANCE_EXEC)
        return prev_cpu;

    if (unlikely(!cpumask_intersects(&p->cpus_allowed, cpu_active_mask)))
        return cpumask_first(&p->cpus_allowed);

    /*
     * Keep the PELT entity synchronized too.  WALT task utilization is used
     * above when CONFIG_SCHED_WALT is enabled, but this preserves the
     * scheduler's existing accounting expectations.
     */
    if (!(sd_flag & SD_BALANCE_FORK))
        sync_entity_load_avg(&p->se);

    sync = (wake_flags & WF_SYNC) && !(current->flags & PF_EXITING);
    return cass_best_cpu(p, prev_cpu, sync);
}
'''

cass_c.write_text(cass_source)

if "config SCHED_CASS" not in kc:
    anchor = "config SCHED_SEC_TASK_BOOST\n"
    block = """config SCHED_CASS
    bool "Capacity Aware Superset Scheduler"
    depends on SMP
    default n
    help
      Enable Sultan Alsawaf's Capacity Aware Superset Scheduler for fair-task
      CPU placement.  CASS balances against CPU capacity and utilization,
      including overloaded CPUs, while preserving latency and cache-affinity
      tie-breakers.  This A52 port retains Samsung WALT accounting.

"""
    if kc.count(anchor) != 1:
        raise SystemExit("init/Kconfig SCHED_SEC_TASK_BOOST anchor mismatch")
    kc = kc.replace(anchor, block + anchor, 1)
    kconfig.write_text(kc)

include_anchor = """/*
 * All the scheduling class methods:
 */
const struct sched_class fair_sched_class = {
"""
include_repl = """#ifdef CONFIG_SCHED_CASS
#include "cass.c"
#endif

/*
 * All the scheduling class methods:
 */
const struct sched_class fair_sched_class = {
"""
if '#include "cass.c"' not in fc:
    if fc.count(include_anchor) != 1:
        raise SystemExit("fair_sched_class include anchor mismatch")
    fc = fc.replace(include_anchor, include_repl, 1)

selector_anchor = """#ifdef CONFIG_SMP
	.select_task_rq		= select_task_rq_fair,
	.migrate_task_rq	= migrate_task_rq_fair,
"""
selector_repl = """#ifdef CONFIG_SMP
#ifdef CONFIG_SCHED_CASS
	.select_task_rq		= cass_select_task_rq_fair,
#else
	.select_task_rq		= select_task_rq_fair,
#endif
	.migrate_task_rq	= migrate_task_rq_fair,
"""
if "cass_select_task_rq_fair," not in fc:
    if fc.count(selector_anchor) != 1:
        raise SystemExit("fair_sched_class select_task_rq anchor mismatch")
    fc = fc.replace(selector_anchor, selector_repl, 1)

fair_c.write_text(fc)

if "CONFIG_SCHED_CASS=y" not in dc:
    anchor = "CONFIG_SCHED_WALT=y\n"
    if dc.count(anchor) != 1:
        raise SystemExit("a52xq_defconfig CONFIG_SCHED_WALT anchor mismatch")
    dc = dc.replace(anchor, anchor + "CONFIG_SCHED_CASS=y\n", 1)
    defconfig.write_text(dc)

checks = {
    cass_c: [
        "Capacity Aware Superset Scheduler",
        "walt_stats.cumulative_runnable_avg_scaled",
        "for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask)",
        "cass_select_task_rq_fair",
        '__setup("sched_cass="',
        "CASS phase 1 fair CPU placement path is active",
    ],
    fair_c: [
        '#include "cass.c"',
        ".select_task_rq\t\t= cass_select_task_rq_fair,",
    ],
    kconfig: ["config SCHED_CASS"],
    defconfig: ["CONFIG_SCHED_CASS=y"],
}
for path, needles in checks.items():
    text = path.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing expected CASS element: {needle!r}")

print("CASS phase 1 A52/WALT port applied")
print("kernel=4.19.x")
print("fair_cpu_placement=CASS")
print("fair_task_ordering=legacy_CFS")
print("walt=retained")
print("eevdf=absent")
print("rt_cpu_placement=legacy")
print("boot_toggle=sched_cass=0|1")
