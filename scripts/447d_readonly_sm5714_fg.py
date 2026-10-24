#!/usr/bin/env python3
"""447D read-only, minimal SM5714 fuel-gauge-to-power_supply bring-up.

Not the vendor 4.19 charger stack. Based on TG 6bf351bd:
 sm5714-private.h 0x71, SRAM RADDR 0x8c RDATA 0x8d
 sm5714_fuelgauge.c soc=(word*10)>>8, VBAT=2700+signed*10/109,
 temperature=((word&0x7fff)*10*2989)>>19.
 Only SRAM *read address selector* written. No WADDR/WDATA, NV, OTP, PDIC.
 Intended as transitional diagnostics; the real Samsung sec_battery must
 replace this once MFD+charger+MUIC+PDIC are compatible.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447D_SM5714_FG_RO_V1"
DIR=Path("drivers/a52_447")
C=DIR/"a52_sm5714_fg_ro.c"
K=DIR/"Kconfig"
M=DIR/"Makefile"
SRC=r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE447D_SM5714_FG_RO_V1:
 * Transitional live-battery monitor with no charging or flash controls.
 * Layout from TG 6bf351bd sm5714-private.h and sm5714_fuelgauge.c.
 */
#include <linux/i2c.h>
#include <linux/err.h>
#include <linux/errno.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/power_supply.h>
#include <linux/workqueue.h>
#include <linux/jiffies.h>
#include <linux/a52_phase447.h>

#define A52D_SM5714_FG_ADDR 0x71
#define A52D_FG_RADDR 0x8c
#define A52D_FG_RDATA 0x8d
#define A52D_FG_SOC 0x00
#define A52D_FG_VBAT 0x03
#define A52D_FG_TEMP 0x07
#define A52D_FG_ID 0x00

struct a52d_fg {
	struct device *dev;
	struct i2c_client *fg;
	struct power_supply *psy;
	struct mutex lock;
	struct delayed_work snapshot;
	bool reported_first;
};
/* This selector changes which SRAM location is read, not any learned/NV
 * parameter. No call is made to 0x8e/0x8f (SRAM WADDR/WDATA) or OTP.
 */
static int a52d_sram_read(struct a52d_fg *fg, u8 address)
{
	int rc;
	mutex_lock(&fg->lock);
	rc=i2c_smbus_write_word_data(fg->fg,A52D_FG_RADDR,address);
	if (rc>=0)
		rc=i2c_smbus_read_word_data(fg->fg,A52D_FG_RDATA);
	mutex_unlock(&fg->lock);
	return rc;
}

static int a52d_soc(struct a52d_fg *fg)
{
	int v=a52d_sram_read(fg,A52D_FG_SOC);
	int soc;
	if (v<0) return v;
	/* TouchGrass uses 0..1000 deci-percent. Rounded whole percent. */
	soc=((v*10)>>8);
	if (soc<0 || soc>1100) return -ERANGE;
	return clamp_val((soc+5)/10,0,100);
}
static int a52d_mv(struct a52d_fg *fg)
{
	int v=a52d_sram_read(fg,A52D_FG_VBAT),mv;
	if (v<0) return v;
	mv=2700+((v & 0x8000) ? -1 : 1)*((v&0x7fff)*10/109);
	if (mv<2500 || mv>5000) return -ERANGE;
	return mv;
}
static int a52d_temp(struct a52d_fg *fg)
{
	int v=a52d_sram_read(fg,A52D_FG_TEMP),t;
	/* Valid negative Celsius readings exist. Use an out-of-range error
	 * sentinel so probe never mistakes -EIO for -5 deci-Celsius.
	 */
	if (v<0) return -10000;
	t=((v&0x7fff)*10*2989)>>19;
	if (v&0x8000)t=-t;
	if (t < -400 || t>1100)return -10000;
	return t;
}

static enum power_supply_property a52d_props[] = {
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_TEMP,
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_TECHNOLOGY,
};

static int a52d_get_property(struct power_supply *psy,
			    enum power_supply_property p,
			    union power_supply_propval *v)
{
	struct a52d_fg *fg=power_supply_get_drvdata(psy);
	int rc;
	switch (p) {
	case POWER_SUPPLY_PROP_PRESENT:
		rc=a52d_soc(fg);
		if (rc<0)return rc;
		v->intval=1;return 0;
	case POWER_SUPPLY_PROP_CAPACITY:
		rc=a52d_soc(fg);
		if (rc<0)return rc;
		v->intval=rc;return 0;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		rc=a52d_mv(fg);
		if (rc<0)return rc;
		v->intval=rc*1000;return 0;
	case POWER_SUPPLY_PROP_TEMP:
		rc=a52d_temp(fg);
		if (rc<-400)return -EIO;
		v->intval=rc;return 0;
	case POWER_SUPPLY_PROP_STATUS:
		/* No charger/MUIC port: do not guess CHARGING or DISCHARGING. */
		v->intval=POWER_SUPPLY_STATUS_UNKNOWN;return 0;
	case POWER_SUPPLY_PROP_TECHNOLOGY:
		v->intval=POWER_SUPPLY_TECHNOLOGY_LION;return 0;
	default:return -EINVAL;
	}
}
static const struct power_supply_desc a52d_battery_desc={
	.name="battery",
	.type=POWER_SUPPLY_TYPE_BATTERY,
	.properties=a52d_props,
	.num_properties=ARRAY_SIZE(a52d_props),
	.get_property=a52d_get_property,
};

static void a52d_fg_snapshot(struct work_struct *work)
{
	struct a52d_fg *fg=container_of(to_delayed_work(work),
			struct a52d_fg,snapshot);
	int soc=a52d_soc(fg),mv=a52d_mv(fg),temp=a52d_temp(fg);
	/* Never fake a percentage if the gauge cannot be read. */
	if (soc>=0 && mv>=0 && temp>=-400)
		dev_info(fg->dev,
			"P447 W2 battery 30S soc=%d mv=%d temp=%d status=UNKNOWN cable=UNAVAILABLE rc=0\n",
			soc,mv,temp);
	else
		dev_err(fg->dev,
			"P447 W2 battery 30S invalid soc=%d mv=%d temp=%d rc=%d\n",
			soc,mv,temp,-EIO);
}
static void a52d_fg_unregister(void *arg)
{
	struct a52d_fg *fg=arg;
	cancel_delayed_work_sync(&fg->snapshot);
	i2c_unregister_device(fg->fg);
}
static int a52d_fg_probe(struct i2c_client *client,
			 const struct i2c_device_id *id)
{
	struct a52d_fg *fg;
	struct power_supply_config psy_cfg={};
	int rc,dev_id,soc,mv,temp;

	rc=a52_447_probe_allowed(&client->dev,A52_447_SM5714);
	if (rc)return rc;
	if (!i2c_check_functionality(client->adapter,I2C_FUNC_SMBUS_WORD_DATA))
		return dev_err_probe(&client->dev,-EOPNOTSUPP,
				     "P447 W2 FG SMBus word not supported\n");
	fg=devm_kzalloc(&client->dev,sizeof(*fg),GFP_KERNEL);
	if (!fg)return -ENOMEM;
	mutex_init(&fg->lock);
	fg->dev=&client->dev;
	INIT_DELAYED_WORK(&fg->snapshot,a52d_fg_snapshot);
	/* MFD primary is 0x49, FG is a separate secondary address 0x71. */
	fg->fg=i2c_new_dummy_device(client->adapter,A52D_SM5714_FG_ADDR);
	if (IS_ERR(fg->fg))
		return dev_err_probe(fg->dev,PTR_ERR(fg->fg),
				     "P447 W2 FG secondary addr=0x71 unavailable\n");
	rc=devm_add_action_or_reset(&client->dev,a52d_fg_unregister,fg);
	if (rc)return rc;
	dev_id=i2c_smbus_read_word_data(fg->fg,A52D_FG_ID);
	if (dev_id<0 || (dev_id & 0xff00)) {
		dev_err(fg->dev,"P447 W2 sm5714 FG_PROBE bad_id=%d rc=%d\n",
			dev_id,dev_id<0?dev_id:-ENODEV);
		return dev_id<0?dev_id:-ENODEV;
	}
	soc=a52d_soc(fg);mv=a52d_mv(fg);temp=a52d_temp(fg);
	if (soc<0 || mv<0 || temp< -400) {
		dev_err(fg->dev,"P447 W2 sm5714 FG_PROBE invalid soc=%d mv=%d temp=%d rc=%d\n",
			soc,mv,temp,-EIO);
		return -ENODEV;
	}
	psy_cfg.drv_data=fg;
	psy_cfg.of_node=client->dev.of_node;
	fg->psy=devm_power_supply_register(&client->dev,&a52d_battery_desc,
					  &psy_cfg);
	if (IS_ERR(fg->psy))
		return dev_err_probe(fg->dev,PTR_ERR(fg->psy),
				     "P447 W2 battery POWER_SUPPLY register failed\n");
	dev_info(fg->dev,
		"P447 W2 sm5714 FG_PROBE rc=0 bus=%d addr=0x%x fg_addr=0x71 irq=NOT_USED fg_id=%#x read_only=1\n",
		client->adapter->nr,client->addr,dev_id);
	dev_info(fg->dev,
		"P447 W2 battery FIRST soc=%d mv=%d temp=%d status=UNKNOWN cable=UNAVAILABLE rc=0\n",
		soc,mv,temp);
	schedule_delayed_work(&fg->snapshot,msecs_to_jiffies(30000));
	return 0;
}
static const struct of_device_id a52d_fg_of_match[]={
	{ .compatible="siliconmitus,sm5714mfd" },{}
};
MODULE_DEVICE_TABLE(of,a52d_fg_of_match);
static const struct i2c_device_id a52d_fg_id[]={
	{"sm5714mfd",0},{}
};
MODULE_DEVICE_TABLE(i2c,a52d_fg_id);
static struct i2c_driver a52d_fg_driver={
	.driver={
		.name="a52-sm5714-ro",
		.of_match_table=a52d_fg_of_match,
	},
	.probe=a52d_fg_probe,
	.id_table=a52d_fg_id,
};
module_i2c_driver(a52d_fg_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("A52 read-only SM5714 fuel-gauge 5.10 proof of real battery measurement");
''';

def check(root):
    f=(root/C).read_text()
    mk=(root/M).read_text()
    cfg=(root/K).read_text()
    for marker in (TAG,"POWER_SUPPLY_PROP_CAPACITY","P447 W2 battery FIRST",
      "A52D_FG_RADDR","A52_447_SM5714","i2c_new_dummy_device",
      "POWER_SUPPLY_STATUS_UNKNOWN"):
        if marker not in f:raise RuntimeError("P447D FG source missing "+marker)
    if "A52D_FG_WADDR" in f or "A52D_FG_WDATA" in f:
        raise RuntimeError("P447D FG contains a forbidden WADDR/WDATA constant")
    if "obj-$(CONFIG_A52_447D_SM5714_RO)" not in mk:
        raise RuntimeError("P447D FG missing Makefile integration")
    if "config A52_447D_SM5714_RO" not in cfg:
        raise RuntimeError("P447D FG missing Kconfig")
    print("P447D FG RO PASS: real SM5714 SRAM SOC/voltage/temp -> power_supply; no learned/NV, charger or PDIC writes")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();root=a.root
    if not a.check_only:
        dest=root/C
        if dest.exists() and TAG not in dest.read_text():
            raise RuntimeError("P447D existing FG provider collision")
        dest.write_text(SRC)
        mk=root/M;ms=mk.read_text()
        entry="obj-$(CONFIG_A52_447D_SM5714_RO) += a52_sm5714_fg_ro.o\n"
        if entry not in ms:mk.write_text(ms+entry)
        cfg=root/K;cs=cfg.read_text()
        if "config A52_447D_SM5714_RO" not in cs:
            cs+="""
config A52_447D_SM5714_RO
    bool "A52 SM5714 read-only interim fuelgauge (NO charger controls)"
    depends on I2C && POWER_SUPPLY
    default n
    help
      Real SOC and voltage from FG 0x71; NOT a replacement for Samsung
      battery, MUIC/PDIC or direct charging. Exposes UNKNOWN status.
"""
            cfg.write_text(cs)
    check(root)
if __name__=="__main__":main()
