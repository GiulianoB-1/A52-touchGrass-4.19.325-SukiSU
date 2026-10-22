#!/usr/bin/env python3
"""447H A52 charger thermistor 0x54 diagnostic (Tchg), independent of bat_thm.

TG A52 R02 battery "adc-chg-temp" IIO name: GPIO3 0x54 voltage in uV,
mapped through battery,chg_temp_table_adc / chg_temp_table_data to deciC.
Never substitute the FG or the generic ADC5 temp scale as Tchg.
No new charger writes. Reuses 447H IIO deferral retry, and exposes all
three temperatures once per 30s CHG_GUARD sample.
"""
from pathlib import Path
import argparse
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE447H_TCHG_GPIO3_IIO_V1"
HELPER=r'''
/* A52_PHASE447H_TCHG_GPIO3_IIO_V1
 * TG battery dt channel "adc-chg-temp" => ADC5 GPIO3/0x54 VOLTAGE uV.
 * Samsung battery,chg_temp_table_adc/data convert uV to deciC.
 * Purely diagnostic; only bat_thm is controlling thermal guard.
 */
static int a52h_chg_ntc_temp(struct a52d_fg *fg)
{
	int raw,t=-10000,j,rc;
	mutex_lock(&fg->a52g_ntc_lock);
	if (!fg->a52h_chg_iio || fg->a52h_chg_points<2)
		goto out;
	rc=iio_read_channel_processed(fg->a52h_chg_iio,&raw);
	if (rc<0 || raw<0 || raw>2000000)
		goto out;
	if (raw<=fg->a52h_chg_adc[0]) {
		t=fg->a52h_chg_deciC[0];
		goto out;
	}
	for (j=1;j<fg->a52h_chg_points;j++) {
		int x0=fg->a52h_chg_adc[j-1],x1=fg->a52h_chg_adc[j];
		int y0=fg->a52h_chg_deciC[j-1],y1=fg->a52h_chg_deciC[j];
		if (x1<=x0)goto out;
		if (raw>x1)continue;
		t=y0+(int)div_s64((s64)(raw-x0)*(y1-y0),x1-x0);
		goto out;
	}
	t=fg->a52h_chg_deciC[fg->a52h_chg_points-1];
out:
	mutex_unlock(&fg->a52g_ntc_lock);
	return t;
}
static void a52h_chg_iio_cleanup(void *data)
{
	struct a52d_fg *fg=data;
	struct iio_channel *ch;
	mutex_lock(&fg->a52g_ntc_lock);
	ch=fg->a52h_chg_iio;
	fg->a52h_chg_iio=NULL;
	fg->a52h_chg_points=0;
	mutex_unlock(&fg->a52g_ntc_lock);
	if (ch)iio_channel_release(ch);
}
static int a52h_chg_ntc_attach(struct platform_device *pdev,struct a52d_fg *fg)
{
	struct iio_channel *ch;
	struct device_node *np=pdev->dev.of_node;
	u32 adc[32],temps[32];
	int n,i,rc;
	if (!np)return -ENODEV;
	if (READ_ONCE(fg->a52h_chg_iio))return 0;
	n=of_property_count_u32_elems(np,"battery,chg_temp_table_adc");
	if (n<2 || n>32 ||
	    n!=of_property_count_u32_elems(np,"battery,chg_temp_table_data"))
		return -EINVAL;
	rc=of_property_read_u32_array(np,"battery,chg_temp_table_adc",adc,n);
	if (rc)return rc;
	rc=of_property_read_u32_array(np,"battery,chg_temp_table_data",temps,n);
	if (rc)return rc;
	for (i=1;i<n;i++)
		if (adc[i]<=adc[i-1])return -EINVAL;
	ch=iio_channel_get(&pdev->dev,"adc-chg-temp");
	if (IS_ERR(ch))return PTR_ERR(ch);
	mutex_lock(&fg->a52g_ntc_lock);
	for (i=0;i<n;i++) {
		fg->a52h_chg_adc[i]=(int)adc[i];
		fg->a52h_chg_deciC[i]=(int)temps[i];
	}
	fg->a52h_chg_points=n;
	fg->a52h_chg_iio=ch;
	mutex_unlock(&fg->a52g_ntc_lock);
	rc=devm_add_action_or_reset(&pdev->dev,a52h_chg_iio_cleanup,fg);
	if (rc)return rc;
	dev_info(&pdev->dev,
		 "P447H TCHG READY src=adc-chg-temp channel=0x54 units=uv points=%d\n",n);
	return 0;
}
'''
def one(s,a,b,why):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447H TCHG {why}: anchors={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for m in ("A52_PHASE447H_NTC_RETRY_V1","P447H TEMP therm=%d",
              "static int a52g_ntc_attach(",
              "int rc=a52g_ntc_attach(bat->a52h_pdev,power_supply_get_drvdata(bat->fg));",
              "ret=a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));"):
        if m not in s:raise RuntimeError(f"P447H TCHG missing {m}")
    s=one(s,"struct iio_channel *a52g_ntc;",
          "struct iio_channel *a52g_ntc;\n"
          "\tstruct iio_channel *a52h_chg_iio;\n"
          "\tint a52h_chg_points;\n"
          "\tint a52h_chg_adc[32];\n"
          "\tint a52h_chg_deciC[32];",
          "new secondary telemetry input")
    # Avoid duplicate IIO channel acquisition during secondary retry.
    s=one(s,"if (!np)return -ENODEV;\n\tpoints=of_property_count_u32_elems",
          "if (!np)return -ENODEV;\n"
          "\tif (READ_ONCE(fg->a52g_ntc))return 0;\n"
          "\tpoints=of_property_count_u32_elems",
          "previous primary channel already acquired")
    s=one(s,"static int a52g_sample(struct a52d_fg *fg, bool first)",
          HELPER+"\nstatic int a52g_sample(struct a52d_fg *fg, bool first)",
          "charger thermistor helpers must be declared before guard sampling")
    s=one(s,"int rc=a52g_ntc_attach(bat->a52h_pdev,power_supply_get_drvdata(bat->fg));",
          "struct a52d_fg *fg=power_supply_get_drvdata(bat->fg);\n"
          "\tint rc=a52g_ntc_attach(bat->a52h_pdev,fg);\n"
          "\tint crc=a52h_chg_ntc_attach(bat->a52h_pdev,fg);",
          "retry both while each missing")
    s=one(s,"if (!rc) {\n\t\tdev_info(&bat->a52h_pdev->dev,",
          "if (!rc && !crc) {\n\t\tdev_info(&bat->a52h_pdev->dev,",
          "both ready")
    s=one(s,'"P447H NTC RETRY rc=%d attempts=%u interval_ms=2000 src=FG_FALLBACK\\n",\n\t\t\t rc,bat->a52h_attempts);',
          '"P447H NTC RETRY rc=%d chg_rc=%d attempts=%u interval_ms=2000 src=FG_FALLBACK\\n",\n\t\t\t rc,crc,bat->a52h_attempts);',
          "retry provenance")
    s=one(s,"ret=a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));\n\tif (ret<0) {",
          "ret=a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));\n"
          "\t{\n"
          "\t\tint chg_rc=a52h_chg_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));\n"
          "\t\tif (ret<0 || chg_rc<0) {",
          "initial attach both")
    s=one(s,'"P447H NTC INITIAL rc=%d retry_ms=2000 backup=FG\\n",ret);',
          '"P447H NTC INITIAL rc=%d chg_rc=%d retry_ms=2000 backup=FG\\n",ret,chg_rc);',
          "initial attach cause")
    s=one(s,"schedule_delayed_work(&bat->a52h_ntc_retry,msecs_to_jiffies(2000));\n\t}\n\treturn 0;",
          "schedule_delayed_work(&bat->a52h_ntc_retry,msecs_to_jiffies(2000));\n"
          "\t\t}\n\t}\n\treturn 0;",
          "initial attach balanced braces")
    s=one(s,'dev_info(fg->dev,"P447H TEMP therm=%d fg=%d src=%s vbus=%d interval_s=30\\n",\n\t\t\t ntc,fgtemp,source,vbus);',
          'dev_info(fg->dev,"P447H TEMP therm=%d chg=%d fg=%d src=%s vbus=%d interval_s=30\\n",\n'
          '\t\t\t ntc,a52h_chg_ntc_temp(fg),fgtemp,source,vbus);',
          "all Tbat/Tchg/FG on same line")
    return s

def check(root):
    s=(root/SRC).read_text()
    for m in (TAG,"P447H TCHG READY","battery,chg_temp_table_adc",
              "battery,chg_temp_table_data",
              'iio_channel_get(&pdev->dev,"adc-chg-temp")',
              "P447H TEMP therm=%d chg=%d fg=%d",
              "if (!rc && !crc)",
              "a52h_chg_iio_cleanup"):
        if m not in s:raise RuntimeError(f"P447H TCHG audit {m}")
    if s.count("i2c_smbus_write_byte_data(")!=1:
        raise RuntimeError("P447H TCHG may not introduce charger writes")
    print("P447H TCHG PASS: independent charger NTC GPIO3/0x54 IIO uV -> Samsung DT deciC; retry and 30s Tbat/Tchg/FG logs")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
