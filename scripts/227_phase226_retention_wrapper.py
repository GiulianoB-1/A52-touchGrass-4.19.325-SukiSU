#!/usr/bin/env python3
"""Run inherited parity through Phase243, then add Phase244 initcall diagnostics.

Phase244 is diagnostic-only. It preserves Phase239 vdd_parent behavior and all
Phase243 live source hooks, then records only the subsys initcall framework and
legacy GDSC registration boundary with phase-unique critical triple copies.
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
    ("244_phase243_gdsc_subsys_initcall_overlay.py", "Phase 244 GDSC subsys-initcall corridor"),
    ("244_phase243_identity_overlay.py", "Phase 244 runtime identity"),
    ("244_phase243_generated_source_audit.py", "Phase 244 final generated-source audit"),
)
EXPECTED_PHASE244_ORDER = tuple(name for name, _ in OVERLAYS)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def phase244_self_test() -> int:
    actual = tuple(name for name, _label in OVERLAYS)
    if actual != EXPECTED_PHASE244_ORDER:
        raise RuntimeError(f"Phase 244 overlay order drifted: actual={actual!r} expected={EXPECTED_PHASE244_ORDER!r}")
    if len(set(actual)) != len(actual):
        raise RuntimeError("Phase 244 overlay list contains duplicates")
    for name in actual:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Phase 244 overlay missing: {path}")
        text = path.read_text(encoding="utf-8")
        if "gki/common" not in text:
            raise RuntimeError(f"Phase 244 overlay lacks generated-tree gki/common locator: {name}")

    vdd = (ROOT / "239_phase238_gpu_cx_vdd_parent_overlay.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE239_GPU_CX_VDD_PARENT_V1", '"vdd_parent-supply"', '"vdd_parent"',
                  "RPMH_REGULATOR_LEVEL_LOW_SVS", "A52GDSC CX_VDD_PARENT_GET_V1",
                  "A52GDSC CX_VDD_PARENT_VOTE_V1"):
        if token not in vdd:
            raise RuntimeError(f"Phase 239 vdd_parent overlay missing {token}")

    for name in ("240_phase239_cx_frozen_latch_overlay_v2.py",
                 "241_phase240_cx_broad_corridor_latch_overlay.py",
                 "241_phase240_cxf241_postcapacity_repair.py",
                 "241_phase240_compile_shape_repair.py",
                 "241_phase240_identity_overlay.py",
                 "241_phase240_generated_source_audit.py",
                 "242_phase241_cx_sticky_state_overlay.py",
                 "242_phase241_identity_overlay.py",
                 "242_phase241_generated_source_audit.py",
                 "243_phase242_cxgx_live_supplier_overlay.py",
                 "243_phase242_identity_overlay.py",
                 "243_phase242_generated_source_audit.py",
                 "244_phase243_gdsc_subsys_initcall_overlay.py",
                 "244_phase243_identity_overlay.py",
                 "244_phase243_generated_source_audit.py"):
        result = subprocess.run([sys.executable, str(ROOT / name), "--self-test"], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase 244 inherited/new self-test failed: {name} rc={result.returncode}")

    live = (ROOT / "243_phase242_cxgx_live_supplier_overlay.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE243_CXGX_LIVE_SUPPLIER_V1",
                  "A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1",
                  "CXF243 M c=%c q=%d rc=%d",
                  "CXF243 R c=%c q=%d ls=%d",
                  "CXF243 L c=%c q=%d n=%d",
                  "CXF243 G c=%c q=%d rc=%d",
                  "CXF243 P c=%c q=%d"):
        if token not in live:
            raise RuntimeError(f"Phase 243 live-supplier overlay missing {token}")
    p244 = (ROOT / "244_phase243_gdsc_subsys_initcall_overlay.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE244_GDSC_SUBSYS_INITCALL_V1", "CXF244 V q=%d l=%d",
                  "CXF244 I q=%d s=E", "CXF244 I q=%d s=B", "CXF244 I q=%d s=X rc=%d"):
        if token not in p244:
            raise RuntimeError(f"Phase 244 initcall overlay missing {token}")
    print("Phase 244 wrapper self-test: PASS (Phase239 behavior retained; Phase243 hooks retained; initcall/GDSC diagnostics only)", flush=True)
    return 0


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc:
        return rc
    if "--self-test" in args:
        return phase244_self_test()
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc
    print("Phase 244 GDSC subsys-initcall diagnostic ordering completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
