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
static void a52h0_state(struct a52d_fg *fg, const char *to,
                        const char *reason)
{
    const char *from=fg->a52h0_state ? fg->a52h0_state : "OFF";
    if (strcmp(from,to)) {
        dev_info(fg->dev,"P452 H0 STATE from=%s to=%s reason=%s\n",
                 from,to,reason);
        fg->a52h0_state=to;
    }
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
    fg->a52h0_active=false;
    if(fg->a52h0_usb && !IS_ERR(fg->a52h0_usb))
        power_supply_changed(fg->a52h0_usb);
    if(fg->a52h0_ac && !IS_ERR(fg->a52h0_ac))
        power_supply_changed(fg->a52h0_ac);
    if(permanent || rc)fg->a52h0_killed=true;
    if(fg->a52h0_killed) {
        fg->a52h0_armed=false;
        a52h0_state(fg,"FAULT_LATCHED",why);
    } else if(!strcmp(why,"TOP_OFF")) {
        a52h0_state(fg,"FULL",why);
    } else if(!strcmp(why,"BOOT_PRE_ARM")) {
        a52h0_state(fg,"HANDOVER",why);
    } else if(!strcmp(why,"MANUAL_DISARM")) {
        a52h0_state(fg,"OFF",why);
    } else {
        fg->a52h0_armed=false;
        fg->a52h0_rearm=true;
        a52h0_state(fg,"STOPPED",why);
    }
    dev_info(fg->dev,"P452 H0 STOP reason=%s\n",why);
    dev_warn(fg->dev,
       "P452 H0 OFF reason=%s rc=%d permanent=%d killed=%d\n",
       why,rc,permanent,fg->a52h0_killed);
    if(fg->psy)power_supply_changed(fg->psy);
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
    int st,fc,flt,ctrl,input,die;
    *vcell=a52d_mv(fg);
    st=a52h0_regread(fg,A52G_CHG_STATUS1);
    *st2=a52h0_regread(fg,A52P450_CHG_STATUS2);
    fc=a52h0_regread(fg,A52H0_FACTORY1);
    flt=a52h0_regread(fg,A52H0_BATREG);
    ctrl=a52h0_regread(fg,A52G_CHG_CNTL1);
    input=a52h0_regread(fg,A52H0_VBUSCNTL);
    die=a52h0_regread(fg,A52G_CHG_DEVICEID);
    if(st<0 || *st2<0 || fc<0 || flt<0 || ctrl<0 || input<0 || die<0 ||
       *vcell<3000 || *vcell>4380)
        return -EIO;
    if(die!=fg->a52h0_id || (fc&(BIT(0)|BIT(1))) ||
       (flt&0x3f)!=A52H0_FLOAT_4380_CODE || input>0x10)
        return -EUCLEAN; /* Immutable hardware safety contract: FAULT */
    *vb=!!(st&BIT(0));
    if(*st2&BIT(7))return -ETIME;
    if(!a52h0_sensor_safe(fg,ntc,chg_ntc))
        return -ERANGE;
    if(!*vb)return -ENOLINK;
    /* H0 prohibits VBUSCNTL writes. Refuse >500mA input rather than
     * pretending to run TG's ramp, which requires that register.
     */
    /* Raw input>0x10 is already a latched FAULT. Never ramp or write VBUSCNTL. */
    /* An expired WDT was handled before temperature checks. */
    if(for_enable && (ctrl&A52G_CHG_ENQ4FET))
        return -EBUSY; /* guard owns bootloader charging handoff */
    return 0;
}
static void a52h0_tick(struct a52d_fg *fg)
{
    int vb=0,mv=0,st2=-1,tbat=-10000,tchg=-10000,rc,wdt,ctrl;
    unsigned long now=jiffies;
    mutex_lock(&fg->a52h0_lock);
    /* Track ten consecutive seconds of both real physical NTCs valid. */
    {
        int bt=a52g_ntc_temp(fg),ct=a52h_chg_ntc_temp(fg);
        if(bt>=-200 && bt<=900 && ct>=-200 && ct<=900) {
            if(!fg->a52h0_ntc_valid) {
                fg->a52h0_ntc_valid=true;
                fg->a52h0_ntc_since=now;
            }
        } else {
            fg->a52h0_ntc_valid=false;
        }
    }
    /* Session timer resets only when unplugged, including while OFF. */
    {
        int st=a52h0_regread(fg,A52G_CHG_STATUS1);
        if(st>=0 && !(st&BIT(0))) {
            fg->a52h0_timer_started=false;
            fg->a52h0_full=false;
        }
    }
    if(fg->a52h0_killed || !fg->a52h0_armed) {
        if(fg->a52h0_active) a52h0_off(fg,"MANUAL_DISARM",false);
        goto done;
    }
    if(fg->a52g_inhibit_latched) {
        a52h0_off(fg,"guard",false);
        goto done;
    }
    rc=a52h0_verify(fg,!fg->a52h0_active,&vb,&mv,&st2,&tbat,&tchg);
    if(rc) {
        if(rc==-ETIME) {
            a52h0_off(fg,"WDT_EXPIRED",true);
            if(a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(6),BIT(6)))
                dev_err(fg->dev,"P452 H0 WDT expired clear FAILED\n");
        } else if(rc==-ERANGE || rc==-ENOLINK)
            a52h0_off(fg,rc==-ERANGE ? "temp" : "vbus",false);
        else
            a52h0_off(fg,"FAULT_VERIFY",true);
        goto done;
    }
    if(fg->a52h0_active) {
        if(time_after_eq(now,fg->a52h0_started+A52H0_MAX_TICKS)) {
            a52h0_off(fg,"timer",false);goto done;
        }
        if(st2&BIT(5)) {
            fg->a52h0_full=true;
            a52h0_off(fg,"TOP_OFF",false);goto done;
        }
        ctrl=a52h0_regread(fg,A52G_CHG_CNTL1);
        wdt=a52h0_regread(fg,A52H0_WDTCNTL);
        if(ctrl<0 || wdt<0 || !(ctrl&A52G_CHG_ENQ4FET) ||
           (wdt&0x07)!=(A52H0_90S_WDT|BIT(0))) {
            a52h0_off(fg,"WDT_OR_ENQ4FET_DRIFT",true);goto done;
        }
        if(time_after_eq(now,fg->a52h0_last_kick+30*HZ)) {
            rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3));
            if(rc){a52h0_off(fg,"WDT_KICK_FAILED",true);goto done;}
            fg->a52h0_last_kick=now;
            dev_info(fg->dev,
               "P452 H0 KICK wdt=90s cycle=30s rc=0 battery=%d chg=%d mv=%d\n",
               tbat,tchg,mv);
        }
        goto done;
    }
    if(fg->a52h0_timer_started &&
       time_after_eq(now,fg->a52h0_started+A52H0_MAX_TICKS)) {
        a52h0_off(fg,"timer",false);goto done;
    }
    if(fg->a52h0_full && mv>4310)goto done;
    if(fg->a52h0_full && mv<=4310) {
        fg->a52h0_full=false;
        dev_info(fg->dev,"P452 H0 RECHARGE vbat=%d\n",mv);
    }
    /* Confirm all limits before setting ENQ4FET. WDT first, always. */
    rc=a52h0_write_masked(fg,A52H0_CHGCNTL2,0xff,A52H0_CHG_FAST_500MA);
    if(rc)goto fail;
    rc=a52h0_write_masked(fg,A52H0_WDTCNTL,0x06,A52H0_90S_WDT);
    if(rc)goto fail;
    rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(0),BIT(0));
    if(rc)goto fail;
    rc=a52h0_write_masked(fg,A52H0_WDTCNTL,BIT(3),BIT(3));
    if(rc)goto fail;
    wdt=a52h0_regread(fg,A52H0_WDTCNTL);
    if(wdt<0 || (wdt&0x7)!=(A52H0_90S_WDT|BIT(0))) {
        rc=-EIO;goto fail;
    }
    rc=a52h0_write_masked(fg,A52G_CHG_CNTL1,A52G_CHG_ENQ4FET,
                           A52G_CHG_ENQ4FET);
    if(rc)goto fail;
    fg->a52h0_active=true;
    if(!fg->a52h0_timer_started) {
        fg->a52h0_started=now;
        fg->a52h0_timer_started=true;
    }
    fg->a52h0_last_kick=now;
    a52h0_state(fg,"CHARGING","enable");
    dev_info(fg->dev,
       "P452 H0 ENABLE cur=500 wdt=90 float=4380 batt=%d chg=%d\n",
       tbat,tchg);
    dev_warn(fg->dev,
       "P452 H0 ON manual=1 WDT=90s kick=before_enable current=500mA float=4380mV battery=%d chg=%d mv=%d readback=PASS\n",
       tbat,tchg,mv);
    if(fg->psy)power_supply_changed(fg->psy);
    if(fg->a52h0_usb && !IS_ERR(fg->a52h0_usb))
        power_supply_changed(fg->a52h0_usb);
    if(fg->a52h0_ac && !IS_ERR(fg->a52h0_ac))
        power_supply_changed(fg->a52h0_ac);
    goto done;
fail:
    a52h0_off(fg,"ENABLE_FAILED",true);
done:
    mutex_unlock(&fg->a52h0_lock);
}
static ssize_t h0_arm_show(struct device *dev,
                           struct device_attribute *attr,char *buf)
{
    struct a52d_fg *fg=dev_get_drvdata(dev);
    return scnprintf(buf,PAGE_SIZE,"armed=%d active=%d killed=%d full=%d\n",
                     fg->a52h0_armed,fg->a52h0_active,
                     fg->a52h0_killed,fg->a52h0_full);
}
static ssize_t h0_arm_store(struct device *dev,
                            struct device_attribute *attr,
                            const char *buf,size_t len)
{
    struct a52d_fg *fg=dev_get_drvdata(dev);
    bool arm;
    if(kstrtobool(buf,&arm))return -EINVAL;
    mutex_lock(&fg->a52h0_lock);
    if(arm) {
        int bt=a52g_ntc_temp(fg),ct=a52h_chg_ntc_temp(fg);
        if(fg->a52h0_killed) {
            mutex_unlock(&fg->a52h0_lock);
            return -EPERM;
        }
        if(!fg->a52h0_ntc_valid ||
           !time_after_eq(jiffies,fg->a52h0_ntc_since+10*HZ)) {
            mutex_unlock(&fg->a52h0_lock);
            return -EAGAIN;
        }
        if(bt<150 || bt>410 || ct< -200 || ct>=500 ||
           ((fg->a52h0_rearm || fg->a52g_inhibit_latched) &&
            (bt<170 || bt>390 || ct>450))) {
            mutex_unlock(&fg->a52h0_lock);
            return -ERANGE;
        }
        /* Only explicit manual re-arm clears P449's historic latch
         * after both physical NTCs recover; next worker runs guard first.
         */
        fg->a52g_inhibit_latched=false;
        fg->a52h0_rearm=false;
        a52h0_state(fg,"ARMED","manual_rearm");
    }
    fg->a52h0_armed=arm;
    if(!arm)a52h0_off(fg,"MANUAL_DISARM",false);
    mutex_unlock(&fg->a52h0_lock);
    dev_warn(fg->dev,"P452 H0 ARM optin=%d killed=%d\n",
             arm,fg->a52h0_killed);
    return len;
}
static ssize_t h0_kill_store(struct device *dev,
                             struct device_attribute *attr,
                             const char *buf,size_t len)
{
    struct a52d_fg *fg=dev_get_drvdata(dev);
    bool kill;
    if(kstrtobool(buf,&kill) || !kill)return -EINVAL;
    mutex_lock(&fg->a52h0_lock);
    fg->a52h0_killed=true;
    fg->a52h0_armed=false;
    a52h0_off(fg,"KILL_SWITCH",true);
    mutex_unlock(&fg->a52h0_lock);
    return len;
}
static DEVICE_ATTR(a52_h0_enable,0600,h0_arm_show,h0_arm_store);
static DEVICE_ATTR(h0_kill,0200,NULL,h0_kill_store);
static void a52h0_attr_cleanup(void *arg)
{
    struct a52d_fg *fg=arg;
    device_remove_file(fg->dev,&dev_attr_h0_kill);
    device_remove_file(fg->dev,&dev_attr_a52_h0_enable);
}
static int a52h0_attrs_create(struct a52d_fg *fg)
{
    int rc=device_create_file(fg->dev,&dev_attr_a52_h0_enable);
    if(rc)return rc;
    rc=device_create_file(fg->dev,&dev_attr_h0_kill);
    if(rc){device_remove_file(fg->dev,&dev_attr_a52_h0_enable);return rc;}
    rc=devm_add_action_or_reset(fg->dev,a52h0_attr_cleanup,fg);
    return rc;
}
/* Minimal ONLINE mirrors TG VBUS_POK. Without MUIC we do not classify cable. */
static enum power_supply_property a52h0_input_props[] = {
    POWER_SUPPLY_PROP_ONLINE,
};
static int a52h0_input_get(struct power_supply *psy,
                           enum power_supply_property prop,
                           union power_supply_propval *val)
{
    struct a52d_fg *fg=power_supply_get_drvdata(psy);
    int st;
    if(prop!=POWER_SUPPLY_PROP_ONLINE)return -EINVAL;
    st=a52h0_regread(fg,A52G_CHG_STATUS1);
    if(st<0)return st;
    val->intval=!!(st&BIT(0));
    return 0;
}
static const struct power_supply_desc a52h0_usb_desc={
    .name="usb", .type=POWER_SUPPLY_TYPE_USB,
    .properties=a52h0_input_props,
    .num_properties=ARRAY_SIZE(a52h0_input_props),
    .get_property=a52h0_input_get,
};
static const struct power_supply_desc a52h0_ac_desc={
    .name="ac", .type=POWER_SUPPLY_TYPE_MAINS,
    .properties=a52h0_input_props,
    .num_properties=ARRAY_SIZE(a52h0_input_props),
    .get_property=a52h0_input_get,
};
static int a52h0_register_inputs(struct a52d_fg *fg)
{
    struct power_supply_config cfg={ .drv_data=fg };
    fg->a52h0_usb=devm_power_supply_register(fg->dev,&a52h0_usb_desc,&cfg);
    if(IS_ERR(fg->a52h0_usb))return PTR_ERR(fg->a52h0_usb);
    fg->a52h0_ac=devm_power_supply_register(fg->dev,&a52h0_ac_desc,&cfg);
    if(IS_ERR(fg->a52h0_ac))return PTR_ERR(fg->a52h0_ac);
    return 0;
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
    bool a52h0_rearm, a52h0_ntc_valid, a52h0_timer_started;
    const char *a52h0_state;
    unsigned long a52h0_ntc_since;
    int a52h0_id;
    struct power_supply *a52h0_usb, *a52h0_ac;
    unsigned long a52h0_started, a52h0_last_kick;""","fields")
    s=once(s,"static void a52g_poll(struct work_struct *work)",
           DECL+"\nstatic void a52g_poll(struct work_struct *work)","controller code")
    s=once(s,"(void)a52g_sample(fg,false);",
           "if(a52g_sample(fg,false)<0) {\n"
           "        mutex_lock(&fg->a52h0_lock);\n"
           "        a52h0_off(fg,\\\"GUARD_IO\\\",true);\n"
           "        mutex_unlock(&fg->a52h0_lock);\n"
           "    } else a52h0_tick(fg);","poll after existing P449 guard")
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
    fg->a52h0_state="OFF";
    fg->a52h0_rearm=true;
    fg->a52h0_id=a52h0_regread(fg,A52G_CHG_DEVICEID);
    if(fg->a52h0_id<0)
        return dev_err_probe(fg->dev,-EIO,"P452 H0 chip ID read failed\n");
    a52h0_off(fg,"BOOT_PRE_ARM",false);
    if (fg->a52h0_killed)
        return dev_err_probe(fg->dev,-EIO,
                 "P452 H0 startup inhibit unverified\n");
    rc=a52h0_attrs_create(fg);
    if (rc)return dev_err_probe(fg->dev,rc,
                        "P452 H0 sysfs arming controls unavailable\n");
    rc=a52h0_register_inputs(fg);
    if (rc)return dev_err_probe(fg->dev,rc,
                        "P452 H0 usb/ac input supplies unavailable\n");
'''+anchor,"attrs after probe")
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
    for key in (TAG,"P452 H0 OFF reason=","P452 H0 ON manual=1",
                "P452 H0 KICK wdt=90s","dev_attr_h0_kill","dev_attr_a52_h0_enable",
                "POWER_SUPPLY_PROP_ONLINE",
                "A52H0_MAX_TICKS (14400UL*HZ)",
                "A52H0_CHG_FAST_500MA 0x20",
                "A52H0_CHGCNTL2 0x18",
                "A52H0_WDTCNTL 0x22",
                "A52H0_FLOAT_4380_CODE 0x27",
                "if(fg->a52g_inhibit_latched)",
                "P452 H0 RECHARGE vbat=",
                "P447G CHG_GUARD INHIBIT_OK"):
        if key not in s:raise RuntimeError("P452 H0 required contract "+key)
    if s.count("i2c_smbus_write_byte_data(")!=2:
        raise RuntimeError("P452 H0 2 generic charge write callsites expected")
    start=s.index(TAG)
    h0=s[start:s.index("static void a52g_poll",start)]
    if "i2c_smbus_write_word_data(" in h0:
        raise RuntimeError("P452 H0 FG/OTP writes prohibited")
    for key in ("input>0x10","P452 H0 STATE from=",
                "P452 H0 ENABLE cur=500","a52h0_ntc_since+10*HZ",
                "P452 H0 RECHARGE vbat=","a52_h0_enable",
                "A52H0_WDTCNTL,0x06","A52H0_WDTCNTL,BIT(0)",
                "a52h0_register_inputs(fg)","P452 H0 WDT expired clear FAILED",
                "GUARD_IO"):
        if key not in s:raise RuntimeError("P452 H0 v2 contract "+key)
    print("P452 H0 SOURCE PASS: guard-first opt-in, handover+thermal hysteresis+FAULT, 500mA, WDT, 4h timer, recharge, strict write allowlist")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
