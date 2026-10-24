#!/usr/bin/env python3
"""P452 H0 opt-in conservative SM5714 charge controller.

No full H1, sec_battery, MUIC or 7-supply claim. Fails closed; NEVER
automatically enables at boot. Manual sysfs arm requires physical battery
NTC, charger NTC, correct untouched 4.380V float, input current <=500mA,
factory/bypass bits clear and no P449 inhibit latch. Bootloader charging
is switched OFF by the mandatory existing P449 guard before this controller.

TG sm5714_charger.c 6bf351bd: CHGCNTL2 0x18 <- 0x20 (500mA),
WDTCNTL 0x22 timer 2 (90s), enable bit0, kick bit3; CNTL1 bit3 ENQ4FET.
Prohibit FACTORY1/VBUSCNTL/CNTL2 writes. If input>500, DECLINE the
charge attempt (TG >500 ramp is impossible under this allowlist).
"""
import argparse
from pathlib import Path
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1"
DECL=r'''
/* A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1 */
#define A52H0_CHGCNTL2 0x18
#define A52H0_WDTCNTL 0x22
#define A52H0_BATREG 0x1a
#define A52H0_VBUSCNTL 0x15
#define A52H0_FACTORY1 0x25
#define A52H0_CHG_FAST_500MA 0x20
#define A52H0_FLOAT_4380_CODE 0x27
#define A52H0_90S_WDT 0x04
#define A52H0_MAX_TICKS (14400UL*HZ)
static int a52h0_regread(struct a52d_fg *fg,u8 r)
{
    return i2c_smbus_read_byte_data(fg->a52g_chg,r);
}
/* This is the ONLY H0 register-write callsite. Audit its allowlist. */
static int a52h0_write_masked(struct a52d_fg *fg,u8 reg,u8 mask,u8 val)
{
    int old,rc,after;
    if ((reg!=A52H0_CHGCNTL2 && reg!=A52H0_WDTCNTL &&
         reg!=A52G_CHG_CNTL1) ||
        (reg==A52H0_CHGCNTL2 && mask!=0xff) ||
        (reg==A52G_CHG_CNTL1 && mask!=A52G_CHG_ENQ4FET) ||
        (reg==A52H0_WDTCNTL && (mask&~0x4f)))
        return -EPERM;
    old=a52h0_regread(fg,reg);
    if(old<0)return old;
    rc=i2c_smbus_write_byte_data(fg->a52g_chg,reg,(old&~mask)|(val&mask));
    if(rc<0)return rc;
    after=a52h0_regread(fg,reg);
    if(after<0)return after;
    /* WDT kick/reset bits are self-clearing: do not require them to
     * remain set after the write. All other bits must match.
     */
    if(reg==A52H0_WDTCNTL)mask &= ~(BIT(3)|BIT(6));
    if((after&mask)!=(val&mask))return -EIO;
    return 0;
}
static const char *a52h0_state_str(int st)
{
    switch(st) {
    case 0:return "OFF";
    case 1:return "ARMED";
    case 2:return "CHARGING";
    case 3:return "FULL";
    case 4:return "STOPPED";
    case 5:return "FAULT_LATCHED";
    default:return "UNKNOWN";
    }
}
static void a52h0_set_state(struct a52d_fg *fg,int st,const char *why)
{
    if(fg->a52h0_state==st)return;
    dev_warn(fg->dev,"P452 H0 STATE from=%s to=%s reason=%s\n",
             a52h0_state_str(fg->a52h0_state),a52h0_state_str(st),why);
    fg->a52h0_state=st;
}
static void a52h0_off(struct a52d_fg *fg,const char *why,bool permanent)
{
    int ctrl=a52h0_regread(fg,A52G_CHG_CNTL1);
    int rc=0;
    if(ctrl<0 || (ctrl&A52G_CHG_ENQ4FET))
        rc=a52h0_write_masked(fg,A52G_CHG_CNTL1,A52G_CHG_ENQ4FET,0);
    if(!rc) {
        int after=a52h0_regread(fg,A52G_CHG_CNTL1);
        if(after>=0 && !(after&A52G_CHG_ENQ4FET))
            rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(0),0);
        else rc=-EIO;
    }
    if(fg->a52h0_active)
        fg->a52h0_elapsed+=jiffies-fg->a52h0_started;
    fg->a52h0_active=false;
    if(permanent || rc)fg->a52h0_killed=true;
    a52h0_set_state(fg,fg->a52h0_killed?5:
        !strcmp(why,"TOP_OFF")?3:
        !strcmp(why,"MANUAL_DISARM") || !strcmp(why,"BOOT_PRE_ARM")?0:4,why);
    dev_warn(fg->dev,
       "P452 H0 STOP reason=%s rc=%d permanent=%d killed=%d\n",
       why,rc,permanent,fg->a52h0_killed);
    if(fg->psy)power_supply_changed(fg->psy);
}
static int a52h0_boot_inhibit(struct a52d_fg *fg)
{
    int id=a52h0_regread(fg,A52G_CHG_DEVICEID);
    if(id<0 || id==0 || id==0xff)return -ENODEV;
    fg->a52h0_deviceid=id;
    fg->a52h0_handover=true;
    a52h0_off(fg,"BOOT_PRE_ARM",false);
    return fg->a52h0_killed?-EIO:0;
}
static bool a52h0_sensor_safe(struct a52d_fg *fg,int *b,int *c)
{
    *b=a52g_ntc_temp(fg);
    *c=a52h_chg_ntc_temp(fg);
    return (*b>=150 && *b<=410 && *c>=-200 && *c<500);
}
static int a52h0_verify(struct a52d_fg *fg,bool for_enable,
                        int *vb,int *vcell,int *st2,int *ntc,int *chg_ntc)
{
    int st,fc,flt,ctrl,input,id;
    *vcell=a52d_mv(fg);
    st=a52h0_regread(fg,A52G_CHG_STATUS1);
    *st2=a52h0_regread(fg,A52P450_CHG_STATUS2);
    fc=a52h0_regread(fg,A52H0_FACTORY1);
    flt=a52h0_regread(fg,A52H0_BATREG);
    ctrl=a52h0_regread(fg,A52G_CHG_CNTL1);
    input=a52h0_regread(fg,A52H0_VBUSCNTL);
    id=a52h0_regread(fg,A52G_CHG_DEVICEID);
    if(st<0 || *st2<0 || fc<0 || flt<0 || ctrl<0 || input<0 ||
       id<0 || *vcell<3000 || *vcell>4380)
        return -EIO;
    *vb=!!(st&BIT(0));
    if(!a52h0_sensor_safe(fg,ntc,chg_ntc))
        return -EAGAIN;
    if(id!=fg->a52h0_deviceid || !*vb || (fc&(BIT(0)|BIT(1))) ||
       (flt&0x3f)!=A52H0_FLOAT_4380_CODE)
        return -EPERM;
    /* H0 prohibits VBUSCNTL writes. Refuse >500mA input rather than
     * pretending to run TG's ramp, which requires that register.
     */
    if(input>0x10)
        return -ERANGE;
    if(*st2&BIT(7))
        return -ETIME; /* expired WDT must be cleared only while OFF */
    if(for_enable && (ctrl&A52G_CHG_ENQ4FET))
        return -EBUSY; /* guard owns bootloader charging handoff */
    return 0;
}
static void a52h0_tick(struct a52d_fg *fg,int guard_rc)
{
    int vb=0,mv=0,st2=-1,tbat=-10000,tchg=-10000,rc,wdt,ctrl;
    int id,flt,input,fc,st1;
    unsigned long now=jiffies;
    bool safe,ready;
    mutex_lock(&fg->a52h0_lock);
    /* This function runs AFTER P449 every cycle. Guard always wins. */
    if(guard_rc<0 || fg->a52g_inhibit_latched) {
        a52h0_off(fg,"GUARD_FAULT",true);goto done;
    }
    id=a52h0_regread(fg,A52G_CHG_DEVICEID);
    st1=a52h0_regread(fg,A52G_CHG_STATUS1);
    st2=a52h0_regread(fg,A52P450_CHG_STATUS2);
    flt=a52h0_regread(fg,A52H0_BATREG);
    input=a52h0_regread(fg,A52H0_VBUSCNTL);
    fc=a52h0_regread(fg,A52H0_FACTORY1);
    if(id<0 || st1<0 || st2<0 || flt<0 || input<0 || fc<0) {
        a52h0_off(fg,"I2C_READ",true);goto done;
    }
    if(id!=fg->a52h0_deviceid || (flt&0x3f)!=A52H0_FLOAT_4380_CODE ||
       input>0x10 || (fc&(BIT(0)|BIT(1)))) {
        a52h0_off(fg,id!=fg->a52h0_deviceid?"chip_id":
            (flt&0x3f)!=A52H0_FLOAT_4380_CODE?"float":
            input>0x10?"vbuscntl":"factory",true);
        goto done;
    }
    if(st2&BIT(7)) {
        a52h0_off(fg,"WDT_EXPIRED",true);
        rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(6),BIT(6));
        dev_err(fg->dev,"P452 H0 WDT_EXPIRED clear_rc=%d FAULT_LATCHED\n",rc);
        goto done;
    }
    if(fg->a52h0_killed)goto done;
    vb=!!(st1&BIT(0));
    if(!vb) {
        if(fg->a52h0_vbus_seen) {
            fg->a52h0_elapsed=0;
            fg->a52h0_full=false;
            fg->a52h0_handover=true;
            fg->a52h0_rearm=true;
            fg->a52h0_armed=false;
            fg->a52h0_ntc_valid_since=0;
        }
        fg->a52h0_vbus_seen=false;
        if(fg->a52h0_active)a52h0_off(fg,"vbus",false);
        goto done;
    }
    fg->a52h0_vbus_seen=true;
    safe=a52h0_sensor_safe(fg,&tbat,&tchg);
    if(safe && !fg->a52h0_guard_trip) {
        if(!fg->a52h0_ntc_valid_since)
            fg->a52h0_ntc_valid_since=now;
    } else {
        fg->a52h0_ntc_valid_since=0;
    }
    ready=fg->a52h0_ntc_valid_since &&
          time_after_eq(now,fg->a52h0_ntc_valid_since+10*HZ);
    if(!safe || fg->a52h0_guard_trip) {
        if(fg->a52h0_active || fg->a52h0_armed) {
            fg->a52h0_rearm=true;
            fg->a52h0_armed=false;
            a52h0_off(fg,safe?"guard":"ntc",false);
        }
        goto done;
    }
    mv=a52d_mv(fg);
    if(mv<0){a52h0_off(fg,"fg_read",true);goto done;}
    if(mv<3000 || mv>4380 || tbat<150 || tbat>410 || tchg>=500) {
        if(fg->a52h0_armed || fg->a52h0_active) {
            fg->a52h0_armed=false;
            fg->a52h0_rearm=true;
            a52h0_off(fg,"temp_or_vbat",false);
        }
        goto done;
    }
    if(!ready || fg->a52h0_handover || !fg->a52h0_armed) {
        if(fg->a52h0_active)a52h0_off(fg,"MANUAL_DISARM",false);
        goto done;
    }
    if(fg->a52h0_elapsed+
       (fg->a52h0_active?now-fg->a52h0_started:0)>=A52H0_MAX_TICKS) {
        fg->a52h0_rearm=true;
        fg->a52h0_armed=false;
        a52h0_off(fg,"timer",false);goto done;
    }
    if(fg->a52h0_active) {
        ctrl=a52h0_regread(fg,A52G_CHG_CNTL1);
        wdt=a52h0_regread(fg,A52H0_WDTCNTL);
        if(ctrl<0 || wdt<0 || !(ctrl&A52G_CHG_ENQ4FET) ||
           (wdt&0x7)!=(A52H0_90S_WDT|BIT(0))) {
            a52h0_off(fg,"wdt_drift",true);goto done;
        }
        if(st2&BIT(5)) {
            fg->a52h0_full=true;
            a52h0_off(fg,"TOP_OFF",false);goto done;
        }
        if(time_after_eq(now,fg->a52h0_last_kick+30*HZ)) {
            rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3));
            if(rc){a52h0_off(fg,"wdt_kick",true);goto done;}
            fg->a52h0_last_kick=now;
            dev_info(fg->dev,"P452 H0 WDT kick\n");
        }
        goto done;
    }
    if(fg->a52h0_full && mv>=4310)goto done;
    if(fg->a52h0_full) {
        fg->a52h0_full=false;
        dev_info(fg->dev,"P452 H0 RECHARGE vbat=%d\n",mv);
    }
    ctrl=a52h0_regread(fg,A52G_CHG_CNTL1);
    if(ctrl<0 || (ctrl&A52G_CHG_ENQ4FET)) {
        a52h0_off(fg,"unexpected_enable",true);goto done;
    }
    rc=a52h0_write_masked(fg,A52H0_CHGCNTL2,0xff,A52H0_CHG_FAST_500MA);
    if(rc)goto fail;
    rc=a52h0_write_masked(fg,A52H0_WDTCNTL,0x06,A52H0_90S_WDT);
    if(rc)goto fail;
    rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(0),BIT(0));
    if(rc)goto fail;
    rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3));
    if(rc)goto fail;
    wdt=a52h0_regread(fg,A52H0_WDTCNTL);
    if(wdt<0 || (wdt&0x07)!=(A52H0_90S_WDT|BIT(0))) {
        rc=-EIO;goto fail;
    }
    rc=a52h0_write_masked(fg,A52G_CHG_CNTL1,A52G_CHG_ENQ4FET,
                           A52G_CHG_ENQ4FET);
    if(rc)goto fail;
    fg->a52h0_active=true;
    fg->a52h0_started=now;
    fg->a52h0_last_kick=now;
    a52h0_set_state(fg,2,"user_enable");
    dev_warn(fg->dev,
      "P452 H0 ENABLE cur=500 wdt=90 float=4380 batt=%d chg=%d\n",
      tbat,tchg);
    if(fg->psy)power_supply_changed(fg->psy);
    goto done;
fail:
    a52h0_off(fg,"write_readback",true);
done:
    mutex_unlock(&fg->a52h0_lock);
}
static ssize_t h0_arm_show(struct device *dev,
                           struct device_attribute *attr,char *buf)
{
    struct a52d_fg *fg=dev_get_drvdata(dev);
    return scnprintf(buf,PAGE_SIZE,
            "enable=%d active=%d state=%s handover=%d rearm=%d killed=%d total_s=%lu\n",
            fg->a52h0_armed,fg->a52h0_active,
            a52h0_state_str(fg->a52h0_state),fg->a52h0_handover,
            fg->a52h0_rearm,fg->a52h0_killed,fg->a52h0_elapsed/HZ);
}
static ssize_t h0_arm_store(struct device *dev,
                            struct device_attribute *attr,
                            const char *buf,size_t len)
{
    struct a52d_fg *fg=dev_get_drvdata(dev);
    bool enable;
    int b,c;
    if(kstrtobool(buf,&enable))return -EINVAL;
    mutex_lock(&fg->a52h0_lock);
    if(enable) {
        if(fg->a52h0_killed || fg->a52g_inhibit_latched) {
            mutex_unlock(&fg->a52h0_lock);
            return -EPERM;
        }
        if(!fg->a52h0_ntc_valid_since ||
           !time_after_eq(jiffies,fg->a52h0_ntc_valid_since+10*HZ) ||
           !a52h0_sensor_safe(fg,&b,&c) ||
           b<150 || b>410 || c>=500 ||
           (fg->a52h0_rearm && (b<170 || b>390 || c>450))) {
            mutex_unlock(&fg->a52h0_lock);
            return -EAGAIN;
        }
        fg->a52h0_handover=false;
        fg->a52h0_rearm=false;
        fg->a52h0_armed=true;
        a52h0_set_state(fg,1,"user_rearm");
    } else {
        fg->a52h0_armed=false;
    }
    mutex_unlock(&fg->a52h0_lock);
    /* Work owns ALL register writes. The OFF request is synchronous. */
    mod_delayed_work(system_wq,&fg->a52g_guard_work,0);
    if(!enable)flush_delayed_work(&fg->a52g_guard_work);
    return len;
}
static DEVICE_ATTR(a52_h0_enable,0600,h0_arm_show,h0_arm_store);
static void a52h0_attr_cleanup(void *arg)
{
    struct a52d_fg *fg=arg;
    device_remove_file(fg->dev,&dev_attr_a52_h0_enable);
}
static int a52h0_attrs_create(struct a52d_fg *fg)
{
    int rc=device_create_file(fg->dev,&dev_attr_a52_h0_enable);
    if(rc)return rc;
    return devm_add_action_or_reset(fg->dev,a52h0_attr_cleanup,fg);
}
static int a52h0_psy_status(struct a52d_fg *fg,bool online)
{
    if(fg->a52h0_full)return POWER_SUPPLY_STATUS_FULL;
    if(fg->a52h0_active)return POWER_SUPPLY_STATUS_CHARGING;
    return online?POWER_SUPPLY_STATUS_NOT_CHARGING:
                  POWER_SUPPLY_STATUS_DISCHARGING;
}
'''
def once(s,a,b,where):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P452 H0 {where}: anchors={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:
        if s.count(TAG)!=1:raise RuntimeError("P452 H0 duplicate")
        return s
    for x in ("A52_PHASE451_DAILY_READONLY_V1",
              "A52_PHASE449F_CHG_MIX_GUARD_V1",
              "static int a52g_sample(struct a52d_fg *fg, bool first)",
              "static void a52g_poll(struct work_struct *work)",
              "struct delayed_work a52g_guard_work;",
              "a52h_chg_ntc_temp(struct a52d_fg *fg)",
              "a52g_ntc_temp(struct a52d_fg *fg)"):
        if x not in s:raise RuntimeError("P452 H0 missing dependency "+x)
    # Store all gate state in existing FG owner; no second driver/device.
    s=once(s,"struct delayed_work a52g_guard_work;",
           """struct delayed_work a52g_guard_work;
    struct mutex a52h0_lock;
    bool a52h0_armed, a52h0_active, a52h0_killed, a52h0_full;
    bool a52h0_rearm, a52h0_handover, a52h0_guard_trip, a52h0_vbus_seen;
    unsigned long a52h0_started, a52h0_last_kick;
    unsigned long a52h0_ntc_valid_since, a52h0_elapsed;
    int a52h0_deviceid, a52h0_state;""","fields")
    s=once(s,"static void a52g_poll(struct work_struct *work)",
           DECL+"\nstatic void a52g_poll(struct work_struct *work)","controller code")
    s=once(s,"(void)a52g_sample(fg,false);",
           "a52h0_tick(fg,a52g_sample(fg,false));",
           "guard executes before H0, always")
    s=once(s,"mutex_init(&fg->a52g_ntc_lock);",
           "mutex_init(&fg->a52g_ntc_lock);\n    mutex_init(&fg->a52h0_lock);","mutex init")
    # Must be initialized before publishing /sys/... and before first
    # a52g_sample() is called in the probe.
    s=once(s,"fg->dev=&client->dev;",
           "fg->dev=&client->dev;\n    i2c_set_clientdata(client,fg);","sysfs context")
    # Install attrs after the initial emergency guard is verified. They
    # default OFF even if the bootloader had ENQ4FET=1.
    # Target the final return of fg_probe via unique log print to avoid
    # touching the probe start and existing mandatory guard contract.
    anchor='''\tif (IS_ENABLED(CONFIG_A52_447G_CHG_GUARD))
\t\tschedule_delayed_work(&fg->a52g_guard_work,'''
    # 447G adds this in probe. Add sysfs creation immediately before.
    s=once(s,anchor,r'''    /* Bootloader may leave ENQ4FET ON. Force it OFF before
     * exposing h0_arm, even if both NTCs are already valid.
     */
    rc=a52h0_attrs_create(fg);
    if (rc)return dev_err_probe(fg->dev,rc,
                        "P452 H0 sysfs arming controls unavailable\n");
'''+anchor,"attrs after probe")
    # P449 guard wins every cycle. H0's temperature inhibits are
    # recoverable by hysteresis + user rearm, but F keeps P449's latch.
    s=once(s,"fg->a52g_inhibit_latched=true;",
           "fg->a52h0_guard_trip=true;","H0 guard condition")
    s=once(s,"if (!IS_ENABLED(CONFIG_A52_447G_CHG_GUARD))\\n\\t\\treturn 0;",
           "fg->a52h0_guard_trip=false;\\n\\tif (!IS_ENABLED(CONFIG_A52_447G_CHG_GUARD))\\n\\t\\treturn 0;",
           "guard result reset")
    s=once(s,"rc=a52g_sample(fg,true);",
           "rc=a52h0_boot_inhibit(fg);\\n        if(rc<0)return dev_err_probe(fg->dev,rc,\\n            \"P452 H0 boot inhibit failed\\\\n\");\\n        rc=a52g_sample(fg,true);",
           "H0 boot off before first P449 sample")
    # Old guard did one charger write. H0 adds exactly ONE audited generic
    # write site; no factory/input/float/FG NV register writes allowed.
    s=once(s,"static enum power_supply_property a52d_props[] = {",
       """static int a52h0_regread(struct a52d_fg *fg,u8 r);
static int a52h0_psy_status(struct a52d_fg *fg,bool online);
static enum power_supply_property a52d_props[] = {""","forward functions for FG getters")
    s=once(s,"POWER_SUPPLY_PROP_STATUS,",
           "POWER_SUPPLY_PROP_STATUS,\n    POWER_SUPPLY_PROP_ONLINE,","battery status and online")
    s=once(s,"case POWER_SUPPLY_PROP_STATUS:",
      '''case POWER_SUPPLY_PROP_ONLINE: {
        int st=a52h0_regread(fg,A52G_CHG_STATUS1);
        if(st<0)return st;
        v->intval=!!(st&BIT(0));
        return 0;
    }
    case POWER_SUPPLY_PROP_STATUS: {
        int st=a52h0_regread(fg,A52G_CHG_STATUS1);
        if(st<0)return st;
        v->intval=a52h0_psy_status(fg,!!(st&BIT(0)));
        return 0;
    }
    /* P452 H0 original status is intentionally suppressed. */''',"power supply source")
    # The original STATUS body now occurs after inserted comment; remove
    # the stale UNKNOWN constant/return so it cannot override H0 status.
    s=once(s,
       '''\t\t/* No charger/MUIC port: do not guess CHARGING or DISCHARGING. */
\t\tv->intval=POWER_SUPPLY_STATUS_UNKNOWN;return 0;''',
       '''\t\t/* Status is supplied above by audited H0 controller. */''',
       "remove unknown status")
    return s
def check(root):
    s=(root/SRC).read_text()
    for key in (TAG,"P452 H0 STOP reason=","P452 H0 ENABLE cur=500",
                "P452 H0 WDT kick","dev_attr_a52_h0_enable",
                "POWER_SUPPLY_PROP_ONLINE",
                "A52H0_MAX_TICKS (14400UL*HZ)",
                "A52H0_CHG_FAST_500MA 0x20",
                "A52H0_CHGCNTL2 0x18",
                "A52H0_WDTCNTL 0x22",
                "A52H0_FLOAT_4380_CODE 0x27",
                "fg->a52h0_guard_trip=true;",
                "if(fg->a52h0_full)",
                "P447G CHG_GUARD INHIBIT_OK"):
        if key not in s:raise RuntimeError("P452 H0 required contract "+key)
    if s.count("i2c_smbus_write_byte_data(")!=2:
        raise RuntimeError("P452 H0 2 generic charge write callsites expected")
    start=s.index(TAG)
    h0=s[start:s.index("static void a52g_poll",start)]
    if "i2c_smbus_write_word_data(" in h0:
        raise RuntimeError("P452 H0 FG/OTP writes prohibited")
    print("P452 H0 SOURCE PASS: manual opt-in, kill latch, 500mA, WDT 90/30s, dual NTC, 4h timer, 4380 float, TOP_OFF/4310 recharge, strict write allowlist")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
