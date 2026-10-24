#!/usr/bin/env python3
"""P453 DAILY: read-only SM5714 battery status using STATUS1/STATUS2.

No charging policy, input-current change, ENQ4FET enable or charger writes.
The bootloader charger enable is transient and P449 guard remains dominant.
"""
import argparse
from pathlib import Path
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE453_DAILY_STATUS1_STATUS2_RO_V1"
STATUS=r'''
/* A52_PHASE453_DAILY_STATUS1_STATUS2_RO_V1
 * Read the actual charger state: STATUS1 VBUS_POK bit0,
 * STATUS2 CHG_ON bit3 and TOP_OFF bit5, plus CNTL1 ENQ4FET bit3.
 * Unlike fuel-gauge current sign this tells the current hardware mode,
 * without allowing software to enable the charger.
 */
static int a52p453_battery_status(struct a52d_fg *fg)
{
    int st1=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_STATUS1);
    int st2=i2c_smbus_read_byte_data(fg->a52g_chg,A52P450_CHG_STATUS2);
    int ctrl=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_CNTL1);
    int val;
    if(st1<0 || st2<0 || ctrl<0) {
        dev_warn_ratelimited(fg->dev,
           "P453 BATTERY STATUS unavailable st1=%d st2=%d ctrl=%d\n",
           st1,st2,ctrl);
        return POWER_SUPPLY_STATUS_UNKNOWN;
    }
    if(!(st1&BIT(0)))val=POWER_SUPPLY_STATUS_DISCHARGING;
    else if((st2&BIT(5)) && (ctrl&A52G_CHG_ENQ4FET))
        val=POWER_SUPPLY_STATUS_FULL;
    else if((st2&BIT(3)) && (ctrl&A52G_CHG_ENQ4FET))
        val=POWER_SUPPLY_STATUS_CHARGING;
    else val=POWER_SUPPLY_STATUS_NOT_CHARGING;
    dev_info_ratelimited(fg->dev,
        "P453 BATTERY STATUS st1=%#x st2=%#x cntl1=%#x status=%d read_only=1\n",
        st1,st2,ctrl,val);
    return val;
}
'''
def once(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P453 {label} anchors {n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for item in ("A52_PHASE451_DAILY_READONLY_V1",
                 "A52_PHASE449F_CHG_MIX_GUARD_V1",
                 "A52P450_CHG_STATUS2","static int a52d_get_property(",
                 "static int a52g_sample(struct a52d_fg *fg, bool first)"):
        if item not in s:raise RuntimeError("P453 missing dependency "+item)
    if "A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1" in s:
        raise RuntimeError("P453 read-only status must not modify H0 controller")
    s=once(s,"static int a52d_get_property(",
       "static int a52p453_battery_status(struct a52d_fg *fg);\n"+
       "static int a52d_get_property(","forward declaration")
    s=once(s,'''		/* No charger/MUIC port: do not guess CHARGING or DISCHARGING. */
		v->intval=POWER_SUPPLY_STATUS_UNKNOWN;return 0;''',
       '''        v->intval=a52p453_battery_status(fg);return 0;''',
       "STATUS getter")
    s=once(s,"static int a52g_sample(struct a52d_fg *fg, bool first)",
        STATUS+"\nstatic int a52g_sample(struct a52d_fg *fg, bool first)",
        "insert implementation")
    s=once(s,"status=Unknown source=FG_NO_SEC_BATTERY",
        "status=STATUS1_STATUS2 source=SM5714_READ_ONLY",
        "correct P451 diagnostic label")
    return s
def check(root):
    s=(root/SRC).read_text()
    for key in (TAG,"v->intval=a52p453_battery_status(fg)",
                "P453 BATTERY STATUS st1=",
                "st1&BIT(0)","st2&BIT(3)","st2&BIT(5)",
                "POWER_SUPPLY_STATUS_UNKNOWN",
                "POWER_SUPPLY_STATUS_NOT_CHARGING",
                "A52_PHASE451_DAILY_READONLY_V1"):
        if key not in s:raise RuntimeError("P453 missing "+key)
    if "A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1" in s:
        raise RuntimeError("P453 status unexpectedly in charger-enabled H0")
    if s.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P453 increased charger write callsites")
    print("P453 BATTERY STATUS PASS: STATUS1/2 and CNTL1 read-only with UNKNOWN on I2C failure")
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args();p=a.root/SRC
    if not a.check_only:p.write_text(patch(p.read_text()))
    check(a.root)
if __name__=="__main__":main()
