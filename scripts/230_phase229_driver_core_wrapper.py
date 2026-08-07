#!/usr/bin/env python3
"""Load the checked-in Phase 230 implementation and apply the Phase 235 recorder overlay."""
from __future__ import annotations

import subprocess
import sys
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
phase235_guard = '''
if __name__ == "__main__":
    _phase230_rc = main()
    if not _phase230_rc and "--self-test" not in sys.argv[1:]:
        _phase235_overlay = Path(__file__).with_name(
            "235_phase234_rscc_master_overlay.py"
        )
        if not _phase235_overlay.is_file():
            raise SystemExit(
                f"missing Phase 235 RSCC component-master overlay: {_phase235_overlay}"
            )
        _phase235_run = subprocess.run(
            [sys.executable, str(_phase235_overlay), *sys.argv[1:]],
            check=False,
        )
        _phase230_rc = _phase235_run.returncode
    raise SystemExit(_phase230_rc)
'''
source = source.replace(main_guard, "\n", 1) + phase235_guard
exec(compile(source, str(payload_dir / "phase230_patcher_impl.py"), "exec"), globals(), globals())
