#!/usr/bin/env python3
"""Run inherited Phase 233/234, apply Phase 239 CX fix, then Phase 238 tracing.

Phases 231-233 pin exact legacy-GDSC source hashes. The functional Phase 239
CX vdd_parent repair must therefore run only after the complete Phase 233 chain
returns, and before Phase 238 instruments that source.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "227_phase226_retention_wrapper_base.py"
OVERLAYS = (
    ("238_phase237_platform_include_preflight.py", "Phase 238 platform include preflight"),
    ("239_phase238_cx_vdd_parent_fix.py", "Phase 239 GPU CX vdd_parent fix"),
    ("238_phase237_broad_gpu_supplier_overlay.py", "Phase 238 broad GPU supplier recorder"),
    ("238_phase237_gdsc_parent_diag_repair.py", "Phase 238 GDSC parent-supply diagnostic repair"),
    ("238_phase237_cx_journal_extension.py", "Phase 238 exact CX late-journal extension"),
    ("238_phase237_cx_driver_walk_extension.py", "Phase 238 exact CX pre-match driver-walk extension"),
    ("238_phase237_controlflow_safety_overlay.py", "Phase 238 control-flow safety pass"),
    ("238_phase237_retention_replay_timing_repair.py", "Phase 238 retention-safe replay timing repair"),
    ("238_phase237_c_indent_sanitize.py", "Phase 238 C indentation sanitize"),
)
EXPECTED_PHASE239_ORDER = (
    "238_phase237_platform_include_preflight.py",
    "239_phase238_cx_vdd_parent_fix.py",
    "238_phase237_broad_gpu_supplier_overlay.py",
    "238_phase237_gdsc_parent_diag_repair.py",
    "238_phase237_cx_journal_extension.py",
    "238_phase237_cx_driver_walk_extension.py",
    "238_phase237_controlflow_safety_overlay.py",
    "238_phase237_retention_replay_timing_repair.py",
    "238_phase237_c_indent_sanitize.py",
)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def phase239_self_test() -> int:
    actual = tuple(name for name, _label in OVERLAYS)
    if actual != EXPECTED_PHASE239_ORDER:
        raise RuntimeError(
            "Phase 239 post-parity overlay order drifted: "
            f"actual={actual!r} expected={EXPECTED_PHASE239_ORDER!r}"
        )
    if len(set(actual)) != len(actual):
        raise RuntimeError("Phase 239 post-parity overlay list contains duplicates")

    for name in actual:
        path = ROOT / name
        if not path.is_file():
            raise RuntimeError(f"Phase 239 post-parity overlay missing: {path}")
        text = path.read_text(encoding="utf-8")
        if "gki/common" not in text:
            raise RuntimeError(
                f"Phase 239 overlay lacks generated-tree gki/common locator: {name}"
            )

    timing = (ROOT / "238_phase237_retention_replay_timing_repair.py").read_text(
        encoding="utf-8"
    )
    for token in (
        "locate_generated",
        'cwd / "gki/common"',
        'cwd / "workspace/gki-phase199-src"',
        "TemporaryDirectory",
    ):
        if token not in timing:
            raise RuntimeError(
                f"Phase 238 replay timing locator regression: missing {token}"
            )
    if "ROOT = Path.cwd()" in timing:
        raise RuntimeError(
            "Phase 238 replay timing repair regressed to repository-root-only paths"
        )

    cx = (ROOT / "239_phase238_cx_vdd_parent_fix.py").read_text(encoding="utf-8")
    for token in (
        "A52_PHASE239_GPU_CX_VDD_PARENT_V1",
        '"vdd_parent-supply"',
        '"vdd_parent"',
        "A52_GDSC_VDD_PARENT_LOW_SVS 64",
        "regulator_is_enabled(gdsc->parent_regulator)",
        "regulator_enable(gdsc->parent_regulator)",
    ):
        if token not in cx:
            raise RuntimeError(f"Phase 239 CX fix source missing {token}")

    print(
        "Phase 239 post-parity wrapper self-test: PASS "
        "(CX functional fix precedes retained Phase 238 tracing)",
        flush=True,
    )
    return 0


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc:
        return rc
    if "--self-test" in args:
        return phase239_self_test()

    # A52_PHASE239_POST_PHASE233_ORDER_V1
    # Phase 239 changes only the exact GPU CX vdd_parent contract, then the
    # retained Phase 238 overlays instrument the resulting functional source.
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc

    print("Phase 239 functional fix plus Phase 238 tracing completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
