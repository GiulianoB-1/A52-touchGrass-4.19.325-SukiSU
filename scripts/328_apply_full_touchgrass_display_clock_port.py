#!/usr/bin/env python3
import argparse
from pathlib import Path

M="A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1"
SAFE=("ahb","byte0","dp_aux","dp_crypto","dp_link","dp_pixel","esc0","mdp","pclk0","rot","vsync")

def one(s,a,b,n):
    c=s.count(a)
    if c!=1: raise SystemExit(f"Phase328 {n}: anchor count {c}")
    return s.replace(a,b,1)

def span(s,n):
    p=s.find(n+"(")
    if p<0: raise SystemExit("Phase328 missing function "+n)
    a=s.rfind("\nstatic ",0,p)+1; q=s.find("{",p); d=0
    for i in range(q,len(s)):
        d += (s[i]=="{")-(s[i]=="}")
        if d==0:
            return a,i+2 if i+1<len(s) and s[i+1]=='\n' else i+1
    raise SystemExit("Phase328 unterminated "+n)

def fnreplace(s,n,new):
    a,b=span(s,n); return s[:a]+new.rstrip()+"\n"+s[b:]

def in_fn(s,n,a,b):
    x,y=span(s,n); q=s[x:y]
    if q.count(a)!=1: raise SystemExit(f"Phase328 {n}: local anchor count {q.count(a)}")
    return s[:x]+q.replace(a,b,1)+s[y:]

def patch_h(s):
    old='''struct clk_rcg2 {\n\tu32\t\t\tcmd_rcgr;\n\tu8\t\t\tmnd_width;\n\tu8\t\t\thid_width;\n\tu8\t\t\tsafe_src_index;\n\tconst struct parent_map\t*parent_map;\n\tconst struct freq_tbl\t*freq_tbl;\n\tstruct clk_regmap\tclkr;\n\tu8\t\t\tcfg_off;\n};\n'''
    new='''/* A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1 */\nstruct clk_rcg2 {\n\tu32\t\t\tcmd_rcgr;\n\tu8\t\t\tmnd_width;\n\tu8\t\t\thid_width;\n\tu8\t\t\tsafe_src_index;\n\tconst struct parent_map\t*parent_map;\n\tconst struct freq_tbl\t*freq_tbl;\n\tunsigned long\t\tcurrent_freq;\n\tbool\t\t\tenable_safe_config;\n\tstruct clk_regmap\tclkr;\n\tu8\t\t\tcfg_off;\n\tu8\t\t\tflags;\n};\n\n#define FORCE_ENABLE_RCG\tBIT(0)\n#define HW_CLK_CTRL_MODE\tBIT(1)\n#define RCG_UPDATE_BEFORE_PLL\tBIT(2)\n'''
    return one(s,old,new,"header")

def patch_r(s):
    e="enum freq_policy {\n\tFLOOR,\n\tCEIL,\n};\n"
    s=one(s,e,e+'''\nstatic struct freq_tbl cxo_f = {\n\t.freq = 19200000,\n\t.src = 0,\n\t.pre_div = 1,\n\t.m = 0,\n\t.n = 0,\n};\n''',"cxo")
    a="static u8 clk_rcg2_get_parent(struct clk_hw *hw)\n"
    s=one(s,a,r'''static int clk_rcg2_set_force_enable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	int count, ret;

	ret = regmap_update_bits(rcg->clkr.regmap, rcg->cmd_rcgr + CMD_REG,
				 CMD_ROOT_EN, CMD_ROOT_EN);
	if (ret)
		return ret;
	for (count = 500; count > 0; count--) {
		ret = clk_rcg2_is_enabled(hw);
		if (ret < 0)
			return ret;
		if (ret)
			return 0;
		udelay(1);
	}
	WARN(1, "%s: rcg didn't turn on.\n", clk_hw_get_name(hw));
	return -EBUSY;
}

static int clk_rcg2_clear_force_enable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);

	return regmap_update_bits(rcg->clkr.regmap, rcg->cmd_rcgr + CMD_REG,
				 CMD_ROOT_EN, 0);
}

'''+a,"force")
    s=fnreplace(s,"clk_rcg2_recalc_rate",r'''static unsigned long
clk_rcg2_recalc_rate(struct clk_hw *hw, unsigned long parent_rate)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	struct clk_hw *parent = clk_hw_get_parent(hw);
	const struct freq_tbl *f_curr;
	u32 cfg, src, hid_div, m = 0, n = 0, mode = 0, mask;
	unsigned long rrate, prate;

	if ((rcg->flags & RCG_UPDATE_BEFORE_PLL) && parent) {
		prate = clk_hw_get_rate(parent);
		if (prate != parent_rate)
			parent_rate = prate;
	}
	regmap_read(rcg->clkr.regmap, RCG_CFG_OFFSET(rcg), &cfg);
	src = (cfg & CFG_SRC_SEL_MASK) >> CFG_SRC_SEL_SHIFT;
	if (rcg->enable_safe_config &&
	    (!clk_hw_is_prepared(hw) || !clk_hw_is_enabled(hw)) && !src) {
		if (!rcg->current_freq)
			rcg->current_freq = cxo_f.freq;
		return rcg->current_freq;
	}
	if (rcg->mnd_width) {
		mask = BIT(rcg->mnd_width) - 1;
		regmap_read(rcg->clkr.regmap, RCG_M_OFFSET(rcg), &m); m &= mask;
		regmap_read(rcg->clkr.regmap, RCG_N_OFFSET(rcg), &n);
		n = (~n & mask) + m;
		mode = (cfg & CFG_MODE_MASK) >> CFG_MODE_SHIFT;
	}
	if (rcg->enable_safe_config && rcg->current_freq && rcg->freq_tbl) {
		f_curr = qcom_find_freq(rcg->freq_tbl, rcg->current_freq);
		if (!f_curr)
			return 0;
		hid_div = f_curr->pre_div;
	} else {
		mask = BIT(rcg->hid_width) - 1;
		hid_div = (cfg >> CFG_SRC_DIV_SHIFT) & mask;
	}
	rrate = calc_rate(parent_rate, m, n, mode, hid_div);
	if (rcg->enable_safe_config && !rcg->current_freq)
		rcg->current_freq = rrate;
	return rrate;
}
''')
    s=fnreplace(s,"__clk_rcg2_configure",r'''static int __clk_rcg2_configure(struct clk_rcg2 *rcg, const struct freq_tbl *f)
{
	u32 cfg, mask;
	struct clk_hw *hw = &rcg->clkr.hw;
	int ret, index = qcom_find_src_index(hw, rcg->parent_map, f->src);

	if (index < 0)
		return index;
	if (rcg->mnd_width && f->n) {
		mask = BIT(rcg->mnd_width) - 1;
		ret = regmap_update_bits(rcg->clkr.regmap, RCG_M_OFFSET(rcg), mask, f->m);
		if (ret) return ret;
		ret = regmap_update_bits(rcg->clkr.regmap, RCG_N_OFFSET(rcg), mask,
					 ~(f->n - f->m));
		if (ret) return ret;
		ret = regmap_update_bits(rcg->clkr.regmap, RCG_D_OFFSET(rcg), mask, ~f->n);
		if (ret) return ret;
	}
	mask = (BIT(rcg->hid_width) - 1) | CFG_SRC_SEL_MASK |
		CFG_MODE_MASK | CFG_HW_CLK_CTRL_MASK;
	cfg = f->pre_div << CFG_SRC_DIV_SHIFT;
	cfg |= rcg->parent_map[index].cfg << CFG_SRC_SEL_SHIFT;
	if (rcg->mnd_width && f->n && f->m != f->n)
		cfg |= CFG_MODE_DUAL_EDGE;
	if (rcg->flags & HW_CLK_CTRL_MODE)
		cfg |= CFG_HW_CLK_CTRL_MASK;
	return regmap_update_bits(rcg->clkr.regmap, RCG_CFG_OFFSET(rcg), mask, cfg);
}
''')
    helper=r'''/* A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1 */
static bool clk_rcg2_current_config(struct clk_rcg2 *rcg,
				    const struct freq_tbl *f)
{
	struct clk_hw *hw = &rcg->clkr.hw;
	u32 cfg, mask, new_cfg;
	int index;

	if (rcg->mnd_width) {
		mask = BIT(rcg->mnd_width) - 1;
		regmap_read(rcg->clkr.regmap, RCG_M_OFFSET(rcg), &cfg);
		if ((cfg & mask) != (f->m & mask)) return false;
		regmap_read(rcg->clkr.regmap, RCG_N_OFFSET(rcg), &cfg);
		if ((cfg & mask) != (~(f->n - f->m) & mask)) return false;
	}
	mask = (BIT(rcg->hid_width) - 1) | CFG_SRC_SEL_MASK;
	index = qcom_find_src_index(hw, rcg->parent_map, f->src);
	if (index < 0) return false;
	new_cfg = ((f->pre_div << CFG_SRC_DIV_SHIFT) |
		(rcg->parent_map[index].cfg << CFG_SRC_SEL_SHIFT)) & mask;
	regmap_read(rcg->clkr.regmap, RCG_CFG_OFFSET(rcg), &cfg);
	return new_cfg == (cfg & mask);
}

'''
    a="static int clk_rcg2_configure(struct clk_rcg2 *rcg, const struct freq_tbl *f)\n"
    s=one(s,a,helper+a,"current config")
    bo="\t\tif (cfg == rcg->parent_map[i].cfg) {\n\t\t\tf.src = rcg->parent_map[i].src;\n\t\t\treturn clk_rcg2_configure(rcg, &f);\n\t\t}\n"
    bn="\t\tif (cfg == rcg->parent_map[i].cfg) {\n\t\t\tf.src = rcg->parent_map[i].src;\n\t\t\tif (clk_rcg2_current_config(rcg, &f))\n\t\t\t\treturn 0;\n\t\t\treturn clk_rcg2_configure(rcg, &f);\n\t\t}\n"
    s=in_fn(s,"clk_byte2_set_rate",bo,bn)
    po="\t\tf.m = frac->num;\n\t\tf.n = frac->den;\n\n\t\treturn clk_rcg2_configure(rcg, &f);\n"
    pn="\t\tf.m = frac->num;\n\t\tf.n = frac->den;\n\n\t\tif (clk_rcg2_current_config(rcg, &f))\n\t\t\treturn 0;\n\t\treturn clk_rcg2_configure(rcg, &f);\n"
    s=in_fn(s,"clk_pixel_set_rate",po,pn)
    s=fnreplace(s,"__clk_rcg2_set_rate",r'''static int __clk_rcg2_set_rate(struct clk_hw *hw, unsigned long rate,
			       enum freq_policy policy)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	const struct freq_tbl *f;
	int ret;

	switch (policy) {
	case FLOOR: f = qcom_find_freq_floor(rcg->freq_tbl, rate); break;
	case CEIL: f = qcom_find_freq(rcg->freq_tbl, rate); break;
	default: return -EINVAL;
	}
	if (!f) return -EINVAL;
	if (rcg->enable_safe_config && !clk_hw_is_prepared(hw)) {
		rcg->current_freq = rate;
		return 0;
	}
	ret = clk_rcg2_configure(rcg, f);
	if (!ret) rcg->current_freq = rate;
	return ret;
}
''')
    o="const struct clk_ops clk_rcg2_ops = {\n"
    life=r'''static int clk_rcg2_enable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	struct clk_hw *parent;
	unsigned long rate, parent_rate = 0;
	const struct freq_tbl *f;
	int ret;

	if (!rcg->enable_safe_config)
		return 0;
	rate = rcg->current_freq;
	if (!rate) {
		parent = clk_hw_get_parent(hw);
		if (parent) parent_rate = clk_hw_get_rate(parent);
		rate = clk_rcg2_recalc_rate(hw, parent_rate);
	}
	f = rate == cxo_f.freq ? &cxo_f : qcom_find_freq(rcg->freq_tbl, rate);
	if (!f) return -EINVAL;
	ret = clk_rcg2_set_force_enable(hw);
	if (ret) return ret;
	ret = clk_rcg2_configure(rcg, f);
	clk_rcg2_clear_force_enable(hw);
	return ret;
}

static void clk_rcg2_disable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	int ret;

	if (!rcg->enable_safe_config) return;
	ret = clk_rcg2_set_force_enable(hw);
	if (ret) return;
	ret = clk_rcg2_configure(rcg, &cxo_f);
	if (ret) pr_err("%s: CXO configuration failed\n", clk_hw_get_name(hw));
	clk_rcg2_clear_force_enable(hw);
}

'''
    s=one(s,o,life+o,"lifecycle")
    s=one(s,o,o+"\t.enable = clk_rcg2_enable,\n\t.disable = clk_rcg2_disable,\n","ops")
    s=one(s,"\t{ 1, 1 },\n\t{ 2, 3 },\n\t{ }\n};\n","\t{ 1, 1 },\n\t{ }\n};\n","pixel fraction")
    return s

def patch_d(s):
    for x in SAFE:
        n="disp_cc_mdss_"+x+"_clk_src"; p=s.find("static struct clk_rcg2 "+n+" = {")
        if p<0: raise SystemExit("Phase328 missing "+n)
        q=s.find("\n};",p); b=s[p:q]
        key="\t.freq_tbl = " if "\t.freq_tbl = " in b else "\t.parent_map = "
        i=b.find(key); j=b.find("\n",i)+1
        b=b[:j]+"\t.enable_safe_config = true,\n"+b[j:]; s=s[:p]+b+s[q:]
    if "#include <linux/regulator/consumer.h>" not in s:
        s=one(s,"#include <linux/regmap.h>\n","#include <linux/regmap.h>\n#include <linux/regulator/consumer.h>\n\n#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>\n","vdd includes")
    a="static int disp_cc_lagoon_probe(struct platform_device *pdev)\n{\n\tstruct regmap *regmap;\n\tint ret;\n"
    s=one(s,a,"static int disp_cc_lagoon_probe(struct platform_device *pdev)\n{\n\tstruct regmap *regmap;\n\tstruct regulator *vdd_cx;\n\tint ret;\n\tint vdd_rc;\n","probe")
    a='\ta52_ackfr_record("DISPCC probe enter dev=%s node=%s",\n'
    v='''\t/* A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1
\t * GKI lacks Samsung per-clock vdd_class/rate_max. Hold NOMINAL as the
\t * upper-bound compatibility vote while the full display-clock port runs.
\t */
\ta52_ackfr_record("P276 328V s=0");
\tvdd_cx = devm_regulator_get(&pdev->dev, "vdd_cx");
\tvdd_rc = IS_ERR(vdd_cx) ? PTR_ERR(vdd_cx) : 0;
\ta52_ackfr_record("P276 328V s=1 rc=%d", vdd_rc);
\tif (IS_ERR(vdd_cx)) return PTR_ERR(vdd_cx);
\tvdd_rc = regulator_set_voltage(vdd_cx, RPMH_REGULATOR_LEVEL_NOM, INT_MAX);
\ta52_ackfr_record("P276 328V s=2 rc=%d", vdd_rc);
\tif (vdd_rc) return vdd_rc;
\tvdd_rc = regulator_enable(vdd_cx);
\ta52_ackfr_record("P276 328V s=3 rc=%d", vdd_rc);
\tif (vdd_rc) return vdd_rc;

'''
    return one(s,a,v+a,"vdd")

def check(h,r,d):
    if h.count(M)!=1 or r.count(M)!=1 or d.count(M)!=1: raise SystemExit("Phase328 marker count")
    if d.count(".enable_safe_config = true,")!=11: raise SystemExit("Phase328 safe flag count")
    if r.count("clk_rcg2_current_config(rcg, &f)")!=2: raise SystemExit("Phase328 guard count")
    for x in ("current_freq","enable_safe_config","RCG_UPDATE_BEFORE_PLL","HW_CLK_CTRL_MODE"):
        if x not in h: raise SystemExit("Phase328 missing "+x)
    for x in ("RCG_D_OFFSET(rcg), mask, ~f->n","\t.enable = clk_rcg2_enable,","\t.disable = clk_rcg2_disable,"):
        if x not in r: raise SystemExit("Phase328 missing "+x)
    t=r[r.find("static const struct frac_entry frac_table_pixel[]"):]; t=t[:t.find("};")]
    if "{ 2, 3 }" in t: raise SystemExit("Phase328 pixel 2/3 remains")
    for x in ("P276 328V s=3 rc=%d","RPMH_REGULATOR_LEVEL_NOM"):
        if x not in d: raise SystemExit("Phase328 missing "+x)

def main():
    a=argparse.ArgumentParser(); a.add_argument("--root",required=True); a.add_argument("--check-only",action="store_true"); x=a.parse_args()
    q=Path(x.root)/"drivers/clk/qcom"; hp=q/"clk-rcg.h"; rp=q/"clk-rcg2.c"; dp=q/"dispcc-lagoon.c"
    h,r,d=hp.read_text(),rp.read_text(),dp.read_text()
    if x.check_only:
        check(h,r,d); print("Phase328 full TouchGrass display-clock semantic port check: PASS"); return
    if M in h+r+d: raise SystemExit("Phase328 already applied")
    h2,r2,d2=patch_h(h),patch_r(r),patch_d(d); check(h2,r2,d2)
    hp.write_text(h2); rp.write_text(r2); dp.write_text(d2)
    print("Phase328 full TouchGrass display-clock semantic port: PASS")
if __name__=="__main__": main()
