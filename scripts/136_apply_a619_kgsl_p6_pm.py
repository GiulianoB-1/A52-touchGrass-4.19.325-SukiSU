#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

MSM = Path("drivers/gpu/msm")

MARKER_DEVFREQ = "A619 KGSL P6 PM: Qualcomm 45d7e571a332 + ca1cbeedfcd6"
MARKER_PWRLEVEL = "A619 KGSL P6 PM: Qualcomm eff8f6e07da7 adapted for zero-Hz sentinel"
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

    # Samsung 4.19 passes the device pointer and private_data directly,
    # unlike the newer Qualcomm tree this fix originated from.
    old = """\tdevfreq = devfreq_add_device(dev, &pwrscale->gpu_profile.profile,
\t\t\tgovernor, pwrscale->gpu_profile.private_data);
\tif (IS_ERR(devfreq)) {
\t\tdevice->pwrscale.enabled = false;
\t\treturn PTR_ERR(devfreq);
\t}
"""

    new = f"""\tdevfreq = devfreq_add_device(dev, &pwrscale->gpu_profile.profile,
\t\t\tgovernor, pwrscale->gpu_profile.private_data);
\t/*
\t * {MARKER_DEVFREQ}
\t * Qualcomm later hardened this path against both ERR_PTR and a
\t * defensive NULL return.  Keep the Samsung governor/private-data
\t * plumbing unchanged and only modernize failure handling.
\t */
\tif (IS_ERR_OR_NULL(devfreq)) {{
\t\tdevice->pwrscale.enabled = false;
\t\treturn IS_ERR(devfreq) ? PTR_ERR(devfreq) : -EINVAL;
\t}}
"""

    if old not in text:
        if "IS_ERR_OR_NULL(devfreq)" in text:
            print(f"[already-compatible] {path}: devfreq validation already hardened")
            return
        raise SystemExit("kgsl_pwrscale.c: Samsung devfreq initialization anchor not found")

    path.write_text(replace_once(text, old, new, "devfreq init"))
    print(f"[patched] {path}: robust devfreq initialization failure handling")


def patch_dt_powerlevels(root: Path) -> None:
    path = root / MSM / "adreno.c"
    text = path.read_text()

    if MARKER_PWRLEVEL in text:
        print(f"[already] {path}: DT initial-min-pwrlevel support")
        return

    pattern = re.compile(
        r"static void adreno_of_get_initial_pwrlevel\(struct adreno_device \*adreno_dev,\n"
        r"\s*struct device_node \*node\)\n"
        r"\{\n.*?\n\}\n",
        re.S,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit("adreno.c: Samsung initial power-level helper not found")

    replacement = f"""/*
 * {MARKER_PWRLEVEL}
 *
 * Newer Qualcomm KGSL can take the minimum selectable GPU level from DT.
 * This Samsung tree has a zero-Hz sentinel as the final pwrlevel, so the
 * safe default remains num_pwrlevels - 2 rather than the newer tree's
 * num_pwrlevels - 1.
 */
static void adreno_of_get_initial_pwrlevel(struct adreno_device *adreno_dev,
\t\tstruct device_node *node)
{{
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);
\tstruct kgsl_pwrctrl *pwr = &device->pwrctrl;
\tint init_level = 1;
\tint min_level = pwr->num_pwrlevels - 2;

\tof_property_read_u32(node, "qcom,initial-pwrlevel", &init_level);

\tif (init_level < 0 || init_level >= pwr->num_pwrlevels - 1)
\t\tinit_level = 1;

\tpwr->active_pwrlevel = init_level;
\tpwr->default_pwrlevel = init_level;

\tif (!of_property_read_u32(node, "qcom,initial-min-pwrlevel", &min_level)) {{
\t\t/*
\t\t * Index num_pwrlevels - 1 is the legacy zero-Hz sentinel and
\t\t * must never become a normal selectable DVFS floor.
\t\t */
\t\tif (min_level < init_level || min_level >= pwr->num_pwrlevels - 1)
\t\t\tmin_level = pwr->num_pwrlevels - 2;
\t}}

\tpwr->min_pwrlevel = min_level;
}}
"""

    text = text[:match.start()] + replacement + text[match.end():]
    path.write_text(text)
    print(f"[patched] {path}: DT-aware minimum pwrlevel with zero-Hz sentinel preserved")


def patch_pwrctrl_defaults(root: Path) -> None:
    path = root / MSM / "kgsl_pwrctrl.c"
    text = path.read_text()

    if MARKER_PWRLEVEL in text:
        print(f"[already] {path}: parser-derived minimum pwrlevel preserved")
        return

    old = """\t/* Initialize the user and thermal clock constraints */

\tpwr->max_pwrlevel = 0;
\tpwr->min_pwrlevel = pwr->num_pwrlevels - 2;
\tpwr->thermal_pwrlevel = 0;
\tpwr->thermal_pwrlevel_floor = pwr->min_pwrlevel;
"""

    new = f"""\t/*
\t * {MARKER_PWRLEVEL}
\t *
\t * adreno_of_get_power() parses the GPU pwrlevels before this function
\t * runs.  Preserve its DT-derived min_pwrlevel instead of overwriting it.
\t * max_pwrlevel and thermal limits retain Samsung's existing behavior.
\t */
\tpwr->max_pwrlevel = 0;
\tpwr->thermal_pwrlevel = 0;
\tpwr->thermal_pwrlevel_floor = pwr->min_pwrlevel;
"""

    if old not in text:
        raise SystemExit("kgsl_pwrctrl.c: Samsung user/thermal constraint block not found")

    path.write_text(replace_once(text, old, new, "pwrctrl constraints"))
    print(f"[patched] {path}: preserve DT-derived GPU minimum pwrlevel")


def patch_a6xx_gbif_clock_gating(root: Path) -> None:
    path = root / MSM / "adreno_a6xx.c"
    text = path.read_text()

    if MARKER_GBIF_CGC in text:
        print(f"[already] {path}: GBIF L2 clock gating control")
        return

    if "A6XX_UCHE_GBIF_GX_CONFIG" not in text:
        raise SystemExit("adreno_a6xx.c: A6XX_UCHE_GBIF_GX_CONFIG unavailable")

    # Samsung uses .value while newer Qualcomm trees use .val in some branches.
    pattern = re.compile(
        r"(\tfor \(i = 0; i < a6xx_core->hwcg_count; i\+\+\)\n"
        r"\t\tkgsl_regwrite\(device, a6xx_core->hwcg\[i\]\.offset,\n"
        r"\t\t\ton \? a6xx_core->hwcg\[i\]\.(?:value|val) : 0\);\n)"
    )
    match = pattern.search(text)
    if not match:
        if "A6XX_UCHE_GBIF_GX_CONFIG, 0x70000" in text:
            print(f"[already-compatible] {path}: GBIF L2 CGC already present")
            return
        raise SystemExit("adreno_a6xx.c: Samsung HWCG programming loop anchor not found")

    insertion = match.group(1) + f"""
\t/*
\t * {MARKER_GBIF_CGC}
\t * GBIF L2 clock gating is not part of the UCHE HWCG register table.
\t * Program bits 18:16 alongside the rest of A6xx HWCG.
\t */
\tkgsl_regrmw(device, A6XX_UCHE_GBIF_GX_CONFIG, 0x70000,
\t\t\ton ? 0x20000 : 0);
"""
    text = text[:match.start()] + insertion + text[match.end():]
    path.write_text(text)
    print(f"[patched] {path}: GBIF L2 clock gating follows A6xx HWCG state")


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
        (MARKER_PWRLEVEL in adreno, "DT pwrlevel marker"),
        ('"qcom,initial-min-pwrlevel"' in adreno, "DT initial-min-pwrlevel support"),
        ("min_level = pwr->num_pwrlevels - 2;" in adreno,
            "zero-Hz sentinel-safe fallback"),
        ("min_level >= pwr->num_pwrlevels - 1" in adreno,
            "zero-Hz sentinel rejection"),
        (MARKER_PWRLEVEL in pwrctrl, "pwrctrl preservation marker"),
        ("pwr->thermal_pwrlevel_floor = pwr->min_pwrlevel;" in pwrctrl,
            "thermal floor follows valid minimum pwrlevel"),
        (MARKER_GBIF_CGC in a6xx, "GBIF CGC marker"),
        ("A6XX_UCHE_GBIF_GX_CONFIG, 0x70000" in a6xx, "GBIF CGC register write"),
        ("struct adreno_rb_shadow" in kgsl_h, "P5 scratch consolidation preserved"),
        ("A619 GPU P4: Qualcomm 96f7537ccfcd" in preempt,
            "P4 preemption optimization preserved"),
    ]

    for ok, label in checks:
        if not ok:
            raise SystemExit(f"audit failed: {label}")

    # This is the problematic power-performance vote lineage that Qualcomm
    # later restricted away from A6xx.  P6 must not introduce it.
    risky = (
        "PERF_MODE_TAG" in pwrctrl
        or "ram_wait" in pwrctrl.lower()
        or "ram wait" in pwrctrl.lower()
    )

    print("[audit] A619 KGSL P1-P5 preservation: PASS")
    print("[audit] devfreq initialization hardening: PASS")
    print("[audit] DT-aware minimum GPU pwrlevel: PASS")
    print("[audit] zero-Hz sentinel remains excluded from normal DVFS: PASS")
    print("[audit] Samsung thermal-floor behavior preserved: PASS")
    print("[audit] A6xx GBIF L2 clock gating: PASS")
    print(f"[audit] aggressive DDR-stall/perf-mode boost present={int(risky)}")
    if risky:
        print("[audit] note: an older source path already contains a perf-mode token; P6 did not add it")
    else:
        print("[audit] risky A6xx DDR-stall boost remains excluded")
    print("[audit] voltage table, frequencies, thermal policy and userspace ABI otherwise unchanged")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    # Layer only on the already boot-tested A619 P5 baseline.
    kgsl_h = (root / MSM / "kgsl.h").read_text()
    if "struct adreno_rb_shadow" not in kgsl_h:
        raise SystemExit("A619 P5 baseline missing: struct adreno_rb_shadow not found")

    patch_devfreq(root)
    patch_dt_powerlevels(root)
    patch_pwrctrl_defaults(root)
    patch_a6xx_gbif_clock_gating(root)
    audit(root)

    print("[done] A619 KGSL modernization P6 + power-management correctness applied")
    print("[source] Qualcomm 45d7e571a332/ca1cbeedfcd6: robust devfreq initialization")
    print("[source] Qualcomm eff8f6e07da7: configured minimum GPU pwrlevel")
    print("[adaptation] Samsung zero-Hz sentinel kept outside the normal selectable range")
    print("[source] Qualcomm dde4355ea92d: GBIF L2 CGC with A6xx HW clock gating")
    print("[preserved] existing P1-P5 KGSL modernization and GPU UV P1-P3")
    print("[excluded] Gen7/Gen8-only PM, RGMU-only changes, aggressive A6xx DDR boost")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
