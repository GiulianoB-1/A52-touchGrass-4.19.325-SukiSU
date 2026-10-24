#!/usr/bin/env python3
"""447H: retry -EPROBE_DEFER on Samsung adc-temp every 2s and print both temperatures.

Never detach the Android battery during an unavailable IIO probe; retain
the SM5714 FG fallback until the real Samsung thermistor is online.
"""
from pathlib import Path
import argparse
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE447H_NTC_RETRY_V1"
HELPER=r'''
/* A52_PHASE447H_NTC_RETRY_V1
 * The platform driver's ->remove synchronously cancels work BEFORE its
 * devres resources, including the IIO channel, are released.
 */
static void a52h_ntc_retry_work(struct work_struct *work)
{
	struct a52e_battery *bat=
		container_of(to_delayed_work(work),struct a52e_battery,a52h_ntc_retry);
	int rc=a52g_ntc_attach(bat->a52h_pdev,power_supply_get_drvdata(bat->fg));
	if (!rc) {
		dev_info(&bat->a52h_pdev->dev,
			 "P447H NTC READY retries=%u source=thermistor\n",
			 bat->a52h_attempts);
		return;
	}
	if (!(bat->a52h_attempts++ % 15))
		dev_info(&bat->a52h_pdev->dev,
			 "P447H NTC RETRY rc=%d attempts=%u interval_ms=2000 src=FG_FALLBACK\n",
			 rc,bat->a52h_attempts);
	schedule_delayed_work(&bat->a52h_ntc_retry,msecs_to_jiffies(2000));
}
static int a52h_battery_remove(struct platform_device *pdev)
{
	struct a52e_battery *bat=platform_get_drvdata(pdev);
	if (bat)
		cancel_delayed_work_sync(&bat->a52h_ntc_retry);
	return 0;
}
'''
def one(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447H NTC {label} anchor={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for marker in ("A52_PHASE447G_BATT_THERM_NTC_V1",
                   "A52_PHASE447G_CHARGER_GUARD_V1",
                   "A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1",
                   "a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));",
                   "P447G CHG_GUARD SAMPLE source=%s"):
        if marker not in s:raise RuntimeError("P447H NTC missing "+marker)
    beg=s.index("static void a52g_ntc_attach(")
    end=s.index("\nstatic int a52g_sample(",beg)
    fn=s[beg:end]
    fn=one(fn,"static void a52g_ntc_attach(","static int a52g_ntc_attach(","return attach status")
    fn=one(fn,"if (!np)return;","if (!np)return -ENODEV;","null DT")
    fn=one(fn,'points);\n\t\treturn;','points);\n\t\treturn -EINVAL;',"table invalid")
    fn=one(fn,"if (rc)return;\n\trc=of_property_read_u32_array",
           "if (rc)return rc;\n\trc=of_property_read_u32_array","ADC table fail")
    fn=one(fn,"if (rc)return;\n\tfor (i=1;i<points;i++)",
           "if (rc)return rc;\n\tfor (i=1;i<points;i++)","temp table fail")
    fn=one(fn,'idx=%d fallback=FG\\n",i);\n\t\t\treturn;',
           'idx=%d fallback=FG\\n",i);\n\t\t\treturn -EINVAL;',"unsorted table")
    fn=one(fn,'PTR_ERR(ch));\n\t\treturn;','PTR_ERR(ch));\n\t\treturn PTR_ERR(ch);',"deferred IIO")
    fn=one(fn,'cleanup_registration rc=%d\\n",rc);\n\t\treturn;',
           'cleanup_registration rc=%d\\n",rc);\n\t\treturn rc;',"cleanup action")
    fn=one(fn,'points,adc[0],adc[points-1]);\n}',
           'points,adc[0],adc[points-1]);\n\treturn 0;\n}',"attach success")
    s=s[:beg]+fn+s[end:]
    s=one(s,"ntc>=-400 && ntc<=900","ntc>=-200 && ntc<=900","plausibility -20C .. 90C")
    s=one(s,"struct a52e_battery {\n",
          "struct a52e_battery {\n\tstruct platform_device *a52h_pdev;\n"
          "\tstruct delayed_work a52h_ntc_retry;\n\tu32 a52h_attempts;\n",
          "per-platform delayed retry state")
    s=one(s,"static int a52e_battery_platform_probe(struct platform_device *pdev)",
          HELPER+"\nstatic int a52e_battery_platform_probe(struct platform_device *pdev)",
          "probe retry helper")
    s=one(s,"bat=devm_kzalloc(&pdev->dev,sizeof(*bat),GFP_KERNEL);\n\tif (!bat)return -ENOMEM;",
          "bat=devm_kzalloc(&pdev->dev,sizeof(*bat),GFP_KERNEL);\n"
          "\tif (!bat)return -ENOMEM;\n\tbat->a52h_pdev=pdev;\n"
          "\tINIT_DELAYED_WORK(&bat->a52h_ntc_retry,a52h_ntc_retry_work);\n"
          "\tplatform_set_drvdata(pdev,bat);",
          "platform work init")
    s=one(s,"a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));",
          "/* P447H: attach only on successfully registered battery platform. */",
          "remove swallowed deferral")
    s=one(s,"kfree(path);\n\treturn 0;\n}\nstatic const struct of_device_id a52e_battery_of_match",
          "kfree(path);\n\tret=a52g_ntc_attach(pdev,power_supply_get_drvdata(bat->fg));\n"
          "\tif (ret<0) {\n"
          "\t\tdev_info(&pdev->dev,\"P447H NTC INITIAL rc=%d retry_ms=2000 backup=FG\\n\",ret);\n"
          "\t\tschedule_delayed_work(&bat->a52h_ntc_retry,msecs_to_jiffies(2000));\n"
          "\t}\n\treturn 0;\n}\nstatic const struct of_device_id a52e_battery_of_match",
          "retry only after complete successful probe")
    s=one(s,"\t.probe=a52e_battery_platform_probe,",
          "\t.probe=a52e_battery_platform_probe,\n\t.remove=a52h_battery_remove,",
          "cancel on remove")
    s=one(s,"if (first || fg->a52g_guard_cycle++ % 15 == 0)\n\t\tdev_info(",
          "if (first || fg->a52g_guard_cycle++ % 15 == 0) {\n\t\tdev_info(",
          "periodic log group")
    s=one(s,"fg->a52g_inhibit_latched);",
          "fg->a52g_inhibit_latched);\n"
          "\t\tdev_info(fg->dev,\"P447H TEMP therm=%d fg=%d src=%s vbus=%d interval_s=30\\n\",\n"
          "\t\t\t ntc,fgtemp,source,vbus);\n\t}",
          "paired temperatures periodic log")
    return s
def check(root):
    s=(root/SRC).read_text()
    for m in (TAG,"P447H NTC READY","P447H NTC RETRY","P447H TEMP therm=%d",
              ".remove=a52h_battery_remove","cancel_delayed_work_sync(&bat->a52h_ntc_retry)",
              "static int a52g_ntc_attach(","ntc>=-200 && ntc<=900"):
        if m not in s:raise RuntimeError("P447H NTC audit "+m)
    assert s.count("i2c_smbus_write_byte_data(")==1
    print("P447H NTC PASS: real IIO ret/probe-defer retry, therm/FG side-by-side, no extra charger writes")
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();src=a.root/SRC
    if not a.check_only:src.write_text(patch(src.read_text()))
    check(a.root)
