#!/usr/bin/env python3
"""P447F: create the exact TouchGrass SM5714-FG platform-child sysfs path.

No fake charger/muic children. Their drivers need full 5.10 verified ports.
The proven interim FG remains the only register reader (RADDR/RDATA), and
the separate sec-battery bridge continues to own Android's battery supply.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447F_SM5714_FG_CHILD_V1"
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
HELPERS=r'''
/* A52_PHASE447F_SM5714_FG_CHILD_V1
 * Mimic the exact TG MFD child device path:
 * .../i2c-34/34-0049/sm5714-fuelgauge/power_supply/sm5714-fuelgauge.
 * Other SM5714 children are NOT instantiated until their actual hardware
 * control drivers and Samsung policy are safely integrated.
 */
static void a52f_fg_child_unregister(void *arg)
{
	platform_device_unregister(arg);
}
static void a52f_fg_psy_unregister(void *arg)
{
	power_supply_unregister(arg);
}
'''
def one(s,a,b,what):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447F FG {what} anchor count={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    for m in ("A52_PHASE447E_SAMSUNG_BATTERY_PLATFORM_PATH_V1",
              "A52_PHASE447D_SM5714_FG_RO_V1",
              'power_supply_get_by_name("sm5714-fuelgauge")',
              "POWER_SUPPLY_STATUS_UNKNOWN"):
        if m not in s:raise RuntimeError("P447F FG requires "+m)
    s=one(s,"struct a52d_fg {",
          "struct a52d_fg {\n\tstruct platform_device *a52f_fg_child;",
          "child field")
    s=one(s,"static int a52d_fg_probe(struct i2c_client *client,",
          HELPERS+"\nstatic int a52d_fg_probe(struct i2c_client *client,",
          "child cleanup callbacks")
    # Insert only after ID and I2C measurements are validated. Fail probe on
    # any child registration error; no intermediate supplier is published.
    anchor="\tpsy_cfg.drv_data=fg;"
    addition=r'''	/* Publish the FG power_supply from its TG MFD child, not I2C root.
	 * The new child is a REAL kernel platform device, not a symlink.
	 */
	fg->a52f_fg_child=platform_device_alloc("sm5714-fuelgauge",
						  PLATFORM_DEVID_NONE);
	if (!fg->a52f_fg_child)
		return -ENOMEM;
	fg->a52f_fg_child->dev.parent=&client->dev;
	rc=platform_device_add(fg->a52f_fg_child);
	if (rc) {
		platform_device_put(fg->a52f_fg_child);
		return dev_err_probe(&client->dev,rc,
				    "P447F SM5714 FG_CHILD platform add failed\n");
	}
	rc=devm_add_action_or_reset(&client->dev,
				    a52f_fg_child_unregister,
				    fg->a52f_fg_child);
	if (rc)return rc;
	dev_info(&client->dev,
		 "P447F SM5714 FG_CHILD parent=%s child=%s bus=%d addr=%#x rc=0\n",
		 dev_name(&client->dev),dev_name(&fg->a52f_fg_child->dev),
		 client->adapter->nr,client->addr);
'''
    s=one(s,anchor,addition+anchor,"instantiate FG child")
    s=one(s,"fg->psy=devm_power_supply_register(&client->dev,&a52e_fg_desc,",
          "fg->psy=power_supply_register(&fg->a52f_fg_child->dev,&a52e_fg_desc,",
          "register supply under child")
    anchor='''	if (IS_ERR(fg->psy))
		return dev_err_probe(fg->dev,PTR_ERR(fg->psy),
				     "P447 W2 battery POWER_SUPPLY register failed\\n");'''
    repl=anchor+'''
	/* The I2C parent releases psy FIRST, child SECOND, dummy FG LAST.
	 * No unowned or unbound devm power_supply resources survive removal.
	 */
	rc=devm_add_action_or_reset(&client->dev,
				    a52f_fg_psy_unregister,fg->psy);
	if (rc)return rc;'''
    s=one(s,anchor,repl,"unregister order")
    return s

def check(root):
    c=(root/SRC).read_text()
    for m in (TAG,'platform_device_alloc("sm5714-fuelgauge"',
              "fg->a52f_fg_child->dev.parent=&client->dev",
              "power_supply_register(&fg->a52f_fg_child->dev",
              "P447F SM5714 FG_CHILD",
              "a52f_fg_psy_unregister,fg->psy",
              "a52f_fg_child_unregister"):
        if m not in c:raise RuntimeError("P447F FG child contract "+m)
    if "devm_power_supply_register(&client->dev,&a52e_fg_desc" in c:
        raise RuntimeError("P447F FG supplier still under I2C root")
    assert '.name="battery"' in c and '.name="sm5714-fuelgauge"' in c
    print("P447F SM5714 FG PATH PASS: child platform device, supply under child, ordered teardown; no charger/MUIC/flash")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
