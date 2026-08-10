#!/usr/bin/env python3
"""Run inherited parity through Phase247, then add Phase248 KGSL/GMU tracing.

Phase248 retains the Phase245 FW_DEVLINK_FLAGS_PERMISSIVE functional state,
Phase246 subsys tracing, Phase247 CAMCC dense-clk_hws compatibility fix, and
all Phase243 CX/GX hooks. Phase244 remains skipped. The only Phase248 change is
diagnostic K248 recording inside the KGSL -> GMU -> IOMMU probe corridor.
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
)
EXPECTED_PHASE248_ORDER = tuple(name for name, _ in OVERLAYS)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def phase248_self_test() -> int:
    actual = tuple(name for name, _label in OVERLAYS)
    if actual != EXPECTED_PHASE248_ORDER:
        raise RuntimeError(f"Phase 248 overlay order drifted: {actual!r}")
    if len(set(actual)) != len(actual):
        raise RuntimeError("Phase 248 overlay list contains duplicates")
    if any(name.startswith("244_") for name in actual):
        raise RuntimeError("Phase 248 must not apply the broken Phase244 overlay")
    if actual[-4:] != (
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
    ):
        raise RuntimeError("Phase245 -> 246 -> 247 -> 248 final ordering drifted")

    for name in actual:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Phase 248 overlay missing: {path}")
        if "gki/common" not in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"Phase 248 overlay lacks generated-tree locator: {name}")

    for name in (
        "239_phase238_gpu_cx_vdd_parent_overlay.py",
        "243_phase242_cxgx_live_supplier_overlay.py",
        "243_phase242_identity_overlay.py",
        "243_phase242_generated_source_audit.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
        "248_phase247_kgsl_gmu_iommu_corridor_overlay.py",
    ):
        result = subprocess.run([sys.executable, str(ROOT / name), "--self-test"], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase 248 inherited/new self-test failed: {name} rc={result.returncode}")

    p248 = (ROOT / "248_phase247_kgsl_gmu_iommu_corridor_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE248_KGSL_GMU_IOMMU_CORRIDOR_V1",
        "A52_PHASE247_CAMCC_DENSE_HWS_V1",
        "K248 A gmu in",
        "K248 G ops in t=%d",
        "K248 M iommu in",
        "K248 I cb in i=%d",
        "K248 C att in n=%.12s",
        "FW_DEVLINK_FLAGS_PERMISSIVE",
        "CXF246 S n=%d f=%ps",
    ):
        if token not in p248:
            raise RuntimeError(f"Phase248 corridor overlay missing {token}")

    print(
        "Phase 248 wrapper self-test: PASS (Phase247 functional state retained; diagnostic KGSL/GMU/IOMMU corridor last)",
        flush=True,
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc:
        return rc
    if "--self-test" in args:
        return phase248_self_test()
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc
    print("Phase 248 KGSL/GMU/IOMMU diagnostic corridor ordering completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
