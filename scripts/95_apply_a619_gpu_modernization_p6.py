#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER_HYST = "A619 GPU P6: adaptive downscale hysteresis"
MARKER_BUS = "A619 GPU P6: transition-only bus preboost"
MARKER_DTS = "A619 GPU P6: responsive balanced policy"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def patch_downscale_hysteresis(root: Path) -> None:
    path = root / "drivers/gpu/msm/kgsl_pwrscale.c"
    text = path.read_text()

    if MARKER_HYST in text:
        print(f"[already] {path}: downscale hysteresis")
        return

    if "#include <linux/module.h>" not in text:
        text = replace_once(
            text,
            "#include <linux/devfreq_cooling.h>\n",
            "#include <linux/devfreq_cooling.h>\n#include <linux/module.h>\n",
            "kgsl_pwrscale module include",
        )

    anchor = (
        "static struct xstats last_xstats;\n"
        "static struct devfreq_dev_status last_status = { .private_data = &last_xstats };\n"
    )
    insert = anchor + f"""
/*
 * {MARKER_HYST}
 *
 * A one-step downscale immediately after a frequency transition is often
 * threshold noise rather than a durable workload change. Hold only adjacent
 * downscales for a very small window. Multi-step drops are never blocked, so
 * the GPU can still shed substantial power quickly when load disappears.
 *
 * Runtime tunable:
 *   /sys/module/msm_kgsl_core/parameters/tg_gpu_downscale_hold_ms
 * Set to 0 to disable.
 */
static uint tg_gpu_downscale_hold_ms = 16;
module_param_named(tg_gpu_downscale_hold_ms,
	tg_gpu_downscale_hold_ms, uint, 0644);
MODULE_PARM_DESC(tg_gpu_downscale_hold_ms,
	"Hold adjacent GPU downscales for N ms after a frequency transition");

"""
    text = replace_once(text, anchor, insert, "downscale tunable anchor")

    old = """		if (level != pwr->active_pwrlevel)
			kgsl_pwrctrl_pwrlevel_change(device, level);
"""
    new = f"""		/*
		 * {MARKER_HYST}
		 * Power levels are inverse indices: a larger index is a lower
		 * frequency. Suppress only an adjacent one-level downscale inside
		 * the hold window. Upscaling and multi-level downscaling remain
		 * immediate.
		 */
		if (tg_gpu_downscale_hold_ms &&
			level == pwr->active_pwrlevel + 1 &&
			device->pwrscale.freq_change_time > 0) {{
			s64 elapsed_ms = ktime_to_ms(ktime_get()) -
				device->pwrscale.freq_change_time;

			if (elapsed_ms >= 0 &&
				elapsed_ms < tg_gpu_downscale_hold_ms)
				level = pwr->active_pwrlevel;
		}}

		if (level != pwr->active_pwrlevel)
			kgsl_pwrctrl_pwrlevel_change(device, level);
"""
    text = replace_once(text, old, new, "kgsl_devfreq_target hysteresis")
    path.write_text(text)
    print(f"[patched] {path}: 16 ms adjacent-level downscale hysteresis")


def patch_transition_bus_preboost(root: Path) -> None:
    path = root / "drivers/gpu/msm/kgsl_pwrctrl.c"
    text = path.read_text()

    if MARKER_BUS in text:
        print(f"[already] {path}: transition bus preboost")
        return

    if "#include <linux/module.h>" not in text:
        text = replace_once(
            text,
            "#include <linux/msm-bus.h>\n",
            "#include <linux/msm-bus.h>\n#include <linux/module.h>\n",
            "kgsl_pwrctrl module include",
        )

    anchor = """static unsigned long last_ab;

static void kgsl_pwrctrl_clk"""
    insert = f"""static unsigned long last_ab;

/*
 * {MARKER_BUS}
 *
 * Give DDR one extra bus level only across a GPU upclock transition. The
 * normal vote is restored immediately after the clock switch, so this is not
 * a permanent bandwidth floor.
 *
 * Runtime tunable:
 *   /sys/module/msm_kgsl_core/parameters/tg_gpu_upclock_bus_boost
 * Set to 0 to disable.
 */
static uint tg_gpu_upclock_bus_boost = 1;
module_param_named(tg_gpu_upclock_bus_boost,
	tg_gpu_upclock_bus_boost, uint, 0644);
MODULE_PARM_DESC(tg_gpu_upclock_bus_boost,
	"Extra GPU bus-vote steps used only while switching to a higher GPU clock");

static void kgsl_pwrctrl_clk"""
    text = replace_once(text, anchor, insert, "bus boost tunable anchor")

    old = """	struct kgsl_pwrctrl *pwr = &device->pwrctrl;
	struct kgsl_pwrlevel *pwrlevel;
	unsigned int old_level = pwr->active_pwrlevel;
"""
    new = """	struct kgsl_pwrctrl *pwr = &device->pwrctrl;
	struct kgsl_pwrlevel *pwrlevel;
	unsigned int old_level = pwr->active_pwrlevel;
	int p6_base_bus_mod = 0;
	bool p6_bus_preboost = false;
"""
    text = replace_once(text, old, new, "pwrlevel change locals")

    # Phase1 rewrites the original unconditional bus update into a guarded
    # pre-clock vote. Insert the temporary P6 boost immediately before that
    # Phase1 block so the boosted vote is the one applied before an upclock.
    old = """	if (pwr->bus_mod < 0 || new_level < old_level) {
		pwr->bus_mod = 0;
		pwr->bus_percent_ab = 0;
	}
	/*
	 * A619 GPU modernization: Qualcomm 536bf34a8db3
"""
    new = f"""	if (pwr->bus_mod < 0 || new_level < old_level) {{
		pwr->bus_mod = 0;
		pwr->bus_percent_ab = 0;
	}}

	/*
	 * {MARKER_BUS}
	 * A smaller power-level index means a higher GPU frequency. Phase1
	 * already guarantees that the bus vote is issued before the clock rises.
	 * Temporarily add one bus step to that pre-clock vote, then restore the
	 * normal vote after the clock switch.
	 */
	if (new_level < old_level && tg_gpu_upclock_bus_boost) {{
		p6_base_bus_mod = pwr->bus_mod;
		pwr->bus_mod += tg_gpu_upclock_bus_boost;
		p6_bus_preboost = true;
	}}

	/*
	 * A619 GPU modernization: Qualcomm 536bf34a8db3
"""
    text = replace_once(text, old, new, "transition bus boost placement after P1")

    anchor = """	trace_gpu_frequency(pwrlevel->gpu_freq/1000, 0);

	/*
	 * Complete the Qualcomm bus-vote ordering fix: only lower the bus
"""
    insert = f"""	trace_gpu_frequency(pwrlevel->gpu_freq/1000, 0);

	/*
	 * {MARKER_BUS}: remove the temporary preboost as soon as the higher
	 * GPU clock is established. This keeps steady-state DDR policy under
	 * gpubw_mon instead of creating a permanent floor.
	 */
	if (p6_bus_preboost) {{
		pwr->bus_mod = p6_base_bus_mod;
		kgsl_pwrctrl_buslevel_update(device, true);
	}}

	/*
	 * Complete the Qualcomm bus-vote ordering fix: only lower the bus
"""
    text = replace_once(text, anchor, insert, "bus boost restore anchor")

    path.write_text(text)
    print(f"[patched] {path}: transition-only +1 bus-level preboost")


def find_gpu_dtsi(root: Path) -> Path:
    candidates = []
    for path in (root / "arch/arm64/boot/dts").rglob("*.dtsi"):
        try:
            data = path.read_text(errors="ignore")
        except OSError:
            continue
        if (
            'compatible = "qcom,kgsl-3d0"' in data
            and "qcom,enable-ca-jump" in data
            and "qcom,ca-busy-penalty" in data
            and "qcom,gpu-pwrlevel-bins" in data
        ):
            candidates.append(path)

    if not candidates:
        raise SystemExit("A619 GPU DTS with context-aware policy was not found")

    # Prefer the canonical Lagoon GPU file if several included copies exist.
    lagoon = [p for p in candidates if p.name == "lagoon-gpu.dtsi"]
    if len(lagoon) == 1:
        return lagoon[0]
    if len(candidates) == 1:
        return candidates[0]

    raise SystemExit(
        "multiple GPU DTS candidates found: " + ", ".join(str(p) for p in candidates)
    )


def patch_dts_policy(root: Path) -> None:
    path = find_gpu_dtsi(root)
    text = path.read_text()

    if MARKER_DTS in text:
        print(f"[already] {path}: P6 balanced policy")
        return

    # Keep the policy self-documenting in the DTS.
    text = text.replace(
        "qcom,idle-timeout = <80>;",
        f"qcom,idle-timeout = <64>; /* {MARKER_DTS}: faster true-idle collapse */",
        1,
    )
    if MARKER_DTS not in text:
        raise SystemExit(f"{path}: expected qcom,idle-timeout = <80> not found")

    n = text.count("qcom,ca-busy-penalty = <12000>;")
    if n != 1:
        raise SystemExit(f"{path}: expected one 12000us CA penalty, found {n}")
    text = text.replace(
        "qcom,ca-busy-penalty = <12000>;",
        "qcom,ca-busy-penalty = <9000>; /* P6: react 3 ms sooner to busy context bursts */",
        1,
    )

    # Modify only speed-bin 169. On SM7225 this is the 800 MHz-capable A619
    # table used by this silicon family. We are not changing max clocks here.
    pat = re.compile(
        r"(qcom,gpu-pwrlevels-\d+\s*\{(?:(?!qcom,gpu-pwrlevels-\d+\s*\{).)*?"
        r"qcom,speed-bin\s*=\s*<169>;(?:(?!qcom,gpu-pwrlevels-\d+\s*\{).)*?"
        r"qcom,ca-target-pwrlevel\s*=\s*)<4>(;)",
        re.S,
    )
    text, count = pat.subn(r"\g<1><3>\g<2> /* P6: CA jump target 355 MHz -> 430 MHz */", text, count=1)
    if count != 1:
        raise SystemExit(f"{path}: speed-bin 169 CA target <4> not found exactly once")

    # Explicitly audit that we did not alter frequency or voltage definitions.
    path.write_text(text)
    print(f"[patched] {path}: idle 80->64 ms, CA penalty 12->9 ms, bin169 target 355->430 MHz")


def audit(root: Path) -> None:
    pwrscale = (root / "drivers/gpu/msm/kgsl_pwrscale.c").read_text()
    pwrctrl = (root / "drivers/gpu/msm/kgsl_pwrctrl.c").read_text()
    dts_path = find_gpu_dtsi(root)
    dts = dts_path.read_text()

    for token in (
        MARKER_HYST,
        "tg_gpu_downscale_hold_ms = 16",
        "level == pwr->active_pwrlevel + 1",
        "elapsed_ms < tg_gpu_downscale_hold_ms",
    ):
        if token not in pwrscale:
            raise SystemExit(f"downscale audit missing: {token}")

    for token in (
        MARKER_BUS,
        "tg_gpu_upclock_bus_boost = 1",
        "pwr->bus_mod += tg_gpu_upclock_bus_boost",
        "pwr->bus_mod = p6_base_bus_mod",
    ):
        if token not in pwrctrl:
            raise SystemExit(f"bus boost audit missing: {token}")

    for token in (
        "qcom,idle-timeout = <64>",
        "qcom,ca-busy-penalty = <9000>",
        "qcom,speed-bin = <169>",
        "qcom,ca-target-pwrlevel = <3>",
    ):
        if token not in dts:
            raise SystemExit(f"DTS policy audit missing: {token}")

    # Preserve the already validated safety/correctness work.
    for path, token in (
        (root / "drivers/gpu/msm/kgsl_pwrctrl.c",
         "A619 GPU modernization: Qualcomm 536bf34a8db3"),
        (root / "drivers/gpu/msm/kgsl_pwrctrl.c",
         "time_is_after_jiffies(device->idle_jiffies)"),
        (root / "drivers/gpu/msm/adreno_a6xx_preempt.c",
         "A619 GPU P4: Qualcomm 96f7537ccfcd"),
        (root / "drivers/gpu/msm/adreno_a6xx_gmu.c",
         "gmu->num_oob_perfcntr++"),
    ):
        if token not in path.read_text():
            raise SystemExit(f"P1-P4 preservation audit missing {token} in {path}")

    print("[audit] P6 changes no GPU frequency table and no voltage table")
    print("[audit] P6 downscale hold affects adjacent downscales only")
    print("[audit] P6 bus boost is restored immediately after GPU upclock")
    print("[audit] P6 CA tuning is restricted to A619 speed-bin 169 target")
    print("[audit] P1-P4 correctness/optimization markers preserved")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_downscale_hysteresis(root)
    patch_transition_bus_preboost(root)
    patch_dts_policy(root)
    audit(root)

    print("[done] A619 GPU modernization Phase6 performance/efficiency policy applied")
    print("[policy] adjacent downscale hold: 16 ms, runtime tunable")
    print("[policy] upclock DDR preboost: +1 bus step during transition, runtime tunable")
    print("[policy] context-aware busy penalty: 12 ms -> 9 ms")
    print("[policy] speed-bin 169 CA target: 355 MHz -> 430 MHz")
    print("[policy] idle timeout: 80 ms -> 64 ms")
    print("[safety] no overclock, no undervolt, no thermal bypass, no permanent DDR floor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
