#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 106_apply_mglru_modern_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
p1 = here / "106_apply_mglru_modern_p1_core.py"
p2 = here / "107_apply_mglru_modern_p2.py"

for script in (p1, p2):
    if not script.is_file():
        raise SystemExit(f"missing chained MGLRU script: {script}")

print("Applying runtime-proven Modern MGLRU P1")
subprocess.run([sys.executable, str(p1), str(root)], check=True)

print("Applying Modern MGLRU P2")
subprocess.run([sys.executable, str(p2), str(root)], check=True)

print("Modern MGLRU P1 + P2 applied")
