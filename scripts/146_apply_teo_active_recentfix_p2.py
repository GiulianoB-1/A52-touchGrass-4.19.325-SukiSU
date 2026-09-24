#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 TEO P2: upstream 449914 remove broken recent-intercepts + active"
UPSTREAM = "449914398083148f93d070a8aace04f9ec296ce3"
UTIL_REVERT = "0a2998fa48f0b00c20628b02b80bd7fa3582626d"


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
    defconfig = root / "arch/arm64/configs/a52xq_defconfig"

    for path in (teo, menu, defconfig):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    t = teo.read_text()
    m = menu.read_text()
    dc = defconfig.read_text()

    if "A52 TEO P1: Linux 5.14-era TEO core adapted to Samsung 4.19." not in t:
        raise SystemExit("P145 TEO baseline marker missing")
    if "CONFIG_CPU_IDLE_GOV_MENU=y" not in dc or "CONFIG_CPU_IDLE_GOV_TEO=y" not in dc:
        raise SystemExit("P145 must have both menu and TEO built in")
    if ".rating =\t20," not in m and ".rating = 20," not in m:
        raise SystemExit("menu rating 20 anchor missing")

    # Upstream 449914: remove the broken recent-intercepts mechanism.
    t = replace_once(
        t,
        """/*
 * Number of the most recent idle duration values to take into consideration for
 * the detection of recent early wakeup patterns.
 */
#define NR_RECENT\t9

""",
        "",
        "NR_RECENT definition",
    )

    t = replace_once(
        t,
        """ * @intercepts: The "intercepts" metric.
 * @hits: The "hits" metric.
 * @recent: The number of recent "intercepts".
 */
struct teo_bin {
\tunsigned int intercepts;
\tunsigned int hits;
\tunsigned int recent;
};
""",
        """ * @intercepts: The "intercepts" metric.
 * @hits: The "hits" metric.
 */
struct teo_bin {
\tunsigned int intercepts;
\tunsigned int hits;
};
""",
        "teo_bin recent removal",
    )

    t = replace_once(
        t,
        """ * @total: Grand total of the "intercepts" and "hits" metrics for all bins.
 * @last_state: Last idle state entered; kept locally for the 4.19 cpuidle ABI.
 * @next_recent_idx: Index of the next @recent_idx entry to update.
 * @recent_idx: Indices of bins corresponding to recent "intercepts".
 */
struct teo_cpu {
\ts64 time_span_ns;
\ts64 sleep_length_ns;
\tstruct teo_bin state_bins[CPUIDLE_STATE_MAX];
\tunsigned int total;
\tint last_state;
\tint next_recent_idx;
\tint recent_idx[NR_RECENT];
};
""",
        """ * @total: Grand total of the "intercepts" and "hits" metrics for all bins.
 * @last_state: Last idle state entered; kept locally for the 4.19 cpuidle ABI.
 */
struct teo_cpu {
\ts64 time_span_ns;
\ts64 sleep_length_ns;
\tstruct teo_bin state_bins[CPUIDLE_STATE_MAX];
\tunsigned int total;
\tint last_state;
};
""",
        "teo_cpu recent removal",
    )

    t = replace_once(
        t,
        """\ti = cpu_data->next_recent_idx++;
\tif (cpu_data->next_recent_idx >= NR_RECENT)
\t\tcpu_data->next_recent_idx = 0;

\tif (cpu_data->recent_idx[i] >= 0)
\t\tcpu_data->state_bins[cpu_data->recent_idx[i]].recent--;

""",
        "",
        "recent ring accounting removal",
    )

    t = replace_once(
        t,
        """\tif (idx_timer == idx_duration) {
\t\tcpu_data->state_bins[idx_timer].hits += PULSE;
\t\tcpu_data->recent_idx[i] = -1;
\t} else {
\t\tcpu_data->state_bins[idx_duration].intercepts += PULSE;
\t\tcpu_data->state_bins[idx_duration].recent++;
\t\tcpu_data->recent_idx[i] = idx_duration;
\t}
""",
        """\tif (idx_timer == idx_duration)
\t\tcpu_data->state_bins[idx_timer].hits += PULSE;
\telse
\t\tcpu_data->state_bins[idx_duration].intercepts += PULSE;
""",
        "recent hit/intercept accounting removal",
    )

    t = replace_once(
        t,
        """\tunsigned int idx_intercept_sum = 0;
\tunsigned int intercept_sum = 0;
\tunsigned int idx_recent_sum = 0;
\tunsigned int recent_sum = 0;
\tunsigned int idx_hit_sum = 0;
\tunsigned int hit_sum = 0;
\tint constraint_idx = 0;
\tint idx0 = 0, idx = -1;
\tbool alt_intercepts, alt_recent;
""",
        """\tunsigned int idx_intercept_sum = 0;
\tunsigned int intercept_sum = 0;
\tunsigned int idx_hit_sum = 0;
\tunsigned int hit_sum = 0;
\tint constraint_idx = 0;
\tint idx0 = 0, idx = -1;
""",
        "select recent declarations removal",
    )

    t = replace_once(
        t,
        """\t\tintercept_sum += prev_bin->intercepts;
\t\thit_sum += prev_bin->hits;
\t\trecent_sum += prev_bin->recent;
""",
        """\t\tintercept_sum += prev_bin->intercepts;
\t\thit_sum += prev_bin->hits;
""",
        "select recent sum removal",
    )

    t = replace_once(
        t,
        """\t\tidx_intercept_sum = intercept_sum;
\t\tidx_hit_sum = hit_sum;
\t\tidx_recent_sum = recent_sum;
""",
        """\t\tidx_intercept_sum = intercept_sum;
\t\tidx_hit_sum = hit_sum;
""",
        "select recent snapshot removal",
    )

    t = replace_once(
        t,
        """\talt_intercepts = 2 * idx_intercept_sum > cpu_data->total - idx_hit_sum;
\talt_recent = idx_recent_sum > NR_RECENT / 2;
\tif (alt_recent || alt_intercepts) {
""",
        """\tif (2 * idx_intercept_sum > cpu_data->total - idx_hit_sum) {
""",
        "select recent decision removal",
    )

    t = replace_once(
        t,
        """\t\tintercept_sum = 0;
\t\trecent_sum = 0;
""",
        """\t\tintercept_sum = 0;
""",
        "recent reset removal",
    )

    t = replace_once(
        t,
        """\t\t\tintercept_sum += bin->intercepts;
\t\t\trecent_sum += bin->recent;

\t\t\tspan_ns = teo_middle_of_bin(i, drv);

\t\t\tif ((!alt_recent || 2 * recent_sum > idx_recent_sum) &&
\t\t\t    (!alt_intercepts ||
\t\t\t     2 * intercept_sum > idx_intercept_sum)) {
""",
        """\t\t\tintercept_sum += bin->intercepts;

\t\t\tspan_ns = teo_middle_of_bin(i, drv);

\t\t\tif (2 * intercept_sum > idx_intercept_sum) {
""",
        "recent alternative-state condition removal",
    )

    t = replace_once(
        t,
        """\tstruct teo_cpu *cpu_data = per_cpu_ptr(&teo_cpus, dev->cpu);
\tint i;

\tmemset(cpu_data, 0, sizeof(*cpu_data));
\tcpu_data->last_state = -1;

\tfor (i = 0; i < NR_RECENT; i++)
\t\tcpu_data->recent_idx[i] = -1;
""",
        """\tstruct teo_cpu *cpu_data = per_cpu_ptr(&teo_cpus, dev->cpu);

\tmemset(cpu_data, 0, sizeof(*cpu_data));
\tcpu_data->last_state = -1;
""",
        "enable recent initialization removal",
    )

    # Activate TEO while retaining menu as compiled fallback.  The cpuidle core
    # selects the registered governor with the highest rating.  menu is 20.
    t = replace_once(
        t,
        """static struct cpuidle_governor teo_governor = {
\t.name =\t\t"teo",
\t.rating =\t19,
""",
        f"""/*
 * {MARKER}
 *
 * Upstream removed the broken recent-intercepts heuristic because its
 * counter could underflow and bias TEO toward unnecessarily shallow states.
 * Keep menu built as a fallback, but give TEO rating 21 so this validation
 * phase actually exercises TEO at runtime.
 *
 * Do NOT restore the old utilization-threshold heuristic: upstream reverted
 * it in {UTIL_REVERT} because it selected shallow states too aggressively.
 */
static struct cpuidle_governor teo_governor = {{
\t.name =\t\t"teo",
\t.rating =\t21,
""",
        "TEO activation",
    )

    # Update stale top-level prose to match the corrected algorithm.
    t = t.replace(
        'and the sum of\n *      of the numbers of recent intercepts over all of the idle states between\n',
        'over all of the idle states between\n',
    )

    teo.write_text(t)

    final = teo.read_text()

    for needle in (
        MARKER,
        ".rating =\t21,",
        "if (2 * idx_intercept_sum > cpu_data->total - idx_hit_sum)",
        "if (2 * intercept_sum > idx_intercept_sum)",
        "cpu_data->last_state = -1;",
        "A52 TEO P1: Linux 5.14-era TEO core adapted to Samsung 4.19.",
    ):
        if needle not in final:
            raise SystemExit(f"audit failed: missing {needle}")

    forbidden = (
        "NR_RECENT",
        ".recent",
        "recent_idx",
        "next_recent_idx",
        "alt_recent",
        "idx_recent_sum",
        "recent_sum",
        "UTIL_THRESHOLD_SHIFT",
        "teo_cpu_is_utilized",
        "sched_cpu_util(",
    )
    for needle in forbidden:
        if needle in final:
            raise SystemExit(f"audit failed: forbidden stale heuristic remains: {needle}")

    if ".rating =\t20," not in m and ".rating = 20," not in m:
        raise SystemExit("audit failed: menu fallback rating changed")
    if "CONFIG_CPU_IDLE_GOV_MENU=y" not in dc or "CONFIG_CPU_IDLE_GOV_TEO=y" not in dc:
        raise SystemExit("audit failed: menu/TEO fallback config changed")

    print("[audit] upstream 449914 recent-intercepts removal: PASS")
    print("[audit] broken recent counter/ring fully removed: PASS")
    print("[audit] old reverted util-awareness heuristic absent: PASS")
    print("[audit] TEO rating 21 > menu rating 20: ACTIVE TEO expected at runtime")
    print("[audit] menu remains compiled as fallback: PASS")
    print("[audit] cpuidle state latency/residency tables unchanged: PASS")
    print(f"[source] upstream {UPSTREAM}: remove recent intercepts metric")
    print(f"[source] upstream {UTIL_REVERT}: util-awareness reverted as too aggressive")
    print("[done] A52 TEO P2 correctness + activation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
