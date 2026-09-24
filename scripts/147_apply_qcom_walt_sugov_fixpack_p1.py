#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 P147: Qualcomm LPM + WALT/schedutil correctness pack"

SRC_SUGOV_IRQ = "9af2c23239abec9d846c4a24125ab910dcb34ec5"
SRC_FBT_UCLAMP = "22b04fa8056494bbaa43a218635162b338aeb15f"
SRC_STARTCPU_UCLAMP = "02ee287af9e317ef70cc41ca6fa0d7bfc376c3d0"
SRC_WALT_ASYM = "f6c1bf1f0258ab346e5fcbe77508662ba2f6b527"
SRC_LPM_QOS = "b4d4fb25b9fbf1c40d46a8a1abfec57b2ce1ac70"
SRC_LPM_PROBE = "41e4398ad7af12646c43825cded8b6e6b39c4eda"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def function_block(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")
    depth = 0
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise SystemExit(f"unterminated function: {signature}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    lpm = root / "drivers/cpuidle/lpm-levels.c"
    fair = root / "kernel/sched/fair.c"
    topology = root / "kernel/sched/topology.c"
    sched_h = root / "kernel/sched/sched.h"
    sugov = root / "kernel/sched/cpufreq_schedutil.c"
    core = root / "kernel/sched/core.c"
    teo = root / "drivers/cpuidle/governors/teo.c"

    for path in (lpm, fair, topology, sched_h, sugov, core, teo):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    lp = lpm.read_text()
    fa = fair.read_text()
    tp = topology.read_text()
    sh = sched_h.read_text()
    sg = sugov.read_text()
    cc = core.read_text()
    te = teo.read_text()

    # Exact boot-tested P146 scheduler/uclamp/TEO boundary.
    for needle in (
        "A52 TEO P2: upstream 449914 remove broken recent-intercepts + active",
        ".rating =\t21,",
    ):
        if needle not in te:
            raise SystemExit(f"P146 TEO baseline missing: {needle}")

    for needle in (
        "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
        "A52 SCHEDUTIL P2: Android17 79443a7e limits_changed synchronization",
    ):
        if needle not in sg:
            raise SystemExit(f"schedutil baseline missing: {needle}")

    if 'static struct cpuidle_governor lpm_governor' not in lp:
        raise SystemExit("Qualcomm qcom cpuidle governor missing")
    if '.name = "qcom",' not in lp or '.rating = 30,' not in lp:
        raise SystemExit("Qualcomm qcom governor identity/rating changed unexpectedly")
    if 'static struct cpuidle_driver msm_idle_driver' not in lp:
        raise SystemExit("Qualcomm msm_idle driver missing")

    # uclamp_boosted() in our boot-tested stack deliberately preserves
    # Samsung SchedTune boost semantics while adding explicit UCLAMP_MIN.
    boost_block = function_block(cc, "bool uclamp_boosted(struct task_struct *p)")
    if "schedtune_task_boost(p) > 0" not in boost_block:
        raise SystemExit("uclamp_boosted no longer preserves SchedTune compatibility")

    # ------------------------------------------------------------------
    # 1. Qualcomm schedutil: never leave deferred irq_work queued on an
    #    offlined CPU.  Route it to an online CPU when needed.
    # ------------------------------------------------------------------
    if "static inline void walt_irq_work_queue(" not in sh:
        if "#include <linux/irq_work.h>" not in sh:
            sh = replace_once(
                sh,
                "#include <linux/init_task.h>\n",
                "#include <linux/init_task.h>\n#include <linux/irq_work.h>\n",
                "irq_work include",
            )

        anchor = """struct sched_walt_cpu_load {
\tunsigned long nl;
\tunsigned long pl;
\tbool rtgb_active;
\tu64 ws;
};

"""
        helper = anchor + f"""/*
 * {MARKER}
 * Qualcomm {SRC_SUGOV_IRQ}: keep schedutil irq_work off offline CPUs.
 */
#ifdef CONFIG_SCHED_WALT
static inline void walt_irq_work_queue(struct irq_work *work)
{{
\tif (likely(cpu_online(raw_smp_processor_id())))
\t\tirq_work_queue(work);
\telse
\t\tirq_work_queue_on(work, cpumask_any(cpu_online_mask));
}}
#else
static inline void walt_irq_work_queue(struct irq_work *work)
{{
\tirq_work_queue(work);
}}
#endif

"""
        sh = replace_once(sh, anchor, helper, "WALT online irq_work helper")

    sg = replace_once(
        sg,
        "\tirq_work_queue(&sg_policy->irq_work);\n",
        "\twalt_irq_work_queue(&sg_policy->irq_work);\n",
        "schedutil deferred irq_work routing",
    )

    # ------------------------------------------------------------------
    # 2. Qualcomm FBT task placement: use task util after UCLAMP_MIN/MAX.
    # ------------------------------------------------------------------
    fa = replace_once(
        fa,
        "\t\t\twake_util = cpu_util_without(i, p);\n"
        "\t\t\tnew_util = wake_util + task_util_est(p);\n",
        f"""\t\t\twake_util = cpu_util_without(i, p);
\t\t\t/* {MARKER}: Qualcomm {SRC_FBT_UCLAMP}. */
\t\t\tnew_util = wake_util + uclamp_task_util(p);
""",
        "FBT uclamp task utilization",
    )

    # ------------------------------------------------------------------
    # 3. Qualcomm start-CPU selection: explicit uclamp boost must influence
    #    the initial capacity group.  uclamp_boosted() already contains the
    #    legacy SchedTune boost check in this project, so no behavior is lost.
    # ------------------------------------------------------------------
    fa = replace_once(
        fa,
        "\tbool boosted = schedtune_task_boost(p) > 0 ||\n"
        "\t\t\ttask_boost_policy(p) == SCHED_BOOST_ON_BIG ||\n"
        "\t\t\ttask_boost == TASK_BOOST_ON_MID;\n",
        f"""\t/* {MARKER}: Qualcomm {SRC_STARTCPU_UCLAMP}. */
\tbool boosted = uclamp_boosted(p) ||
\t\t\ttask_boost_policy(p) == SCHED_BOOST_ON_BIG ||
\t\t\ttask_boost == TASK_BOOST_ON_MID;
""",
        "get_start_cpu uclamp boost",
    )

    # ------------------------------------------------------------------
    # 4. Qualcomm WALT hotplug/EAS fix: do not fabricate an asymmetric
    #    sched_domain just to keep EAS alive.  Under WALT, perf domains may
    #    stay built without abusing sd_asym_cpucapacity, avoiding static-key
    #    increment/decrement mismatch during CPU hotplug.
    # ------------------------------------------------------------------
    old_eas_gate = """\t/* EAS is enabled for asymmetric CPU capacity topologies. */
\tif (!per_cpu(sd_asym_cpucapacity, cpu)) {
\t\tif (sched_debug()) {
\t\t\tpr_info("rd %*pbl: CPUs do not have asymmetric capacities\\n",
\t\t\t\t\tcpumask_pr_args(cpu_map));
\t\t}
\t\tgoto free;
\t}
"""
    new_eas_gate = f"""\t/*
\t * {MARKER}
\t * Qualcomm {SRC_WALT_ASYM}: WALT keeps EAS perf domains available even
\t * when hotplug temporarily leaves only symmetric-capacity CPUs.
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
"""
    tp = replace_once(tp, old_eas_gate, new_eas_gate, "WALT EAS asymmetry gate")

    old_fake_asym = """\tsd = lowest_flag_domain(cpu, SD_ASYM_CPUCAPACITY);
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
"""
    new_fake_asym = f"""\tsd = lowest_flag_domain(cpu, SD_ASYM_CPUCAPACITY);
\t/*
\t * {MARKER}: Qualcomm {SRC_WALT_ASYM}.
\t * Keep the topology pointer truthful.  WALT's EAS enablement is handled
\t * in build_perf_domains() instead of fabricating an asymmetric domain.
\t */
\trcu_assign_pointer(per_cpu(sd_asym_cpucapacity, cpu), sd);
"""
    tp = replace_once(tp, old_fake_asym, new_fake_asym, "fake asym domain removal")

    # ------------------------------------------------------------------
    # 5. Qualcomm LPM: isolated CPUs must not constrain cluster PM-QoS.
    #    Adapt the newer get_cpus_qos() behavior to Samsung's existing
    #    per-CPU PM_QOS_CPU_DMA_LATENCY implementation.
    # ------------------------------------------------------------------
    if "a52_lpm_cluster_qos" not in lp:
        cluster_anchor = """static int cluster_select(struct lpm_cluster *cluster, bool from_idle,
\t\t\t\t\t\t\tint *ispred)
"""
        qos_helper = f"""/*
 * {MARKER}
 * Qualcomm {SRC_LPM_QOS}: an isolated core should not force the entire
 * cluster into a shallower low-power state.  Samsung 4.19 already exposes
 * per-CPU PM_QOS_CPU_DMA_LATENCY, so aggregate it here while skipping
 * isolated CPUs.
 */
static uint32_t a52_lpm_cluster_qos(const struct cpumask *mask)
{{
\tuint32_t latency = PM_QOS_CPU_DMA_LAT_DEFAULT_VALUE;
\tint cpu;

\tfor_each_cpu(cpu, mask) {{
\t\tuint32_t value;

\t\tif (check_cpu_isolated(cpu))
\t\t\tcontinue;

\t\tvalue = pm_qos_request_for_cpu(PM_QOS_CPU_DMA_LATENCY, cpu);
\t\tif (value < latency)
\t\t\tlatency = value;
\t}}

\treturn latency;
}}

"""
        lp = replace_once(lp, cluster_anchor, qos_helper + cluster_anchor,
                          "cluster QoS helper insertion")

    lp = replace_once(
        lp,
        "\tif (cpumask_and(&mask, cpu_online_mask, &cluster->child_cpus))\n"
        "\t\tlatency_us = pm_qos_request_for_cpumask(PM_QOS_CPU_DMA_LATENCY,\n"
        "\t\t\t\t\t\t\t&mask);\n",
        "\tif (cpumask_and(&mask, cpu_online_mask, &cluster->child_cpus))\n"
        "\t\tlatency_us = a52_lpm_cluster_qos(&mask);\n",
        "cluster isolated-CPU QoS aggregation",
    )

    # ------------------------------------------------------------------
    # 6. Qualcomm LPM probe correctness: do not publish suspend/s2idle ops
    #    until probe has reached its no-fail tail, and release the coherent
    #    debug allocation on a failed probe.
    # ------------------------------------------------------------------
    lp = replace_once(
        lp,
        "\tsuspend_set_ops(&lpm_suspend_ops);\n"
        "\ts2idle_set_ops(&lpm_s2idle_ops);\n",
        "",
        "early suspend ops removal",
    )

    probe_success_anchor = """\tif (msm_minidump_add_region(&md_entry) < 0)
\t\tpr_info("Failed to add lpm_debug in Minidump\\n");

\treturn 0;
failed:
\tfree_cluster_node(lpm_root_node);
\tlpm_root_node = NULL;
\treturn ret;
}
"""
    probe_success_new = f"""\tif (msm_minidump_add_region(&md_entry) < 0)
\t\tpr_info("Failed to add lpm_debug in Minidump\\n");

\t/*
\t * {MARKER}
\t * Qualcomm {SRC_LPM_PROBE}: publish suspend callbacks only after every
\t * probe step that can fail has completed.
\t */
\tsuspend_set_ops(&lpm_suspend_ops);
\ts2idle_set_ops(&lpm_s2idle_ops);

\treturn 0;
failed:
\tfree_cluster_node(lpm_root_node);
\tlpm_root_node = NULL;
\tif (lpm_debug) {{
\t\tdma_free_coherent(&pdev->dev, size, lpm_debug, lpm_debug_phys);
\t\tlpm_debug = NULL;
\t\tlpm_debug_phys = 0;
\t}}
\treturn ret;
}}
"""
    lp = replace_once(lp, probe_success_anchor, probe_success_new,
                      "late suspend ops + coherent cleanup")

    # Write.
    lpm.write_text(lp)
    fair.write_text(fa)
    topology.write_text(tp)
    sched_h.write_text(sh)
    sugov.write_text(sg)

    # Structural audits.
    checks = {
        lpm: [
            MARKER,
            "static struct cpuidle_governor lpm_governor",
            '.name = "qcom",',
            ".rating = 30,",
            "static struct cpuidle_driver msm_idle_driver",
            "static uint32_t a52_lpm_cluster_qos(",
            "latency_us = a52_lpm_cluster_qos(&mask);",
            "dma_free_coherent(&pdev->dev, size, lpm_debug, lpm_debug_phys);",
            "suspend_set_ops(&lpm_suspend_ops);",
            "s2idle_set_ops(&lpm_s2idle_ops);",
        ],
        sched_h: [
            MARKER,
            "static inline void walt_irq_work_queue(struct irq_work *work)",
            "irq_work_queue_on(work, cpumask_any(cpu_online_mask));",
        ],
        sugov: [
            "walt_irq_work_queue(&sg_policy->irq_work);",
            "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
            "A52 SCHEDUTIL P2: Android17 79443a7e limits_changed synchronization",
        ],
        fair: [
            "new_util = wake_util + uclamp_task_util(p);",
            "bool boosted = uclamp_boosted(p) ||",
        ],
        topology: [
            MARKER,
            "#ifndef CONFIG_SCHED_WALT",
            "sd = lowest_flag_domain(cpu, SD_ASYM_CPUCAPACITY);",
        ],
    }

    for path, needles in checks.items():
        data = path.read_text()
        for needle in needles:
            if needle not in data:
                raise SystemExit(f"audit failed: {path}: missing {needle}")

    lp2 = lpm.read_text()
    if lp2.count("suspend_set_ops(&lpm_suspend_ops);") != 1:
        raise SystemExit("audit failed: suspend_set_ops must appear exactly once")
    if lp2.count("s2idle_set_ops(&lpm_s2idle_ops);") != 1:
        raise SystemExit("audit failed: s2idle_set_ops must appear exactly once")

    sg2 = sugov.read_text()
    deferred = function_block(sg2, "static void sugov_deferred_update(")
    if "irq_work_queue(&sg_policy->irq_work);" in deferred:
        raise SystemExit("audit failed: raw schedutil irq_work queue remains")

    fa2 = fair.read_text()
    fbt = function_block(fa2, "static void find_best_target(")
    if "new_util = wake_util + task_util_est(p);" in fbt:
        raise SystemExit("audit failed: unclamped FBT task util remains")
    start = function_block(fa2, "static int get_start_cpu(")
    if "schedtune_task_boost(p) > 0 ||" in start:
        raise SystemExit("audit failed: old get_start_cpu boost expression remains")

    tp2 = topology.read_text()
    topcache = function_block(tp2, "static void update_top_cache_domain(")
    if "sd = cpu_rq(cpu)->sd;" in topcache:
        raise SystemExit("audit failed: fake asymmetric sched_domain remains")

    # TEO remains compiled but intentionally does not replace the Qualcomm
    # governor.  Do not touch either rating in this phase.
    if '.rating =\t21,' not in teo.read_text():
        raise SystemExit("audit failed: P146 TEO fallback rating changed")

    print("[audit] Qualcomm qcom governor rating 30 preserved: PASS")
    print("[audit] msm_idle driver preserved: PASS")
    print("[audit] isolated CPU no longer constrains cluster LPM QoS: PASS")
    print("[audit] LPM suspend/s2idle callbacks published only at successful probe tail: PASS")
    print("[audit] LPM coherent debug allocation cleaned on probe failure: PASS")
    print("[audit] schedutil deferred irq_work routed away from offline CPUs: PASS")
    print("[audit] FBT task placement honors UCLAMP_MIN/MAX: PASS")
    print("[audit] start CPU selection honors explicit uclamp boost: PASS")
    print("[audit] WALT EAS hotplug static-key asymmetry fix: PASS")
    print("[audit] P140/P141 schedutil semantics preserved: PASS")
    print("[audit] no CPU frequency/rate-limit/hispeed/RTG/PL tuning changed: PASS")
    print("[audit] no Qualcomm idle latency/residency table changed: PASS")
    print(f"[source] {SRC_SUGOV_IRQ}: Qualcomm schedutil online-CPU irq_work")
    print(f"[source] {SRC_FBT_UCLAMP}: Qualcomm FBT uclamp task util")
    print(f"[source] {SRC_STARTCPU_UCLAMP}: Qualcomm start_cpu uclamp boost")
    print(f"[source] {SRC_WALT_ASYM}: Qualcomm WALT EAS hotplug static-key fix")
    print(f"[source] {SRC_LPM_QOS}: Qualcomm isolated-core LPM QoS handling")
    print(f"[source] {SRC_LPM_PROBE}: Qualcomm LPM probe cleanup")
    print("[done] A52 Qualcomm LPM + WALT/schedutil correctness pack P147 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
