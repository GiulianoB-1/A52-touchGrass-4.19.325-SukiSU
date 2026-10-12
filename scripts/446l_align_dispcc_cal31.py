#!/usr/bin/env python3
"""446L independent CAL_L=0x31 A/B treatment, not part of passive control."""
import argparse
from pathlib import Path

MARK="A52_PHASE446L_CAL31_AB_V1"
ANCHOR="\tclk_fabia_pll_configure(&disp_cc_pll0, regmap, &disp_cc_pll0_config);\n"
INSERT=r'''	/* A52_PHASE446L_CAL31_AB_V1: Restore the downstream Fabia PLL
	 * calibration L value, which 54/55 GKI clock-port transformations
	 * stripped because the older struct lacks .cal_l.
	 * PLL_OFF_CAL_L_VAL is 0x08 for PLL_TYPE_FABIA; test only.
	 * This is a separate intervention from the 446L passive twin.
	 */
	clk_fabia_pll_configure(&disp_cc_pll0, regmap, &disp_cc_pll0_config);
	ret = regmap_write(regmap, 0x08, 0x31);
	if (ret)
		dev_warn(&pdev->dev, "446L CAL_L=0x31 write failed: %d\n", ret);
'''

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    c=a.root/"drivers/clk/qcom/dispcc-lagoon.c"
    if not c.is_file():raise RuntimeError("dispcc-lagoon.c missing")
    s=c.read_text()
    if not a.check_only and MARK not in s:
        if s.count(ANCHOR)!=1:raise RuntimeError("CAL_L anchor count differs")
        if '.cal_l =' in s:raise RuntimeError("native .cal_l exists: do not double program")
        s=s.replace(ANCHOR,INSERT,1)
        c.write_text(s)
    if c.read_text().count(MARK)!=1:raise RuntimeError("CAL31 treatment missing")
    print("Phase446L CAL31 A/B PASS: explicit DISPCC Fabia CAL_L=0x31 treatment only")

if __name__=="__main__":
    main()
