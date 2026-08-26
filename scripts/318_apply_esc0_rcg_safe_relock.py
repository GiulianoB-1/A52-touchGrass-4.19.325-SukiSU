#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE318_ESC0_RCG_SAFE_RELOCK_AB_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase318 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if MARK in text:
        return text
    old = "\tconst struct freq_tbl\t*freq_tbl;\n\tstruct clk_regmap\tclkr;\n\tu8\t\t\tcfg_off;\n"
    new = (
        "\tconst struct freq_tbl\t*freq_tbl;\n"
        "\t/* " + MARK + ": opt-in relatch on RCG enable. */\n"
        "\tbool\t\t\tenable_safe_config;\n"
        "\tstruct clk_regmap\tclkr;\n"
        "\tu8\t\t\tcfg_off;\n"
    )
    return replace_once(text, old, new, "clk-rcg.h struct anchor")


def patch_rcg2(text: str) -> str:
    if MARK in text:
        return text

    helper_anchor = "static u8 clk_rcg2_get_parent(struct clk_hw *hw)\n"
    helper = r'''/* A52_PHASE318_ESC0_RCG_SAFE_RELOCK_AB_V1
 * Narrow downstream-safe-config backport used only by Lagoon ESC0.
 *
 * TouchGrass marks disp_cc_mdss_esc0_clk_src enable_safe_config=true.
 * During continuous splash, DSI deliberately skips esc clk_set_rate() but
 * still executes clk_prepare_enable(esc_clk). Downstream clk_rcg2_ops then
 * force-enables the RCG and re-applies its inherited mux/divider config,
 * pulsing CMD_UPDATE before the leaf ESC branch is used. The original GKI
 * port stripped that callback and flag.
 *
 * All existing GKI RCGs default to false. Phase318 opts in ESC0 only.
 */
static int a52_p318_rcg_set_force_enable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	int ret, count;

	ret = regmap_update_bits(rcg->clkr.regmap, rcg->cmd_rcgr + CMD_REG,
				 CMD_ROOT_EN, CMD_ROOT_EN);
	if (ret)
		return ret;

	for (count = 500; count > 0; count--) {
		if (clk_rcg2_is_enabled(hw))
			return 0;
		udelay(1);
	}

	WARN(1, "%s: Phase318 RCG force-enable timed out\n",
	     clk_hw_get_name(hw));
	return -EBUSY;
}

static int a52_p318_rcg_clear_force_enable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);

	return regmap_update_bits(rcg->clkr.regmap, rcg->cmd_rcgr + CMD_REG,
				  CMD_ROOT_EN, 0);
}

'''
    text = replace_once(text, helper_anchor, helper + helper_anchor,
                        "force-enable helper anchor")

    enable_anchor = "const struct clk_ops clk_rcg2_ops = {\n"
    enable_fn = r'''static int a52_p318_clk_rcg2_enable(struct clk_hw *hw)
{
	struct clk_rcg2 *rcg = to_clk_rcg2(hw);
	const struct freq_tbl *f;
	unsigned long rate;
	int ret, clear_ret;

	if (!rcg->enable_safe_config)
		return 0;

	/* For ESC0 the inherited Golden rate is 19.2 MHz and is present in
	 * its one-entry frequency table. Read the live/cached CCF rate rather
	 * than introducing downstream VDD/current_freq machinery into GKI.
	 */
	rate = clk_hw_get_rate(hw);
	f = qcom_find_freq(rcg->freq_tbl, rate);
	if (!f) {
		pr_err("A52P318 %s: no freq entry for inherited rate %lu\n",
		       clk_hw_get_name(hw), rate);
		return -EINVAL;
	}

	pr_info("A52P318 %s: relock inherited RCG rate=%lu\n",
		clk_hw_get_name(hw), rate);

	ret = a52_p318_rcg_set_force_enable(hw);
	if (ret)
		return ret;

	ret = clk_rcg2_configure(rcg, f);
	clear_ret = a52_p318_rcg_clear_force_enable(hw);
	if (!ret)
		ret = clear_ret;

	pr_info("A52P318 %s: relock complete rc=%d\n",
		clk_hw_get_name(hw), ret);
	return ret;
}

'''
    text = replace_once(text, enable_anchor, enable_fn + enable_anchor,
                        "clk_rcg2_ops anchor")

    old_ops = "const struct clk_ops clk_rcg2_ops = {\n\t.is_enabled = clk_rcg2_is_enabled,\n"
    new_ops = (
        "const struct clk_ops clk_rcg2_ops = {\n"
        "\t.enable = a52_p318_clk_rcg2_enable,\n"
        "\t.is_enabled = clk_rcg2_is_enabled,\n"
    )
    return replace_once(text, old_ops, new_ops, "clk_rcg2_ops enable hook")


def patch_dispcc(text: str) -> str:
    if MARK in text:
        return text
    old = '''static struct clk_rcg2 disp_cc_mdss_esc0_clk_src = {
	.cmd_rcgr = 0x10e0,
	.mnd_width = 0,
	.hid_width = 5,
	.parent_map = disp_cc_parent_map_1,
	.freq_tbl = ftbl_disp_cc_mdss_dp_aux_clk_src,
'''
    new = old + '''	/* A52_PHASE318_ESC0_RCG_SAFE_RELOCK_AB_V1: Golden-only opt-in. */
	.enable_safe_config = true,
'''
    return replace_once(text, old, new, "Lagoon ESC0 opt-in")


def validate(header: str, rcg2: str, dispcc: str) -> None:
    if header.count(MARK) != 1:
        raise SystemExit("Phase318 header marker count mismatch")
    if rcg2.count(MARK) != 1:
        raise SystemExit("Phase318 clk-rcg2 marker count mismatch")
    if dispcc.count(MARK) != 1:
        raise SystemExit("Phase318 DISP_CC marker count mismatch")
    if dispcc.count(".enable_safe_config = true,") != 1:
        raise SystemExit("Phase318 requires exactly one safe-config opt-in")
    esc = dispcc[dispcc.index("static struct clk_rcg2 disp_cc_mdss_esc0_clk_src"):]
    esc = esc[:esc.index("};")]
    if ".enable_safe_config = true," not in esc:
        raise SystemExit("Phase318 safe-config opt-in is not ESC0")
    for name in ("disp_cc_mdss_byte0_clk_src", "disp_cc_mdss_pclk0_clk_src"):
        block = dispcc[dispcc.index("static struct clk_rcg2 " + name):]
        block = block[:block.index("};")]
        if ".enable_safe_config" in block:
            raise SystemExit(f"Phase318 must not opt in {name}")
    for token in (
        "a52_p318_clk_rcg2_enable",
        "a52_p318_rcg_set_force_enable",
        "CMD_ROOT_EN",
        "clk_rcg2_configure(rcg, f)",
        "A52P318 %s: relock inherited RCG rate=%lu",
        "A52P318 %s: relock complete rc=%d",
    ):
        if token not in rcg2:
            raise SystemExit("Phase318 missing clk-rcg2 token: " + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    qcom = args.root / "drivers/clk/qcom"
    header = qcom / "clk-rcg.h"
    rcg2 = qcom / "clk-rcg2.c"
    dispcc = qcom / "dispcc-lagoon.c"
    for path in (header, rcg2, dispcc):
        if not path.is_file():
            raise SystemExit("Phase318 missing source: " + str(path))

    if not args.check_only:
        header.write_text(patch_header(header.read_text()))
        rcg2.write_text(patch_rcg2(rcg2.read_text()))
        dispcc.write_text(patch_dispcc(dispcc.read_text()))

    validate(header.read_text(), rcg2.read_text(), dispcc.read_text())
    print("Phase318 ESC0-only RCG safe relock A/B: PASS")


if __name__ == "__main__":
    main()
