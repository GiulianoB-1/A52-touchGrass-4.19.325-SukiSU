#!/usr/bin/env python3
"""P447G: optional real TG battery NTC read-only input for emergency guard.

TG pinned A52 R02 samsung,sec-battery DTS:
  io-channel-names="adc-temp", ...
  battery,temp_table_adc + battery,temp_table_data (23 points, deciC).
TG sec_adc.c obtains iio_channel_get(&pdev->dev,"adc-temp"),
iio_read_channel_processed() and interpolates the DT voltage->temp table.

If ADC5 provider fails, log missing and fall back to FG. Never silently
interpret FG temperature as a battery NTC, never enable/restart charging.
"""
import argparse
from pathlib import Path
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE447G_BATT_THERM_NTC_V1"
NTC=r'''
/* A52_PHASE447G_BATT_THERM_NTC_V1
 * Exact read-only A52 R02 io-channel "adc-temp" + Samsung temperature table.
 * A failed/missing IIO ADC must fall back to the FG safety thermometer.
 */
#define A52G_NTC_MAX_POINTS 32
static int a52g_ntc_temp(struct a52d_fg *fg)
{
	int raw,rc,j,t=-10000;
	mutex_lock(&fg->a52g_ntc_lock);
	if (!fg->a52g_ntc || fg->a52g_ntc_points<2)
		goto out;
	rc=iio_read_channel_processed(fg->a52g_ntc,&raw);
	if (rc<0 || raw<0 || raw>2000000)
		goto out;
	if (raw<=fg->a52g_ntc_adc[0]) {
		t=fg->a52g_ntc_deciC[0];
		goto out;
	}
	for (j=1;j<fg->a52g_ntc_points;j++) {
		int x0=fg->a52g_ntc_adc[j-1],x1=fg->a52g_ntc_adc[j];
		int y0=fg->a52g_ntc_deciC[j-1],y1=fg->a52g_ntc_deciC[j];
		if (x1<=x0)
			goto out;
		if (raw>x1)
			continue;
		t=y0+(int)div_s64((s64)(raw-x0)*(y1-y0),x1-x0);
		goto out;
	}
	t=fg->a52g_ntc_deciC[fg->a52g_ntc_points-1];
out:
	mutex_unlock(&fg->a52g_ntc_lock);
	return t;
}
static void a52g_ntc_cleanup(void *data)
{
	struct a52d_fg *fg=data;
	struct iio_channel *ch;
	mutex_lock(&fg->a52g_ntc_lock);
	ch=fg->a52g_ntc;
	fg->a52g_ntc=NULL;
	fg->a52g_ntc_points=0;
	mutex_unlock(&fg->a52g_ntc_lock);
	if (ch)
		iio_channel_release(ch);
}
static void a52g_ntc_attach(struct platform_device *pdev,struct a52d_fg *fg)
{
	struct iio_channel *ch;
	u32 adc[A52G_NTC_MAX_POINTS],temps[A52G_NTC_MAX_POINTS];
	int points,i,rc;
	struct device_node *np=pdev->dev.of_node;
	if (!np)return;
	points=of_property_count_u32_elems(np,"battery,temp_table_adc");
	if (points<2 || points>A52G_NTC_MAX_POINTS ||
	    points!=of_property_count_u32_elems(np,"battery,temp_table_data")) {
		dev_warn(&pdev->dev,
			"P447G CHG_GUARD THERM_NTC table_invalid count=%d fallback=FG\n",
			points);
		return;
	}
	rc=of_property_read_u32_array(np,"battery,temp_table_adc",adc,points);
	if (rc)return;
	rc=of_property_read_u32_array(np,"battery,temp_table_data",temps,points);
	if (rc)return;
	for (i=1;i<points;i++)
		if (adc[i]<=adc[i-1]) {
			dev_warn(&pdev->dev,
				"P447G CHG_GUARD THERM_NTC unsorted_table idx=%d fallback=FG\n",i);
			return;
		}
	/* Match TouchGrass sec_adc.c AP-channel lookup on the actual
	 * battery platform node; not an invented alias on the I2C client.
	 */
	ch=iio_channel_get(&pdev->dev,"adc-temp");
	if (IS_ERR(ch)) {
		dev_info(&pdev->dev,
			"P447G CHG_GUARD THERM_NTC adc-temp unavailable rc=%ld fallback=FG\n",
			PTR_ERR(ch));
		return;
	}
	mutex_lock(&fg->a52g_ntc_lock);
	for (i=0;i<points;i++) {
		fg->a52g_ntc_adc[i]=(int)adc[i];
		fg->a52g_ntc_deciC[i]=(int)temps[i];
	}
	fg->a52g_ntc_points=points;
	fg->a52g_ntc=ch;
	mutex_unlock(&fg->a52g_ntc_lock);
	rc=devm_add_action_or_reset(&pdev->dev,a52g_ntc_cleanup,fg);
	if (rc) {
		dev_err(&pdev->dev,
			"P447G CHG_GUARD THERM_NTC cleanup_registration rc=%d\n",rc);
		return;
	}
	dev_info(&pdev->dev,
		"P447G CHG_GUARD THERM_NTC source=BAT_THERM_IIO channel=adc-temp points=%d first_adc=%u last_adc=%u\n",
		points,adc[0],adc[points-1]);
}
'''
def once(s,a,b,descr):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447G NTC {descr} anchor count={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for m in ("A52_PHASE447G_CHARGER_GUARD_V1",
              "A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1",
              "int die,st,temp,ctrl,rc,readback,vbus;",
              "temp=a52d_temp(fg);",
              "fg->a52g_chg=client;"):
        if m not in s:raise RuntimeError("P447G NTC prerequisite "+m)
    s=once(s,"#include <linux/platform_device.h>",
           "#include <linux/iio/consumer.h>\n#include <linux/math64.h>\n#include <linux/platform_device.h>",
           "IIO headers")
    s=once(s,"struct i2c_client *a52g_chg;",
           "struct i2c_client *a52g_chg;\n"
           "\tstruct mutex a52g_ntc_lock;\n"
           "\tstruct iio_channel *a52g_ntc;\n"
           "\tint a52g_ntc_points;\n"
           "\tint a52g_ntc_adc[32];\n"
           "\tint a52g_ntc_deciC[32];",
           "NTC state")
    s=once(s,"mutex_init(&fg->lock);",
           "mutex_init(&fg->lock);\n"
           "\tmutex_init(&fg->a52g_ntc_lock);",
           "NTC mutex")
    s=once(s,"static int a52g_sample(struct a52d_fg *fg, bool first)",
           NTC+"\nstatic int a52g_sample(struct a52d_fg *fg, bool first)",
           "NTC converter helper")
    s=once(s,"int die,st,temp,ctrl,rc,readback,vbus;",
           "int die,st,temp,fgtemp,ntc,ctrl,rc,readback,vbus;\n"
           "\tconst char *source;",
           "safety source")
    s=once(s,"temp=a52d_temp(fg);\n\tst=i2c_smbus_read_byte_data",
           "fgtemp=a52d_temp(fg);\n"
           "\tntc=a52g_ntc_temp(fg);\n"
           "\tsource=(ntc>=-400 && ntc<=900)?\"BATT_THERM\":\"FG_FALLBACK\";\n"
           "\ttemp=!strcmp(source,\"BATT_THERM\") ? ntc : fgtemp;\n"
           "\tst=i2c_smbus_read_byte_data",
           "prefer battery NTC, only inside guard")
    s=once(s,'''			"P447G CHG_GUARD SAMPLE source=FG_FALLBACK batt_therm=UNAVAILABLE fg_temp_deciC=%d effective_deciC=%d threshold=%d VBUS=%d status1=%d latched=%d\\n",
			temp,temp,A52G_GUARD_TEMP_DECI_C,vbus,st,
			fg->a52g_inhibit_latched);''',
'''			"P447G CHG_GUARD SAMPLE source=%s batt_therm_deciC=%d fg_temp_deciC=%d effective_deciC=%d threshold=%d VBUS=%d status1=%d latched=%d\\n",
			source,ntc,fgtemp,temp,A52G_GUARD_TEMP_DECI_C,vbus,st,
			fg->a52g_inhibit_latched);''',
           "print both temperature sources")
    s=once(s,'''			"P447G CHG_GUARD INHIBIT_OK source=FG_FALLBACK batt_therm=UNAVAILABLE fg_temp_deciC=%d VBUS=%d deviceid=%#x cntl1_before=%#x after=%#x mask=0x08 never_reenable=1\\n",
			temp,vbus,die,ctrl,readback);''',
'''			"P447G CHG_GUARD INHIBIT_OK source=%s batt_therm_deciC=%d fg_temp_deciC=%d effective_deciC=%d VBUS=%d deviceid=%#x cntl1_before=%#x after=%#x mask=0x08 never_reenable=1\\n",
			source,ntc,fgtemp,temp,vbus,die,ctrl,readback);''',
           "inhibit source trace")
    s=once(s,"ret=power_supply_get_property(bat->fg,POWER_SUPPLY_PROP_CAPACITY,&cap);",
           "a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));\n"
           "\tret=power_supply_get_property(bat->fg,POWER_SUPPLY_PROP_CAPACITY,&cap);",
           "real Samsung platform consumer IIO binding")
    return s
def check(root):
    c=(root/SRC).read_text()
    for item in (TAG,"iio_channel_get(&pdev->dev,\"adc-temp\")",
                 "iio_read_channel_processed(fg->a52g_ntc",
                 "battery,temp_table_adc","battery,temp_table_data",
                 "P447G CHG_GUARD SAMPLE source=%s",
                 "P447G CHG_GUARD INHIBIT_OK source=%s",
                 "a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg))",
                 "devm_add_action_or_reset(&pdev->dev,a52g_ntc_cleanup"):
        if item not in c:raise RuntimeError("P447G NTC missing "+item)
    expected_writes=2 if "A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1" in c else 1
    if c.count("i2c_smbus_write_byte_data(")!=expected_writes:
        raise RuntimeError("P447G NTC unexpected charger write callsites")
    print("P447G THERM NTC PASS: Samsung adc-temp voltage+DT table when available, FG fallback otherwise, side-by-side logs, latch-only inhibitor")
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    args=p.parse_args();f=args.root/SRC
    if not args.check_only:f.write_text(patch(f.read_text()))
    check(args.root)
if __name__=="__main__":main()
