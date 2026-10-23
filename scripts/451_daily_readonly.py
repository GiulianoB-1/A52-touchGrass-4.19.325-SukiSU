#!/usr/bin/env python3
"""P451 DAILY read-only SM5714 pre-inhibit register dump and concise FG telemetry.

Preserves Phase450 hardware inhibit. No new I2C writes, no charge enable.
TG 6bf351bd SM5714 named CHGCNTL1..8 (0x17..0x1e) and CHGCNTL11
(0x46). Addresses 0x1f/0x20 are logged RAW, not named CHGCNTL9/10.
"""
import argparse
from pathlib import Path
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE451_DAILY_READONLY_V1"
HELPER=r'''
/* A52_PHASE451_DAILY_READONLY_V1 — no writes: TG charger snapshot */
static void a52p451_chg_regs(struct a52d_fg *fg,const char *why,int vbus)
{
    static const u8 addresses[]={
        0x13,0x14,0x15,0x25,0x22,
        0x17,0x18,0x19,0x1a,0x1b,0x1c,0x1d,0x1e,
        0x1f,0x20,0x46,0x0d,0x0e,0x0f,0x10,0x11
    };
    int i,val;
    dev_info(fg->dev,"P451 CHG_REGS why=%s VBUS=%d begin read_only=1\n",
             why,vbus);
    for (i=0;i<ARRAY_SIZE(addresses);i++) {
        val=i2c_smbus_read_byte_data(fg->a52g_chg,addresses[i]);
        dev_info(fg->dev,"P451 CHG_REG addr=%#x val=%d rc=%d why=%s\n",
                 addresses[i],val,val<0?val:0,why);
    }
    dev_info(fg->dev,"P451 CHG_REGS why=%s end\n",why);
}
'''
def once(s,a,b,n):
    count=s.count(a)
    if count!=1:raise RuntimeError(f"P451 DAILY {n} anchors={count}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:
        if s.count(TAG)!=1:raise RuntimeError("P451 duplicate source marker")
        return s
    for p in ("A52_PHASE450_DAILY_GUARD_FG_DIAG_V1",
              "A52_PHASE449F_CHG_MIX_GUARD_V1",
              "a52p450_chg_state(fg,\"AFTER_INHIBIT\");",
              "a52p450_started=jiffies;"):
        if p not in s:raise RuntimeError("P451 DAILY prerequisite missing "+p)
    s=once(s,"static int a52g_sample(struct a52d_fg *fg, bool first)",
           HELPER+"\nstatic int a52g_sample(struct a52d_fg *fg, bool first)","helper before guard")
    s=once(s,"u32 a52g_guard_cycle;",
           "u32 a52g_guard_cycle;\n    int a52p451_last_vbus;","per-boot VBUS tracker")
    s=once(s,"fg->a52p450_started=jiffies;",
           "fg->a52p450_started=jiffies;\n    fg->a52p451_last_vbus=-2;","tracker init")
    s=once(s,"vbus=st>=0 ? !!(st & BIT(0)) : -1;",
        '''vbus=st>=0 ? !!(st & BIT(0)) : -1;
    if (first || vbus!=fg->a52p451_last_vbus ||
        fg->a52g_guard_cycle % 150 == 0)
        a52p451_chg_regs(fg,first?"BOOT_PRE_INHIBIT":
                        vbus!=fg->a52p451_last_vbus?"VBUS_CHANGED":"FIVE_MIN",vbus);
    fg->a52p451_last_vbus=vbus;''',"reg read before inhibit, VBUS, 5min")
    s=once(s,'a52p450_chg_state(fg,"AFTER_INHIBIT");',
        '''if (first || (ctrl&A52G_CHG_ENQ4FET) ||
        fg->a52g_guard_cycle % 15 == 0)
        a52p450_chg_state(fg,"AFTER_INHIBIT");''',"throttle after inhibit")
    s=once(s,'status_flag=UNVERIFIED\\n"',
           'range_x25=%d aux_bit3=%d status_flag=UNVERIFIED\\n"',"range fields")
    s=once(s,"now,avg,aux,stat,now_rc,avg_rc);",
           "now,avg,aux,stat,now_rc,avg_rc,aux<0?-1:!!(aux&0x400),aux<0?-1:!!(aux&0x8));","FG field args")
    # Status UNKNOWN is intentional: this is an FG-only bridge, not sec_battery.
    old='''    }
    if (first || fg->a52g_guard_cycle++ % 15 == 0)'''
    if old not in s:
        # It is valid for P450 logging block to be inserted adjacent to existing counter.
        pass
    # Add battery-style diagnostic alongside current log without a false CHARGING status.
    s=once(s,'''now,avg,aux,stat,now_rc,avg_rc,aux<0?-1:!!(aux&0x400),aux<0?-1:!!(aux&0x8));
    }''',
        '''now,avg,aux,stat,now_rc,avg_rc,aux<0?-1:!!(aux&0x400),aux<0?-1:!!(aux&0x8));
        dev_info(fg->dev,
            "P451 BATTERY current_mA=%d avg_mA=%d status=Unknown source=FG_NO_SEC_BATTERY\n",
            now/1000,avg/1000);
    }''',"battery current mA with honest status")
    return s
def check(root):
    s=(root/SRC).read_text()
    for marker in (TAG,"BOOT_PRE_INHIBIT","VBUS_CHANGED","FIVE_MIN",
                   "P451 CHG_REGS why=%s","P451 BATTERY current_mA=",
                   "range_x25=%d aux_bit3=%d",
                   "fg->a52p451_last_vbus=-2;"):
        if marker not in s:raise RuntimeError("P451 source contract missing "+marker)
    if s.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P451 diagnostic illegally changes charger write count")
    print("P451 DAILY PASS: pre-inhibit and 5min/VBUS status dump, battery mA, range x2.5, 30s throttle; no charger enables")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
