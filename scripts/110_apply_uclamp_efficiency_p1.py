#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 110_apply_uclamp_efficiency_p1.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
core = root / "kernel/sched/core.c"
sugov = root / "kernel/sched/cpufreq_schedutil.c"
cass = root / "kernel/sched/cass.c"

for p in (core, sugov, cass):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

cc = core.read_text()
sg = sugov.read_text()
ca = cass.read_text()


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {n}")
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

# -------------------------------------------------------------------------
# 1. Samsung 4.19 never had a blanket uclamp floor for every RT task.
#    Android17/mainline defaults UCLAMP_MIN for unmodified RT tasks to 1024,
#    which is appropriate only when the platform intentionally wants every RT
#    task to request maximum capacity. On this legacy WALT platform it turns
#    Binder/audio/display/vendor RT activity into a persistent DVFS floor.
#    Preserve the modern sysctl and live-sync machinery, but use a compatibility
#    default of 0. Userspace may still request an explicit clamp per task.
# -------------------------------------------------------------------------
old_rt_default = (
    "unsigned int sysctl_sched_uclamp_util_min_rt_default = "
    "SCHED_CAPACITY_SCALE;"
)
new_rt_default = '''/*
 * A52 WALT compatibility policy: do not force every legacy RT task to max
 * capacity merely because modern uclamp is present. Explicit userspace RT
 * clamps continue to work normally.
 */
unsigned int sysctl_sched_uclamp_util_min_rt_default = 0;'''
cc = replace_once(cc, old_rt_default, new_rt_default, "rt_uclamp_default")

# -------------------------------------------------------------------------
# 2. Phase85 composed Samsung schedtune_task_margin() into uclamp_task().
#    Phase86 then fed that value into CASS. This made legacy SchedTune boost
#    influence task placement in addition to already influencing WALT/schedutil.
#
#    Keep one policy owner per signal:
#      - WALT + SchedTune remain the legacy frequency/boost source.
#      - UCLAMP_MIN/MAX remain explicit task constraints.
#      - CASS sees the task's base demand with explicit uclamp constraints,
#        not an additional SchedTune margin.
# -------------------------------------------------------------------------
new_uclamp_task = r'''unsigned int uclamp_task(struct task_struct *p)
{
\tunsigned long util = task_util_est(p);

\tif (uclamp_is_used()) {
\t\tutil = max(util, uclamp_eff_value(p, UCLAMP_MIN));
\t\tutil = min(util, uclamp_eff_value(p, UCLAMP_MAX));
\t}

\treturn util;
}'''
cc = replace_function(cc, "unsigned int uclamp_task(struct task_struct *p)",
                      new_uclamp_task)

# The rq frequency path is deliberately retained. stune_util() constructs the
# Samsung WALT demand signal, while uclamp_rq_util_with() applies a max/min
# constraint to that signal. This is not additive double accounting and keeps
# Android17 per-task uclamp semantics available to schedutil.
for needle in (
    "util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);",
    "util = uclamp_rq_util_with(rq, util, NULL);",
):
    if needle not in sg:
        raise SystemExit(f"schedutil compatibility path missing: {needle}")

# Phase86 must still route CASS through uclamp_task(), now with the corrected
# non-SchedTune task demand semantics.
if "unsigned long p_util = uclamp_task(p);" not in ca:
    raise SystemExit("CASS is not using uclamp_task()")

# Add one-time markers for hardware verification.
marker_anchor = '''\tutil = task_util_est(p);

\tif (uclamp_is_used()) {'''
marker_repl = '''\tutil = task_util_est(p);

\tpr_info_once("A52 uclamp efficiency P1: explicit task clamps, no SchedTune task-margin stacking\\n");

\tif (uclamp_is_used()) {'''
cc = replace_once(cc, marker_anchor, marker_repl, "uclamp_runtime_marker")

core.write_text(cc)

checks = {
    core: [
        "sysctl_sched_uclamp_util_min_rt_default = 0;",
        "A52 uclamp efficiency P1",
        "unsigned int uclamp_task(struct task_struct *p)",
        "util = task_util_est(p);",
        "util = max(util, uclamp_eff_value(p, UCLAMP_MIN));",
        "util = min(util, uclamp_eff_value(p, UCLAMP_MAX));",
    ],
    sugov: [
        "util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);",
        "uclamp_rq_util_with(rq, util, NULL)",
    ],
    cass: ["unsigned long p_util = uclamp_task(p);"],
}
for path, needles in checks.items():
    text = path.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{path}: missing uclamp efficiency element: {needle!r}")

# Ensure the old task-level SchedTune margin is no longer stacked inside the
# uclamp task-demand helper itself. It remains available to uclamp_boosted()
# and latency/prefer-idle compatibility code elsewhere in core.c.
start = cc.find("unsigned int uclamp_task(struct task_struct *p)")
end = cc.find("bool uclamp_boosted(struct task_struct *p)", start)
if start < 0 or end < 0:
    raise SystemExit("could not bound uclamp_task")
if "schedtune_task_margin" in cc[start:end]:
    raise SystemExit("uclamp_task still stacks schedtune_task_margin")

print("A52 Android17 uclamp efficiency P1 applied")
print("rt_default_min=0")
print("schedtune_abi=retained")
print("walt_schedutil=retained")
print("rq_uclamp_constraints=retained")
print("cass_task_demand=base-util-plus-explicit-uclamp")
print("task_schedtune_margin_stacking=removed")
