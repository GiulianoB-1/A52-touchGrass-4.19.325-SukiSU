#!/usr/bin/env python3
"""P452 hard blocker: never publish DSP-CHG without genuine H1 charger stack.

A pass here is NECESSARY, not sufficient; charger hardware validation
requires supervised NTC, input-current, VBUS, battery/full/recharge checks.
All checks are source/manifest assertions. No I2C register writes.
"""
from pathlib import Path
import argparse
import json
import re

REQUIRED_FILES = [
 "drivers/mfd/sm/sm5714/sm5714_core.c",
 "drivers/mfd/sm/sm5714/sm5714-irq.c",
 "drivers/battery/charger/sm5714_charger/sm5714_charger.c",
 "drivers/battery/charger/sm5714_charger/sm5714_charger_oper.c",
 "drivers/battery/common/sec_battery.c",
 "drivers/battery/common/sec_battery_dt.c",
 "drivers/battery/common/sec_battery_thermal.c",
 "drivers/battery/common/sec_battery_vote.c",
 "drivers/muic/sm/sm5714/sm5714-muic.c",
]
BANNED_FACTORY_WRITE = re.compile(
    r"\b(?:sm5714_(?:write|update)_reg|i2c_smbus_write_(?:byte|word)_data)"
    r"\s*\([^;]*(?:FACTORY1|0x29|0xea|0xed|0xe4|0xcb)\b",
    re.IGNORECASE | re.DOTALL)
def fail(reason):raise RuntimeError("P452 DSP-CHG NO-GO: "+reason)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--config",type=Path,required=True)
    p.add_argument("--report",type=Path)
    a=p.parse_args()
    root=a.root
    missing=[x for x in REQUIRED_FILES if not (root/x).is_file()]
    if missing:fail("genuine TG H1 drivers are not ported into final GKI tree: "+repr(missing))
    src={x:(root/x).read_text(errors="replace") for x in REQUIRED_FILES}
    merged="\n".join(src.values())
    if "sm5714-charger" not in merged:fail("real SM5714 charger supply missing")
    if "sec_battery" not in merged:fail("Samsung battery policy absent")
    if "WDTCNTL" not in merged or "wdt" not in merged.lower():
        fail("90-second watchdog/charger event mode not audited")
    if BANNED_FACTORY_WRITE.search(merged):
        fail("unsafe TG factory/option register write path still present")
    # Current FG bridge claims the whole siliconmitus,sm5714mfd I2C device.
    # H1 must instead use an MFD parent with child FG + charger and
    # move P449 guard above their writes. Do not allow dual ownership.
    legacy=root/"drivers/a52_447/a52_sm5714_fg_ro.c"
    if legacy.is_file():
        l=legacy.read_text(errors="replace")
        if 'compatible="siliconmitus,sm5714mfd"' in l and (
            "module_i2c_driver(a52d_fg_driver)" in l or
            "module_i2c_driver(" in l):
            fail("P449 FG bridge still registers as SM5714 MFD owner at 0x49; must refactor")
        if "P449" not in l and "P447G CHG_GUARD" not in l:
            fail("P449 safety guard not preserved in real H1")
    config=a.config.read_text()
    # These names may change as the TG files are adapted; check actual
    # built .config and linker object roster separately before release.
    if "# CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT is not set" not in config:
        fail("download-mode default unsafe")
    if "CONFIG_A52_447G_CHG_GUARD=y" not in config:
        fail("P449 guard not enabled")
    if 'battery,charger_name = "sec-direct-charger"' in merged:
        fail("H1 must bind directly to real sm5714-charger, not fast-charge proxy")
    # Fails closed until full runtime, build and DT current groups are
    # cross-checked from the actual packaged DTB.
    fail("H1 source prerequisites found, but no proven 7-supply sec_battery/MUIC cable-table/thermal/WDT/500mA-run-time contract yet")
if __name__=="__main__":main()
