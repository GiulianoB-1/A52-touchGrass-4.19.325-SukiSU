#!/usr/bin/env python3
"""P450 DAILY diagnostics/guard startup improvement, NO charger enable path.

Source baseline: P449 F, P447G/H sensor inputs. This is STAGING ONLY.
Do not enable charging, modify power policies or claim H1 validation.
"""
import argparse
from pathlib import Path

SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE450_DAILY_GUARD_FG_DIAG_V1"

HELPERS=r'''
/* A52_PHASE450_DAILY_GUARD_FG_DIAG_V1
 * TG sm5714_fg_get_curr() reads SRAM 0x05/0x09; the signed value
 * below is a RAW current indication without TG charger-status gating.
 * Report uA, not guaranteed net charge/discharge classification.
 */
#define A52P450_FG_CURR 0x05
#define A52P450_FG_CURR_AVG 0x09
#define A52P450_CHG_STATUS2 0x0e
#define A52P450_CHG_STATUS3 0x0f
static int a52p450_current_ua(struct a52d_fg *fg, u8 addr)
{
    int word=a52d_sram_read(fg,addr);
    int ma;
    if (word<0) return word;
    ma=((word&0x7fff)*1000)/2044;
    return ((word&0x8000) ? -ma : ma)*1000;
}
static void a52p450_chg_state(struct a52d_fg *fg, const char *action)
{
    int ctrl=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_CNTL1);
    int st1=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_STATUS1);
    int st2=i2c_smbus_read_byte_data(fg->a52g_chg,A52P450_CHG_STATUS2);
    int st3=i2c_smbus_read_byte_data(fg->a52g_chg,A52P450_CHG_STATUS3);
    dev_info(fg->dev,
       "P450 CHG_STATE action=%s en=%d status1=%d status2=%d status3=%d charging=%d topoff=%d read_only=1\n",
       action,ctrl<0?-1:!!(ctrl&BIT(3)),st1,st2,st3,
       st2<0?-1:!!(st2&BIT(3)),st2<0?-1:!!(st2&BIT(5)));
}
'''
WAIT=r'''
    /* Initial Tchg IIO acquisition can lag the I2C FG probe.
     * Only defer the software latch while BOTH register indications
     * independently prove charging is already disabled. Otherwise
     * preserve P449's immediate fail-closed inhibit.
     * This never sets ENQ4FET and never resumes charging.
     */
    if (missing_chg && !batt_trip && !chg_trip && !mix_trip &&
        vbus>0 && !fg->a52g_inhibit_latched &&
        time_before(jiffies,fg->a52p450_started+10*HZ)) {
        int st2=i2c_smbus_read_byte_data(fg->a52g_chg,A52P450_CHG_STATUS2);
        int cntl=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_CNTL1);
        if (st2>=0 && cntl>=0 &&
            !(st2&BIT(3)) && !(cntl&A52G_CHG_ENQ4FET)) {
            dev_info(fg->dev,
                "P450 CHG_GUARD sensor=NOT_READY action=WAIT charging=0 ctrl=%#x status2=%#x grace_ms=10000 never_enable=1\n",
                cntl,st2);
            a52p450_chg_state(fg,"WAIT_SENSOR");
            return 0;
        }
    }
'''
def once(s,old,new,where):
    n=s.count(old)
    if n!=1:raise RuntimeError(f"P450 {where}: expected 1 anchor got {n}")
    return s.replace(old,new,1)
def patch(s):
    if TAG in s:
        if s.count(TAG)!=1:raise RuntimeError("P450 duplicate marker")
        return s
    for a in ("A52_PHASE449F_CHG_MIX_GUARD_V1",
              "A52_PHASE447H_TCHG_GPIO3_IIO_V1",
              "static int a52g_sample(struct a52d_fg *fg, bool first)",
              "static enum power_supply_property a52d_props[]"):
        if a not in s:raise RuntimeError("P450 missing prerequisite "+a)
    s=once(s,"static enum power_supply_property a52d_props[]",
           HELPERS+"\nstatic enum power_supply_property a52d_props[]","current helpers")
    s=once(s,"POWER_SUPPLY_PROP_VOLTAGE_NOW,",
           "POWER_SUPPLY_PROP_VOLTAGE_NOW,\n    POWER_SUPPLY_PROP_CURRENT_NOW,\n    POWER_SUPPLY_PROP_CURRENT_AVG,","FG exposed property list")
    s=once(s,"case POWER_SUPPLY_PROP_VOLTAGE_NOW:",
           '''case POWER_SUPPLY_PROP_CURRENT_NOW:
        rc=a52p450_current_ua(fg,A52P450_FG_CURR);
        if (rc<0 && rc>-4096)return rc;
        v->intval=rc;return 0;
    case POWER_SUPPLY_PROP_CURRENT_AVG:
        rc=a52p450_current_ua(fg,A52P450_FG_CURR_AVG);
        if (rc<0 && rc>-4096)return rc;
        v->intval=rc;return 0;
    case POWER_SUPPLY_PROP_VOLTAGE_NOW:''',"FG read properties")
    s=once(s,"u32 a52g_guard_cycle;","u32 a52g_guard_cycle;\n    unsigned long a52p450_started;","startup epoch")
    s=once(s,"fg->a52g_chg=client;",
           "fg->a52p450_started=jiffies;\n    fg->a52g_chg=client;","init epoch")
    start=s.index("static int a52g_sample(struct a52d_fg *fg, bool first)")
    part=s[start:]
    anchor="if (first || unsafe || fg->a52g_guard_cycle % 15 == 0)"
    if part.count(anchor)!=1:raise RuntimeError("P450 guard log anchor")
    part=part.replace(anchor,WAIT+"\n    "+anchor,1)
    anchor="if (!unsafe && !fg->a52g_inhibit_latched)\n\t\treturn 0;"
    part=once(part,anchor,'''if (!unsafe && !fg->a52g_inhibit_latched) {
        if (first || fg->a52g_guard_cycle % 15 == 1)
            a52p450_chg_state(fg,"MONITOR");
        return 0;
    }''',"normal monitor return")
    anchor="if (!fg->a52g_inhibit_latched || (ctrl & A52G_CHG_ENQ4FET))"
    part=once(part,anchor,'a52p450_chg_state(fg,"AFTER_INHIBIT");\n    '+anchor,"inhibit postread")
    anchor="if (first || fg->a52g_guard_cycle++ % 15 == 0)"
    part=once(part,anchor,'''if (first || fg->a52g_guard_cycle++ % 15 == 0) {
        int now=a52p450_current_ua(fg,A52P450_FG_CURR);
        int avg=a52p450_current_ua(fg,A52P450_FG_CURR_AVG);
        dev_info(fg->dev,
            "P450 FG current_ua=%d avg_ua=%d src=SRAM_RAW signed=1 charging_status=UNVERIFIED\n",
            now,avg);
    }''',"30-second FG current")
    s=s[:start]+part
    return s
def check(root):
    s=(root/SRC).read_text()
    for a in (TAG,"P450 CHG_STATE action=%s",
              "P450 CHG_GUARD sensor=NOT_READY action=WAIT",
              "P450 FG current_ua=%d avg_ua=%d",
              "time_before(jiffies,fg->a52p450_started+10*HZ)",
              "POWER_SUPPLY_PROP_CURRENT_NOW",
              "POWER_SUPPLY_PROP_CURRENT_AVG",
              "A52P450_FG_CURR 0x05","A52P450_FG_CURR_AVG 0x09"):
        if a not in s:raise RuntimeError("P450 source missing "+a)
    if s.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P450 guard may not add charger register writes")
    if s.count("bool unsafe;")!=1:raise RuntimeError("P450 duplicate unsafe")
    if "ctrl & ~A52G_CHG_ENQ4FET" not in s:
        raise RuntimeError("P450 inhibit-only bit3 path changed")
    print("P450 DAILY SOURCE PASS: current read-only, no charge enable, conditional startup wait, status trace")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__": main()
