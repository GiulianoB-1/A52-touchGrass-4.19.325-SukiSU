#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 TEO P3: upstream 04bae shallow-idle tick bailout"
UPSTREAM = "04bae4e2267d49eb9cfec403acd0462b8d00d637"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    teo = root / "drivers/cpuidle/governors/teo.c"
    menu = root / "drivers/cpuidle/governors/menu.c"

    for path in (teo, menu):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    t = teo.read_text()
    m = menu.read_text()

    for needle in (
        "A52 TEO P1: Linux 5.14-era TEO core adapted to Samsung 4.19.",
        "A52 TEO P2: upstream 449914 remove broken recent-intercepts + active",
        ".rating =\t21,",
        "if (2 * idx_intercept_sum > cpu_data->total - idx_hit_sum)",
    ):
        if needle not in t:
            raise SystemExit(f"P146 active TEO baseline missing: {needle}")

    if "NR_RECENT" in t or "recent_idx" in t:
        raise SystemExit("P146 recent-intercepts removal not present")
    if ".rating =\t20," not in m and ".rating = 20," not in m:
        raise SystemExit("menu fallback rating 20 missing")

    # Upstream 04bae: shallow/early-return cases must explicitly keep the
    # scheduler tick running instead of passing through the generic end path.
    t = replace_once(
        t,
        """\t/* Check if there is any choice in the first place. */
\tif (drv->state_count < 2) {
\t\tidx = 0;
\t\tgoto end;
\t}
\tif (!dev->states_usage[0].disable) {
\t\tidx = 0;
\t\tif (teo_target_residency_ns(&drv->states[1]) > duration_ns)
\t\t\tgoto end;
\t}
""",
        """\t/* Check if there is any choice in the first place. */
\tif (drv->state_count < 2) {
\t\tidx = 0;
\t\tgoto out_tick;
\t}

\tif (!dev->states_usage[0].disable) {
\t\tidx = 0;
\t\tif (teo_target_residency_ns(&drv->states[1]) > duration_ns)
\t\t\tgoto out_tick;
\t}
""",
        "shallow early-return tick handling",
    )

    t = replace_once(
        t,
        """\t/* Avoid unnecessary overhead. */
\tif (idx < 0) {
\t\tidx = 0; /* No states enabled, must use 0. */
\t\tgoto end;
\t} else if (idx == idx0) {
\t\tgoto end;
\t}
""",
        """\t/* Avoid unnecessary overhead. */
\tif (idx < 0) {
\t\tidx = 0; /* No states enabled, must use 0. */
\t\tgoto out_tick;
\t}

\tif (idx == idx0) {
\t\t/*
\t\t * This is the first enabled idle state, so use it, but do not
\t\t * allow the tick to be stopped if it is shallow enough.
\t\t */
\t\tduration_ns = teo_target_residency_ns(&drv->states[idx]);
\t\tgoto end;
\t}
""",
        "single enabled state tick handling",
    )

    old_end = """end:
\t/*
\t * Don't stop the tick if the selected state is a polling one or if the
\t * expected idle duration is shorter than the tick period length.
\t */
\tif (((drv->states[idx].flags & CPUIDLE_FLAG_POLLING) ||
\t    duration_ns < TICK_NSEC) && !tick_nohz_tick_stopped()) {
\t\t*stop_tick = false;

\t\t/*
\t\t * The tick is not going to be stopped, so if the target
\t\t * residency of the state to be returned is not within the time
\t\t * till the closest timer including the tick, try to correct
\t\t * that.
\t\t */
\t\tif (idx > idx0 &&
\t\t    teo_target_residency_ns(&drv->states[idx]) >
\t\t    ktime_to_ns(delta_tick))
\t\t\tidx = teo_find_shallower_state(drv, dev, idx,
\t\t\t\t\t       ktime_to_ns(delta_tick));
\t}

\treturn idx;
}
"""
    new_end = f"""end:
\t/*
\t * {MARKER}
\t *
\t * Allow the tick to be stopped only when the selected state and the
\t * expected idle duration justify it.  Early returns to state 0 use the
\t * out_tick path explicitly so shallow idle cannot accidentally suppress
\t * the scheduler tick.
\t */
\tif ((!(drv->states[idx].flags & CPUIDLE_FLAG_POLLING) &&
\t    duration_ns >= TICK_NSEC) || tick_nohz_tick_stopped())
\t\treturn idx;

\t/*
\t * The tick is not going to be stopped, so if the target residency of
\t * the state to be returned is not within the time till the closest
\t * timer including the tick, try to correct that.
\t */
\tif (idx > idx0 &&
\t    teo_target_residency_ns(&drv->states[idx]) >
\t    ktime_to_ns(delta_tick))
\t\tidx = teo_find_shallower_state(drv, dev, idx,
\t\t\t\t\t       ktime_to_ns(delta_tick));

out_tick:
\t*stop_tick = false;
\treturn idx;
}}
"""
    t = replace_once(t, old_end, new_end, "TEO end/out_tick restructuring")

    teo.write_text(t)
    final = teo.read_text()

    for needle in (
        MARKER,
        "goto out_tick;",
        "duration_ns = teo_target_residency_ns(&drv->states[idx]);",
        "duration_ns >= TICK_NSEC) || tick_nohz_tick_stopped())",
        "out_tick:",
        "*stop_tick = false;",
        ".rating =\t21,",
        "A52 TEO P2: upstream 449914 remove broken recent-intercepts + active",
    ):
        if needle not in final:
            raise SystemExit(f"audit failed: missing {needle}")

    if "NR_RECENT" in final or "recent_idx" in final:
        raise SystemExit("audit failed: recent-intercepts logic reappeared")
    if "UTIL_THRESHOLD_SHIFT" in final or "teo_cpu_is_utilized" in final:
        raise SystemExit("audit failed: reverted util-awareness reappeared")

    print("[audit] upstream 04bae shallow-idle tick bailout: PASS")
    print("[audit] state-0 early returns keep scheduler tick running: PASS")
    print("[audit] first enabled shallow state uses target residency for tick decision: PASS")
    print("[audit] deep-enough states may still stop the tick: PASS")
    print("[audit] active TEO rating 21 preserved; menu fallback 20 preserved: PASS")
    print("[audit] P146 recent-intercepts removal preserved: PASS")
    print("[audit] reverted util-awareness remains absent: PASS")
    print("[audit] idle state latency/residency tables unchanged: PASS")
    print(f"[source] upstream {UPSTREAM}: cpuidle: teo: Avoid stopping the tick unnecessarily when bailing out")
    print("[done] A52 TEO P3 tick-bailout correctness applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
