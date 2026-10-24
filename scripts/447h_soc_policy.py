#!/usr/bin/env python3
"""447H: TG SM5714 scaled battery SOC with writable learned capacity_max.

Use confirmed TouchGrass sm5714_fg_get_scaled_capacity() integer math:
(raw_soc_deci - capacity_min)*1000 / (capacity_max-capacity_min),
then truncate to integer percent. The user-confirmed TG display difference
is consistent with a learned max near 920 but the value is NEVER hardcoded.
At start it is the NOMINAL DT 1000; Samsung may restore the actual value
through batt_capacity_max. No fuel-gauge SRAM/NV writes; dynamic full-charge
learning/atomic state stays disabled until charger state machine is ported.

TG path: sec_bat_create_attrs(&battery->psy_bat->dev); install on the
actual battery power_supply DEVICE, not the I2C FG or platform root.
"""
import argparse
from pathlib import Path
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
TAG="A52_PHASE447H_LEARNED_SOC_SYSFS_V1"
PROP=r'''
/* A52_PHASE447H_LEARNED_SOC_SYSFS_V1.
 * Source: TG sm5714_fg_get_scaled_capacity, sec_device_sysfs
 * BATT_CAPACITY_MAX / BATT_READ_RAW_SOC. No kernel writes to learned FG NV.
 */
static ssize_t batt_capacity_max_show(struct device *dev,
				      struct device_attribute *attr,char *buf)
{
	struct power_supply *psy=dev_get_drvdata(dev);
	struct a52e_battery *bat=power_supply_get_drvdata(psy);
	int max;
	mutex_lock(&bat->a52h_cap_lock);
	max=bat->a52h_cap_max;
	mutex_unlock(&bat->a52h_cap_lock);
	return scnprintf(buf,PAGE_SIZE,"%d\n",max);
}
static ssize_t batt_capacity_max_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf,size_t count)
{
	struct power_supply *psy=dev_get_drvdata(dev);
	struct a52e_battery *bat=power_supply_get_drvdata(psy);
	int value,old,lower;
	if (kstrtoint(buf,10,&value))
		return -EINVAL;
	mutex_lock(&bat->a52h_cap_lock);
	lower=max_t(int,bat->a52h_cap_min+1,
		    bat->a52h_cap_nominal-bat->a52h_cap_margin);
	if (value<lower || value>bat->a52h_cap_nominal) {
		mutex_unlock(&bat->a52h_cap_lock);
		return -ERANGE;
	}
	old=bat->a52h_cap_max;
	bat->a52h_cap_max=value;
	bat->a52h_written=true;
	mutex_unlock(&bat->a52h_cap_lock);
	power_supply_changed(psy); /* notify Android after learned-capacity restore */
	dev_info(dev,
		 "P447H SOC writer=sysfs pid=%d comm=%s cap_old=%d cap_new=%d range=%d..%d no_NV_write=1\n",
		 task_pid_nr(current),current->comm,old,value,lower,
		 bat->a52h_cap_nominal);
	return count;
}
static ssize_t batt_read_raw_soc_show(struct device *dev,
				      struct device_attribute *attr,char *buf)
{
	struct power_supply *psy=dev_get_drvdata(dev);
	struct a52e_battery *bat=power_supply_get_drvdata(psy);
	int deci=a52f_soc_deci(power_supply_get_drvdata(bat->fg));
	if (deci<0)return deci;
	/* TG RAW capacity type reports 0.01%, ten times raw-deci. */
	return scnprintf(buf,PAGE_SIZE,"%d\n",deci*10);
}
static ssize_t batt_full_capacity_show(struct device *dev,
				       struct device_attribute *attr,char *buf)
{
	struct power_supply *psy=dev_get_drvdata(dev);
	struct a52e_battery *bat=power_supply_get_drvdata(psy);
	/* A52 DT battery,battery_full_capacity, nominal units mAh;
	 * not a claimed live/learned CHARGE_COUNTER.
	 */
	return scnprintf(buf,PAGE_SIZE,"%d\n",bat->a52h_nominal_full_mah);
}
/* The original TG SYSFS_DEVICE_ATTR group uses mode 0664. This interim
 * port limits learned-capacity writes to owner/group (0660); the vendor
 * ueventd/init must still supply the expected system:radio ownership.
 */
static DEVICE_ATTR(batt_capacity_max,0660,
		   batt_capacity_max_show,batt_capacity_max_store);
static DEVICE_ATTR(batt_read_raw_soc,0444,batt_read_raw_soc_show,NULL);
static DEVICE_ATTR(batt_full_capacity,0444,batt_full_capacity_show,NULL);
static void a52h_attrs_release(void *arg)
{
	struct device *dev=arg;
	device_remove_file(dev,&dev_attr_batt_full_capacity);
	device_remove_file(dev,&dev_attr_batt_read_raw_soc);
	device_remove_file(dev,&dev_attr_batt_capacity_max);
}
static int a52h_attrs_add(struct platform_device *pdev,
			   struct power_supply *psy)
{
	int rc;
	rc=device_create_file(&psy->dev,&dev_attr_batt_capacity_max);
	if (rc)return rc;
	rc=device_create_file(&psy->dev,&dev_attr_batt_read_raw_soc);
	if (rc) {
		device_remove_file(&psy->dev,&dev_attr_batt_capacity_max);
		return rc;
	}
	rc=device_create_file(&psy->dev,&dev_attr_batt_full_capacity);
	if (rc) {
		device_remove_file(&psy->dev,&dev_attr_batt_read_raw_soc);
		device_remove_file(&psy->dev,&dev_attr_batt_capacity_max);
		return rc;
	}
	rc=devm_add_action_or_reset(&pdev->dev,a52h_attrs_release,&psy->dev);
	return rc;
}
'''
GET=r'''static int a52e_battery_get_property(struct power_supply *psy,
				    enum power_supply_property psp,
				    union power_supply_propval *val)
{
	struct a52e_battery *bat=power_supply_get_drvdata(psy);
	if (psp==POWER_SUPPLY_PROP_TEMP) {
		struct a52d_fg *fg=power_supply_get_drvdata(bat->fg);
		int ntc=a52g_ntc_temp(fg);
		if (ntc>=-200 && ntc<=900) {
			val->intval=ntc;
			return 0;
		}
		/* FG die remains labeled fallback, not claimed cell temperature. */
		return power_supply_get_property(bat->fg,psp,val);
	}
	if (psp==POWER_SUPPLY_PROP_CAPACITY) {
		struct a52d_fg *fg=power_supply_get_drvdata(bat->fg);
		int raw=a52f_soc_deci(fg);
		int scaled,max,min;
		bool written;
		if (raw<0)return raw;
		mutex_lock(&bat->a52h_cap_lock);
		max=bat->a52h_cap_max;
		min=bat->a52h_cap_min;
		written=bat->a52h_written;
		mutex_unlock(&bat->a52h_cap_lock);
		if (max<=min)return -ERANGE;
		/* TG 0.1% integer math, capped, then TRUNCATE to %.
		 * Do NOT change FG SRAM registers or charge controls.
		 */
		scaled=clamp_val((raw-min)*1000/(max-min),0,1000);
		val->intval=scaled/10;
		pr_info_ratelimited(
			"P447H SOC raw=%d capacity_max=%d capacity_min=%d scaled=%d ui=%d writer=%s no_NV_write=1\n",
			raw,max,min,scaled,val->intval,written?"sysfs":"default");
		return 0;
	}
	return power_supply_get_property(bat->fg,psp,val);
}
'''
def one(s,a,b,why):
    count=s.count(a)
    if count!=1:raise RuntimeError("P447H SOC "+why+" anchor="+str(count))
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for item in ("A52_PHASE447H_NTC_RETRY_V1",
                 "A52_PHASE447F_SOC_PARITY_CORRECTED_V2",
                 "A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1",
                 "P447E battery PLATFORM_PROBE rc=0",
                 "static int a52e_battery_get_property"):
        if item not in s:raise RuntimeError("P447H SOC needs "+item)
    s="#include <linux/sched.h>\n#include <linux/device.h>\n"+s
    s=one(s,"struct a52e_battery {\n",
          "struct a52e_battery {\n"
          "\tstruct mutex a52h_cap_lock;\n"
          "\tint a52h_cap_max,a52h_cap_min,a52h_cap_nominal,a52h_cap_margin;\n"
          "\tint a52h_nominal_full_mah;\n"
          "\tbool a52h_written;\n",
          "cap fields")
    start=s.index("static int a52e_battery_get_property(")
    end=s.index("static const struct power_supply_desc a52e_battery_desc=",start)
    s=s[:start]+GET+"\n"+s[end:]
    s=one(s,"static void a52e_put_fg(void *arg)",
          PROP+"\nstatic void a52e_put_fg(void *arg)","TG sysfs group")
    s=one(s,"bat->a52h_pdev=pdev;",
'''mutex_init(&bat->a52h_cap_lock);
	bat->a52h_cap_nominal=1000;
	bat->a52h_cap_min=0;
	bat->a52h_cap_margin=300;
	bat->a52h_cap_max=1000;
	of_property_read_u32(pdev->dev.of_node,"battery,battery_full_capacity",
			     (u32 *)&bat->a52h_nominal_full_mah);
	bat->a52h_pdev=pdev;''',"capacity state init")
    s=one(s,"ret=devm_add_action_or_reset(&pdev->dev,a52e_put_fg,bat->fg);",
'''/* TG has a nominal DT max; a sysfs writer may restore its learned
	 * value only within the TG capacity_max_margin. No guessed 920.
	 */
	{
		struct a52d_fg *fg=power_supply_get_drvdata(bat->fg);
		struct device_node *np=of_find_node_by_name(NULL,"sm5714-fuelgauge");
		bat->a52h_cap_nominal=fg->a52f_capacity_max;
		bat->a52h_cap_min=fg->a52f_capacity_min;
		bat->a52h_cap_max=bat->a52h_cap_nominal;
		if (np) {
			of_property_read_u32(np,"fuelgauge,capacity_max_margin",
					    (u32 *)&bat->a52h_cap_margin);
			of_node_put(np);
		}
		if (bat->a52h_cap_nominal<=bat->a52h_cap_min ||
		    bat->a52h_cap_margin<=0 ||
		    bat->a52h_cap_margin>bat->a52h_cap_nominal)
			return -EINVAL;
	}
	ret=devm_add_action_or_reset(&pdev->dev,a52e_put_fg,bat->fg);''',
           "TG learned max init")
    s=one(s,"path=kobject_get_path(&psy->dev.kobj,GFP_KERNEL);",
          r'''ret=a52h_attrs_add(pdev,psy);
	if (ret)
		return dev_err_probe(&pdev->dev,ret,
			"P447H SOC sysfs attrs registration failed\n");
	dev_info(&pdev->dev,
		 "P447H SOC initialized cap_max=%d margin=%d min=%d writer=default sysfs=batt_capacity_max\n",
		 bat->a52h_cap_max,bat->a52h_cap_margin,bat->a52h_cap_min);
	path=kobject_get_path(&psy->dev.kobj,GFP_KERNEL);''',
          "attrs setup")
    return s
def check(root):
    t=(root/SRC).read_text()
    for marker in (TAG,"P447H SOC raw=%d","P447H SOC writer=sysfs",
                   "batt_capacity_max,0660","batt_read_raw_soc,0444",
                   "batt_full_capacity,0444","a52h_attrs_add(pdev,psy)",
                   "power_supply_changed(psy)","if (psp==POWER_SUPPLY_PROP_TEMP)",
                   "capacity_max_margin","writer=default","writer=%s",
                   "no_NV_write=1"):
        if marker not in t:raise RuntimeError("P447H SOC missing "+marker)
    if "CONFIG_A52_447F_CAPACITY_MAX_DECI" in t:
        raise RuntimeError("P447H forbids guessed 920 Kconfig scale")
    assert t.count("i2c_smbus_write_byte_data(")==1
    print("P447H SOC PASS: TG dynamic-max RESTORE sysfs, nominal 1000 until writer, raw FG unaltered, bounded writer, no NV writes")
def main():
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();file=a.root/SRC
    if not a.check_only:file.write_text(patch(file.read_text()))
    check(a.root)
if __name__=="__main__":main()
