#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"
if not dd.is_file():
    raise SystemExit(f"missing required source: {dd}")

s = dd.read_text()
marker = "A52 P161: expanded async probe census capacity"

if marker in s:
    print("A52 P161 expanded census already applied")
    raise SystemExit(0)

old = "#define A52_PROBE_CENSUS_MAX 512"
new = """/*
 * A52 P161: expanded async probe census capacity.
 *
 * P160 reached fail=0/defer=0 in the tracked table, but the 512-entry
 * census overflowed by 103 records.  Expand the diagnostic table so the
 * next hardware run can prove whether any hidden async probe failures
 * remain instead of inferring from the truncated sample.
 */
#define A52_PROBE_CENSUS_MAX 2048"""

if s.count(old) != 1:
    raise SystemExit(f"P161 census-capacity anchor count={s.count(old)}")

s = s.replace(old, new, 1)

for token in (
    marker,
    "#define A52_PROBE_CENSUS_MAX 2048",
    "A52 P156 EARLY PROBE CENSUS",
    "A52_PROBE_CENSUS CHECKPOINT_END",
):
    if token not in s:
        raise SystemExit(f"P161 audit missing: {token}")

if "#define A52_PROBE_CENSUS_MAX 512" in s:
    raise SystemExit("P161 old 512-entry census still present")

dd.write_text(s)

print("A52 P161 expanded async-probe census applied")
print("  capacity: 512 -> 2048 records")
print("  checkpoints remain: 2s / 4s / 6s / 8s")
print("  P160 DWC3 fix and P159 sync exceptions unchanged")
