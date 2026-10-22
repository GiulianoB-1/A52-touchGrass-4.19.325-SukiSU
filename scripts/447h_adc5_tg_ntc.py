#!/usr/bin/env python3
"""447H addendum, source-verified at TG commit 6bf351bd.

0x52 GPIO1, 0x53 BAT_THM, 0x54 CHG_THM, 0x55 QUIET_THERM.
TouchGrass 4.19 qcom-spmi-adc5.c adc_chans_pmic5[] represents all FOUR as
ADC_CHAN_VOLT / SCALE_HW_CALIB_DEFAULT, not IIO_TEMP. Samsung sec_adc
expects PROCESSED VOLTAGE in microvolts and maps battery,temp_table_adc
through battery,temp_table_data itself. Do not double-convert.

The earlier 447G script already adds GPIO2/3/4. This script adds GPIO1,
verifies exact TG semantics for all four, and instruments probe membership.
Unknown DT channels remain FAIL-CLOSED; 0x53/54 may never be skipped.
"""
import argparse
from pathlib import Path

FILE=Path("drivers/iio/adc/qcom-spmi-adc5.c")
HDR=Path("include/dt-bindings/iio/qcom,spmi-vadc.h")
TAG="A52_PHASE447H_ADC5_TG_GPIO1_4_VOLTS_V1"
CHANNELS=(
    ("ADC5_GPIO1_100K_PU","gpio1_pu2","0x52"),
    ("ADC5_GPIO2_100K_PU","gpio2_pu2","0x53"),
    ("ADC5_GPIO3_100K_PU","gpio3_pu2","0x54"),
    ("ADC5_GPIO4_100K_PU","gpio4_pu2","0x55"),
)

def one(src,needle,repl,why):
    n=src.count(needle)
    if n!=1: raise RuntimeError(f"P447H ADC5 {why} anchor count={n}")
    return src.replace(needle,repl,1)

def check(root):
    src=(root/FILE).read_text()
    hdr=(root/HDR).read_text()
    for name,label,num in CHANNELS:
        expected=f'[{name}] = ADC5_CHAN_VOLT("{label}", 0,'
        if expected not in src or f'#define {name}' not in hdr:
            raise RuntimeError(f"P447H ADC5 MISSING {num} {name}: cannot build unsafe missing NTC")
    if TAG not in src or "P447H ADC5 GPIO" not in src or "P447H ADC5 PROVIDER" not in src:
        raise RuntimeError("P447H ADC5 probe diagnostics missing")
    # A generic TEMP-scaled channel would double-convert with Samsung DT table.
    for name,_,num in CHANNELS:
        if f'[{name}] = ADC5_CHAN_TEMP' in src:
            raise RuntimeError(f"P447H ADC5 {num} TEMP double-conversion prohibited")
    if "SKIP_CHANNEL" in src:
        raise RuntimeError("P447H ADC5 reject silent unknown channel skipping")
    print("P447H ADC5 STRICT PASS: 0x52/53/54/55 mandatory TG VOLTAGE(uV), native Samsung DT therm table, no unknown skip")

def patch(src):
    if TAG in src:return src
    if "A52_PHASE447G_ADC5_GPIO2_PU2_COMPAT_V1" not in src:
        raise RuntimeError("P447H requires 447G GPIO2/3/4 compatibility")
    anchor='''\t[ADC5_GPIO2_100K_PU] = ADC5_CHAN_VOLT("gpio2_pu2", 0,
\t\t\t\t\tSCALE_HW_CALIB_DEFAULT)'''
    addition='''\t/* A52_PHASE447H_ADC5_TG_GPIO1_4_VOLTS_V1
\t * Exact TG ADC_CHAN_VOLT / DEFAULT semantics. A52 battery 0x53
\t * and charger 0x54 require Samsung voltage-to-temp lookup.
\t * Mainline ADC5_CHAN_TEMP would DOUBLE CONVERT temperatures.
\t */
\t[ADC5_GPIO1_100K_PU] = ADC5_CHAN_VOLT("gpio1_pu2", 0,
\t\t\t\t\tSCALE_HW_CALIB_DEFAULT)
'''
    src=one(src,anchor,addition+anchor,"insert GPIO1")
    marker='''\t\tprop.scale_fn_type =
\t\t\tdata->adc_chans[prop.channel].scale_fn_type;'''
    rep='''\t\t/* TG A52 NTCs: mandatory, no silent skipped DT channel.
\t\t * Runtime log confirms each channel made it into the IIO list.
\t\t */
\t\tif (prop.channel >= ADC5_GPIO1_100K_PU &&
\t\t    prop.channel <= ADC5_GPIO4_100K_PU)
\t\t\tdev_info(adc->dev,
\t\t\t\t "P447H ADC5 GPIO name=%s ch=0x%x type=VOLT scale=DEFAULT prescale=%u table=Samsung_DT\\n",
\t\t\t\t child->name,prop.channel,prop.prescale);
\t\tprop.scale_fn_type =
\t\t\tdata->adc_chans[prop.channel].scale_fn_type;'''
    src=one(src,marker,rep,"per-channel provenance")
    marker='''\tret = adc5_get_dt_data(adc, node);
\tif (ret) {
\t\tdev_err(dev, "adc get dt data failed\\n");
\t\treturn ret;
\t}'''
    rep='''\tret = adc5_get_dt_data(adc, node);
\tif (ret) {
\t\tdev_err(dev, "P447H ADC5 PROVIDER DT_FAIL rc=%d missing required channel?\\n",ret);
\t\tdev_err(dev, "adc get dt data failed\\n");
\t\treturn ret;
\t}
\tdev_info(dev,"P447H ADC5 PROVIDER DT_OK nchannels=%u source=TG_6bf351bd voltage_to_Samsung_table\\n",
\t\t adc->nchannels);'''
    src=one(src,marker,rep,"provider status")
    return src

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    args=p.parse_args()
    dest=args.root/FILE
    if not args.check_only:dest.write_text(patch(dest.read_text()))
    check(args.root)

if __name__=="__main__":main()
