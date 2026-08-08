#!/usr/bin/env python3
"""Run the inherited Phase 233/234 chain, then apply Phase 238 last.

Phase 231, 232 and 233 pin exact legacy-GDSC source hashes. Phase 238 also
instruments that source, so it must not run from the Phase 230 wrapper. This
entrypoint keeps the original Phase 227 wrapper byte-for-byte in
227_phase226_retention_wrapper_base.py, lets the whole inherited final-parity
chain finish first, and only then applies the Phase 238 recorder overlays.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "227_phase226_retention_wrapper_base.py"
OVERLAYS = (
    ("238_phase237_platform_include_preflight.py", "Phase 238 platform include preflight"),
    ("238_phase237_broad_gpu_supplier_overlay.py", "Phase 238 broad GPU supplier recorder"),
    ("238_phase237_gdsc_parent_diag_repair.py", "Phase 238 GDSC parent-supply diagnostic repair"),
    ("238_phase237_cx_journal_extension.py", "Phase 238 exact CX late-journal extension"),
    ("238_phase237_controlflow_safety_overlay.py", "Phase 238 control-flow safety pass"),
    ("238_phase237_retention_replay_timing_repair.py", "Phase 238 retention-safe replay timing repair"),
    ("238_phase237_c_indent_sanitize.py", "Phase 238 C indentation sanitize"),
)


def run_script(path: Path, args: list[str], label: str) -> int:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {path}")
    result = subprocess.run([sys.executable, str(path), *args], check=False)
    if result.returncode:
        print(f"{label} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def main() -> int:
    args = sys.argv[1:]
    rc = run_script(BASE, args, "Phase 227/233 inherited final-parity base")
    if rc or "--self-test" in args:
        return rc

    # A52_PHASE238_POST_PHASE233_ORDER_V1
    # The GDSC source is SHA-locked by Phases 231/232/233, therefore Phase 238
    # is intentionally the final source overlay after that chain has returned.
    for name, label in OVERLAYS:
        rc = run_script(ROOT / name, args, label)
        if rc:
            return rc

    print("Phase 238 post-Phase233 overlay ordering completed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
