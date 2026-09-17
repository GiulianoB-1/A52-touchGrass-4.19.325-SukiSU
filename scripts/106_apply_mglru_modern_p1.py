#!/usr/bin/env python3
# CI entry point: exact baseline -> runtime-proven P1 -> Modern P2
# P2 rerun: baseline-safe MMU-notifier include insertion
# CPU UV probe: read-only qcom-cpufreq-hw hardware voltage LUT exposure
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 106_apply_mglru_modern_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
p1 = here / "106_apply_mglru_modern_p1_core.py"
p2 = here / "107_apply_mglru_modern_p2.py"
cpu_probe = here / "107_apply_cpu_uv_probe.py"

for script in (p1, p2, cpu_probe):
    if not script.is_file():
        raise SystemExit(f"missing chained patch script: {script}")

print("Applying runtime-proven Modern MGLRU P1")
subprocess.run([sys.executable, str(p1), str(root)], check=True)

print("Applying Modern MGLRU P2")
subprocess.run([sys.executable, str(p2), str(root)], check=True)

print("Applying read-only qcom-cpufreq-hw CPU voltage LUT probe")
subprocess.run([sys.executable, str(cpu_probe), str(root)], check=True)

cpufreq_hw = root / "drivers/cpufreq/qcom-cpufreq-hw.c"
probe_text = cpufreq_hw.read_text()
for needle in (
    "A52 CPU UV PROBE: read-only qcom-cpufreq-hw voltage LUT",
    "show_a52_cpu_uv_probe",
    "cpufreq_freq_attr_ro(a52_cpu_uv_probe)",
    "&a52_cpu_uv_probe,",
):
    if needle not in probe_text:
        raise SystemExit(f"CPU UV probe verification failed: missing {needle!r}")

print("Modern MGLRU P1 + P2 + CPU UV read-only probe applied")
