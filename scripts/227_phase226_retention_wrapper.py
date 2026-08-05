#!/usr/bin/env python3
"""Load the Phase 232 GPU GX optional-AON wrapper."""
from __future__ import annotations

from pathlib import Path

source_path = Path(__file__).with_name("232_phase231_optional_aon_wrapper.py")
if not source_path.is_file():
    raise SystemExit(f"missing Phase 232 wrapper: {source_path}")
source = source_path.read_text(encoding="utf-8")
exec(compile(source, str(source_path), "exec"), globals(), globals())
