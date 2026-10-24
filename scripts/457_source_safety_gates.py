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
    # The active-session WDT kick appears before the initial-enable
    # sequence in the tick function. Compare ONLY the enable block,
    # otherwise the first BIT(3) match is the periodic kick.
    enable=tick[tick.index("/* Confirm all limits before setting ENQ4FET."):]
    where = [enable.find(x) for x in sequence]
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
    # H0 must not keep the CPU suspended during a charging session:
    # register a dedicated wakeup_source, acquire before ENQ4FET=1,
    # release in every off path and unregister only after poll cancellation.
    for marker in (
        "#include <linux/pm_wakeup.h>",
        "struct wakeup_source *a52h0_ws;",
        "bool a52h0_wake_held;",
        'wakeup_source_register(fg->dev,"a52-h0-charging")',
        "__pm_stay_awake(fg->a52h0_ws);",
        "fg->a52h0_wake_held=true;",
        "__pm_relax(fg->a52h0_ws);",
        "fg->a52h0_wake_held=false;",
        "wakeup_source_unregister(fg->a52h0_ws);",
    ):
        if marker not in s:
            raise RuntimeError("P457 H0 WAKE_SOURCE gate missing " + marker)
    off=s[s.index("static void a52h0_off("):s.index("static bool a52h0_sensor_safe(")]
    if not (off.index("fg->a52h0_active=false;") <
            off.index("if(fg->a52h0_wake_held)") <
            off.index("__pm_relax(fg->a52h0_ws)") <
            off.index("P452 H0 STOP reason=%s")):
        raise RuntimeError("P457 H0 wake source must be released on every stop")
    on=tick[tick.index("/* Confirm all limits before setting ENQ4FET."):]
    if not (on.index("__pm_stay_awake(fg->a52h0_ws)") <
            on.index("a52h0_write_masked(fg,A52G_CHG_CNTL1,A52G_CHG_ENQ4FET,") <
            on.index("fg->a52h0_active=true;")):
        raise RuntimeError("P457 H0 stay_awake must precede charge enable")
    cleanup=s[s.index("static void a52d_fg_unregister("):s.index("static int a52d_fg_probe(")]
    if not (cleanup.index("cancel_delayed_work_sync(&fg->a52g_guard_work);") <
            cleanup.index("if(fg->a52h0_active)") <
            cleanup.index("a52h0_write_masked(fg,A52G_CHG_CNTL1,") <
            cleanup.index("__pm_relax(fg->a52h0_ws)") <
            cleanup.index("wakeup_source_unregister(fg->a52h0_ws);")):
        raise RuntimeError("P457 H0 teardown must inhibit charging before wake release and after worker cancellation")
    if 'a52h0_off(fg,"REMOVE"' in cleanup or "power_supply_changed" in cleanup:
        raise RuntimeError("P457 H0 teardown must not notify possibly-freed power supplies")
    if 'a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(0),0)' not in cleanup:
        raise RuntimeError("P457 H0 unbind must stop WDT after ENQ4FET disable")

    # One watchdog kick on every active tick. The same guard worker is
    # rescheduled every 10 seconds while H0 is active; it still checks
    # P449 before servicing the watchdog and remains at 2s while inactive.
    poll=s[s.index("static void a52g_poll("):s.index("static enum power_supply_property a52d_props[]")]
    if "fg->a52h0_active ? A52H0_ACTIVE_POLL_MS : A52G_GUARD_POLL_MS" not in poll:
        raise RuntimeError("P457 H0 active 10s / baseline 2s poll gate")
    if "#define A52H0_ACTIVE_POLL_MS 10000" not in s:
        raise RuntimeError("P457 H0 active interval cannot exceed 10s")
    active=tick[tick.index("if(fg->a52h0_active) {"):tick.index("if(fg->a52h0_timer_started &&")]
    if active.count("a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3))")!=1:
        raise RuntimeError("P457 H0 must kick WDT exactly once per active tick")
    if "time_after_eq(now,fg->a52h0_last_kick+" in active or "cycle=30s" in active:
        raise RuntimeError("P457 H0 stale 30s conditional WDT kick")
    if not active.index("WDT_OR_ENQ4FET_DRIFT") < active.index("a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3))"):
        raise RuntimeError("P457 H0 verify WDT before kick")

    # Failures creating the optional H0 controller must LATCH H0 disabled,
    # never return from the existing FG probe before P449 guard scheduling.
    probe=s[s.index("static int a52d_fg_probe("):s.index("static const struct of_device_id a52d_fg_of_match")]
    p0=probe.index('fg->a52h0_state="OFF";')
    p1=probe.index("if (IS_ENABLED(CONFIG_A52_447G_CHG_GUARD))",p0)
    init=probe[p0:p1]
    for marker in (
        "fg->a52h0_killed=true;",
        "P452 H0 INIT_FAIL chip id",
        "P452 H0 INIT_FAIL wakeup source",
        "P452 H0 INIT_FAIL sysfs",
        "P452 H0 INIT_FAIL usb",
        'a52h0_off(fg,"BOOT_PRE_ARM",false);',
    ):
        if marker not in init:
            raise RuntimeError("P457 H0 optional init fail-closed contract " + marker)
    if "return dev_err_probe(" in init or "return -EIO;" in init or "return rc;" in init:
        raise RuntimeError("P457 H0 init must not abort FG/P449 probe")
    if "schedule_delayed_work(&fg->a52g_guard_work" not in probe[p1:]:
        raise RuntimeError("P457 H0 init may suppress periodic P449 guard")
    if "if(fg->a52h0_killed)return" in probe:
        raise RuntimeError("P457 H0 killed state cannot prevent FG probe")

    # Higher-than-float cell voltage is FULL, not FAULT, until 4430mV;
    # WDT errors and genuinely excessive voltage must still latch FAULT.
    verify=s[s.index("static int a52h0_verify("):s.index("static void a52h0_tick(")]
    for marker in (
        "#define A52H0_OVERVOLT_FAULT_MV 4430",
        "*vcell>A52H0_OVERVOLT_FAULT_MV",
        "if(*vcell>4380)return -EOVERFLOW;",
        "if(for_enable && (ctrl&A52G_CHG_ENQ4FET))",
        "return -EALREADY;",
    ):
        if marker not in (s if marker.startswith("#define") else verify):
            raise RuntimeError("P457 H0 voltage/HANDOVER gate missing " + marker)
    for marker in (
        "if(rc==-EOVERFLOW) {",
        "fg->a52h0_full=true;",
        'a52h0_off(fg,"TOP_OFF",false);',
        "if(rc==-EALREADY) {",
        'a52h0_off(fg,"BOOT_PRE_ARM",false);',
    ):
        if marker not in tick:
            raise RuntimeError("P457 H0 FULL/HANDOVER must not latch FAULT " + marker)

    # The minimal H0 cannot classify AC without MUIC, only usb ONLINE.
    if '.name="ac"' in s or "a52h0_ac" in s or "a52h0_ac_desc" in s:
        raise RuntimeError("P457 H0 must not publish a fake AC supply")
    if '.name="usb", .type=POWER_SUPPLY_TYPE_USB' not in s:
        raise RuntimeError("P457 H0 expected one VBUS-backed USB supply")
    print("P457 H0 REVIEW PASS: wake held/cleanup, active 10s WDT kick, no FG probe abort, FULL/HANDOVER, USB only")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-smmu", type=Path, required=True)
    a=parser.parse_args()
    check_smmu(a.baseline_smmu.read_text(), (a.root/SMMU).read_text())
    check_h0((a.root/FG).read_text())

if __name__=="__main__":
    main()
