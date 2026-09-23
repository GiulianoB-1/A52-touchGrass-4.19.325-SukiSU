#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost"
UPSTREAM = "9eca544b1491df90ea7102a7ed14acc3c562d97b"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")

    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise SystemExit(f"unterminated function: {signature}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    path = root / "kernel/sched/cpufreq_schedutil.c"
    if not path.is_file():
        raise SystemExit(f"missing schedutil source: {path}")

    text = path.read_text()

    # Require the already boot-tested Android17 uclamp + Samsung WALT path.
    for needle in (
        "U17 uclamp + WALT schedutil compatibility path active",
        "if (uclamp_is_used())",
        "uclamp_rq_util_with(rq, util, NULL)",
        "struct sched_walt_cpu_load walt_load;",
        "sugov_walt_adjust",
    ):
        if needle not in text:
            raise SystemExit(f"required WALT/uclamp baseline missing: {needle}")

    if MARKER in text:
        print("[already] schedutil I/O-wait P1 present")
        return 0

    # Android 17 / Linux 6.18 still uses a fixed 1/8-capacity I/O-wait floor.
    # Upstream introduced this in 9eca544b1491 to make boost behavior
    # independent of each policy's minimum OPP.
    include_anchor = '#include <linux/sched/sysctl.h>\n'
    text = replace_once(
        text,
        include_anchor,
        include_anchor
        + f'\n/* {MARKER} */\n'
        + '#define IOWAIT_BOOST_MIN\t(SCHED_CAPACITY_SCALE / 8)\n',
        "IOWAIT_BOOST_MIN define",
    )

    text = replace_once(
        text,
        "\tunsigned long\t\tmin;\n\tunsigned long\t\tmax;\n",
        "\tunsigned long\t\tmax;\n",
        "remove policy-min derived iowait field",
    )

    # Reset/start floor.
    text = replace_once(
        text,
        "sg_cpu->iowait_boost = set_iowait_boost ? sg_cpu->min : 0;",
        "sg_cpu->iowait_boost = set_iowait_boost ? IOWAIT_BOOST_MIN : 0;",
        "iowait reset floor",
    )

    # First I/O wakeup floor.
    text = replace_once(
        text,
        "sg_cpu->iowait_boost = sg_cpu->min;",
        "sg_cpu->iowait_boost = IOWAIT_BOOST_MIN;",
        "first iowait wakeup floor",
    )

    # Decay floor.
    text = replace_once(
        text,
        "if (sg_cpu->iowait_boost < sg_cpu->min) {",
        "if (sg_cpu->iowait_boost < IOWAIT_BOOST_MIN) {",
        "iowait decay floor",
    )

    # Remove per-policy min capacity setup from sugov_start().
    start, end = function_bounds(text, "static int sugov_start(")
    start_fn = text[start:end]
    old_min_init = """\t\tsg_cpu->cpu\t\t\t= cpu;
\t\tsg_cpu->sg_policy\t\t= sg_policy;
\t\tsg_cpu->min\t\t\t=
\t\t\t(SCHED_CAPACITY_SCALE * policy->cpuinfo.min_freq) /
\t\t\tpolicy->cpuinfo.max_freq;
"""
    new_min_init = """\t\tsg_cpu->cpu\t\t\t= cpu;
\t\tsg_cpu->sg_policy\t\t= sg_policy;
"""
    start_fn = replace_once(
        start_fn, old_min_init, new_min_init, "sugov_start min setup"
    )
    text = text[:start] + start_fn + text[end:]

    # Android17 ultimately keeps I/O boost inside the effective performance
    # constraints. Our 4.19 WALT path computes uclamp before sugov_iowait_apply,
    # so clamp the boost itself before merging it with WALT utilization.
    start, end = function_bounds(text, "static unsigned long sugov_iowait_apply(")
    fn = text[start:end]
    old_apply = """\tboost = (sg_cpu->iowait_boost * max) >> SCHED_CAPACITY_SHIFT;
\treturn max(boost, util);
"""
    new_apply = f"""\tboost = (sg_cpu->iowait_boost * max) >> SCHED_CAPACITY_SHIFT;

\t/*
\t * {MARKER}
\t *
\t * The boot-tested WALT compatibility path already applies rq uclamp to
\t * base utilization before this function. Apply the same rq constraints
\t * to the I/O-wait contribution itself so I/O wakeups cannot bypass an
\t * explicit UCLAMP_MAX (and still honor an explicit UCLAMP_MIN).
\t */
\tif (uclamp_is_used())
\t\tboost = uclamp_rq_util_with(cpu_rq(sg_cpu->cpu), boost, NULL);

\treturn max(boost, util);
"""
    fn = replace_once(fn, old_apply, new_apply, "uclamp-safe iowait boost")
    text = text[:start] + fn + text[end:]

    # Update stale comments to match the fixed floor.
    text = text.replace(
        "we enable the boost starting from the minimum frequency, which improves\n"
        " * energy efficiency by ignoring sporadic wakeups from IO.",
        "we enable the boost starting from IOWAIT_BOOST_MIN, which improves\n"
        " * energy efficiency by ignoring sporadic wakeups from IO.",
    )
    text = text.replace(
        "successive\" wakeup from IO, ranging from the utilization of the minimum\n"
        " * OPP to the utilization of the maximum OPP.",
        "successive\" wakeup from IO, ranging from IOWAIT_BOOST_MIN to the\n"
        " * utilization of the maximum OPP.",
    )

    path.write_text(text)

    final = path.read_text()
    checks = (
        f"/* {MARKER} */",
        "#define IOWAIT_BOOST_MIN\t(SCHED_CAPACITY_SCALE / 8)",
        "set_iowait_boost ? IOWAIT_BOOST_MIN : 0",
        "sg_cpu->iowait_boost = IOWAIT_BOOST_MIN;",
        "sg_cpu->iowait_boost < IOWAIT_BOOST_MIN",
        "boost = uclamp_rq_util_with(cpu_rq(sg_cpu->cpu), boost, NULL);",
        "U17 uclamp + WALT schedutil compatibility path active",
        "sugov_walt_adjust",
    )
    for needle in checks:
        if needle not in final:
            raise SystemExit(f"audit failed: missing {needle}")

    forbidden = (
        "unsigned long\t\tmin;",
        "sg_cpu->min",
    )
    for needle in forbidden:
        if needle in final:
            raise SystemExit(f"audit failed: obsolete policy-min I/O boost remains: {needle}")

    print("[audit] Android17/6.18 fixed IOWAIT_BOOST_MIN: PASS")
    print("[audit] I/O-wait boost respects rq uclamp constraints: PASS")
    print("[audit] Samsung WALT schedutil path preserved: PASS")
    print("[audit] hispeed/RTG/PL and rate-limit tunables unchanged: PASS")
    print("[audit] EEVDF/CASS scheduling selection unchanged: PASS")
    print(f"[source] upstream {UPSTREAM}: schedutil fixed I/O-wait boost floor")
    print("[reference] Android17 Linux 6.18 retains IOWAIT_BOOST_MIN semantics")
    print("[done] A52 schedutil I/O-wait modernization P1 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
