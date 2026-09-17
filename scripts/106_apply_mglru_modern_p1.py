#!/usr/bin/env python3
# CI entry point: exact baseline -> MGLRU P1/P2/P3 -> scheduler efficiency P1
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
eevdf = here / "109_apply_eevdf_efficiency_p1.py"
eevdf_finalize = here / "109b_finalize_eevdf_efficiency_p1.py"
uclamp = here / "110_apply_uclamp_efficiency_p1.py"

for script in (p1, p2, p3, eevdf, eevdf_finalize, uclamp):
    if not script.is_file():
        raise SystemExit(f"missing chained script: {script}")

print("Applying runtime-proven Modern MGLRU P1")
subprocess.run([sys.executable, str(p1), str(root)], check=True)

print("Applying runtime-proven Modern MGLRU P2")
subprocess.run([sys.executable, str(p2), str(root)], check=True)

print("Applying Modern MGLRU P3")
subprocess.run([sys.executable, str(p3), str(root)], check=True)

print("Applying EEVDF efficiency/correctness P1")
subprocess.run([sys.executable, str(eevdf), str(root)], check=True)
subprocess.run([sys.executable, str(eevdf_finalize), str(root)], check=True)

print("Applying Android17 uclamp efficiency P1")
subprocess.run([sys.executable, str(uclamp), str(root)], check=True)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase108-p1-p2-p3-chain.txt").write_text(
    "mglru=phase108-modern-p1-p2-p3\n"
    "baseline_run=35193063669\n"
    "baseline_commit=810113f97015ba235d640bc7578ce1c3ece280f2\n"
    "p1=runtime-proven\n"
    "p2=runtime-proven\n"
    "p2_mmu_notifier_young_fix=1d4832becdc2cdb2cffe2a6050c9d9fd8ff1c58c\n"
    "p3=c28ac3c7eb945fee6e20f47d576af68fdff1392a\n"
    "p3_target=rmap-lookaround-special-vma-correctness\n"
)
(report_dir / "scheduler-efficiency-p1.txt").write_text(
    "experiment=scheduler-efficiency-p1\n"
    "requested_baseline_run=35199901520\n"
    "requested_baseline_commit=3de2b4cdb394a1c13c8fbfc68e8a081c8aa6a022\n"
    "eevdf=linux-6.17-protection-series-adapted\n"
    "eevdf_no_run_to_parity=not-applicable-always-enabled-backport\n"
    "uclamp=android17-a52-walt-efficiency-bridge\n"
    "uclamp_rt_default_min=0\n"
    "uclamp_task_schedtune_margin_stacking=removed\n"
    "schedtune_abi=retained\n"
    "walt_schedutil=retained\n"
)

print("Modern MGLRU P1 + P2 + P3 applied")
print("Scheduler efficiency P1 applied")
