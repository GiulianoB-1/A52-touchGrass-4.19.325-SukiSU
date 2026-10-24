#!/usr/bin/env python3
"""447F corrected: replicate TG nominal SOC mathematics without inferred scale.

2026-10-22 correction: GKI 51% vs TG 57% is explained by charging in
recovery between captures. GKI's actual reading was CORRECT. Absolutely
do not apply the inferred 920 effective max to the shipped UI.

Port the read-only TG 0.1%-resolution SOC/scaling computation for
diagnostic parity, with DT capacity_max/min and nominal 1000/0 fallback.
Preserve the already-working GKI Android CAPACITY output unmodified until
back-to-back, NO-CHARGING comparisons prove policy changes are needed.
TG atomic/skip-abnormal/full calibration require charger state and are not
enabled here; no gauge calibration/NV/OTP writes.
"""
import argparse
from pathlib import Path
TAG="A52_PHASE447F_SOC_PARITY_CORRECTED_V2"
SRC=Path("drivers/a52_447/a52_sm5714_fg_ro.c")
KCFG=Path("drivers/a52_447/Kconfig")
def one(s,a,b,what):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P447F soc parity {what} anchors={n}")
    return s.replace(a,b,1)

def patch(s):
    if TAG in s:return s
    if "A52_PHASE447F_SM5714_FG_CHILD_V1" not in s:
        raise RuntimeError("P447F requires SM5714 FG child source")
    raw='''static int a52d_soc(struct a52d_fg *fg)
{
	int v=a52d_sram_read(fg,A52D_FG_SOC);
	int soc;
	if (v<0) return v;
	/* TouchGrass uses 0..1000 deci-percent. Rounded whole percent. */
	soc=((v*10)>>8);
	if (soc<0 || soc>1100) return -ERANGE;
	return clamp_val((soc+5)/10,0,100);
}'''
    replacement='''/* A52_PHASE447F_SOC_PARITY_CORRECTED_V2
 * TG sm5714_get_soc(): 0.1% = (raw_sram_word * 10) >> 8.
 * Preserve the GKI capacity rounding which was independently verified.
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
    s=one(s,raw,replacement,"TG raw 0.1 percent conversion")
    # No synthetic 920! The running DT has nominal max=1000 and min=0.
    s=one(s,"struct a52d_fg {\n",
          "struct a52d_fg {\n\tu32 a52f_capacity_max;\n\tu32 a52f_capacity_min;\n\tu32 a52f_calc_type;\n",
          "DT capacity configuration")
    anchor='''	mutex_init(&fg->lock);
	fg->dev=&client->dev;'''
    dt='''	mutex_init(&fg->lock);
	/* TG parse_dt() reads the sm5714-fuelgauge DT node. Missing
	 * policy is NOT a reason to invent 920; use pinned A52 R02's
	 * capacity_max=1000, min=0 only for read-only comparison.
	 */
	fg->a52f_capacity_max=1000;
	fg->a52f_capacity_min=0;
	fg->a52f_calc_type=0x1f;
	{
		struct device_node *np=of_find_node_by_name(NULL,
							"sm5714-fuelgauge");
		if (np) {
			of_property_read_u32(np,"fuelgauge,capacity_max",
					    &fg->a52f_capacity_max);
			of_property_read_u32(np,"fuelgauge,capacity_min",
					    &fg->a52f_capacity_min);
			of_property_read_u32(np,
				"fuelgauge,capacity_calculation_type",
				&fg->a52f_calc_type);
			of_node_put(np);
		}
	}
	if (fg->a52f_capacity_max<=fg->a52f_capacity_min ||
	    fg->a52f_capacity_max>1000) {
		dev_err(&client->dev,
			"P447F SOC invalid DT capacity_max=%u min=%u rc=%d\\n",
			fg->a52f_capacity_max,fg->a52f_capacity_min,-EINVAL);
		return -EINVAL;
	}
	dev_info(&client->dev,
		"P447F SOC TG_DT max=%u min=%u type=%#x mode=compare_only (no inferred 920)\\n",
		fg->a52f_capacity_max,fg->a52f_capacity_min,
		fg->a52f_calc_type);
	fg->dev=&client->dev;'''
    s=one(s,anchor,dt,"probe DT capacity reading")
    original='''	/* Preserve read failures; NEVER synthesize capacity, charger status,
	 * USB/AC online state, or an unverified battery-present value.
	 */
	return power_supply_get_property(bat->fg,psp,val);'''
    new='''	/* Port the read-only TG scale calculation, but never ALTER Android
	 * based on the now-disproven 51% vs 57% comparison. We lack the
	 * production charging-state-dependent ATOMIC/SKIP_ABNORMAL logic.
	 * Compare the candidate only until truly back-to-back same-SOC
	 * boot captures establish a real discrepancy.
	 */
	if (psp==POWER_SUPPLY_PROP_CAPACITY) {
		struct a52d_fg *fg=power_supply_get_drvdata(bat->fg);
		int deci=a52f_soc_deci(fg);
		int ret,scaled;
		if (deci<0)return deci;
		ret=power_supply_get_property(bat->fg,psp,val);
		if (ret)return ret;
		scaled=clamp_val(
			((deci-(int)fg->a52f_capacity_min)*1000) /
			(int)(fg->a52f_capacity_max-fg->a52f_capacity_min),
			0,1000);
		pr_info_ratelimited(
			"P447F SOC raw_deci=%d TG_max=%u TG_min=%u TG_type=%#x candidate_pct=%d ui_preserved=%d source=DT_no_charge_comparison_pending\\n",
			deci,fg->a52f_capacity_max,fg->a52f_capacity_min,
			fg->a52f_calc_type,scaled/10,val->intval);
		return 0;
	}
	return power_supply_get_property(bat->fg,psp,val);'''
    s=one(s,original,new,"Android CAPACITY passthrough + TG candidate audit")
    return s

def check(root):
    c=(root/SRC).read_text()
    # Phase447H supersedes 447F's comparison-only Android capacity value.
    # Validate the actual learned-max successor while retaining raw FG math.
    if "A52_PHASE447H_LEARNED_SOC_SYSFS_V1" in c:
        for m in (TAG,"static int a52f_soc_deci",
                  "P447H SOC raw=%d","writer=%s",
                  "batt_capacity_max,0660"):
            if m not in c:raise RuntimeError("P447F/447H SOC contract "+m)
        if "CONFIG_A52_447F_CAPACITY_MAX_DECI" in c:
            raise RuntimeError("P447F/447H guessed 920 prohibited")
        print("P447F -> P447H SOC PASS: raw FG mathematics, bounded learned capacity ABI")
        return
    for item in (TAG,"static int a52f_soc_deci",
                 "source=DT_no_charge_comparison_pending",
                 "ui_preserved=%d","fuelgauge,capacity_max",
                 "sm5714-fuelgauge","P447F SM5714 FG_CHILD"):
        if item not in c:raise RuntimeError("P447F corrected SOC audit missing "+item)
    if "A52_PHASE452_H0_MANUAL_SAFE_CHARGER_V1" in c:
        if "v->intval=a52h0_psy_status(fg,!!(st&BIT(0)))" not in c:
            raise RuntimeError("P447F H0 status implementation missing")
    elif "POWER_SUPPLY_STATUS_UNKNOWN" not in c:
        raise RuntimeError("P447F read-only status UNKNOWN missing")
    if "CONFIG_A52_447F_CAPACITY_MAX_DECI" in c:
        raise RuntimeError("P447F inferred max used in compiled source")
    if "default 920" in (root/KCFG).read_text():
        raise RuntimeError("P447F inferred 920 still in Kconfig")
    if "scaled/10,val->intval" not in c:
        raise RuntimeError("P447F raw capacity passthrough not verified")
    print("P447F SOC PASS: TG math diagnostics, DT max/min read-only; GKI 51% preserved; no guessed 920")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();f=a.root/SRC
    if not a.check_only:
        f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
