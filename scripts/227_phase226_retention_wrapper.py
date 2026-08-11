#!/usr/bin/env python3
"""Run inherited parity through Phase251, then restore legacy MSM-bus RPMh.

Phase252 retains the Phase250 GPU-SMMU power-contract correction plus Phase245
FW_DEVLINK_FLAGS_PERMISSIVE, Phase246 subsys tracing, Phase247 CAMCC dense
clk_hws compatibility, Phase248 KGSL/GMU/IOMMU tracing, Phase249 SMMU/IOMMU
root diagnostics, and all Phase243 CX/GX hooks. Phase244 remains skipped.
Phase251 diagnostics remain active. Phase252 restores the hardware-proven
TouchGrass legacy MSM-bus RPMh client/provider contract required by KGSL/GMU,
then applies fail-closed Linux 5.10 API and format compatibility passes to that
pinned 4.19 implementation before compilation.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "227_phase226_retention_wrapper_base.py"
OVERLAYS = (
    ("238_phase237_platform_include_preflight.py", "Phase 238 platform include preflight"),
    ("239_phase238_gpu_cx_vdd_parent_overlay.py", "Phase 239 GPU CX vdd_parent parity"),
    ("238_phase237_broad_gpu_supplier_overlay.py", "Phase 238 broad GPU supplier recorder"),
    ("238_phase237_gdsc_parent_diag_repair.py", "Phase 238 GDSC parent-supply diagnostic repair"),
    ("238_phase237_cx_journal_extension.py", "Phase 238 exact CX late-journal extension"),
    ("238_phase237_cx_driver_walk_extension.py", "Phase 238 exact CX pre-match driver-walk extension"),
    ("238_phase237_controlflow_safety_overlay.py", "Phase 238 control-flow safety pass"),
    ("238_phase237_retention_replay_timing_repair.py", "Phase 238 retention-safe replay timing repair"),
    ("238_phase237_c_indent_sanitize.py", "Phase 238 C indentation sanitize"),
    ("239_phase238_identity_overlay.py", "Phase 239 runtime identity"),
    ("240_phase239_cx_frozen_latch_overlay_v2.py", "Phase 240 CX frozen supplier-gate latch"),
    ("240_phase239_identity_overlay.py", "Phase 240 runtime identity"),
    ("241_phase240_cx_broad_corridor_latch_overlay.py", "Phase 241 broad CX failure-corridor latch"),
    ("241_phase240_cxf241_postcapacity_repair.py", "Phase 241 CXF241 classification/post-capacity retention repair"),
    ("241_phase240_compile_shape_repair.py", "Phase 241 C90 compile-shape repair"),
    ("241_phase240_identity_overlay.py", "Phase 241 runtime identity"),
    ("241_phase240_generated_source_audit.py", "Phase 241 final generated-source reachability audit"),
    ("242_phase241_cx_sticky_state_overlay.py", "Phase 242 compact sticky CX state"),
    ("242_phase241_identity_overlay.py", "Phase 242 runtime identity"),
    ("242_phase241_generated_source_audit.py", "Phase 242 final generated-source audit"),
    ("243_phase242_cxgx_live_supplier_overlay.py", "Phase 243 live CX/GX own-supplier corridor"),
    ("243_phase242_identity_overlay.py", "Phase 243 runtime identity"),
    ("243_phase242_generated_source_audit.py", "Phase 243 final generated-source audit"),
    ("245_phase243_fwdevlink_permissive_overlay.py", "Phase 245 fw_devlink permissive controlled test"),
    ("246_phase245_subsys_initcall_corridor_overlay.py", "Phase 246 subsys initcall corridor"),
    ("247_phase246_camcc_dense_hws_overlay.py", "Phase 247 CAMCC dense clk_hws compatibility"),
    ("248_phase247_kgsl_gmu_iommu_corridor_overlay.py", "Phase 248 KGSL/GMU/IOMMU diagnostic corridor"),
    ("249_phase248_gpu_smmu_enodev_root_overlay.py", "Phase 249 GPU SMMU / GMU ENODEV root diagnostic"),
    ("250_phase249_gpu_smmu_power_contract_overlay.py", "Phase 250 GPU SMMU downstream power contract"),
    ("251_phase250_gmu_post_mmio_tail_diag_overlay.py", "Phase 251 GMU post-MMIO tail diagnostic"),
    ("252_phase251_legacy_msm_bus_rpmh_overlay.py", "Phase 252 legacy MSM-bus RPMh contract"),
    ("252_msm_bus_510_compat.py", "Phase 252 GKI 5.10 MSM-bus compatibility"),
    ("252_msm_bus_510_format_guard.py", "Phase 252 GKI 5.10 MSM-bus format guard"),
)
EXPECTED_PHASE251_ORDER = tuple(name for name, _ in OVERLAYS)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def phase251_self_test() -> int:
    actual = tuple(name for name, _label in OVERLAYS)
    if actual != EXPECTED_PHASE251_ORDER:
        raise RuntimeError(f"Phase 251 overlay order drifted: {actual!r}")
    if len(set(actual)) != len(actual):
        raise RuntimeError("Phase 251 overlay list contains duplicates")
    if any(name.startswith("244_") for name in actual):
        raise RuntimeError("Phase 251 must not apply the broken Phase244 overlay")
    if actual[-10:] != (
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
        "249_phase248_gpu_smmu_enodev_root_overlay.py",
        "250_phase249_gpu_smmu_power_contract_overlay.py",
        "251_phase250_gmu_post_mmio_tail_diag_overlay.py",
        "252_phase251_legacy_msm_bus_rpmh_overlay.py",
        "252_msm_bus_510_compat.py",
        "252_msm_bus_510_format_guard.py",
    ):
        raise RuntimeError("Phase245 -> 246 -> 247 -> 248 -> 249 -> 250 -> 251 -> 252 -> 252compat -> format-guard final ordering drifted")

    for name in actual:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Phase 251 overlay missing: {path}")
        if "gki/common" not in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"Phase 251 overlay lacks generated-tree locator: {name}")

    for name in (
        "239_phase238_gpu_cx_vdd_parent_overlay.py",
        "243_phase242_cxgx_live_supplier_overlay.py",
        "243_phase242_identity_overlay.py",
        "243_phase242_generated_source_audit.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
        "249_phase248_gpu_smmu_enodev_root_overlay.py",
        "250_phase249_gpu_smmu_power_contract_overlay.py",
        "251_phase250_gmu_post_mmio_tail_diag_overlay.py",
        "252_phase251_legacy_msm_bus_rpmh_overlay.py",
        "252_msm_bus_510_compat.py",
        "252_msm_bus_510_format_guard.py",
    ):
        result = subprocess.run([sys.executable, str(ROOT / name), "--self-test"], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase 251 inherited/new self-test failed: {name} rc={result.returncode}")

    p249 = (ROOT / "249_phase248_gpu_smmu_enodev_root_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE249_GPU_SMMU_ENODEV_ROOT_V1",
        "A52_PHASE248_KGSL_GMU_IOMMU_CORRIDOR_V1",
        "K249 S clkon rc=%d",
        "K249 I grp ok=%d",
        "K249 I ret rc=%d s=nogrp",
        "FW_DEVLINK_FLAGS_PERMISSIVE",
    ):
        if token not in p249:
            raise RuntimeError(f"Phase249 retained root overlay missing {token}")

    p250 = (ROOT / "250_phase249_gpu_smmu_power_contract_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1",
        "qcom,regulator-names",
        "devm_regulator_bulk_get",
        "regulator_bulk_enable",
        "K250 S gdscget rc=%d n=%d",
        "K250 S regon rc=%d n=%d",
        "K250 S clkon rc=%d",
    ):
        if token not in p250:
            raise RuntimeError(f"Phase250 corrective overlay missing {token}")

    p251 = (ROOT / "251_phase250_gmu_post_mmio_tail_diag_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE251_GMU_POST_MMIO_TAIL_DIAG_V1",
        "K251 G hfiirq rc=%d",
        "K251 G gmuirq rc=%d",
        "K251 B gpu tbl=%d",
        "K251 B gpu pcl=%u",
        "K251 B cnoc tbl=%d",
        "K251 B cnoc ccl=%u",
        "K251 G gpubw rc=%d",
        "K251 G cnoc rc=%d",
        "K251 G rpmh rc=%d",
        "K251 R bus rc=%d",
        "K251 R gmuvote rc=%d",
    ):
        if token not in p251:
            raise RuntimeError(f"Phase251 diagnostic overlay missing {token}")

    p252 = (ROOT / "252_phase251_legacy_msm_bus_rpmh_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE252_LEGACY_MSM_BUS_RPMH_V1",
        "CONFIG_QCOM_BUS_SCALING",
        "CONFIG_QCOM_BUS_CONFIG_RPMH",
        "msm_bus_fabric_rpmh.o",
        "msm_bus_of_rpmh.o",
        "K251 B gpu tbl=%d",
    ):
        if token not in p252:
            raise RuntimeError(f"Phase252 corrective overlay missing {token}")

    compat = (ROOT / "252_msm_bus_510_compat.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE252_MSM_BUS_GKI510_COMPAT_V1",
        "ktime_to_timespec64",
        "cmd_db_read_aux_data(bcmdev->name, &aux_len)",
        "IS_ERR(aux)",
        "rpmh_invalidate() is void",
        "cur_fal = pdata->usecase_lat[cur_idx].fal_ns",
        "const void *id",
    ):
        if token not in compat:
            raise RuntimeError(f"Phase252 GKI5.10 compatibility pass missing {token}")

    fmt = (ROOT / "252_msm_bus_510_format_guard.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE252_MSM_BUS_GKI510_FORMAT_GUARD_V1",
        "%lld.%09ld",
        "(long long)ts.tv_sec, (long)ts.tv_nsec",
        "#include <linux/ktime.h>",
        "stale unsigned tv_nsec format",
    ):
        if token not in fmt:
            raise RuntimeError(f"Phase252 GKI5.10 format guard missing {token}")

    print(
        "Phase 252 wrapper self-test: PASS (legacy MSM-bus imported, GKI5.10 API+format compatibility audited)",
        flush=True,
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc:
        return rc
    if "--self-test" in args:
        return phase251_self_test()
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc
    print("Phase 252 legacy MSM-bus RPMh ordering and GKI5.10 API/format compatibility completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
