#!/usr/bin/env python3
"""P457 fail-closed source gate for 452-G without forced SMMU probe deferral."""
import argparse
from pathlib import Path

SMMU = Path("drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c")
FG = Path("drivers/a52_447/a52_sm5714_fg_ro.c")
INCLUDE = "#include <linux/of_device.h>"
OLD = "bool scm = qcom_scm_is_available();"
NEW = '''/* A52_PHASE452_SCM_ICE_SMMU_FREEZE_V1:
         * Do NOT defer the APPS/KGSL SMMUs on Lagoon. The existing P451
         * downstream_handoff path must run even if PAS exposes SCM.
         */
        bool scm = !of_machine_is_compatible("qcom,lagoon") &&
                   qcom_scm_is_available();'''

def once(s, old, new, label):
    if s.count(old) != 1:
        raise RuntimeError("P457 expected exactly one " + label)
    return s.replace(old, new, 1)

def check_smmu(base, current):
    # Exact expected delta. This rejects *all* added SMMU early returns,
    # not just one known bad pattern, and preserves all 451 probe semantics.
    expect = once(base, INCLUDE, INCLUDE + "\n#include <linux/of.h>", "include")
    expect = once(expect, OLD, NEW, "SCM guard")
    if current != expect:
        raise RuntimeError("P457 SMMU differs from P451 beyond SCM=false-on-Lagoon: reject any new deferral/return")
    if current.count("return ERR_PTR(-EPROBE_DEFER)") != base.count("return ERR_PTR(-EPROBE_DEFER)"):
        raise RuntimeError("P457 introduced a new SMMU probe defer")
    if "P452 SCM QCOM_SMMU_FROZEN defer" in current:
        raise RuntimeError("P457 fatal SMMU forced-defer marker detected")
    print("P457 SMMU STRICT PASS: same probe as 451; only SCM=false-on-Lagoon")

def check_h0(s):
    required = [
        "A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1",
        "if(a52g_sample(fg,false)<0)",
        "else a52h0_tick(fg);",
        "fg->a52h0_state=\"OFF\";",
        'a52h0_off(fg,"BOOT_PRE_ARM",false);',
        'static DEVICE_ATTR(a52_h0_enable,0600,h0_arm_show,h0_arm_store);',
        "if(fg->a52g_inhibit_latched)",
        "if(bt<150 || bt>410 || ct< -200 || ct>=500",
        "return (*b>=150 && *b<=410 && *c>=-200 && *c<500);",
        "input>0x10",
        "A52H0_MAX_TICKS (14400UL*HZ)",
        "A52H0_FLOAT_4380_CODE 0x27",
        "A52H0_CHG_FAST_500MA 0x20",
        "P452 H0 WDT expired clear FAILED",
        'a52h0_off(fg,"WDT_KICK_FAILED",true)',
    ]
    for marker in required:
        if marker not in s:
            raise RuntimeError("P457 H0 review contract missing: " + marker)
    if s.count("i2c_smbus_write_byte_data(") != 2:
        raise RuntimeError("P457 H0 must have exactly existing guard + audited H0 write callsites")
    start = s.index("static void a52h0_tick(")
    end = s.index("static ssize_t h0_arm_show",start)
    tick = s[start:end]
    sequence = [
        "a52h0_write_masked(fg,A52H0_CHGCNTL2,0xff,A52H0_CHG_FAST_500MA)",
        "a52h0_write_masked(fg,A52H0_WDTCNTL,0x06,A52H0_90S_WDT)",
        "a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(0),BIT(0))",
        "a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3))",
        "a52h0_write_masked(fg,A52G_CHG_CNTL1,A52G_CHG_ENQ4FET,",
    ]
    where = [tick.find(x) for x in sequence]
    if any(x < 0 for x in where) or where != sorted(where):
        raise RuntimeError("P457 H0 reviewed watchdog-before-enable sequencing changed")
    # No direct VBUSCNTL/FACTORY writes: all H0 writes must flow through
    # a52h0_write_masked() and its exact register allowlist.
    h0 = s[s.index("/* A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1 */"):
           s.index("static void a52g_poll(")]
    if h0.count("i2c_smbus_write_byte_data(") != 1:
        raise RuntimeError("P457 H0 unauthorized write site")
    if 'reg!=A52H0_CHGCNTL2 && reg!=A52H0_WDTCNTL &&' not in h0:
        raise RuntimeError("P457 H0 register allowlist changed")
    print("P457 H0 REVIEW PASS: guard-first, VBUS read/refuse, 500mA, pre-enable 90s WDT, opt-in only")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-smmu", type=Path, required=True)
    a=parser.parse_args()
    check_smmu(a.baseline_smmu.read_text(), (a.root/SMMU).read_text())
    check_h0((a.root/FG).read_text())

if __name__=="__main__":
    main()
