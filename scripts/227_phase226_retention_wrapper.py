#!/usr/bin/env python3
"""Load the checked-in, readable Phase 230 implementation fragments."""
from __future__ import annotations

from pathlib import Path

payload_dir = Path(__file__).resolve().parent / "230_patcher_parts"
parts = sorted(payload_dir.glob("*.pyfrag"))
if not parts:
    raise SystemExit(f"missing Phase 230 patcher source fragments: {payload_dir}")
source = "".join(part.read_text(encoding="utf-8") for part in parts)
main_guard = '\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
if source.count(main_guard) != 1:
    raise SystemExit(
        f"expected one Phase 230 main guard before extension, found {source.count(main_guard)}"
    )
source = source.replace(main_guard, "\n", 1) + main_guard
exec(compile(source, str(payload_dir / "phase230_patcher_impl.py"), "exec"), globals(), globals())
