#!/usr/bin/env python3
"""447E: publish battery under real samsung_mobile_device:battery, not I2C.

Retain the proven SM5714 read-only I2C fuelgauge. It registers a supplier
called sm5714-fuelgauge; an independent, minimal sec-battery-compatible
platform consumer registers 'battery' with the real Samsung platform parent.
No Samsung charging logic, fake online status, learned NV or policy changes.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1"
FILE=Path("drivers/a52_447/a52_sm5714_fg_ro.c")

PROXY=r'''
/* A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1
 * This is a deliberately read-only bridge, NOT the full Samsung sec_battery
 * driver. Binding the actual samsung,sec-battery DT node places 'battery'
 * below samsung_mobile_device:battery, as TG's sec_battery does.
 * Future production sec_battery replaces this bridge entirely.
 */
struct a52e_battery {
	struct power_supply *fg;
};
static int a52e_battery_get_property(struct power_supply *psy,
				    enum power_supply_property psp,
				    union power_supply_propval *val)
{
	struct a52e_battery *bat=power_supply_get_drvdata(psy);
	/* Preserve read failures; NEVER synthesize capacity, charger status,
	 * USB/AC online state, or an unverified battery-present value.
	 */
	return power_supply_get_property(bat->fg,psp,val);
}
static const struct power_supply_desc a52e_battery_desc={
	.name="battery",
	.type=POWER_SUPPLY_TYPE_BATTERY,
	.properties=a52d_props,
	.num_properties=ARRAY_SIZE(a52d_props),
	.get_property=a52e_battery_get_property,
};
static void a52e_put_fg(void *arg)
{
	power_supply_put(arg);
}
static int a52e_battery_platform_probe(struct platform_device *pdev)
{
	struct a52e_battery *bat;
	struct power_supply *psy;
	struct power_supply_config cfg={};
	union power_supply_propval cap={};
	char *path;
	int ret;

	if (!a52_447_enabled(A52_447_BATTERY))
		return -ENODEV;
	/* Exact DT compatible used by TG's production sec_battery. */
	if (!of_device_is_compatible(pdev->dev.of_node,"samsung,sec-battery"))
		return -ENODEV;
	bat=devm_kzalloc(&pdev->dev,sizeof(*bat),GFP_KERNEL);
	if (!bat)return -ENOMEM;
	bat->fg=power_supply_get_by_name("sm5714-fuelgauge");
	if (!bat->fg)
		return dev_err_probe(&pdev->dev,-EPROBE_DEFER,
				      "P447E battery FG supplier pending\n");
	ret=devm_add_action_or_reset(&pdev->dev,a52e_put_fg,bat->fg);
	if (ret)return ret;
	ret=power_supply_get_property(bat->fg,POWER_SUPPLY_PROP_CAPACITY,&cap);
	if (ret || cap.intval<0 || cap.intval>100)
		return dev_err_probe(&pdev->dev,-EPROBE_DEFER,
				     "P447E battery FG invalid capacity\n");
	cfg.drv_data=bat;
	cfg.of_node=pdev->dev.of_node;
	psy=devm_power_supply_register(&pdev->dev,&a52e_battery_desc,&cfg);
	if (IS_ERR(psy))
		return dev_err_probe(&pdev->dev,PTR_ERR(psy),
				     "P447E battery platform supply registration failed\n");
	path=kobject_get_path(&psy->dev.kobj,GFP_KERNEL);
	dev_info(&pdev->dev,
		 "P447E battery PLATFORM_PROBE rc=0 parent=%s fg=sm5714-fuelgauge soc=%d path=%s\n",
		 dev_name(&pdev->dev),cap.intval,path?path:"(OOM)");
	kfree(path);
	return 0;
}
static const struct of_device_id a52e_battery_of_match[]={
	{.compatible="samsung,sec-battery"},{}
};
MODULE_DEVICE_TABLE(of,a52e_battery_of_match);
static struct platform_driver a52e_battery_platform_driver={
	.probe=a52e_battery_platform_probe,
	.driver={
		.name="sec-battery",
		.of_match_table=a52e_battery_of_match,
	},
};
'''
def once(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447E {label}: matches={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for x in ("A52_PHASE447D_SM5714_FG_RO_V1",
              "POWER_SUPPLY_STATUS_UNKNOWN",
              'module_i2c_driver(a52d_fg_driver);',
              "P447 W2 battery FIRST"):
        if x not in s:raise RuntimeError("P447E missing prerequisite "+x)
    s=once(s,"#include <linux/i2c.h>",
              "#include <linux/i2c.h>\n#include <linux/platform_device.h>\n#include <linux/of.h>\n#include <linux/kobject.h>",
              "platform and sysfs headers")
    s=once(s,'static const struct power_supply_desc a52d_battery_desc={',
              'static const struct power_supply_desc a52e_fg_desc={',
              "source descriptor name")
    s=once(s,'.name="battery",','.name="sm5714-fuelgauge",',
              "source supply must NOT steal 'battery' name")
    s=once(s,'&a52d_battery_desc,','&a52e_fg_desc,',
              "register read-only hardware FG only on I2C")
    s=once(s,'static const struct of_device_id a52d_fg_of_match[]={',
              PROXY+'\nstatic const struct of_device_id a52d_fg_of_match[]={',
              "Samsung platform battery bridge")
    s=once(s,'module_i2c_driver(a52d_fg_driver);',
'''/* Register the I2C supplier first, then the Samsung platform consumer.
 * The platform may also defer until SE7 creates the I2C fuelgauge.
 */
static int __init a52e_fg_init(void)
{
	int rc=i2c_add_driver(&a52d_fg_driver);
	if (rc)return rc;
	rc=platform_driver_register(&a52e_battery_platform_driver);
	if (rc)i2c_del_driver(&a52d_fg_driver);
	return rc;
}
module_init(a52e_fg_init);
static void __exit a52e_fg_exit(void)
{
	platform_driver_unregister(&a52e_battery_platform_driver);
	i2c_del_driver(&a52d_fg_driver);
}
module_exit(a52e_fg_exit);''',"explicit init of 2 drivers")
    return s
def check(root):
    s=(root/FILE).read_text()
    for x in (TAG,"a52e_battery_platform_probe",
              "power_supply_get_by_name(\"sm5714-fuelgauge\")",
              '.name="sec-battery"',
              '.compatible="samsung,sec-battery"',
              'devm_power_supply_register(&pdev->dev',
              "P447E battery PLATFORM_PROBE rc=0",
              "power_supply_put(arg);"):
        if x not in s:raise RuntimeError("P447E bridge missing "+x)
    assert s.count('.name="battery"')==1
    assert s.count('.name="sm5714-fuelgauge"')==1
    assert 'module_i2c_driver(a52d_fg_driver)' not in s
    assert "POWER_SUPPLY_STATUS_UNKNOWN" in s
    assert "A52D_FG_RADDR 0x8c" in s
    print("P447E BRIDGE PASS: FG under SE7 as 'sm5714-fuelgauge'; 'battery' under Samsung DT platform parent; all gauge reads unchanged")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/FILE
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
