#!/usr/bin/env python3
"""Load Phase 230 and apply the Phase 235 + 236 + 237 recorder overlays."""
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
phase237_guard = '''
if __name__ == "__main__":
    _phase230_rc = main()
    if not _phase230_rc and "--self-test" not in sys.argv[1:]:
        for _overlay_name, _overlay_label in (
            ("235_phase234_rscc_master_overlay.py", "Phase 235 RSCC component-master"),
            ("236_phase235_display_init_overlay.py", "Phase 236 display-init"),
            ("237_phase236_ofpop_probe_overlay.py", "Phase 237 OF/platform-probe"),
        ):
            _overlay = Path(__file__).with_name(_overlay_name)
            if not _overlay.is_file():
                raise SystemExit(f"missing {_overlay_label} overlay: {_overlay}")
            _run = subprocess.run(
                [sys.executable, str(_overlay), *sys.argv[1:]],
                check=False,
            )
            _phase230_rc = _run.returncode
            if _phase230_rc:
                break
    raise SystemExit(_phase230_rc)
'''
source = source.replace(main_guard, "\n", 1) + phase237_guard
exec(compile(source, str(payload_dir / "phase230_patcher_impl.py"), "exec"), globals(), globals())
