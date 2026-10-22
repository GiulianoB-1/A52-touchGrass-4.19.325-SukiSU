#!/usr/bin/env python3
"""P447G Stage1: fix a measured ADC5 DT channel rejected by ACK 5.10.

GKI 20261022 dmesg: rfc_cam_therm invalid channel number 83.
83 (0x53) = ADC5_GPIO2_100K_PU in ACK dt-bindings.
TouchGrass 6bf351bd qcom-spmi-adc5.c adc_chans_pmic5 declares
[ADC_GPIO2_PU2] = ADC_CHAN_VOLT("gpio2_pu2",1,SCALE_HW_CALIB_DEFAULT).
This port adds one missing ACK table entry as a VOLTAGE channel.
It does not claim this channel is batt_therm, nor edit thermal thresholds.
"""
from pathlib import Path
import argparse
FILE=Path("drivers/iio/adc/qcom-spmi-adc5.c")
HEADER=Path("include/dt-bindings/iio/qcom,spmi-vadc.h")
TAG="A52_PHASE447G_ADC5_GPIO2_PU2_COMPAT_V1"
def patch(s):
    if TAG in s:return s
    beg=s.find("static const struct adc5_channels adc5_chans_pmic[ADC5_MAX_CHANNEL] = {")
    end=s.find("\n};",beg)
    if beg<0 or end<0:
        raise RuntimeError("447G ADC5 table does not match ACK layout")
    table=s[beg:end]
    if "[ADC5_GPIO2_100K_PU]" in table:
        raise RuntimeError("447G upstream GPIO2 channel already populated; inspect instead")
    marker='''	[ADC5_AMUX_THM2]	= ADC5_CHAN_TEMP("amux_thm2", 0,
					SCALE_HW_CALIB_PM5_SMB_TEMP)'''
    if table.count(marker)!=1:
        raise RuntimeError("447G unexpected ADC5 anchor count "+str(table.count(marker)))
    extra=r'''
	/* A52_PHASE447G_ADC5_GPIO2_PU2_COMPAT_V1
	 * A52 R02 rfc_cam_therm channel 0x53 (83).
	 * TG uses VOLTAGE/default scale for this GPIO ADC input.
	 * This is NOT the battery NTC until the matched DT channel is
	 * verified and converted with the Samsung temp_table_adc.
	 */
	[ADC5_GPIO2_100K_PU] = ADC5_CHAN_VOLT("gpio2_pu2", 0,
					SCALE_HW_CALIB_DEFAULT)'''
    return s[:beg]+table.replace(marker,marker+extra)+s[end:]
def check(root):
    source=(root/FILE).read_text();head=(root/HEADER).read_text()
    for val in (TAG,'[ADC5_GPIO2_100K_PU] = ADC5_CHAN_VOLT("gpio2_pu2", 0',
                "SCALE_HW_CALIB_DEFAULT"):
        if val not in source:raise RuntimeError("447G ADC5 source missing "+val)
    if "#define ADC5_GPIO2_100K_PU" not in head:
        raise RuntimeError("447G ADC5 DT header missing GPIO2 channel")
    print("P447G ADC5 PASS: channel 83 restored as TG voltage input; NOT misidentified as battery thermistor")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/FILE
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
