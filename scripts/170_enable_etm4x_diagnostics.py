#!/usr/bin/env python3
"""P170: enable upstream ETM4X diagnostics on the P169 Clang22+ThinLTO kernel.

Only the defconfig is changed; no MMIO writes, power-handshake bypasses,
bootloader changes, vendor-driver modifications, or debug authorization hacks.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: 170_enable_etm4x_diagnostics.py <kernel-root>")

root = Path(sys.argv[1]).resolve()
config = root / "arch/arm64/configs/a52xq_defconfig"
text = config.read_text()
old = "# CONFIG_CORESIGHT_SOURCE_ETM4X is not set\n"
new = "CONFIG_CORESIGHT_SOURCE_ETM4X=y\n"

for setting in (
    "CONFIG_CORESIGHT=y",
    "CONFIG_CORESIGHT_LINK_AND_SINK_TMC=y",
    "CONFIG_PERF_EVENTS=y",
    "CONFIG_ARM_PMU=y",
    "CONFIG_AUTOFDO_CLANG=y",
    "CONFIG_CC_OPTIMIZE_FOR_PERFORMANCE=y",
    "CONFIG_LTO_CLANG=y",
    "CONFIG_THINLTO=y",
):
    if setting + "\n" not in text:
        raise SystemExit(f"P170 prerequisite missing: {setting}")

if old in text:
    if text.count(old) != 1:
        raise SystemExit("P170 unexpected duplicate ETM4X disabled configs")
    text = text.replace(old, new, 1)
    config.write_text(text)
    print("P170_ETM4X_CONFIG=enabled")
elif new in text and text.count(new) == 1:
    print("P170_ETM4X_CONFIG=already_enabled")
else:
    raise SystemExit("P170 ETM4X defconfig has unknown state")

driver = root / "drivers/hwtracing/coresight/coresight-etm4x.c"
sysfs = root / "drivers/hwtracing/coresight/coresight-etm4x-sysfs.c"
header = root / "drivers/hwtracing/coresight/coresight-etm4x.h"

for path in (driver, sysfs, header):
    if not path.is_file():
        raise SystemExit(f"P170 required source missing: {path}")

driver_text = driver.read_text()
sysfs_text = sysfs.read_text()
header_text = header.read_text()

assert "static void etm4_os_unlock(" in driver_text
assert "writel_relaxed(0x0, drvdata->base + TRCOSLAR)" in driver_text
assert "etm4_init_arch_data" in driver_text
assert "etm4_os_unlock(drvdata);" in driver_text
for register in ("trcauthstatus", "trcoslsr", "trclsr", "trcpdsr", "trcpdcr"):
    if f"dev_attr_{register}.attr" not in sysfs_text:
        raise SystemExit(f"P170 missing ETM read-only sysfs attribute: {register}")
for register in ("TRCOSLAR", "TRCOSLSR", "TRCAUTHSTATUS"):
    if register not in header_text:
        raise SystemExit(f"P170 missing ETM register definition: {register}")

print("P170_ETM4X_STOCK_OS_UNLOCK=present")
print("P170_ETM4X_SYSFS_DIAGNOSTICS=present")
print("P170_O2_CLANG22_THINLTO_AUTOFDO=preserved")
print("P170_DEBUG_AUTHORIZATION=must_be_measured_on_device")
