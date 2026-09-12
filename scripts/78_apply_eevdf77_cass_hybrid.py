#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 78_apply_eevdf77_cass_hybrid.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
sched_h = kernel / "kernel/sched/sched.h"
core_c = kernel / "kernel/sched/core.c"
cass_c = kernel / "kernel/sched/cass.c"
kconfig = kernel / "init/Kconfig"
defconfig = kernel / "arch/arm64/configs/a52xq_defconfig"

for path in (fair_c, sched_h, core_c, kconfig, defconfig):
    if not path.is_file():
        raise SystemExit(f"missing required file: {path}")

fc = fair_c.read_text()
sh = sched_h.read_text()
cc = core_c.read_text()
kc = kconfig.read_text()
dc = defconfig.read_text()

# This hybrid must be applied only after the complete fixed EEVDF stack,
# including Phase 74 multitask correctness and Phase 77 lifecycle correctness.
required_eevdf_markers = (
    "sched_eevdf_enabled",
    "EEVDF phase 2 accounting",
    "EEVDF phase 3 deadline/lag bookkeeping",
    "EEVDF phase 4 augmented deadline tree",
    "EEVDF phase 5 picker integration",
    "EEVDF phase 6 wakeup/preemption integration",
    "EEVDF phase 7 placement consistency",
    "static bool eevdf_update_deadline",
    "struct sched_entity *best_left = NULL",
    "return eevdf_pick_entity(cfs_rq) == se;",
    "likely(!sched_eevdf_enabled)",
    "EEVDF lifecycle-correctness instrumentation.",
    'proc_create_single("eevdf_status"',
    "curr->vlag == curr->deadline",
    "walt_latency_policy_seen",
)
for marker in required_eevdf_markers:
    if marker not in fc:
        raise SystemExit(f"refusing hybrid without fixed EEVDF element: {marker}")


if "#define ENQUEUE_INITIAL" not in sh:
    raise SystemExit("refusing hybrid without Phase 77 ENQUEUE_INITIAL")
if "ENQUEUE_NOCLOCK | ENQUEUE_INITIAL" not in cc:
    raise SystemExit("refusing hybrid without Phase 77 fork destination placement")

cass_source = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Capacity Aware Superset Scheduler (CASS)
 *
 * Copyright (C) 2023-2024 Sultan Alsawaf <sultan@kerneltoast.com>.
 *
 * A52 hybrid: Phase 77 lifecycle-corrected EEVDF + Phase 1b WALT-safe CASS placement.
 *
 * The first A52 CASS experiment mixed Samsung's WALT runnable-demand signal
 * with separate RT/DL/IRQ accounting from a newer PELT-oriented CASS revision.
 * Samsung's WALT cpu_util() already derives CPU load from
 * walt_stats.cumulative_runnable_avg_scaled, so charging hard utilization
 * again can distort placement badly.  This revision deliberately removes that
 * mixed accounting and goes back to the small original CASS placement model.
 *
 * Kept A52-specific compatibility:
 * - five-argument select_task_rq_fair() ABI
 * - task_struct::cpus_allowed instead of cpus_ptr
 * - RCU read lock around idle_get_state()
 * - no sched_idle_cpu() helper in this tree
 * - WALT task demand from task_util_est()
 *
 * EEVDF remains runtime-gated in fair.c; CASS only replaces fair CPU placement.\n * RT placement remains legacy.
 */

struct cass_cpu_cand {
    int cpu;
    unsigned int exit_lat;
    unsigned long cap;
    unsigned long util;
};

static bool sched_cass_enabled __read_mostly = true;

static int __init sched_cass_setup(char *str)
{
    bool enable;

    if (!str || kstrtobool(str, &enable))
        return 0;

    sched_cass_enabled = enable;
    pr_info("CASS+EEVDF77 hybrid CPU placement %s\n",
            enable ? "enabled" : "disabled");
    return 1;
}
__setup("sched_cass=", sched_cass_setup);

static __always_inline unsigned long
cass_walt_cpu_util(int cpu)
{
#ifdef CONFIG_SCHED_WALT
    /*
     * Use the unclipped WALT runnable-demand aggregate so CASS can still
     * distinguish CPUs above 100% relative load.  Do not add RT/DL/IRQ again:
     * this is the key Phase 1b correction.
     */
    return (unsigned long)READ_ONCE(
        cpu_rq(cpu)->walt_stats.cumulative_runnable_avg_scaled);
#else
    struct cfs_rq *cfs_rq = &cpu_rq(cpu)->cfs;
    unsigned long util = READ_ONCE(cfs_rq->avg.util_avg);

    if (sched_feat(UTIL_EST))
        util = max(util,
                   (unsigned long)READ_ONCE(cfs_rq->avg.util_est.enqueued));

    return util;
#endif
}

/* Returns true if @a is a better destination CPU than @b. */
static __always_inline
bool cass_cpu_better(const struct cass_cpu_cand *a,
                     const struct cass_cpu_cand *b,
                     int this_cpu, int prev_cpu, bool sync)
{
#define cass_cmp(a, b) ({ res = (long)(a) - (long)(b); })
#define cass_eq(a, b)  ({ res = (long)((a) == (b)); })
    long res;

    /* Fundamental CASS rule: lower relative utilization wins. */
    if (cass_cmp(b->util, a->util))
        goto done;

    /* Keep synchronous wakeups local when otherwise tied. */
    if (sync &&
        (cass_eq(a->cpu, this_cpu) || !cass_cmp(b->cpu, this_cpu)))
        goto done;

    /* Prefer the CPU with higher currently usable capacity. */
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
    bool has_idle = false;
    int cidx = 0, cpu;

    /*
     * This Samsung 4.19 tree checks RCU coverage in idle_get_state().
     * Make that lifetime explicit.
     */
    rcu_read_lock();

    for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask) {
        struct cass_cpu_cand *curr = &cands[cidx];
        struct cpuidle_state *idle_state;
        struct rq *rq = cpu_rq(cpu);
        unsigned long util;

        curr->cpu = cpu;

        /*
         * CASS prefers an idle candidate whenever one exists.  This tree has
         * no sched_idle_cpu(), so use genuinely idle CPUs plus the sync-waker
         * CPU only when the waker is the sole runnable task.
         */
        if ((sync && cpu == this_cpu && rq->nr_running == 1) ||
            available_idle_cpu(cpu)) {
            if (!has_idle)
                best = curr;
            has_idle = true;

            curr->exit_lat = 1;
            idle_state = idle_get_state(rq);
            if (idle_state)
                curr->exit_lat += idle_state->exit_latency;
        } else {
            if (has_idle)
                continue;
            curr->exit_lat = 0;
        }

        /*
         * Samsung's own WALT wake-placement code evaluates the current CPU
         * load and then adds the waking task's demand.  Do the same for every
         * candidate rather than mixing WALT CPU demand with PELT hard-util
         * components.
         */
        util = cass_walt_cpu_util(cpu);
        util += p_util;

        curr->cap = max_t(unsigned long, capacity_of(cpu), 1UL);
        curr->util = util * SCHED_CAPACITY_SCALE / curr->cap;

        /*
         * Keep Sultan's no-idle-candidate fix: after evaluating the first
         * candidate, always flip to the spare slot even when best == curr.
         */
        if (best == curr ||
            cass_cpu_better(curr, best, this_cpu, prev_cpu, sync)) {
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

    pr_info_once("CASS+EEVDF77 hybrid fair CPU placement path is active\n");

    /* Match original CASS behavior: do not balance exec placements. */
    if (sd_flag & SD_BALANCE_EXEC)
        return prev_cpu;

    if (unlikely(!cpumask_intersects(&p->cpus_allowed, cpu_active_mask)))
        return cpumask_first(&p->cpus_allowed);

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
      CPU placement. This A52 Phase 1b port uses Samsung WALT runnable demand
      without separately charging RT/DL/IRQ utilization a second time.

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
\t.select_task_rq\t\t= select_task_rq_fair,
\t.migrate_task_rq\t= migrate_task_rq_fair,
"""
selector_repl = """#ifdef CONFIG_SMP
#ifdef CONFIG_SCHED_CASS
\t.select_task_rq\t\t= cass_select_task_rq_fair,
#else
\t.select_task_rq\t\t= select_task_rq_fair,
#endif
\t.migrate_task_rq\t= migrate_task_rq_fair,
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
        "A52 hybrid: Phase 77 lifecycle-corrected EEVDF + Phase 1b WALT-safe CASS placement.",
        "walt_stats.cumulative_runnable_avg_scaled",
        "util += p_util",
        "capacity_of(cpu)",
        "for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask)",
        "cass_select_task_rq_fair",
        '__setup("sched_cass="',
        "CASS+EEVDF77 hybrid fair CPU placement path is active",
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

# Explicitly forbid the mixed accounting that made Phase 1 unsafe on WALT.
cass_text = cass_c.read_text()
for forbidden in (
    "cpu_util_rt(",
    "cpu_util_dl(",
    "cpu_util_irq(",
    "hard_util",
    "eff_util",
):
    if forbidden in cass_text:
        raise SystemExit(f"Phase 1b contains forbidden mixed-accounting token: {forbidden}")

print("CASS+EEVDF77 hybrid A52/WALT-safe placement port applied")
print("kernel=4.19.x")
print("fair_cpu_placement=CASS")
print("fair_task_ordering=EEVDF_when_sched_eevdf_1")
print("walt=raw_runnable_demand_no_hard_util")
print("eevdf=phase77_lifecycle_correctness_retained")
print("rt_cpu_placement=legacy")
print("boot_toggle=sched_cass=0|1")
