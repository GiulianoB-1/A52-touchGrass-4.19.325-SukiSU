#!/usr/bin/env python3
"""Run inherited parity through Phase246, then add Phase247 CAMCC compatibility.

Phase247 retains the Phase245 FW_DEVLINK_FLAGS_PERMISSIVE functional state,
Phase246 subsys-initcall tracing, and all Phase243 CX/GX hooks. Phase244 remains
skipped. The only Phase247 functional change is conversion of Lagoon CAMCC's
TouchGrass sparse hwclks representation into the dense clk_hws registration
list expected by exact GKI 5.10.
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
)
EXPECTED_PHASE247_ORDER = tuple(name for name, _ in OVERLAYS)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def phase247_self_test() -> int:
    actual = tuple(name for name, _label in OVERLAYS)
    if actual != EXPECTED_PHASE247_ORDER:
        raise RuntimeError(f"Phase 247 overlay order drifted: {actual!r}")
    if len(set(actual)) != len(actual):
        raise RuntimeError("Phase 247 overlay list contains duplicates")
    if any(name.startswith("244_") for name in actual):
        raise RuntimeError("Phase 247 must not apply the broken Phase244 overlay")
    if actual[-3:] != (
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
    ):
        raise RuntimeError("Phase245 permissive -> Phase246 recorder -> Phase247 CAMCC order is not final")

    for name in actual:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Phase 247 overlay missing: {path}")
        if "gki/common" not in path.read_text(encoding="utf-8"):
            raise RuntimeError(f"Phase 247 overlay lacks generated-tree locator: {name}")

    for name in (
        "239_phase238_gpu_cx_vdd_parent_overlay.py",
        "243_phase242_cxgx_live_supplier_overlay.py",
        "243_phase242_identity_overlay.py",
        "243_phase242_generated_source_audit.py",
        "245_phase243_fwdevlink_permissive_overlay.py",
        "246_phase245_subsys_initcall_corridor_overlay.py",
        "247_phase246_camcc_dense_hws_overlay.py",
    ):
        result = subprocess.run([sys.executable, str(ROOT / name), "--self-test"], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase 247 inherited/new self-test failed: {name} rc={result.returncode}")

    p245 = (ROOT / "245_phase243_fwdevlink_permissive_overlay.py").read_text(encoding="utf-8")
    if "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;" not in p245:
        raise RuntimeError("Phase245 permissive compiled-default patch is missing")

    p246 = (ROOT / "246_phase245_subsys_initcall_corridor_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE246_SUBSYS_INITCALL_CORRIDOR_V1",
        "do_initcall_level",
        "char\\s*\\*\\s*command_line",
        "CXF246 V q=%d l=%d",
        "CXF246 S n=%d f=%ps",
        "CXF246 X q=%d n=%d",
        "FW_DEVLINK_FLAGS_PERMISSIVE",
    ):
        if token not in p246:
            raise RuntimeError(f"Phase246 overlay missing {token}")

    p247 = (ROOT / "247_phase246_camcc_dense_hws_overlay.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE247_CAMCC_DENSE_HWS_V1",
        "cam_cc_lagoon_hws",
        "CAM_CC_PLL2_OUT_EARLY",
        "&cam_cc_pll2_out_early.hw",
        "FW_DEVLINK_FLAGS_PERMISSIVE",
        "CXF246 S n=%d f=%ps",
    ):
        if token not in p247:
            raise RuntimeError(f"Phase247 CAMCC overlay missing {token}")

    print(
        "Phase 247 wrapper self-test: PASS (Phase245 permissive + Phase246 recorder retained; CAMCC dense clk_hws last)",
        flush=True,
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc:
        return rc
    if "--self-test" in args:
        return phase247_self_test()
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc
    print("Phase 247 CAMCC dense-clk_hws compatibility ordering completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
