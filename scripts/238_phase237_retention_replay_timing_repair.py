#!/usr/bin/env python3
"""Move Phase 238 passive replays beyond the observed recorder retention hole.

The first Phase 238 hardware capture retained records through about 0.556 s and
then resumed at about 148.369 s.  The original 145 s passive replay therefore
ran inside the missing window.  This post-overlay repair changes only the three
Phase 238 delayed-work replay timers in generated C from 145 s to 155 s.  It
does not force reprobes and does not change GPU/GDSC/KGSL behavior.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path.cwd()
OLD = "msecs_to_jiffies(145000)"
NEW = "msecs_to_jiffies(155000)"
TARGETS = (
    Path("drivers/base/dd.c"),
    Path("drivers/base/platform.c"),
    Path("drivers/regulator/a52-legacy-gdsc-regulator.c"),
)


def patch_one(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"Phase 238 replay timing repair missing generated source: {path}")
    text = path.read_text(encoding="utf-8")
    old_count = text.count(OLD)
    new_count = text.count(NEW)
    if old_count != 1 or new_count != 0:
        raise SystemExit(
            f"Phase 238 replay timing repair expected exactly one old timer and no "
            f"new timer in {path}: old={old_count} new={new_count}"
        )
    text = text.replace(OLD, NEW, 1)
    if text.count(OLD) != 0 or text.count(NEW) != 1:
        raise SystemExit(f"Phase 238 replay timing repair verification failed: {path}")
    path.write_text(text, encoding="utf-8")
    print(f"Phase 238 replay timing repair: {path} 145000 -> 155000 ms", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        if OLD == NEW or len(TARGETS) != 3:
            raise SystemExit("Phase 238 replay timing repair self-test failed")
        print("Phase 238 replay timing repair self-test: PASS", flush=True)
        return 0
    for path in TARGETS:
        patch_one(ROOT / path)
    print("Phase 238 retention-safe replay timing repair: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
