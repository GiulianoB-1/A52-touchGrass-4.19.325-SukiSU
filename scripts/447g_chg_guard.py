#!/usr/bin/env python3
"""Phase447G emergency thermal inhibit, based ONLY on TG charger_disable bit.

Pinned TG 6bf351bd:
  drivers/battery/charger/sm5714_charger/sm5714_charger.c
  chg_set_enq4fet(false) -> sm5714_update_reg(CHG_REG_CNTL1,0,BIT(3)).
  psy_chg_get_online -> CHG_REG_STATUS1 bit0 (VBUSPOK).
  include/linux/mfd/sm/sm5714/sm5714-private.h
  charger I2C address 0x49, STATUS1=0x0d CNTL1=0x13 DEVICEID=0x50.

This is a TEMPORARY protection, not full charger control. No current/voltage,
OTP, fuelgauge NV, watchdog, flash, muic, PD, direct-charger writes.
Input is **FG fallback** until batt_therm IIO + DT thermal tables work.
Never re-enable charging; if the bit is rewritten, inhibit again.
"""
import argparse
from pathlib import Path

SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
KCFG=Path("drivers/a52_447/Kconfig")
TAG="A52_PHASE447G_CHARGER_GUARD_V1"

HELPER=r'''
/* A52_PHASE447G_CHARGER_GUARD_V1
 * WARNING: backup thermal inhibit only. FG is NOT the verified battery
 * NTC. A full charger/thermistor/JEITA state machine remains necessary.
 * TG sm5714_charger.c chg_set_enq4fet(FALSE) clears CNTL1 bit 3.
 * Primary I2C client is SM5714 charger 0x49. FG at 0x71 is read-only.
 */
#define A52G_CHG_STATUS1 0x0d
#define A52G_CHG_CNTL1 0x13
#define A52G_CHG_DEVICEID 0x50
#define A52G_CHG_ENQ4FET BIT(3)
#define A52G_GUARD_TEMP_DECI_C 450
#define A52G_GUARD_POLL_MS 2000

static int a52g_sample(struct a52d_fg *fg, bool first)
{
	int die,st,temp,ctrl,rc,readback,vbus;
	bool unsafe;
	if (!IS_ENABLED(CONFIG_A52_447G_CHG_GUARD))
		return 0;
	die=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_DEVICEID);
	if (die<0) {
		dev_err_ratelimited(fg->dev,
			"P447G CHG_GUARD device_id_read_failed rc=%d addr=%#x cannot_verify_inhibit\n",
			die,fg->a52g_chg->addr);
		return die;
	}
	temp=a52d_temp(fg);
	st=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_STATUS1);
	vbus=st>=0 ? !!(st & BIT(0)) : -1;
	/* No batt_therm channel is available while qcom adc5 rejects DT
	 * channel 83. FG is a conservative FALLBACK, not battery temperature.
	 * If its read fails with VBUS present, inhibit instead of guessing.
	 * A missing VBUS read is treated conservatively as potentially ON.
	 */
	unsafe=(temp>=A52G_GUARD_TEMP_DECI_C ||
		temp< -400);
	if (first || fg->a52g_guard_cycle++ % 15 == 0)
		dev_info(fg->dev,
			"P447G CHG_GUARD SAMPLE source=FG_FALLBACK batt_therm=UNAVAILABLE fg_temp_deciC=%d effective_deciC=%d threshold=%d VBUS=%d status1=%d latched=%d\n",
			temp,temp,A52G_GUARD_TEMP_DECI_C,vbus,st,
			fg->a52g_inhibit_latched);
	if (!unsafe && !fg->a52g_inhibit_latched)
		return 0;
	if (vbus==0 && !fg->a52g_inhibit_latched)
		return 0;

	/* Confirm current register, mask only ENQ4FET, read it back.
	 * IMPORTANT: NO writes besides CHG_CNTL1(0x13) BIT(3)->0.
	 */
	ctrl=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_CNTL1);
	if (ctrl<0) {
		dev_err_ratelimited(fg->dev,
			"P447G CHG_GUARD INHIBIT_FAILED step=read_cntl1 rc=%d VBUS=%d fg_temp=%d\n",
			ctrl,vbus,temp);
		return ctrl;
	}
	if (ctrl & A52G_CHG_ENQ4FET) {
		rc=i2c_smbus_write_byte_data(fg->a52g_chg,A52G_CHG_CNTL1,
				ctrl & ~A52G_CHG_ENQ4FET);
		if (rc<0) {
			dev_err_ratelimited(fg->dev,
				"P447G CHG_GUARD INHIBIT_FAILED step=write_cntl1 rc=%d VBUS=%d fg_temp=%d\n",
				rc,vbus,temp);
			return rc;
		}
	}
	readback=i2c_smbus_read_byte_data(fg->a52g_chg,A52G_CHG_CNTL1);
	if (readback<0 || (readback & A52G_CHG_ENQ4FET)) {
		dev_err_ratelimited(fg->dev,
			"P447G CHG_GUARD INHIBIT_FAILED step=verify rc=%d cntl1_before=%#x readback=%d\n",
			readback<0 ? readback : -EIO,ctrl,readback);
		return readback<0 ? readback : -EIO;
	}
	if (!fg->a52g_inhibit_latched || (ctrl & A52G_CHG_ENQ4FET))
		dev_warn(fg->dev,
			"P447G CHG_GUARD INHIBIT_OK source=FG_FALLBACK batt_therm=UNAVAILABLE fg_temp_deciC=%d VBUS=%d deviceid=%#x cntl1_before=%#x after=%#x mask=0x08 never_reenable=1\n",
			temp,vbus,die,ctrl,readback);
	fg->a52g_inhibit_latched=true;
	return 0;
}
static void a52g_poll(struct work_struct *work)
{
	struct a52d_fg *fg=container_of(to_delayed_work(work),
					struct a52d_fg,a52g_guard_work);
	(void)a52g_sample(fg,false);
	schedule_delayed_work(&fg->a52g_guard_work,
			      msecs_to_jiffies(A52G_GUARD_POLL_MS));
}
'''
def once(s,a,b,reason):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447G {reason} anchors={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for need in ("A52_PHASE447F_SOC_PARITY_CORRECTED_V2",
                 "A52_PHASE447F_SM5714_FG_CHILD_V1",
                 "A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1",
                 "a52d_temp(struct a52d_fg *fg)",
                 "struct delayed_work snapshot;",
                 "a52d_fg_unregister(void *arg)",
                 "INIT_DELAYED_WORK(&fg->snapshot",
                 "schedule_delayed_work(&fg->snapshot"):
        if need not in s:raise RuntimeError("P447G expected baseline "+need)
    s=once(s,"struct delayed_work snapshot;",
          "struct delayed_work snapshot;\n"
          "\tstruct delayed_work a52g_guard_work;\n"
          "\tstruct i2c_client *a52g_chg;\n"
          "\tu32 a52g_guard_cycle;\n"
          "\tbool a52g_inhibit_latched;",
          "FG guard-owned state")
    s=once(s,"static enum power_supply_property a52d_props[] = {",
           HELPER+"\nstatic enum power_supply_property a52d_props[] = {",
           "guard helper after FG temperature function")
    s=once(s,"cancel_delayed_work_sync(&fg->snapshot);",
           "cancel_delayed_work_sync(&fg->a52g_guard_work);\n"
           "\tcancel_delayed_work_sync(&fg->snapshot);",
           "guard shutdown before secondary I2C unregister")
    s=once(s,"INIT_DELAYED_WORK(&fg->snapshot,a52d_fg_snapshot);",
           "INIT_DELAYED_WORK(&fg->snapshot,a52d_fg_snapshot);\n"
           "\tINIT_DELAYED_WORK(&fg->a52g_guard_work,a52g_poll);\n"
           "\tfg->a52g_chg=client;",
           "guard work initialization")
    # After FG exists, initial valid measurements, but BEFORE creating any
    # Android battery supply. If emergency disable fails, probe fails.
    a='''	if (soc<0 || mv<0 || temp< -400) {
		dev_err(fg->dev,"P447 W2 sm5714 FG_PROBE invalid soc=%d mv=%d temp=%d rc=%d\\n",
			soc,mv,temp,-EIO);
		return -ENODEV;
	}'''
    b=a+'''
	if (IS_ENABLED(CONFIG_A52_447G_CHG_GUARD)) {
		if (!i2c_check_functionality(client->adapter,
					I2C_FUNC_SMBUS_BYTE_DATA))
			return dev_err_probe(&client->dev,-EOPNOTSUPP,
				"P447G CHG_GUARD SMBus byte DATA required\\n");
		/* Must run BEFORE exposing the temporary Android battery.
		 * This is best-effort software safety; OFF does not imply
		 * hardware charge circuitry is guaranteed safe.
		 */
		rc=a52g_sample(fg,true);
		if (rc<0)
			return dev_err_probe(&client->dev,rc,
				"P447G CHG_GUARD initial verification failed; no battery supplier registered\\n");
	}'''
    s=once(s,a,b,"immediate pre-psy thermal inhibit")
    s=once(s,"schedule_delayed_work(&fg->snapshot,msecs_to_jiffies(30000));",
           "schedule_delayed_work(&fg->snapshot,msecs_to_jiffies(30000));\n"
           "\tif (IS_ENABLED(CONFIG_A52_447G_CHG_GUARD))\n"
           "\t\tschedule_delayed_work(&fg->a52g_guard_work,\n"
           "\t\t\tmsecs_to_jiffies(A52G_GUARD_POLL_MS));",
           "periodic check after initial hardware inhibit")
    return s
def check(root):
    src=(root/SRC).read_text()
    k=(root/KCFG).read_text()
    for marker in (TAG,"P447G CHG_GUARD SAMPLE","P447G CHG_GUARD INHIBIT_OK",
                   "P447G CHG_GUARD INHIBIT_FAILED",
                   "A52G_CHG_STATUS1 0x0d","A52G_CHG_CNTL1 0x13",
                   "A52G_CHG_ENQ4FET BIT(3)","A52G_GUARD_TEMP_DECI_C 450",
                   "I2C_FUNC_SMBUS_BYTE_DATA",
                   "schedule_delayed_work(&fg->a52g_guard_work",
                   "cancel_delayed_work_sync(&fg->a52g_guard_work)",
                   "never_reenable=1",
                   "A52_PHASE447F_SOC_PARITY_CORRECTED_V2"):
        if marker not in src:raise RuntimeError("P447G safety contract missing "+marker)
    if src.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P447G exactly one charger register write site")
    if "config A52_447G_CHG_GUARD" not in k or "default y" not in k:
        raise RuntimeError("P447G guard must be Kconfig default ON")
    print("P447G GUARD PASS: 45C FG fallback, real VBUS status, CNTL1 bit3-only disable/readback, 2s poll, latched, default ON; NO other charger writes")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/SRC;k=a.root/KCFG
    if not a.check_only:
        s=k.read_text()
        if "config A52_447G_CHG_GUARD" not in s:
            s+="""
config A52_447G_CHG_GUARD
    bool "Emergency SM5714 temperature-controlled charge-inhibit (NOT full thermal policy)"
    depends on A52_447D_SM5714_RO && I2C && POWER_SUPPLY
    default y
    help
      Observational SM5714 STATUS1 VBUS bit0 and FG fallback temperature.
      Clear charger CNTL1 ENQ4FET bit3 when VBUS and >=45C. Never
      re-enable. Does NOT replace validated battery NTC, USB-PD,
      watchdog, direct charger, thermal policy or hardware protection.
"""
            k.write_text(s)
        f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__": main()
