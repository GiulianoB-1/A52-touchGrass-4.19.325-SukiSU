#!/usr/bin/env python3
"""Load the Phase 233 final graphics parity wrapper."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
source_path = root / "233_phase232_final_graphics_parity_wrapper.py"
if not source_path.is_file():
    payload = root / "233_payload.py"
    if not payload.is_file():
        raise SystemExit(f"missing Phase 233 payload: {payload}")
    subprocess.run([sys.executable, str(payload)], check=True)
if not source_path.is_file():
    raise SystemExit(f"missing Phase 233 wrapper after payload: {source_path}")
source = source_path.read_text(encoding="utf-8")
exec(compile(source, str(source_path), "exec"), globals(), globals())
