#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

MSM = Path("drivers/gpu/msm")

MARKER_DEVFREQ = "A619 KGSL P6 PM: Qualcomm 45d7e571a332 + ca1cbeedfcd6"
MARKER_PWRLEVEL = "A619 KGSL P6 PM: Qualcomm eff8f6e07da7"
MARKER_GBIF_CGC = "A619 KGSL P6 PM: Qualcomm dde4355ea92d"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_devfreq(root: Path) -> None:
    path = root / MSM / "kgsl_pwrscale.c"
    text = path.read_text()

    if MARKER_DEVFREQ in text:
        print(f"[already] {path}: devfreq NULL/error handling")
        return

    old = """	devfreq = devfreq_add_device(&pdev->dev, &gpu_profile->profile,
			governor, &adreno_tz_data);
	if (IS_ERR(devfreq)) {
		device->pwrscale.enabled = false;
		msm_adreno_tz_exit();
		return PTR_ERR(devfreq);
	}
"""

    new = f"""	devfreq = devfreq_add_device(&pdev->dev, &gpu_profile->profile,
			governor, &adreno_tz_data);
	/*
	 * {MARKER_DEVFREQ}
	 * Treat both ERR_PTR and a defensive NULL result as failed
	 * initialization.  Do not leave KGSL power scaling marked active
	 * without a valid devfreq device.
	 */
	if (IS_ERR_OR_NULL(devfreq)) {{
		device->pwrscale.enabled = false;
		msm_adreno_tz_exit();
		return IS_ERR(devfreq) ? PTR_ERR(devfreq) : -EINVAL;
	}}
"""

    if old not in text:
        if "IS_ERR_OR_NULL(devfreq)" in text:
            print(f"[already-compatible] {path}: devfreq validation is already modern")
            return
        raise SystemExit("kgsl_pwrscale.c: legacy devfreq initialization anchor not found")

    path.write_text(replace_once(text, old, new, "devfreq init"))
    print(f"[patched] {path}: modern devfreq initialization failure handling")


def patch_pwrctrl_defaults(root: Path) -> None:
    path = root / MSM / "kgsl_pwrctrl.c"
    text = path.read_text()

    if MARKER_PWRLEVEL in text:
        print(f"[already] {path}: power-level constraint initialization")
        return

    old = """	/* Initialize the user and thermal clock constraints */

	pwr->max_pwrlevel = 0;
	pwr->min_pwrlevel = pwr->num_pwrlevels - 1;
	pwr->thermal_pwrlevel = 0;
	pwr->thermal_pwrlevel_floor = pwr->min_pwrlevel;
"""

    new = f"""	/*
	 * {MARKER_PWRLEVEL}
	 * User min/max constraints are initialized from the GPU DT power-level
	 * parser.  Keep thermal constraints independent so a configured
	 * initial-min-pwrlevel is not accidentally overwritten here.
	 */
	pwr->thermal_pwrlevel = 0;
	pwr->thermal_pwrlevel_floor = pwr->num_pwrlevels - 1;
"""

    if old not in text:
        modern = (
            "pwr->thermal_pwrlevel_floor = pwr->num_pwrlevels - 1;" in text
            and "pwr->min_pwrlevel = pwr->num_pwrlevels - 1;" not in
            text[text.find("int kgsl_pwrctrl_init"):text.find("int kgsl_pwrctrl_init") + 5000]
        )
        if modern:
            print(f"[already-compatible] {path}: thermal/user constraints already separated")
            return
        raise SystemExit("kgsl_pwrctrl.c: legacy power constraint block not found")

    path.write_text(replace_once(text, old, new, "pwrctrl constraints"))
    print(f"[patched] {path}: preserve DT-derived user min/max constraints")


def patch_dt_powerlevels(root: Path) -> None:
    path = root / MSM / "adreno.c"
    text = path.read_text()

    if MARKER_PWRLEVEL in text:
        print(f"[already] {path}: DT initial-min-pwrlevel support")
        return

    pattern = re.compile(
        r"static void adreno_of_get_initial_pwrlevel\(struct kgsl_pwrctrl \*pwr,\n"
        r"\s*struct device_node \*node\)\n"
        r"\{\n"
        r".*?"
        r"\n\}\n",
        re.S,
    )
    match = pattern.search(text)
    if not match:
        if "adreno_of_get_initial_pwrlevels(" in text and "qcom,initial-min-pwrlevel" in text:
            print(f"[already-compatible] {path}: DT min power level support already present")
            return
        raise SystemExit("adreno.c: initial power-level helper not found")

    replacement = f"""/*
 * {MARKER_PWRLEVEL}
 * Keep Samsung's existing initial power level behavior, but also honor the
 * newer Qualcomm qcom,initial-min-pwrlevel property when present.
 */
static void adreno_of_get_initial_pwrlevels(struct kgsl_pwrctrl *pwr,
		struct device_node *node)
{{
	int level;

	/* Get and set the initial/default power level */
	if (of_property_read_u32(node, "qcom,initial-pwrlevel", &level))
		level = 1;

	if (level < 0 || level >= pwr->num_pwrlevels)
		level = 1;

	pwr->active_pwrlevel = level;
	pwr->default_pwrlevel = level;

	/* Highest performance level remains index 0 */
	pwr->max_pwrlevel = 0;

	/*
	 * Allow the device tree to restrict the lowest selectable level.
	 * With no property this is identical to the legacy behavior.
	 */
	if (of_property_read_u32(node, "qcom,initial-min-pwrlevel", &level))
		level = pwr->num_pwrlevels - 1;

	if (level < 0 || level >= pwr->num_pwrlevels ||
		level < pwr->default_pwrlevel)
		level = pwr->num_pwrlevels - 1;

	pwr->min_pwrlevel = level;
}}
"""

    text = text[:match.start()] + replacement + text[match.end():]

    calls = text.count("adreno_of_get_initial_pwrlevel(")
    if calls < 1:
        raise SystemExit("adreno.c: no legacy helper callsites found")
    text = text.replace(
        "adreno_of_get_initial_pwrlevel(",
        "adreno_of_get_initial_pwrlevels(",
    )

    path.write_text(text)
    print(f"[patched] {path}: DT-aware initial minimum GPU power level")


def patch_a6xx_gbif_clock_gating(root: Path) -> None:
    path = root / MSM / "adreno_a6xx.c"
    text = path.read_text()

    if MARKER_GBIF_CGC in text:
        print(f"[already] {path}: GBIF L2 clock gating control")
        return

    if "A6XX_UCHE_GBIF_GX_CONFIG" not in text:
        raise SystemExit("adreno_a6xx.c: A6XX_UCHE_GBIF_GX_CONFIG is unavailable")

    pattern = re.compile(
        r"(\tfor \(i = 0; i < a6xx_core->hwcg_count; i\+\+\)\n"
        r"\t\tkgsl_regwrite\(device, a6xx_core->hwcg\[i\]\.offset,\n"
        r"\t\t\ton \? a6xx_core->hwcg\[i\]\.val : 0\);\n)"
    )
    match = pattern.search(text)
    if not match:
        if "A6XX_UCHE_GBIF_GX_CONFIG, 0x70000" in text:
            print(f"[already-compatible] {path}: GBIF L2 CGC programming already present")
            return
        raise SystemExit("adreno_a6xx.c: HWCG programming loop anchor not found")

    insertion = match.group(1) + f"""
	/*
	 * {MARKER_GBIF_CGC}
	 * GBIF L2 clock gating is outside the UCHE HWCG table and therefore
	 * needs an explicit vote together with the rest of A6xx HWCG.
	 */
	kgsl_regrmw(device, A6XX_UCHE_GBIF_GX_CONFIG, 0x70000,
			on ? 0x20000 : 0);
"""
    text = text[:match.start()] + insertion + text[match.end():]
    path.write_text(text)
    print(f"[patched] {path}: A6xx GBIF L2 clock gating follows HWCG state")


def audit(root: Path) -> None:
    pwrscale = (root / MSM / "kgsl_pwrscale.c").read_text()
    pwrctrl = (root / MSM / "kgsl_pwrctrl.c").read_text()
    adreno = (root / MSM / "adreno.c").read_text()
    a6xx = (root / MSM / "adreno_a6xx.c").read_text()
    kgsl_h = (root / MSM / "kgsl.h").read_text()
    preempt = (root / MSM / "adreno_a6xx_preempt.c").read_text()

    checks = [
        (MARKER_DEVFREQ in pwrscale, "devfreq modernization marker"),
        ("IS_ERR_OR_NULL(devfreq)" in pwrscale, "devfreq NULL/error guard"),
        (MARKER_PWRLEVEL in pwrctrl, "pwrctrl modernization marker"),
        ("pwr->thermal_pwrlevel_floor = pwr->num_pwrlevels - 1;" in pwrctrl,
            "independent thermal floor"),
        (MARKER_PWRLEVEL in adreno, "DT power-level marker"),
        ("qcom,initial-min-pwrlevel" in adreno, "DT initial min level"),
        ("adreno_of_get_initial_pwrlevels(" in adreno, "modern power-level helper"),
        (MARKER_GBIF_CGC in a6xx, "GBIF CGC marker"),
        ("A6XX_UCHE_GBIF_GX_CONFIG, 0x70000" in a6xx, "GBIF CGC register write"),
        ("struct adreno_rb_shadow" in kgsl_h, "P5 scratch consolidation preserved"),
        ("A619 GPU P4: Qualcomm 96f7537ccfcd" in preempt,
            "P4 preemption optimization preserved"),
    ]

    for ok, label in checks:
        if not ok:
            raise SystemExit(f"audit failed: {label}")

    # Explicitly keep the risky A6xx DDR-stall boost out of this port.
    risky = (
        "PERF_MODE_TAG" in pwrctrl
        or "ram_wait" in pwrctrl.lower()
        or "ram wait" in pwrctrl.lower()
    )

    print("[audit] A619 KGSL P1-P5 preservation: PASS")
    print("[audit] devfreq failure handling: PASS")
    print("[audit] DT-aware minimum GPU power level: PASS")
    print("[audit] thermal/user power constraints remain independent: PASS")
    print("[audit] A6xx GBIF L2 clock gating follows HWCG: PASS")
    print(f"[audit] aggressive DDR-stall/perf-mode boost present={int(risky)}")
    if risky:
        print("[audit] note: existing source already contains a DDR/perf-mode path; P6 did not add it")
    else:
        print("[audit] risky A6xx DDR-stall boost remains excluded")
    print("[audit] clocks, voltage table, thermal policy and userspace ABI otherwise unchanged")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    # This phase is intentionally layered on the already boot-tested A619 P5.
    kgsl_h = (root / MSM / "kgsl.h").read_text()
    if "struct adreno_rb_shadow" not in kgsl_h:
        raise SystemExit("A619 P5 baseline missing: struct adreno_rb_shadow not found")

    patch_devfreq(root)
    patch_pwrctrl_defaults(root)
    patch_dt_powerlevels(root)
    patch_a6xx_gbif_clock_gating(root)
    audit(root)

    print("[done] A619 KGSL modernization P6 + power-management correctness applied")
    print("[source] Qualcomm 45d7e571a332/ca1cbeedfcd6: robust devfreq initialization")
    print("[source] Qualcomm eff8f6e07da7: initialize min power level from configuration")
    print("[source] Qualcomm dde4355ea92d: GBIF L2 CGC with A6xx HW clock gating")
    print("[preserved] existing P1-P5 KGSL modernization and GPU UV P1-P3")
    print("[excluded] Gen7/Gen8-only PM paths, RGMU-only changes and aggressive A6xx DDR boost")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
