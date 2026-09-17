#!/usr/bin/env python3
# CI entry point: exact baseline -> runtime-proven P1 -> runtime-proven P2 -> Modern P3
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 106_apply_mglru_modern_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
p1 = here / "106_apply_mglru_modern_p1_core.py"
p2 = here / "107_apply_mglru_modern_p2.py"
p3 = here / "108_apply_mglru_modern_p3.py"

for script in (p1, p2, p3):
    if not script.is_file():
        raise SystemExit(f"missing chained MGLRU script: {script}")

print("Applying runtime-proven Modern MGLRU P1")
subprocess.run([sys.executable, str(p1), str(root)], check=True)

print("Applying runtime-proven Modern MGLRU P2")
subprocess.run([sys.executable, str(p2), str(root)], check=True)

print("Applying Modern MGLRU P3")
subprocess.run([sys.executable, str(p3), str(root)], check=True)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase108-p1-p2-p3-chain.txt").write_text(
    "mglru=phase108-modern-p1-p2-p3\n"
    "baseline_run=35193063669\n"
    "baseline_commit=810113f97015ba235d640bc7578ce1c3ece280f2\n"
    "p1=runtime-proven\n"
    "p2=runtime-proven\n"
    "p2_mmu_notifier_young_fix=1d4832becdc2cdb2cffe2a6050c9d9fd8ff1c58c\n"
    "p3=5e1d25ac2ab670561949d82de7b5027e5a9676d5\n"
    "p3_target=rmap-lookaround-can_swap-correctness\n"
)

print("Modern MGLRU P1 + P2 + P3 applied")
