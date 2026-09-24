#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 QCOM LPM P1: raw idle locks + short-idle tick + hotpath cleanup"
RAW_LOCK_SRC = "111233749b135174a9d2195c400a39bf02733f08"
TICK_SRC = "83073360b566c5fe496d743b3029516ac6baec47"
DEBUG_SRC = "c0690373188c0b3c8d52ab1c4ca69010701d02e1"
STATS_SRC = "d90771fb5142948594e017c9f0e5334434c4514e"
BATTERY_MARKER = "A52 P148 BATTERY: Qualcomm LPM + WALT/schedutil correctness"
IPI_SRC = "c153dfba1ab33cbb48a8a1154e8a72cbba6d4b40"
IRQWORK_SRC = "9af2c23239abec9d846c4a24125ab910dcb34ec5"
RESERVATION_SRC = "dc23aae552c1579a8eb258ade569814544e8a745"
FBT_UCLAMP_SRC = "22b04fa8056494bbaa43a218635162b338aeb15f"
STARTCPU_UCLAMP_SRC = "02ee287af9e317ef70cc41ca6fa0d7bfc376c3d0"
WALT_ASYM_SRC = "f6c1bf1f0258ab346e5fcbe77508662ba2f6b527"
LPM_QOS_SRC = "b4d4fb25b9fbf1c40d46a8a1abfec57b2ce1ac70"
LPM_PROBE_SRC = "41e4398ad7af12646c43825cded8b6e6b39c4eda"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def remove_once(text: str, old: str, label: str) -> str:
    return replace_once(text, old, "", label)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    lpm = root / "drivers/cpuidle/lpm-levels.c"
    lpm_of = root / "drivers/cpuidle/lpm-levels-of.c"
    lpm_h = root / "drivers/cpuidle/lpm-levels.h"
    cfg = root / "arch/arm64/configs/a52xq_defconfig"
    sugov = root / "kernel/sched/cpufreq_schedutil.c"
    fair = root / "kernel/sched/fair.c"
    walt = root / "kernel/sched/walt.c"
    topology = root / "kernel/sched/topology.c"
    core = root / "kernel/sched/core.c"

    for path in (lpm, lpm_of, lpm_h, cfg, sugov, fair, walt, topology, core):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    c = lpm.read_text()
    o = lpm_of.read_text()
    h = lpm_h.read_text()
    dc = cfg.read_text()
    sg = sugov.read_text()
    fa = fair.read_text()
    wa = walt.read_text()
    to = topology.read_text()
    cc = core.read_text()

    # Require the exact Qualcomm vendor architecture that is actually active
    # on A52. P148 deliberately does not replace it with generic TEO.
    for needle in (
        'static struct cpuidle_governor lpm_governor = {',
        '.name =\t\t"qcom",',
        '.rating =\t30,',
        'lpm_cpu->drv->name = "msm_idle";',
        'static int cpu_power_select(struct cpuidle_device *dev,',
        'predicted = lpm_cpuidle_predict(dev, cpu,',
        'pred_mode = cluster_predict(cluster, &pred_us);',
        'set_update_ipi_history_callback(update_ipi_history);',
        'sched_lpm_disallowed_time(cpu)',
    ):
        if needle not in c:
            raise SystemExit(f"Qualcomm LPM baseline missing: {needle}")

    if "CONFIG_NO_HZ_IDLE=y" not in dc:
        raise SystemExit("NO_HZ_IDLE must remain enabled")
    if "# CONFIG_MSM_IDLE_STATS is not set" not in dc:
        raise SystemExit("expected A52 production CONFIG_MSM_IDLE_STATS=n baseline")

    for needle in (
        "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
        "A52 SCHEDUTIL P2: Android17 79443a7e limits_changed synchronization",
        "static void sugov_deferred_update(",
        "irq_work_queue(&sg_policy->irq_work);",
        "sg_policy->cached_raw_freq = sg_policy->prev_cached_raw_freq;",
        "if (sg_policy->next_freq == next_freq)",
    ):
        if needle not in sg:
            raise SystemExit(f"schedutil baseline missing: {needle}")

    for needle, blob in (
        ("void clear_walt_request(int cpu)", wa),
        ("stop_one_cpu_nowait(cpu_of(busiest)", fa),
        ("mark_reserved(this_cpu);", fa),
    ):
        if needle not in blob:
            raise SystemExit(f"WALT/fair baseline missing: {needle}")

    for needle in (
        "static inline unsigned long uclamp_task_util(struct task_struct *p)",
        "new_util = wake_util + task_util_est(p);",
        "bool boosted = schedtune_task_boost(p) > 0 ||",
    ):
        if needle not in fa:
            raise SystemExit(f"uclamp/WALT placement baseline missing: {needle}")

    boost_start = cc.find("bool uclamp_boosted(struct task_struct *p)")
    if boost_start < 0:
        raise SystemExit("uclamp_boosted helper missing")
    boost_end = cc.find("bool uclamp_latency_sensitive(struct task_struct *p)", boost_start)
    if boost_end < 0 or "schedtune_task_boost(p) > 0" not in cc[boost_start:boost_end]:
        raise SystemExit("uclamp_boosted no longer preserves SchedTune compatibility")

    # ------------------------------------------------------------------
    # 1) Raw spin locks for the cluster synchronization path.
    # The cpuidle loop runs with IRQs disabled, so the synchronization lock
    # used from idle context should be a raw spin lock.
    # ------------------------------------------------------------------
    h = replace_once(
        h,
        "\tspinlock_t sync_lock;\n",
        "\traw_spinlock_t sync_lock;\n",
        "sync_lock type",
    )
    o = replace_once(
        o,
        "\tspin_lock_init(&c->sync_lock);\n",
        "\traw_spin_lock_init(&c->sync_lock);\n",
        "sync_lock init",
    )

    # These are only the LPM cluster synchronization locks; leave unrelated
    # spin locks alone.
    c = c.replace("spin_lock(&cluster->sync_lock);",
                  "raw_spin_lock(&cluster->sync_lock);")
    c = c.replace("spin_unlock(&cluster->sync_lock);",
                  "raw_spin_unlock(&cluster->sync_lock);")
    c = c.replace("spin_lock(&p->sync_lock);",
                  "raw_spin_lock(&p->sync_lock);")
    c = c.replace("spin_unlock(&p->sync_lock);",
                  "raw_spin_unlock(&p->sync_lock);")

    # ------------------------------------------------------------------
    # 2) Do not stop the scheduler tick for an idle interval shorter than
    # one tick. Adapt the later fix to reuse Qualcomm's existing sleep_us
    # calculation instead of calling tick_nohz_get_sleep_length() twice.
    # ------------------------------------------------------------------
    c = replace_once(
        c,
        """static int cpu_power_select(struct cpuidle_device *dev,
\t\tstruct lpm_cpu *cpu)
{""",
        """static int cpu_power_select(struct cpuidle_device *dev,
\t\tstruct lpm_cpu *cpu, bool *stop_tick)
{""",
        "cpu_power_select signature",
    )

    c = replace_once(
        c,
        """\ts64 sleep_us = ktime_to_us(tick_nohz_get_sleep_length(&delta_next));
\tuint32_t modified_time_us = 0;""",
        f"""\ts64 sleep_us = ktime_to_us(tick_nohz_get_sleep_length(&delta_next));
\t/*
\t * {MARKER}
\t *
\t * Reuse the Qualcomm governor's already-computed sleep horizon.  If it
\t * is no longer than one scheduler tick, stopping the tick has no power
\t * benefit and only adds nohz transition work.
\t */
#ifdef CONFIG_NO_HZ_COMMON
\tif (sleep_us <= (s64)(TICK_NSEC / NSEC_PER_USEC))
\t\t*stop_tick = false;
#endif
\tuint32_t modified_time_us = 0;""",
        "short-idle tick policy",
    )

    c = replace_once(
        c,
        "return cpu_power_select(dev, cpu);",
        "return cpu_power_select(dev, cpu, stop_tick);",
        "qcom select stop_tick plumbing",
    )

    # ------------------------------------------------------------------
    # 3) Remove Qualcomm's dedicated LPM debug ring/minidump logger.
    # It takes a spin lock and arch counter in idle enter/exit paths. Keep
    # normal tracepoints and functional LPM stats APIs; only this extra ring
    # is removed.
    # ------------------------------------------------------------------
    c = remove_once(c, "#include <soc/qcom/minidump.h>\n", "minidump include")

    c = remove_once(
        c,
        """enum {
\tMSM_LPM_LVL_DBG_SUSPEND_LIMITS = BIT(0),
\tMSM_LPM_LVL_DBG_IDLE_LIMITS = BIT(1),
};

enum debug_event {
\tCPU_ENTER,
\tCPU_EXIT,
\tCLUSTER_ENTER,
\tCLUSTER_EXIT,
\tCPU_HP_STARTING,
\tCPU_HP_DYING,
};

struct lpm_debug {
\tu64 time;
\tenum debug_event evt;
\tint cpu;
\tuint32_t arg1;
\tuint32_t arg2;
\tuint32_t arg3;
\tuint32_t arg4;
};

""",
        "debug ring types",
    )

    c = remove_once(
        c,
        """static struct lpm_debug *lpm_debug;
static phys_addr_t lpm_debug_phys;
static const int num_dbg_elements = 0x100;
""",
        "debug ring storage",
    )

    start = c.find("static void update_debug_pc_event(")
    if start < 0:
        raise SystemExit("debug event helper missing")
    brace = c.find("{", start)
    depth = 0
    end = -1
    for i in range(brace, len(c)):
        if c[i] == "{":
            depth += 1
        elif c[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise SystemExit("unterminated update_debug_pc_event")
    while end < len(c) and c[end] == "\n":
        end += 1
    c = c[:start] + c[end:]

    for block, label in (
        ("""\tupdate_debug_pc_event(CPU_HP_DYING, cpu,
\t\t\t\tcluster->num_children_in_sync.bits[0],
\t\t\t\tcluster->child_cpus.bits[0], false);
""", "CPU hotplug dying debug event"),
        ("""\tupdate_debug_pc_event(CPU_HP_STARTING, cpu,
\t\t\t\tcluster->num_children_in_sync.bits[0],
\t\t\t\tcluster->child_cpus.bits[0], false);
""", "CPU hotplug starting debug event"),
        ("""\t\tupdate_debug_pc_event(CLUSTER_ENTER, idx,
\t\t\tcluster->num_children_in_sync.bits[0],
\t\t\tcluster->child_cpus.bits[0], from_idle);
""", "cluster enter debug event"),
        ("""\tupdate_debug_pc_event(CLUSTER_EXIT, cluster->last_level,
\t\t\tcluster->num_children_in_sync.bits[0],
\t\t\tcluster->child_cpus.bits[0], from_idle);
""", "cluster exit debug event"),
        ("""\tupdate_debug_pc_event(CPU_ENTER, state_id,
\t\t\t0xdeaffeed, 0xdeaffeed, from_idle);
""", "CPU enter debug event"),
        ("""\tupdate_debug_pc_event(CPU_EXIT, state_id,
\t\t\tsuccess, 0xdeaffeed, from_idle);
""", "CPU exit debug event"),
    ):
        c = remove_once(c, block, label)

    c = replace_once(
        c,
        """static int lpm_probe(struct platform_device *pdev)
{
\tint ret;
\tint size;
\tunsigned int cpu;
\tstruct hrtimer *cpu_histtimer;
\tstruct kobject *module_kobj = NULL;
\tstruct md_region md_entry;
""",
        """static int lpm_probe(struct platform_device *pdev)
{
\tint ret;
\tunsigned int cpu;
\tstruct hrtimer *cpu_histtimer;
\tstruct kobject *module_kobj = NULL;
""",
        "lpm probe debug vars",
    )

    c = remove_once(
        c,
        """\tsize = num_dbg_elements * sizeof(struct lpm_debug);
\tlpm_debug = dma_alloc_coherent(&pdev->dev, size,
\t\t\t&lpm_debug_phys, GFP_KERNEL);

""",
        "debug ring DMA allocation",
    )

    # Samsung source drops vary slightly in the wording/spacing of this
    # registration block, so remove it by semantic anchors instead.
    k = c.find('"KLPMDEBUG"')
    if k < 0:
        raise SystemExit("debug ring KLPMDEBUG registration missing")
    reg_start = c.rfind("\n\t/*", 0, k)
    if reg_start < 0:
        reg_start = c.rfind("\n\tstrlcpy(", 0, k)
    call = c.find("msm_minidump_add_region", k)
    if reg_start < 0 or call < 0:
        raise SystemExit("debug ring minidump structural anchors missing")
    reg_end = c.find("\n", call)
    # Include a following pr_info() statement if present.
    nxt = c.find("\n", reg_end + 1)
    if nxt > 0 and "pr_info(" in c[reg_end + 1:nxt]:
        reg_end = nxt
        nxt2 = c.find("\n", reg_end + 1)
        if nxt2 > 0 and c[reg_end + 1:nxt2].lstrip().startswith('"'):
            reg_end = nxt2
    while reg_end + 1 < len(c) and c[reg_end + 1] == "\n":
        reg_end += 1
    c = c[:reg_start + 1] + c[reg_end + 1:]


    # ------------------------------------------------------------------
    # 4) CONFIG_MSM_IDLE_STATS=n hardening. The A52 ships with stats off.
    # Keep the stub APIs, but never dereference cluster->stats in that config.
    # ------------------------------------------------------------------
    c = c.replace(
        "if (!IS_ERR_OR_NULL(cluster->stats))\n\t\tcluster->stats->sleep_time = start_time;",
        "if (!IS_ERR_OR_NULL(cluster->stats) &&\n"
        "\t    IS_ENABLED(CONFIG_MSM_IDLE_STATS))\n"
        "\t\tcluster->stats->sleep_time = start_time;",
        1,
    )
    c = c.replace(
        "if (!IS_ERR_OR_NULL(cluster->stats))\n\t\tcluster->stats->sleep_time = 0;",
        "if (!IS_ERR_OR_NULL(cluster->stats) &&\n"
        "\t    IS_ENABLED(CONFIG_MSM_IDLE_STATS))\n"
        "\t\tcluster->stats->sleep_time = 0;",
        1,
    )
    c = c.replace(
        "if (!IS_ERR_OR_NULL(cluster->stats) && cluster->stats->sleep_time)",
        "if (!IS_ERR_OR_NULL(cluster->stats) &&\n"
        "\t    IS_ENABLED(CONFIG_MSM_IDLE_STATS) && cluster->stats->sleep_time)",
        1,
    )
    c = c.replace(
        "if (IS_ERR_OR_NULL(cl->stats))\n\t\tpr_info(\"Cluster (%s) stats not registered\\n\",",
        "if (IS_ERR_OR_NULL(cl->stats) && IS_ENABLED(CONFIG_MSM_IDLE_STATS))\n"
        "\t\tpr_info(\"Cluster (%s) stats not registered\\n\",",
        1,
    )

    # ------------------------------------------------------------------
    # 5) Qualcomm SM8350: do not enter core LPM when an IPI is already
    # pending. This closes the race after governor selection and before PSCI.
    # ------------------------------------------------------------------
    c = replace_once(
        c,
        """\tif (need_resched())
\t\tgoto exit;
""",
        """\t/* A52 P148: Qualcomm core-LPM pending-IPI race fix. */
\tif (need_resched() || is_IPI_pending(cpumask_of(dev->cpu)))
\t\tgoto exit;
""",
        "core LPM pending IPI check",
    )

    # ------------------------------------------------------------------
    # 6) Qualcomm schedutil: deferred irq_work must not get stranded on an
    # offline callback CPU. Queue it on any online CPU when necessary.
    # ------------------------------------------------------------------
    deferred_anchor = """static void sugov_deferred_update(struct sugov_policy *sg_policy, u64 time,
\t\t\t\t  unsigned int next_freq)
{"""
    if deferred_anchor not in sg:
        raise SystemExit("sugov_deferred_update anchor missing")

    irq_helper = f"""/*
 * {BATTERY_MARKER}
 * Qualcomm {IRQWORK_SRC}: keep deferred WALT/schedutil irq_work off offline
 * CPUs without changing frequency policy or rate-limit values.
 */
static inline void a52_sugov_irq_work_queue(struct irq_work *work)
{{
#ifdef CONFIG_SCHED_WALT
\tint cpu = raw_smp_processor_id();

\tif (likely(cpu_online(cpu)))
\t\tirq_work_queue(work);
\telse {{
\t\tcpu = cpumask_any(cpu_online_mask);
\t\tif (cpu < nr_cpu_ids)
\t\t\tirq_work_queue_on(work, cpu);
\t}}
#else
\tirq_work_queue(work);
#endif
}}

""" + deferred_anchor
    sg = replace_once(sg, deferred_anchor, irq_helper,
                      "schedutil online irq-work helper")
    sg = replace_once(
        sg,
        "\tirq_work_queue(&sg_policy->irq_work);\n",
        "\ta52_sugov_irq_work_queue(&sg_policy->irq_work);\n",
        "schedutil deferred irq-work queue",
    )

    # ------------------------------------------------------------------
    # 7) Qualcomm WALT reservation correctness. stop_one_cpu_nowait() is a
    # bool in this exact 4.19 tree: false means the stopper work was not
    # queued, so undo the reservation immediately in that case.
    # ------------------------------------------------------------------
    fa = replace_once(
        fa,
        """\t\t\tif (active_balance) {
\t\t\t\tstop_one_cpu_nowait(cpu_of(busiest),
\t\t\t\t\tactive_load_balance_cpu_stop, busiest,
\t\t\t\t\t&busiest->active_balance_work);
\t\t\t\t*continue_balancing = 0;
\t\t\t}
""",
        """\t\t\tif (active_balance) {
\t\t\t\tbool queued;

\t\t\t\tqueued = stop_one_cpu_nowait(cpu_of(busiest),
\t\t\t\t\tactive_load_balance_cpu_stop, busiest,
\t\t\t\t\t&busiest->active_balance_work);
\t\t\t\tif (!queued) {
\t\t\t\t\tclear_reserved(this_cpu);
\t\t\t\t\tbusiest->active_balance = 0;
\t\t\t\t\tactive_balance = 0;
\t\t\t\t}
\t\t\t\t*continue_balancing = 0;
\t\t\t}
""",
        "WALT active-balance queue failure cleanup",
    )

    wa = replace_once(
        wa,
        """\t\traw_spin_lock_irqsave(&rq->lock, flags);
\t\tif (rq->push_task) {
\t\t\tclear_reserved(rq->push_cpu);
\t\t\tpush_task = rq->push_task;
\t\t\trq->push_task = NULL;
\t\t}
\t\trq->active_balance = 0;
""",
        f"""\t\traw_spin_lock_irqsave(&rq->lock, flags);
\t\tif (rq->push_task) {{
\t\t\tpush_task = rq->push_task;
\t\t\trq->push_task = NULL;
\t\t}}
\t\t/* Qualcomm {RESERVATION_SRC}: clear this independently of
\t\t * push_task, which may have changed before we acquired rq->lock.
\t\t */
\t\tclear_reserved(rq->push_cpu);
\t\trq->active_balance = 0;
""",
        "WALT stale reservation clear",
    )

    # ------------------------------------------------------------------
    # 8) Qualcomm FBT placement: use task utilization after UCLAMP_MIN/MAX
    # instead of raw task_util_est(). This avoids waking a larger CPU for a
    # task whose explicit UCLAMP_MAX says otherwise.
    # ------------------------------------------------------------------
    fa = replace_once(
        fa,
        """\t\t\twake_util = cpu_util_without(i, p);
\t\t\tnew_util = wake_util + task_util_est(p);
""",
        f"""\t\t\twake_util = cpu_util_without(i, p);
\t\t\t/* Qualcomm {FBT_UCLAMP_SRC}: honor task uclamp in FBT. */
\t\t\tnew_util = wake_util + uclamp_task_util(p);
""",
        "FBT uclamp task utilization",
    )

    # ------------------------------------------------------------------
    # 9) Qualcomm start-CPU selection: explicit UCLAMP_MIN boost must affect
    # the initial capacity group. Our uclamp_boosted() helper already retains
    # Samsung SchedTune boost semantics, so this does not drop legacy boosts.
    # ------------------------------------------------------------------
    fa = replace_once(
        fa,
        """\tbool boosted = schedtune_task_boost(p) > 0 ||
\t\t\ttask_boost_policy(p) == SCHED_BOOST_ON_BIG ||
\t\t\ttask_boost == TASK_BOOST_ON_MID;
""",
        f"""\t/* Qualcomm {STARTCPU_UCLAMP_SRC}: include explicit uclamp boost. */
\tbool boosted = uclamp_boosted(p) ||
\t\t\ttask_boost_policy(p) == SCHED_BOOST_ON_BIG ||
\t\t\ttask_boost == TASK_BOOST_ON_MID;
""",
        "get_start_cpu uclamp boost",
    )

    # ------------------------------------------------------------------
    # 10) Qualcomm WALT/EAS hotplug correctness: do not fabricate an
    # asymmetric sched_domain merely to keep EAS alive. WALT may keep the
    # energy perf domains while the static-key topology pointer stays truthful.
    # ------------------------------------------------------------------
    to = replace_once(
        to,
        """\t/* EAS is enabled for asymmetric CPU capacity topologies. */
\tif (!per_cpu(sd_asym_cpucapacity, cpu)) {
\t\tif (sched_debug()) {
\t\t\tpr_info("rd %*pbl: CPUs do not have asymmetric capacities\\n",
\t\t\t\t\tcpumask_pr_args(cpu_map));
\t\t}
\t\tgoto free;
\t}
""",
        f"""\t/* Qualcomm {WALT_ASYM_SRC}: WALT keeps EAS perf domains alive
\t * without abusing the sched_asym_cpucapacity static-key topology.
\t */
#ifndef CONFIG_SCHED_WALT
\tif (!per_cpu(sd_asym_cpucapacity, cpu)) {{
\t\tif (sched_debug()) {{
\t\t\tpr_info("rd %*pbl: CPUs do not have asymmetric capacities\\n",
\t\t\t\t\tcpumask_pr_args(cpu_map));
\t\t}}
\t\tgoto free;
\t}}
#endif
""",
        "WALT EAS asymmetry gate",
    )

    to = replace_once(
        to,
        """\tsd = lowest_flag_domain(cpu, SD_ASYM_CPUCAPACITY);
\t/*
\t * EAS gets disabled when there are no asymmetric capacity
\t * CPUs in the system. For example, all big CPUs are
\t * hotplugged out on a b.L system. We want EAS enabled
\t * all the time to get both power and perf benefits. So,
\t * lets assign sd_asym_cpucapacity to the only available
\t * sched domain. This is also important for a single cluster
\t * systems which wants to use EAS.
\t *
\t * Setting sd_asym_cpucapacity() to a sched domain which
\t * has all symmetric capacity CPUs is technically incorrect but
\t * works well for us in getting EAS enabled all the time.
\t */
\tif (!sd)
\t\tsd = cpu_rq(cpu)->sd;

\trcu_assign_pointer(per_cpu(sd_asym_cpucapacity, cpu), sd);
""",
        f"""\tsd = lowest_flag_domain(cpu, SD_ASYM_CPUCAPACITY);
\t/* Qualcomm {WALT_ASYM_SRC}: keep this pointer topology-correct. */
\trcu_assign_pointer(per_cpu(sd_asym_cpucapacity, cpu), sd);
""",
        "fake asymmetric sched_domain removal",
    )

    # ------------------------------------------------------------------
    # 11) Qualcomm LPM cluster PM-QoS: isolated CPUs should not constrain the
    # whole cluster's idle depth. Adapt the newer behavior to Samsung's
    # existing per-CPU PM_QOS_CPU_DMA_LATENCY implementation.
    # ------------------------------------------------------------------
    cluster_anchor = """static int cluster_select(struct lpm_cluster *cluster, bool from_idle,
\t\t\t\t\t\t\tint *ispred)
"""
    qos_helper = f"""/*
 * Qualcomm {LPM_QOS_SRC}: isolated CPUs must not hold the rest of the cluster
 * in a shallower LPM state.
 */
static uint32_t a52_lpm_cluster_qos(const struct cpumask *mask)
{{
\tuint32_t latency = PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE;
\tint cpu;

\tfor_each_cpu(cpu, mask) {{
\t\tuint32_t value;

\t\t/*
\t\t * Test the exported scheduler isolation mask directly. This avoids
\t\t * configuration-local vendor isolation wrappers and uses the actual
\t\t * WALT/core_ctl isolation state maintained by the scheduler.
\t\t */
\t\tif (cpumask_test_cpu(cpu, cpu_isolated_mask))
\t\t\tcontinue;

\t\tvalue = pm_qos_request_for_cpu(PM_QOS_CPU_DMA_LATENCY, cpu);
\t\tif (value < latency)
\t\t\tlatency = value;
\t}}

\treturn latency;
}}

""" + cluster_anchor
    c = replace_once(c, cluster_anchor, qos_helper,
                     "isolated-core cluster PM-QoS helper")
    c = replace_once(
        c,
        """\tif (cpumask_and(&mask, cpu_online_mask, &cluster->child_cpus))
\t\tlatency_us = pm_qos_request_for_cpumask(PM_QOS_CPU_DMA_LATENCY,
\t\t\t\t\t\t\t&mask);
""",
        """\tif (cpumask_and(&mask, cpu_online_mask, &cluster->child_cpus))
\t\tlatency_us = a52_lpm_cluster_qos(&mask);
""",
        "cluster PM-QoS isolated-core aggregation",
    )

    # ------------------------------------------------------------------
    # 12) Qualcomm LPM probe correctness: only publish suspend/s2idle ops
    # after all probe steps that can fail. P148 already removes the dedicated
    # DMA debug ring, so the related allocation leak is eliminated entirely.
    # ------------------------------------------------------------------
    c = replace_once(
        c,
        """\tsuspend_set_ops(&lpm_suspend_ops);
\ts2idle_set_ops(&lpm_s2idle_ops);
""",
        "",
        "early suspend/s2idle ops removal",
    )
    c = replace_once(
        c,
        """\tset_update_ipi_history_callback(update_ipi_history);

\treturn 0;
failed:
""",
        f"""\tset_update_ipi_history_callback(update_ipi_history);

\t/* Qualcomm {LPM_PROBE_SRC}: publish PM callbacks at no-fail probe tail. */
\tsuspend_set_ops(&lpm_suspend_ops);
\ts2idle_set_ops(&lpm_s2idle_ops);

\treturn 0;
failed:
""",
        "late suspend/s2idle ops publication",
    )

    # Later Qualcomm WALT fixed raw-frequency caching around rejected down-rate
    # transitions. Samsung already carries the equivalent restore-and-recompute
    # behavior, and already suppresses unchanged resolved frequencies.
    for needle in (
        "if (sg_policy->next_freq == next_freq)",
        "sg_policy->cached_raw_freq = sg_policy->prev_cached_raw_freq;",
        "if (freq == sg_policy->cached_raw_freq && !sg_policy->need_freq_update)",
    ):
        if needle not in sg:
            raise SystemExit(f"schedutil cache correctness semantic missing: {needle}")

    lpm.write_text(c)
    lpm_of.write_text(o)
    lpm_h.write_text(h)
    sugov.write_text(sg)
    fair.write_text(fa)
    walt.write_text(wa)
    topology.write_text(to)

    C = lpm.read_text()
    O = lpm_of.read_text()
    H = lpm_h.read_text()
    SG = sugov.read_text()
    FA = fair.read_text()
    WA = walt.read_text()
    TO = topology.read_text()

    required = (
        MARKER,
        "raw_spin_lock(&cluster->sync_lock);",
        "raw_spin_unlock(&cluster->sync_lock);",
        "raw_spin_lock(&p->sync_lock);",
        "raw_spin_unlock(&p->sync_lock);",
        "cpu_power_select(dev, cpu, stop_tick)",
        "sleep_us <= (s64)(TICK_NSEC / NSEC_PER_USEC)",
        "IS_ENABLED(CONFIG_MSM_IDLE_STATS)",
        '.name =\t\t"qcom",',
        '.rating =\t30,',
        'lpm_cpu->drv->name = "msm_idle";',
        "predicted = lpm_cpuidle_predict(dev, cpu,",
        "pred_mode = cluster_predict(cluster, &pred_us);",
        "is_IPI_pending(cpumask_of(dev->cpu))",
    )
    for needle in required:
        if needle not in C:
            raise SystemExit(f"audit failed: missing {needle}")

    if "raw_spin_lock_init(&c->sync_lock);" not in O:
        raise SystemExit("audit failed: raw sync_lock init missing")
    if "raw_spinlock_t sync_lock;" not in H:
        raise SystemExit("audit failed: raw sync_lock type missing")

    forbidden = (
        "update_debug_pc_event(",
        "struct lpm_debug",
        "lpm_debug_phys",
        "num_dbg_elements",
        "KLPMDEBUG",
        "#include <soc/qcom/minidump.h>",
    )
    for needle in forbidden:
        if needle in C:
            raise SystemExit(f"audit failed: stale code remains: {needle}")

    legacy_lock_lines = {
        "spin_lock(&cluster->sync_lock);",
        "spin_unlock(&cluster->sync_lock);",
        "spin_lock(&p->sync_lock);",
        "spin_unlock(&p->sync_lock);",
    }
    for line in C.splitlines():
        if line.strip() in legacy_lock_lines:
            raise SystemExit(f"audit failed: legacy idle lock remains: {line.strip()}")

    # Explicitly verify that policy knobs and prediction machinery were not
    # "optimized" away in this fix pack.
    for needle in (
        "static bool lpm_prediction = true;",
        "static bool lpm_ipi_prediction = true;",
        "DEFAULT_PREMATURE_CNT",
        "DEFAULT_STDDEV",
        "DEFAULT_TIMER_ADD",
    ):
        blob = C if needle.startswith("static bool") else H
        if needle not in blob:
            raise SystemExit(f"audit failed: Qualcomm policy changed: {needle}")

    for needle in (
        BATTERY_MARKER,
        "a52_sugov_irq_work_queue",
        "irq_work_queue_on(work, cpu)",
        "a52_sugov_irq_work_queue(&sg_policy->irq_work);",
        "sg_policy->cached_raw_freq = sg_policy->prev_cached_raw_freq;",
        "if (sg_policy->next_freq == next_freq)",
    ):
        if needle not in SG:
            raise SystemExit(f"audit failed: schedutil fix missing: {needle}")

    for needle in (
        "bool queued;",
        "queued = stop_one_cpu_nowait(cpu_of(busiest)",
        "clear_reserved(this_cpu);",
        "busiest->active_balance = 0;",
    ):
        if needle not in FA:
            raise SystemExit(f"audit failed: active-balance fix missing: {needle}")

    if "clear_reserved(rq->push_cpu);" not in WA:
        raise SystemExit("audit failed: stale WALT reservation clear missing")

    for needle in (
        "new_util = wake_util + uclamp_task_util(p);",
        "bool boosted = uclamp_boosted(p) ||",
    ):
        if needle not in FA:
            raise SystemExit(f"audit failed: uclamp placement fix missing: {needle}")

    if "sd = cpu_rq(cpu)->sd;" in TO[TO.find("static void update_top_cache_domain("):]:
        raise SystemExit("audit failed: fake asymmetric sched_domain remains")
    if "#ifndef CONFIG_SCHED_WALT" not in TO:
        raise SystemExit("audit failed: WALT EAS asymmetry gate missing")

    for needle in (
        "static uint32_t a52_lpm_cluster_qos(",
        "latency_us = a52_lpm_cluster_qos(&mask);",
        "if (cpumask_test_cpu(cpu, cpu_isolated_mask))",
    ):
        if needle not in C:
            raise SystemExit(f"audit failed: LPM QoS fix missing: {needle}")

    qos_start = C.find("static uint32_t a52_lpm_cluster_qos(")
    qos_end = C.find("static int cluster_select(", qos_start)
    if qos_start < 0 or qos_end < 0:
        raise SystemExit("audit failed: could not bound LPM QoS helper")
    qos_blob = C[qos_start:qos_end]
    qos_lines = {line.strip() for line in qos_blob.splitlines()}
    for forbidden_line in (
        "if (check_cpu_isolated(cpu))",
        "if (cpu_isolated(cpu))",
    ):
        if forbidden_line in qos_lines:
            raise SystemExit(
                f"audit failed: fragile isolation helper remains in QoS helper: {forbidden_line}"
            )
    if "if (cpumask_test_cpu(cpu, cpu_isolated_mask))" not in qos_lines:
        raise SystemExit(
            "audit failed: direct cpu_isolated_mask test missing from QoS helper"
        )

    if C.count("suspend_set_ops(&lpm_suspend_ops);") != 1:
        raise SystemExit("audit failed: suspend_set_ops must appear exactly once")
    if C.count("s2idle_set_ops(&lpm_s2idle_ops);") != 1:
        raise SystemExit("audit failed: s2idle_set_ops must appear exactly once")
    print("[audit] qcom governor rating 30 / msm_idle preserved: PASS")
    print("[audit] Qualcomm CPU + IPI + cluster prediction preserved: PASS")
    print("[audit] raw cluster synchronization locks: PASS")
    print("[audit] short-idle nohz tick preservation reuses existing sleep horizon: PASS")
    print("[audit] dedicated LPM debug ring/minidump hot-path logger removed: PASS")
    print("[audit] CONFIG_MSM_IDLE_STATS=n dereference hardening: PASS")
    print("[audit] core LPM pending-IPI race fix: PASS")
    print("[audit] schedutil deferred irq_work targets an online CPU: PASS")
    print("[audit] WALT stale active-balance reservation cleanup: PASS")
    print("[audit] newer raw-frequency cache/down-rate fixes already present: PASS")
    print("[audit] residency/latency thresholds and prediction tunables unchanged: PASS")
    print(f"[source] {RAW_LOCK_SRC}: raw idle synchronization locks")
    print(f"[source] {TICK_SRC}: do not stop tick when shorter than one tick")
    print(f"[source] {DEBUG_SRC}: remove measurable idle hot-path debug logging")
    print(f"[source] {STATS_SRC}: harden idle-stats-disabled path")
    print(f"[source] {IPI_SRC}: abort core LPM when an IPI is pending")
    print(f"[source] {IRQWORK_SRC}: queue schedutil irq_work on an online CPU")
    print(f"[source] {RESERVATION_SRC}: clear stale WALT CPU reservations")
    print(f"[source] {FBT_UCLAMP_SRC}: FBT honors task UCLAMP_MIN/MAX")
    print(f"[source] {STARTCPU_UCLAMP_SRC}: start CPU honors uclamp boost")
    print(f"[source] {WALT_ASYM_SRC}: WALT/EAS hotplug static-key correctness")
    print(f"[source] {LPM_QOS_SRC}: isolated CPUs do not constrain cluster LPM QoS")
    print(f"[source] {LPM_PROBE_SRC}: publish suspend ops only at successful probe tail")
    print("[done] A52 P148 Qualcomm LPM + WALT/schedutil battery fix pack applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
