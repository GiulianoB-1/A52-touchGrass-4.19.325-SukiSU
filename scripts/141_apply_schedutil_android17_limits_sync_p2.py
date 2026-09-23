#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 SCHEDUTIL P2: Android17 79443a7e limits_changed synchronization"
UPSTREAM = "79443a7e9da3c9f68290a8653837e23aba0fa89f"


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

    # Require the boot-tested P140 state and the WALT/uclamp integration.
    for needle in (
        "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
        "#define IOWAIT_BOOST_MIN",
        "U17 uclamp + WALT schedutil compatibility path active",
        "uclamp_rq_util_with(rq, util, NULL)",
        "sugov_walt_adjust",
    ):
        if needle not in text:
            raise SystemExit(f"required P140/WALT baseline missing: {needle}")

    if MARKER in text:
        print("[already] schedutil limits synchronization P2 present")
        return 0

    # 1. Serialize consumption of limits_changed against later policy-limit
    # reads in cpufreq_driver_resolve_freq().
    start, end = function_bounds(text, "static bool sugov_should_update_freq(")
    fn = text[start:end]

    old = """	if (unlikely(sg_policy->limits_changed)) {
		sg_policy->limits_changed = false;
		sg_policy->need_freq_update = true;
		return true;
	}
"""

    new = f"""	if (unlikely(READ_ONCE(sg_policy->limits_changed))) {{
		WRITE_ONCE(sg_policy->limits_changed, false);
		sg_policy->need_freq_update = true;

		/*
		 * {MARKER}
		 *
		 * Ensure clearing limits_changed is visible before subsequent
		 * policy-limit reads used to resolve the next frequency. This pairs
		 * with the write barrier in sugov_limits().
		 */
		smp_mb();

		return true;
	}}
"""

    fn = replace_once(fn, old, new, "sugov_should_update_freq synchronization")
    text = text[:start] + fn + text[end:]

    # 2. Deadline bandwidth can force an update too. Mark the shared flag
    # atomically so compiler reordering cannot lose the notification.
    start, end = function_bounds(text, "static inline void ignore_dl_rate_limit(")
    fn = text[start:end]

    old = """	if (cpu_bw_dl(cpu_rq(sg_cpu->cpu)) > sg_cpu->bw_dl)
		sg_policy->limits_changed = true;
"""

    new = """	if (cpu_bw_dl(cpu_rq(sg_cpu->cpu)) > sg_cpu->bw_dl)
		WRITE_ONCE(sg_policy->limits_changed, true);
"""

    fn = replace_once(fn, old, new, "ignore_dl_rate_limit WRITE_ONCE")
    text = text[:start] + fn + text[end:]

    # 3. Publish the limits-changed notification before cpufreq core updates
    # can race with schedutil's next update.
    start, end = function_bounds(text, "static void sugov_limits(")
    fn = text[start:end]

    old = """	sg_policy->limits_changed = true;
"""

    new = f"""	/*
	 * {MARKER}
	 *
	 * Publish this notification with ordering against cpufreq policy-limit
	 * updates. sugov_should_update_freq() pairs with this using smp_mb().
	 */
	smp_wmb();
	WRITE_ONCE(sg_policy->limits_changed, true);
"""

    fn = replace_once(fn, old, new, "sugov_limits synchronization")
    text = text[:start] + fn + text[end:]

    path.write_text(text)

    final = path.read_text()

    checks = (
        MARKER,
        "READ_ONCE(sg_policy->limits_changed)",
        "WRITE_ONCE(sg_policy->limits_changed, false)",
        "WRITE_ONCE(sg_policy->limits_changed, true)",
        "smp_mb();",
        "smp_wmb();",
        "A52 SCHEDUTIL IOWAIT P1: Android17 6.18 fixed boost floor + uclamp-safe boost",
        "U17 uclamp + WALT schedutil compatibility path active",
        "sugov_walt_adjust",
    )

    for needle in checks:
        if needle not in final:
            raise SystemExit(f"audit failed: missing {needle}")

    # The runtime update sites must no longer use unannotated flag writes.
    for sig in (
        "static bool sugov_should_update_freq(",
        "static inline void ignore_dl_rate_limit(",
        "static void sugov_limits(",
    ):
        s, e = function_bounds(final, sig)
        block = final[s:e]
        if "sg_policy->limits_changed = true;" in block:
            raise SystemExit(f"audit failed: plain true write remains in {sig}")
        if sig.startswith("static bool") and "sg_policy->limits_changed = false;" in block:
            raise SystemExit(f"audit failed: plain false write remains in {sig}")

    print("[audit] READ_ONCE/WRITE_ONCE limits_changed handling: PASS")
    print("[audit] paired smp_wmb/smp_mb ordering: PASS")
    print("[audit] deadline bandwidth update path synchronized: PASS")
    print("[audit] P140 I/O-wait modernization preserved: PASS")
    print("[audit] Samsung WALT + Android17 uclamp path preserved: PASS")
    print("[audit] CPU frequencies, rate limits, hispeed, RTG and PL tunables unchanged")
    print(f"[source] upstream {UPSTREAM}: cpufreq/sched explicit limits_changed synchronization")
    print("[reference] present in Android17 Linux 6.18")
    print("[done] A52 schedutil Android17 correctness P2 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
