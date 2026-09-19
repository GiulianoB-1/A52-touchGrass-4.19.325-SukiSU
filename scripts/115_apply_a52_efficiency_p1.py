#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 115_apply_a52_efficiency_p1.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"
cass = root / "kernel/sched/cass.c"
sched_h = root / "kernel/sched/sched.h"
sugov = root / "kernel/sched/cpufreq_schedutil.c"

for p in (vmscan, cass, sched_h, sugov):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def replace_function(text, signature, replacement):
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")
    depth = 0
    end = None
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f"missing closing brace: {signature}")
    return text[:start] + replacement.rstrip() + text[end:]


# ---------------------------------------------------------------------------
# MGLRU efficiency P4
#
# Newer MGLRU clears the seed PTE that caused rmap look-around before checking
# contention/special-VMA early exits.  The old v9 path can otherwise leave the
# same seed PTE young and rediscover it repeatedly.  Also cache whether the mm
# has notifiers so the common Android path does not repeat the notifier test
# for every adjacent PTE.
# ---------------------------------------------------------------------------
vm = vmscan.read_text()
for marker in (
    "A52 MGLRU Modern P1: Android17 9eff4247",
    "A52 MGLRU Modern P3: upstream c28ac3c7 skip special VMAs",
    "ptep_clear_young_notify(pvmw->vma, addr, pte + i)",
):
    if marker not in vm:
        raise SystemExit(f"MGLRU prerequisite missing: {marker}")

look_start = vm.find("void lru_gen_look_around(struct page_vma_mapped_walk *pvmw)")
look_end = vm.find(
    "/******************************************************************************\n *                          the eviction",
    look_start,
)
if look_start < 0 or look_end < 0:
    raise SystemExit("cannot bound lru_gen_look_around")
look = vm[look_start:look_end]

if "A52 MGLRU Efficiency P4: clear seed PTE before look-around bailouts" in look:
    raise SystemExit("MGLRU Efficiency P4 already applied")

look = replace_once(
    look,
    """\tint i;
\tpte_t *pte;
""",
    """\tint i;
\tbool has_notifiers;
\tpte_t *pte;
""",
    "mglru has_notifiers local",
)

seed_anchor = """\tlockdep_assert_held(pvmw->ptl);
\tVM_BUG_ON_PAGE(PageLRU(pvmw->page), pvmw->page);

\tif (spin_is_contended(pvmw->ptl))
"""
seed_repl = """\tlockdep_assert_held(pvmw->ptl);
\tVM_BUG_ON_PAGE(PageLRU(pvmw->page), pvmw->page);

\t/*
\t * A52 MGLRU Efficiency P4: clear seed PTE before look-around bailouts.
\t * This mirrors the newer MGLRU ordering and prevents the same accessed
\t * seed from repeatedly retriggering rmap look-around under contention
\t * or on a special VMA. Cache notifier presence for the adjacent scan.
\t */
\thas_notifiers = mm_has_notifiers(pvmw->vma->vm_mm);
\tif (!(has_notifiers ?
\t      ptep_clear_young_notify(pvmw->vma, pvmw->address, pvmw->pte) :
\t      ptep_test_and_clear_young(pvmw->vma, pvmw->address, pvmw->pte)))
\t\treturn;
\tyoung = 1;

\tif (spin_is_contended(pvmw->ptl))
"""
look = replace_once(look, seed_anchor, seed_repl, "mglru seed clear")

loop_anchor = """\t\tVM_BUG_ON(addr < pvmw->vma->vm_start || addr >= pvmw->vma->vm_end);

\t\tif (!pte_present(pte[i]) || is_zero_pfn(pfn))
"""
loop_repl = """\t\tVM_BUG_ON(addr < pvmw->vma->vm_start || addr >= pvmw->vma->vm_end);

\t\t/* The seed PTE was already cleared above. */
\t\tif (addr == pvmw->address)
\t\t\tcontinue;

\t\tif (!pte_present(pte[i]) || is_zero_pfn(pfn))
"""
look = replace_once(look, loop_anchor, loop_repl, "mglru skip seed in adjacent loop")

look = replace_once(
    look,
    "if (!pte_young(pte[i]) && !mm_has_notifiers(pvmw->vma->vm_mm))",
    "if (!pte_young(pte[i]) && !has_notifiers)",
    "mglru cached notifier prefilter",
)

look = replace_once(
    look,
    """\t\tif (!ptep_clear_young_notify(pvmw->vma, addr, pte + i))
\t\t\tcontinue;
""",
    """\t\tif (!(has_notifiers ?
\t\t      ptep_clear_young_notify(pvmw->vma, addr, pte + i) :
\t\t      ptep_test_and_clear_young(pvmw->vma, addr, pte + i)))
\t\t\tcontinue;
""",
    "mglru notifier fast path",
)

vm = vm[:look_start] + look + vm[look_end:]
vmscan.write_text(vm)


# ---------------------------------------------------------------------------
# CASS efficiency P3
#
# Keep the WALT-safe utilization model (no separate RT/DL/IRQ accounting), but
# bring over the useful parts of newer CASS:
#   * sync-waker subtraction
#   * do not double-count the waking task on its current CPU
#   * separate base task demand from UCLAMP_MIN/MAX
#   * prefer CPUs that satisfy the task/uclamp capacity requirement
#   * for uclamp-boosted tasks, do not throw away all busy candidates merely
#     because any idle CPU exists
# ---------------------------------------------------------------------------
ca = cass.read_text()
if "CASS uclamp-aware task demand path is active" not in ca:
    raise SystemExit("CASS P2/uclamp prerequisite missing")

old_struct = """struct cass_cpu_cand {
    int cpu;
    unsigned int exit_lat;
    unsigned long cap;
    unsigned long util;
};
"""
new_struct = """struct cass_cpu_cand {
    int cpu;
    unsigned int exit_lat;
    unsigned long cap;
    unsigned long cap_max;
    unsigned long util;
};
"""
ca = replace_once(ca, old_struct, new_struct, "cass candidate capacity fields")

new_walt_util = r'''static __always_inline unsigned long
cass_walt_cpu_util(int cpu, int this_cpu, bool sync)
{
#ifdef CONFIG_SCHED_WALT
    unsigned long util = (unsigned long)READ_ONCE(
        cpu_rq(cpu)->walt_stats.cumulative_runnable_avg_scaled);

    /*
     * For a synchronous wake the waker is expected to block. Newer CASS
     * removes the waker's demand from the destination CPU estimate so the
     * transient hand-off does not make the current CPU look artificially busy.
     */
    if (sync && cpu == this_cpu && !rt_task(current))
        util -= min(util, task_util(current));

    return util;
#else
    struct cfs_rq *cfs_rq = &cpu_rq(cpu)->cfs;
    unsigned long util = READ_ONCE(cfs_rq->avg.util_avg);

    if (sched_feat(UTIL_EST))
        util = max(util,
                   (unsigned long)READ_ONCE(cfs_rq->avg.util_est.enqueued));

    if (sync && cpu == this_cpu && !rt_task(current))
        util -= min(util, task_util(current));

    return util;
#endif
}'''
ca = replace_function(
    ca,
    "static __always_inline unsigned long\ncass_walt_cpu_util(int cpu)",
    new_walt_util,
)

new_better = r'''static __always_inline
bool cass_cpu_better(const struct cass_cpu_cand *a,
                     const struct cass_cpu_cand *b,
                     unsigned long p_util, unsigned long uc_min,
                     int this_cpu, int prev_cpu, bool sync)
{
#define cass_cmp(a, b) ({ res = (long)(a) - (long)(b); })
#define cass_eq(a, b)  ({ res = (long)((a) == (b)); })
    long res;

    /* Prefer a CPU that can satisfy the explicit UCLAMP_MIN request. */
    if (cass_cmp(a->cap_max >= uc_min, b->cap_max >= uc_min))
        goto done;

    /* Then prefer a CPU that can fit the task's measured demand. */
    if (cass_cmp(a->cap_max >= p_util, b->cap_max >= p_util))
        goto done;

    /* Fundamental CASS rule: lower relative utilization wins. */
    if (cass_cmp(b->util, a->util))
        goto done;

    /*
     * Boosted tasks are allowed to compare idle and busy CPUs; when their
     * relative load is otherwise tied, retain the latency advantage of idle.
     */
    if (uc_min && cass_cmp(!!a->exit_lat, !!b->exit_lat))
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
}'''
ca = replace_function(
    ca,
    "static __always_inline\nbool cass_cpu_better(",
    new_better,
)

new_best = r'''static int cass_best_cpu(struct task_struct *p, int prev_cpu, bool sync)
{
    struct cass_cpu_cand cands[2], *best = cands;
    int this_cpu = raw_smp_processor_id();
    unsigned long p_util = task_util_est(p);
    unsigned long uc_min = 0;
    unsigned long uc_max = SCHED_CAPACITY_SCALE;
    bool has_idle = false;
    int cidx = 0, cpu;

    /*
     * Keep measured WALT task demand separate from explicit utilization
     * constraints. Treat UCLAMP_MAX as a task-demand ceiling and UCLAMP_MIN
     * as a candidate floor/capacity requirement rather than adding the clamp
     * value as if it were real runnable load on every CPU.
     */
    if (uclamp_is_used()) {
        uc_min = uclamp_eff_value(p, UCLAMP_MIN);
        uc_max = uclamp_eff_value(p, UCLAMP_MAX);
        p_util = min(p_util, uc_max);
    }

    pr_info_once("A52 CASS P3: uclamp-fit + sync-waker + no task double-count active\n");
    rcu_read_lock();

    for_each_cpu_and(cpu, &p->cpus_allowed, cpu_active_mask) {
        struct cass_cpu_cand *curr = &cands[cidx];
        struct cpuidle_state *idle_state;
        struct rq *rq = cpu_rq(cpu);
        unsigned long util;

        curr->cpu = cpu;
        curr->cap_max = max_t(unsigned long, capacity_orig_of(cpu), 1UL);

        /*
         * For normal tasks, preserve the original CASS energy policy of using
         * the idle-candidate pool once one exists. Uclamp-boosted tasks must
         * still be allowed to choose a busy CPU with materially better
         * capacity, matching the newer CASS behavior.
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

        util = cass_walt_cpu_util(cpu, this_cpu, sync);

        /*
         * A waking task is already represented in the WALT runnable sum on
         * its current CPU. Only add its demand when evaluating another CPU.
         */
        if (cpu != task_cpu(p))
            util += p_util;

        if (util < uc_min)
            util = uc_min;

        curr->cap = max_t(unsigned long, capacity_of(cpu), 1UL);
        curr->util = util * SCHED_CAPACITY_SCALE / curr->cap;

        if (best == curr ||
            cass_cpu_better(curr, best, p_util, uc_min,
                            this_cpu, prev_cpu, sync)) {
            best = curr;
            cidx ^= 1;
        }
    }

    rcu_read_unlock();
    return best->cpu;
}'''
ca = replace_function(
    ca,
    "static int cass_best_cpu(struct task_struct *p, int prev_cpu, bool sync)",
    new_best,
)

for forbidden in ("cpu_util_rt(", "cpu_util_dl(", "cpu_util_irq(", "hard_util", "eff_util"):
    if forbidden in ca:
        raise SystemExit(f"CASS P3 reintroduced forbidden mixed accounting: {forbidden}")

cass.write_text(ca)


# ---------------------------------------------------------------------------
# UCLAMP + WALT schedutil efficiency P2
#
# Modern uclamp uses a static key specifically so the hot frequency path is
# nearly free until userspace actually opts into clamps. Keep that property in
# the helper itself, not just at selected callers.
# ---------------------------------------------------------------------------
sh = sched_h.read_text()
uclamp_anchor = """static __always_inline
unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,
                                  struct task_struct *p)
{
    unsigned long min_util = READ_ONCE(rq->uclamp[UCLAMP_MIN].value);
"""
if uclamp_anchor not in sh:
    # Vendor source uses tabs in this declaration.
    uclamp_anchor = """static __always_inline
unsigned long uclamp_rq_util_with(struct rq *rq, unsigned long util,
\t\t\t\t  struct task_struct *p)
{
\tunsigned long min_util = READ_ONCE(rq->uclamp[UCLAMP_MIN].value);
"""
uclamp_repl = uclamp_anchor.replace(
    "{\n",
    "{\n\tif (likely(!uclamp_is_used()))\n\t\treturn util;\n\n",
    1,
)
if uclamp_anchor not in sh:
    raise SystemExit("uclamp_rq_util_with anchor missing")
sh = sh.replace(uclamp_anchor, uclamp_repl, 1)
sched_h.write_text(sh)


# ---------------------------------------------------------------------------
# WALT schedutil efficiency P2
#
# Use the current scheduler timestamp when deciding whether a sibling CPU's
# utilization sample is stale. Basing the decision on the previous frequency
# update can retain stale WALT demand longer than necessary when the policy
# does not change frequency, which can keep a shared cluster elevated.
# ---------------------------------------------------------------------------
sg = sugov.read_text()
sg = replace_once(
    sg,
    """\tu64 last_freq_update_time = sg_policy->last_freq_update_time;
\tunsigned long util = 0, max = 1;
""",
    """\tunsigned long util = 0, max = 1;
""",
    "schedutil stale-time local removal",
)

sg = replace_once(
    sg,
    """\t\tdelta_ns = last_freq_update_time - j_sg_cpu->last_update;
\t\tif (delta_ns > stale_ns) {
\t\t\tsugov_iowait_reset(j_sg_cpu, last_freq_update_time,
\t\t\t\t\t   false);
\t\t\tcontinue;
\t\t}
""",
    """\t\t/* A52 WALT schedutil P2: expire stale sibling demand by wall time. */
\t\tdelta_ns = time - j_sg_cpu->last_update;
\t\tif (delta_ns > stale_ns) {
\t\t\tsugov_iowait_reset(j_sg_cpu, time, false);
\t\t\tcontinue;
\t\t}
""",
    "schedutil stale sibling accounting",
)

sugov.write_text(sg)


# ---------------------------------------------------------------------------
# Final structural audits.
# ---------------------------------------------------------------------------
checks = {
    vmscan: [
        "A52 MGLRU Efficiency P4: clear seed PTE before look-around bailouts",
        "young = 1;",
        "if (addr == pvmw->address)",
        "if (!pte_young(pte[i]) && !has_notifiers)",
    ],
    cass: [
        "unsigned long cap_max;",
        "cass_walt_cpu_util(int cpu, int this_cpu, bool sync)",
        "util -= min(util, task_util(current));",
        "if (cpu != task_cpu(p))",
        "uc_min = uclamp_eff_value(p, UCLAMP_MIN);",
        "uc_max = uclamp_eff_value(p, UCLAMP_MAX);",
        "A52 CASS P3:",
    ],
    sched_h: [
        "if (likely(!uclamp_is_used()))",
        "return util;",
    ],
    sugov: [
        "A52 WALT schedutil P2: expire stale sibling demand by wall time.",
        "delta_ns = time - j_sg_cpu->last_update;",
        "sugov_iowait_reset(j_sg_cpu, time, false);",
    ],
}
for path_obj, needles in checks.items():
    txt = path_obj.read_text()
    for needle in needles:
        if needle not in txt:
            raise SystemExit(f"{path_obj}: missing efficiency element: {needle!r}")

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "a52-efficiency-p1.txt").write_text(
    "baseline=35249413635-clean\n"
    "mglru=seed-clear-before-bailout+cached-notifier-fastpath\n"
    "cass=uclamp-fit+sync-waker-subtraction+no-wake-task-double-count+boost-aware-idle\n"
    "cass_mixed_rt_dl_irq_accounting=forbidden\n"
    "uclamp=static-key-fastpath-in-rq-helper\n"
    "walt_schedutil=wall-time-stale-sibling-expiry\n"
    "gpu_uv=p1+p2+p3-unchanged\n"
    "bt_custom=absent\n"
    "rcu_custom=absent\n"
)

print("A52 efficiency P1 applied")
print("mglru=efficiency-p4-seed-clear")
print("cass=efficiency-p3-uclamp-fit-sync-waker")
print("uclamp=static-key-fastpath")
print("walt_schedutil=stale-sibling-walltime")
