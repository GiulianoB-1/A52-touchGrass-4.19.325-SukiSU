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
# 1. Keep modern uclamp support, but do not force every legacy Samsung RT
#    task to request maximum capacity by default.
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
# 2. Give each signal one policy owner:
#      - WALT + SchedTune remain the legacy frequency/boost source.
#      - UCLAMP_MIN/MAX remain explicit task constraints.
#      - CASS sees base task demand constrained by explicit uclamp only.
#
#    Important: use a normal Python string here, not a raw string. The old
#    generator emitted literal "\\t" text, then immediately searched for real
#    tab-indented C and failed its own uclamp_runtime_marker anchor.
# -------------------------------------------------------------------------
new_uclamp_task = '''unsigned int uclamp_task(struct task_struct *p)
{
\tunsigned long util = task_util_est(p);

\tpr_info_once("A52 uclamp efficiency P1: explicit task clamps, no SchedTune task-margin stacking\\n");

\tif (uclamp_is_used()) {
\t\tutil = max(util, uclamp_eff_value(p, UCLAMP_MIN));
\t\tutil = min(util, uclamp_eff_value(p, UCLAMP_MAX));
\t}

\treturn util;
}'''
cc = replace_function(
    cc,
    "unsigned int uclamp_task(struct task_struct *p)",
    new_uclamp_task,
)


# The rq frequency path is deliberately retained. stune_util() constructs the
# Samsung WALT demand signal, while uclamp_rq_util_with() applies min/max
# constraints to that signal without re-adding schedtune_task_margin().
for needle in (
    "util = stune_util(sg_cpu->cpu, 0, &sg_cpu->walt_load);",
    "util = uclamp_rq_util_with(rq, util, NULL);",
):
    if needle not in sg:
        raise SystemExit(f"schedutil compatibility path missing: {needle}")


# Phase86 must still route CASS through uclamp_task(), now with corrected
# non-SchedTune task-demand semantics.
if "unsigned long p_util = uclamp_task(p);" not in ca:
    raise SystemExit("CASS is not using uclamp_task()")


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


# Ensure task-level SchedTune margin is not stacked inside uclamp_task().
# It remains available to the legacy compatibility paths elsewhere in core.c.
start = cc.find("unsigned int uclamp_task(struct task_struct *p)")
end = cc.find("bool uclamp_boosted(struct task_struct *p)", start)
if start < 0 or end < 0:
    raise SystemExit("could not bound uclamp_task")
if "schedtune_task_margin" in cc[start:end]:
    raise SystemExit("uclamp_task still stacks schedtune_task_margin")
if "\\t" in cc[start:end]:
    raise SystemExit("literal backslash-t remains in generated uclamp_task")

print("A52 Android17 uclamp efficiency P1 applied")
print("rt_default_min=0")
print("schedtune_abi=retained")
print("walt_schedutil=retained")
print("rq_uclamp_constraints=retained")
print("cass_task_demand=base-util-plus-explicit-uclamp")
print("task_schedtune_margin_stacking=removed")
print("runtime_marker=embedded-in-generated-function")
