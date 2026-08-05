#!/usr/bin/env python3
"""Load the checked-in, readable Phase 229 packaging fragments."""
from __future__ import annotations

from pathlib import Path

payload_dir = Path(__file__).resolve().parent / "229_package_parts"
parts = sorted(payload_dir.glob("*.pyfrag"))
if not parts:
    raise SystemExit(f"missing Phase 229 package source fragments: {payload_dir}")
source = "".join(part.read_text(encoding="utf-8") for part in parts)
exec(compile(source, str(payload_dir / "phase229_package_impl.py"), "exec"), globals(), globals())
