#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


MARKER_BUS = "A619 GPU modernization: Qualcomm 536bf34a8db3"
MARKER_BW = "A619 GPU modernization: Qualcomm e94d6c0d8e77"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"{label}: missing required token: {needle}")


def patch_bus_vote_order(kernel: Path) -> None:
    path = kernel / "drivers/gpu/msm/kgsl_pwrctrl.c"
    text = path.read_text()

    if MARKER_BUS in text:
        print(f"[already] {path}: bus vote ordering")
        return

    old = """\t/*
\t * Update the bus before the GPU clock to prevent underrun during
\t * frequency increases.
\t */
\tkgsl_pwrctrl_buslevel_update(device, true);

\tpwrlevel = &pwr->pwrlevels[pwr->active_pwrlevel];
"""

    new = f"""\t/*
\t * {MARKER_BUS}
\t *
\t * For a frequency increase, raise the memory/bus vote before the GPU
\t * clock changes so the faster GPU cannot outrun memory bandwidth.
\t * For a frequency decrease, keep the old bus vote until after the GPU
\t * clock has dropped. This mirrors Qualcomm's later A6xx ordering fix.
\t */
\tif (new_level < old_level ||
\t\t(new_level == old_level &&
\t\t test_bit(GMU_DCVS_REPLAY, &device->gmu_core.flags)))
\t\tkgsl_pwrctrl_buslevel_update(device, true);

\tpwrlevel = &pwr->pwrlevels[pwr->active_pwrlevel];
"""

    text = replace_once(text, old, new, "bus vote pre-clock ordering")

    old = """\ttrace_gpu_frequency(pwrlevel->gpu_freq/1000, 0);

\t/*
\t * Some targets do not support the bandwidth requirement of
"""

    new = """\ttrace_gpu_frequency(pwrlevel->gpu_freq/1000, 0);

\t/*
\t * Complete the Qualcomm bus-vote ordering fix: only lower the bus
\t * after the GPU clock itself has been lowered.
\t */
\tif (new_level > old_level)
\t\tkgsl_pwrctrl_buslevel_update(device, true);

\t/*
\t * Some targets do not support the bandwidth requirement of
"""

    text = replace_once(text, old, new, "bus vote post-clock ordering")
    path.write_text(text)
    print(f"[patched] {path}: Qualcomm 536bf34a8db3 bus vote ordering")


def patch_gpubw_error_path(kernel: Path) -> None:
    path = kernel / "drivers/devfreq/governor_gpubw_mon.c"
    text = path.read_text()

    if MARKER_BW in text:
        print(f"[already] {path}: devfreq error path")
        return

    old = """\tresult = devfreq_update_stats(df);

\t*freq = stats->current_frequency;
"""

    new = f"""\tresult = devfreq_update_stats(df);
\t/*
\t * {MARKER_BW}
\t *
\t * Do not consume stale devfreq statistics when the devfreq core says
\t * the device is not currently available for an update.
\t */
\tif (result)
\t\treturn result;

\t*freq = stats->current_frequency;
"""

    text = replace_once(text, old, new, "gpubw devfreq error handling")
    path.write_text(text)
    print(f"[patched] {path}: Qualcomm e94d6c0d8e77 devfreq guard")


def audit_existing_a619_features(kernel: Path) -> None:
    gpulist_path = kernel / "drivers/gpu/msm/adreno-gpulist.h"
    gpulist = gpulist_path.read_text()
    anchor = gpulist.find("static const struct adreno_a6xx_core adreno_gpu_core_a619")
    if anchor < 0:
        raise SystemExit("A619 GPU definition not found")

    block = gpulist[anchor:anchor + 2600]
    for token in (
        "ADRENO_PREEMPTION",
        "ADRENO_IFPC",
        "ADRENO_IOCOHERENT",
        "ADRENO_RPMH",
        "ADRENO_GPMU",
    ):
        require(block, token, "A619 feature audit")

    adreno = (kernel / "drivers/gpu/msm/adreno.c").read_text()
    require(adreno, ".pwrctrl_flag = BIT(ADRENO_THROTTLING_CTRL) | BIT(ADRENO_HWCG_CTRL)", "HWCG default audit")
    require(adreno, "preempt_level = 1", "A6xx preemption level audit")

    pwrscale = (kernel / "drivers/gpu/msm/kgsl_pwrscale.c").read_text()
    require(pwrscale, "kgsl_midframe", "mid-frame power scaling audit")
    require(pwrscale, '"gpubw_mon"', "GPU bandwidth governor audit")

    tz = (kernel / "drivers/devfreq/governor_msm_adreno_tz.c").read_text()
    require(tz, "ctxt_aware_enable", "context-aware DCVS audit")
    require(tz, "ADRENO_DEVFREQ_NOTIFY_RETIRE", "event-driven DCVS audit")

    dts_root = kernel / "arch/arm64/boot/dts"
    dts_text = ""
    for path in dts_root.rglob("*.dtsi"):
        try:
            chunk = path.read_text(errors="ignore")
        except OSError:
            continue
        if "qcom,kgsl-3d0" in chunk or "qcom,enable-ca-jump" in chunk:
            dts_text += chunk

    for token in ("qcom,enable-ca-jump", "qcom,bus-control", "qcom,gpubw-dev"):
        require(dts_text, token, "Lagoon GPU DT audit")

    print("[audit] A619 IFPC: present")
    print("[audit] A619 preemption: present")
    print("[audit] A619 IO coherency: present")
    print("[audit] A6xx HW clock gating default: present")
    print("[audit] KGSL mid-frame power scaling: present")
    print("[audit] msm-adreno-tz context-aware DCVS: present")
    print("[audit] gpubw_mon dynamic bus scaling: present")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    kernel = Path(sys.argv[1]).resolve()
    if not (kernel / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {kernel}")

    patch_bus_vote_order(kernel)
    patch_gpubw_error_path(kernel)
    audit_existing_a619_features(kernel)

    print("[done] A619 easy GPU modernization bundle applied")
    print("[source] Qualcomm KGSL 536bf34a8db3: correct bus-vote ordering")
    print("[source] Qualcomm KGSL e94d6c0d8e77: stop on devfreq stats failure")
    print("[excluded] high-DDR-stall A6xx bus boost: Qualcomm later identified a severe A6xx power regression")
    print("[excluded] SMMU slumber TLB skip: requires a newer KGSL/io-pgtable architecture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
