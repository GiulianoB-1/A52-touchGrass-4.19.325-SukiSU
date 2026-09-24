#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
grip = kernel / "drivers/sensors/sx9360.c"
vib = kernel / "drivers/vibrator/dc/dc_vibrator.c"

for path in (grip, vib):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1))

# P152: expand the boot-proven P151 async-probe set only to additional
# non-boot-critical peripherals. These drivers are consumers, not providers
# for storage/display/power/clock/interconnect dependencies.
replace_once(
    grip,
    "\t\t.pm = &sx9360_pm_ops\n\t},\n",
    "\t\t.pm = &sx9360_pm_ops,\n"
    "\t\t.probe_type = PROBE_PREFER_ASYNCHRONOUS, /* A52 P152 */\n"
    "\t},\n",
    "SX9364 grip async probe",
)

replace_once(
    vib,
    "\t\t.of_match_table\t= of_match_ptr(dc_vib_dt_ids),\n\t},\n",
    "\t\t.of_match_table\t= of_match_ptr(dc_vib_dt_ids),\n"
    "\t\t.probe_type = PROBE_PREFER_ASYNCHRONOUS, /* A52 P152 */\n"
    "\t},\n",
    "DC vibrator async probe",
)

grip_text = grip.read_text()
vib_text = vib.read_text()

assert grip_text.count("PROBE_PREFER_ASYNCHRONOUS, /* A52 P152 */") == 1
assert vib_text.count("PROBE_PREFER_ASYNCHRONOUS, /* A52 P152 */") == 1

print("A52 P152: second selective asynchronous probe batch applied")
print("  async: SX9364/SX9360 grip sensor")
print("  async: Samsung DC vibrator")
print("  retained from P151: deferred page init + sec_nfc + ET7xx fingerprint")
print("  still synchronous: UFS/display/PMIC/SPMI/clocks/regulators/interconnect/IOMMU/CPUfreq/devfreq/eSE")
print("  untouched for later phases: thermal/Bluetooth/audio/touchscreen/camera core")
