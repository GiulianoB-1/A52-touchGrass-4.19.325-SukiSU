#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 110_apply_uclamp_cass_efficiency.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cass = root / "kernel/sched/cass.c"
core = root / "kernel/sched/core.c"
sugov = root / "kernel/sched/cpufreq_schedutil.c"

for p in (cass, core, sugov):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

ca = cass.read_text()
cc = core.read_text()
sg = sugov.read_text()

marker = "A52 uclamp/CASS efficiency: clamp-only wake placement"
if marker in ca:
    print("uclamp/CASS efficiency fix already applied")
    raise SystemExit(0)

# Verify the exact hybrid we are fixing. Phase85 deliberately keeps Samsung
# SchedTune beside modern uclamp; Phase86 then fed uclamp_task(), which also
# adds schedtune_task_margin(), into CASS CPU placement. That makes the legacy
# boost affect both DVFS and wake placement. Keep SchedTune for vendor ABI and
# frequency policy, but make CASS placement consume raw task demand plus only
# explicit UCLAMP_MIN/MAX bounds.
for needle in (
    "unsigned long p_util = uclamp_task(p);",
    "CASS uclamp-aware task demand path is active",
    "DEFINE_STATIC_KEY_FALSE(sched_uclamp_used)",
    "U17 uclamp + WALT schedutil compatibility path active",
):
    if needle not in (ca + "\n" + cc + "\n" + sg):
        raise SystemExit(f"missing expected uclamp Phase86 element: {needle!r}")

old_block = """    /*
     * P2: CASS placement consumes the same task demand policy that feeds
     * modern uclamp-aware scheduling.  uclamp_task() preserves Samsung
     * schedtune/WALT compatibility and then applies task UCLAMP_MIN/MAX.
     */
    unsigned long p_util = uclamp_task(p);
    bool has_idle = false;
"""
new_block = """    /*
     * A52 uclamp/CASS efficiency: clamp-only wake placement.
     *
     * Start from the task's real estimated demand.  Explicit uclamp bounds
     * are still honoured, but do not add Samsung schedtune_task_margin() here.
     * SchedTune already influences the WALT/schedutil DVFS path; feeding the
     * same boost into CASS also biases wake placement toward higher-capacity
     * CPUs and can unnecessarily amplify interactive boosts.
     */
    unsigned long p_util = task_util_est(p);
#ifdef CONFIG_UCLAMP_TASK
    if (uclamp_is_used()) {
        p_util = max(p_util, uclamp_eff_value(p, UCLAMP_MIN));
        p_util = min(p_util, uclamp_eff_value(p, UCLAMP_MAX));
    }
#endif
    bool has_idle = false;
"""
if ca.count(old_block) != 1:
    raise SystemExit(f"CASS Phase86 task-demand anchor mismatch: {ca.count(old_block)}")
ca = ca.replace(old_block, new_block, 1)

old_marker = '    pr_info_once("CASS uclamp-aware task demand path is active\\n");\n'
new_marker = '    pr_info_once("CASS uclamp-bounds-only task demand path is active\\n");\n'
if ca.count(old_marker) != 1:
    raise SystemExit("CASS runtime marker mismatch")
ca = ca.replace(old_marker, new_marker, 1)

cass.write_text(ca)

# Deliberately preserve the schedutil composition. stune_util() is the vendor
# frequency-demand source and uclamp_rq_util_with() is a clamp, not a second
# additive boost. This is the least invasive way to retain Android uclamp ABI
# while removing the duplicated placement bias introduced in Phase86.
ca2 = cass.read_text()
if "unsigned long p_util = uclamp_task(p);" in ca2:
    raise SystemExit("CASS still uses schedtune-composed uclamp_task()")
for needle in (
    "A52 uclamp/CASS efficiency: clamp-only wake placement",
    "unsigned long p_util = task_util_est(p);",
    "uclamp_eff_value(p, UCLAMP_MIN)",
    "uclamp_eff_value(p, UCLAMP_MAX)",
    "CASS uclamp-bounds-only task demand path is active",
):
    if needle not in ca2:
        raise SystemExit(f"missing uclamp/CASS efficiency element: {needle!r}")

if "util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);" not in sg:
    raise SystemExit("unexpected WALT schedutil baseline")
if "util = uclamp_rq_util_with(rq, util, NULL);" not in sg:
    raise SystemExit("uclamp schedutil clamp path unexpectedly missing")

# Keep the upstream RT default at 1024 in this correctness build. Lowering it
# is a policy/latency experiment and can be tested live through the Phase86
# sysctl without rebuilding, so do not confound this kernel comparison.
if "sysctl_sched_uclamp_util_min_rt_default = SCHED_CAPACITY_SCALE;" not in cc:
    raise SystemExit("RT uclamp default differs from expected upstream value")

print("A52 uclamp/CASS efficiency fix applied")
print("cass_task_demand=raw-util-est-plus-explicit-uclamp-bounds")
print("cass_schedtune_margin=removed")
print("walt_schedutil=schedtune-plus-final-uclamp-clamp-retained")
print("rt_uclamp_default=1024-retained-for-controlled-testing")
