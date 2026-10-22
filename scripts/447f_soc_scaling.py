#!/usr/bin/env python3
"""447F read-only TG-style capacity scaling for Android-facing battery ONLY.

TouchGrass: raw 52.8% -> 57.4%, implying effective runtime max ~920
deci-percent; nominal DT capacity_max=1000, margin=300, type=0x1f.
The effective maximum can be modified by Samsung policy/calibration;
we do NOT pretend 920 is an always-correct factory value or write learned NV.
Retain the "sm5714-fuelgauge" supply as raw real hardware percentage.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447F_SOC_READONLY_SCALE_V1"
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
KCFG=Path("drivers/a52_447/Kconfig")
def one(s,a,b,what):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447F scale {what} anchors={n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:return s
    if "A52_PHASE447F_SM5714_FG_CHILD_V1" not in s:
        raise RuntimeError("P447F needs MFD-FG-child generated C")
    before='''static int a52d_soc(struct a52d_fg *fg)
{
	int v=a52d_sram_read(fg,A52D_FG_SOC);
	int soc;
	if (v<0) return v;
	/* TouchGrass uses 0..1000 deci-percent. Rounded whole percent. */
	soc=((v*10)>>8);
	if (soc<0 || soc>1100) return -ERANGE;
	return clamp_val((soc+5)/10,0,100);
}'''
    after='''/* A52_PHASE447F_SOC_READONLY_SCALE_V1
 * TG sm5714_get_soc uses this exact deci-percent conversion.
 */
static int a52f_soc_deci(struct a52d_fg *fg)
{
	int v=a52d_sram_read(fg,A52D_FG_SOC),soc;
	if (v<0)return v;
	soc=(v*10)>>8;
	if (soc<0 || soc>1100)return -ERANGE;
	return clamp_val(soc,0,1000);
}
static int a52d_soc(struct a52d_fg *fg)
{
	int deci=a52f_soc_deci(fg);
	if (deci<0)return deci;
	return clamp_val((deci+5)/10,0,100);
}'''
    s=one(s,before,after,"deci-percent source")
    before='''	/* Preserve read failures; NEVER synthesize capacity, charger status,
	 * USB/AC online state, or an unverified battery-present value.
	 */
	return power_supply_get_property(bat->fg,psp,val);'''
    after='''	/* Preserve read failures; never synthesize charger state.
	 * The FG supply remains RAW. Only Android's battery value is
	 * scaled using a runtime-observed, compile-time auditable estimate
	 * of TouchGrass capacity_max. No learned/NV writes.
	 */
	if (psp==POWER_SUPPLY_PROP_CAPACITY) {
		int deci=a52f_soc_deci(power_supply_get_drvdata(bat->fg));
		int scaled;
		if (deci<0)return deci;
		/* TG: scaled_deci = (raw_deci - min) * 1000 /
		 * (capacity_max - min), min=0. TG truncates to integer %.
		 */
		scaled=clamp_val(deci*1000 /
				 CONFIG_A52_447F_CAPACITY_MAX_DECI,0,1000);
		val->intval=scaled/10;
		pr_info_ratelimited("P447F SOC raw_deci=%d max_deci=%d scaled_deci=%d ui=%d source=TG_20261022_estimate (not NV)\\n",
			deci,CONFIG_A52_447F_CAPACITY_MAX_DECI,
			scaled,val->intval);
		return 0;
	}
	return power_supply_get_property(bat->fg,psp,val);'''
    return one(s,before,after,"Android-facing battery scaling")
def check(root):
    c=(root/SRC).read_text()
    for item in (TAG,"static int a52f_soc_deci",
                 "P447F SOC raw_deci=%d","CONFIG_A52_447F_CAPACITY_MAX_DECI",
                 "sm5714-fuelgauge", "P447F SM5714 FG_CHILD",
                 "POWER_SUPPLY_STATUS_UNKNOWN"):
        if item not in c:raise RuntimeError("P447F scale missing "+item)
    k=(root/KCFG).read_text()
    assert "config A52_447F_CAPACITY_MAX_DECI" in k
    print("P447F SOC PASS: raw hardware supplier intact; Android battery scaled via compiled effective maximum, no NV writes")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();k=a.root/KCFG;f=a.root/SRC
    if not a.check_only:
        s=k.read_text()
        if "config A52_447F_CAPACITY_MAX_DECI" not in s:
            s+="""
config A52_447F_CAPACITY_MAX_DECI
    int "A52 empirical TG effective FG capacity maximum (0.1%)"
    range 700 1000
    default 920
    help
      TG 2026-10-22: raw SOC 528 -> scaled 574 implies 920.
      This is a read-only DISPLAY estimate, not production auto calibration.
      Do not use it to program the gauge or control charger current.
"""
            k.write_text(s)
        f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
