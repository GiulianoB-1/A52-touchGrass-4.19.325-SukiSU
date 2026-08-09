#!/usr/bin/env python3
"""Run inherited parity through Phase241, then add Phase242 sticky CX state.

Phase242 is diagnostic-only. It preserves Phase239 vdd_parent behavior and all
Phase240/241 source-side hooks, but disables the unreliable Phase241 bulk replay
from the heartbeat path. Existing early records update compact sticky state and
late checkpoints emit only bounded critical summaries before HB.
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
)
EXPECTED_PHASE242_ORDER = (
    "238_phase237_platform_include_preflight.py",
    "239_phase238_gpu_cx_vdd_parent_overlay.py",
    "238_phase237_broad_gpu_supplier_overlay.py",
    "238_phase237_gdsc_parent_diag_repair.py",
    "238_phase237_cx_journal_extension.py",
    "238_phase237_cx_driver_walk_extension.py",
    "238_phase237_controlflow_safety_overlay.py",
    "238_phase237_retention_replay_timing_repair.py",
    "238_phase237_c_indent_sanitize.py",
    "239_phase238_identity_overlay.py",
    "240_phase239_cx_frozen_latch_overlay_v2.py",
    "240_phase239_identity_overlay.py",
    "241_phase240_cx_broad_corridor_latch_overlay.py",
    "241_phase240_cxf241_postcapacity_repair.py",
    "241_phase240_compile_shape_repair.py",
    "241_phase240_identity_overlay.py",
    "241_phase240_generated_source_audit.py",
    "242_phase241_cx_sticky_state_overlay.py",
    "242_phase241_identity_overlay.py",
    "242_phase241_generated_source_audit.py",
)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def phase242_self_test() -> int:
    actual = tuple(name for name, _label in OVERLAYS)
    if actual != EXPECTED_PHASE242_ORDER:
        raise RuntimeError(f"Phase 242 overlay order drifted: actual={actual!r} expected={EXPECTED_PHASE242_ORDER!r}")
    if len(set(actual)) != len(actual):
        raise RuntimeError("Phase 242 overlay list contains duplicates")
    for name in actual:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Phase 242 overlay missing: {path}")
        text = path.read_text(encoding="utf-8")
        if "gki/common" not in text:
            raise RuntimeError(f"Phase 242 overlay lacks generated-tree gki/common locator: {name}")

    vdd = (ROOT / "239_phase238_gpu_cx_vdd_parent_overlay.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE239_GPU_CX_VDD_PARENT_V1", '"vdd_parent-supply"', '"vdd_parent"',
                  "RPMH_REGULATOR_LEVEL_LOW_SVS", "A52GDSC CX_VDD_PARENT_GET_V1",
                  "A52GDSC CX_VDD_PARENT_VOTE_V1"):
        if token not in vdd:
            raise RuntimeError(f"Phase 239 vdd_parent overlay missing {token}")

    compat = ROOT / "240_phase239_cx_frozen_latch_overlay_v2.py"
    if subprocess.run([sys.executable, str(compat), "--self-test"], check=False).returncode:
        raise RuntimeError("Phase 240 latch compatibility self-test failed")

    for name in ("241_phase240_cx_broad_corridor_latch_overlay.py",
                 "241_phase240_cxf241_postcapacity_repair.py",
                 "241_phase240_compile_shape_repair.py",
                 "241_phase240_identity_overlay.py",
                 "241_phase240_generated_source_audit.py"):
        result = subprocess.run([sys.executable, str(ROOT / name), "--self-test"], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase 241 self-test failed: {name} rc={result.returncode}")

    broad = (ROOT / "241_phase240_cx_broad_corridor_latch_overlay.py").read_text(encoding="utf-8")
    for token in ("A52_R241_POP_CAPACITY 24U", "A52_R241_DRV_CAPACITY 32U",
                  "A52_R241_PRB_CAPACITY 48U", "A52_R241_SUP_CAPACITY 48U",
                  "CXF241 create-in", "CXF241 dreg-in", "CXF241 live t=%u"):
        if token not in broad:
            raise RuntimeError(f"Phase 241 broad latch overlay missing {token}")

    retention = (ROOT / "241_phase240_cxf241_postcapacity_repair.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1",
                  "A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1",
                  'return !strncmp(message, "CXF241 ", 7) ||',
                  "critical = a52_r179_is_critical_message(event.message);"):
        if token not in retention:
            raise RuntimeError(f"Phase 241 classification/retention repair missing {token}")

    compile_shape = (ROOT / "241_phase240_compile_shape_repair.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE241_COMPILE_SHAPE_REPAIR_V1",
                  "A52_PHASE241_R240_REPLAY_MAYBE_UNUSED_V1",
                  "A52_PHASE241_OF_DECLARATION_ORDER_V1",
                  "A52_PHASE241_DRIVER_DECLARATION_ORDER_V1",
                  "__maybe_unused a52_r240_cxf_replay"):
        if token not in compile_shape:
            raise RuntimeError(f"Phase 241 compile-shape repair missing {token}")

    audit = (ROOT / "241_phase240_generated_source_audit.py").read_text(encoding="utf-8")
    for token in ('"HB tick=%u', 'a52_ackfr_record("CXF241 live t=%u", tick);',
                  "a52_r241_corridor_replay(tick);", "obsolete Phase240 replay"):
        if token not in audit:
            raise RuntimeError(f"Phase 241 generated-source audit missing {token}")

    for name in ("242_phase241_cx_sticky_state_overlay.py",
                 "242_phase241_identity_overlay.py",
                 "242_phase241_generated_source_audit.py"):
        result = subprocess.run([sys.executable, str(ROOT / name), "--self-test"], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase 242 self-test failed: {name} rc={result.returncode}")

    sticky = (ROOT / "242_phase241_cx_sticky_state_overlay.py").read_text(encoding="utf-8")
    for token in ("A52_PHASE242_CX_STICKY_STATE_V1",
                  "A52_PHASE242_PHASE241_REPLAY_DISABLED_V1",
                  "CXF242 A t=%u", "CXF242 B t=%u", "CXF242 U t=%u",
                  "a52_r242_snapshot(tick);", "a52_r242_sticky_latch(event.message);"):
        if token not in sticky:
            raise RuntimeError(f"Phase 242 sticky overlay missing {token}")

    print("Phase 242 wrapper self-test: PASS (Phase239 behavior retained; Phase240/241 source hooks retained; compact pre-HB sticky state; bulk replay disabled)", flush=True)
    return 0


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc:
        return rc
    if "--self-test" in args:
        return phase242_self_test()

    # A52_PHASE242_POST_PHASE241_STICKY_STATE_ORDER_V1
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc
    print("Phase 242 post-Phase241 compact sticky CX diagnostic ordering completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
