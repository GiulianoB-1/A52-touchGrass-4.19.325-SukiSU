#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY = Path('techpack/display/msm/dsi/dsi_phy.c')
PLL = Path('techpack/display/pll/pll_drv.c')
MARK = 'A52_PHASE308G_GOLDEN_PLL_LOCK_CLAMP_REFERENCE_V1'
P307 = 'A52_PHASE307_GOLDEN_V3_PHY_CLOCKLANE_REFERENCE_V1'


def repl(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase308G {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    if P307 not in text:
        raise SystemExit('Phase308G requires Phase307G observer first')

    old = '''#define A52_G307_V3_LANE_STATUS1    0x0f8\n\nvoid a52_g307_phy_snapshot(unsigned int index, unsigned int point)\n'''
    new = '''#define A52_G307_V3_LANE_STATUS1    0x0f8\n\n/* A52_PHASE308G_GOLDEN_PLL_LOCK_CLAMP_REFERENCE_V1\n * Matched read-only runtime control for the Phase308 GKI observer.\n * Clamp counters are software-only latches so an early FreezeIO release is\n * still visible at the exact F0 q0/q1/q2 points.\n */\n#define A52_G308_V3_LNX_TX_DCTRL(n) (0x22c + (0x80 * (n)))\n#define A52_G308_MAX_PHY             2\nextern void a52_g308_pll_snapshot(unsigned int index, unsigned int point);\nstatic atomic_t a52_g308_clamp_enable[A52_G308_MAX_PHY] = {\n\tATOMIC_INIT(0), ATOMIC_INIT(0)\n};\nstatic atomic_t a52_g308_clamp_release[A52_G308_MAX_PHY] = {\n\tATOMIC_INIT(0), ATOMIC_INIT(0)\n};\n\nvoid a52_g307_phy_snapshot(unsigned int index, unsigned int point)\n'''
    text = repl(text, old, new, 'PHY definitions')

    old_tail = '''\tpr_info("TG307 P3 q=%u %x %x %x\\n", point,\n\t\treadl_relaxed(base + A52_G307_V3_LANE_CTRL2),\n\t\treadl_relaxed(base + A52_G307_V3_LANE_CTRL3),\n\t\treadl_relaxed(base + A52_G307_V3_LANE_CTRL4));\n}\n'''
    new_tail = '''\tpr_info("TG307 P3 q=%u %x %x %x\\n", point,\n\t\treadl_relaxed(base + A52_G307_V3_LANE_CTRL2),\n\t\treadl_relaxed(base + A52_G307_V3_LANE_CTRL3),\n\t\treadl_relaxed(base + A52_G307_V3_LANE_CTRL4));\n\tpr_info("TG308 T q=%u %x %x %x %x %x ce=%d cr=%d\\n", point,\n\t\treadl_relaxed(base + A52_G308_V3_LNX_TX_DCTRL(0)),\n\t\treadl_relaxed(base + A52_G308_V3_LNX_TX_DCTRL(1)),\n\t\treadl_relaxed(base + A52_G308_V3_LNX_TX_DCTRL(2)),\n\t\treadl_relaxed(base + A52_G308_V3_LNX_TX_DCTRL(3)),\n\t\treadl_relaxed(base + A52_G308_V3_LNX_TX_DCTRL(4)),\n\t\tindex < A52_G308_MAX_PHY ? atomic_read(&a52_g308_clamp_enable[index]) : -1,\n\t\tindex < A52_G308_MAX_PHY ? atomic_read(&a52_g308_clamp_release[index]) : -1);\n\ta52_g308_pll_snapshot(index, point);\n}\n'''
    text = repl(text, old_tail, new_tail, 'q0/q1/q2 TX_DCTRL and PLL snapshot')

    old_clamp = '''int dsi_phy_set_clamp_state(struct msm_dsi_phy *phy, bool enable)\n{\n\tif (!phy)\n\t\treturn -EINVAL;\n\n\tDSI_PHY_DBG(phy, "enable=%d\\n", enable);\n\n\tif (phy->hw.ops.clamp_ctrl)\n\t\tphy->hw.ops.clamp_ctrl(&phy->hw, enable);\n\n\treturn 0;\n}\n'''
    new_clamp = '''int dsi_phy_set_clamp_state(struct msm_dsi_phy *phy, bool enable)\n{\n\tif (!phy)\n\t\treturn -EINVAL;\n\n\tDSI_PHY_DBG(phy, "enable=%d\\n", enable);\n\n\tif (phy->hw.ops.clamp_ctrl) {\n\t\tphy->hw.ops.clamp_ctrl(&phy->hw, enable);\n\t\tif (phy->index < A52_G308_MAX_PHY) {\n\t\t\tif (enable)\n\t\t\t\tatomic_inc(&a52_g308_clamp_enable[phy->index]);\n\t\t\telse\n\t\t\t\tatomic_inc(&a52_g308_clamp_release[phy->index]);\n\t\t}\n\t\tpr_info("TG308 K i=%u e=%u ce=%d cr=%d\\n", phy->index, enable,\n\t\t\tphy->index < A52_G308_MAX_PHY ? atomic_read(&a52_g308_clamp_enable[phy->index]) : -1,\n\t\t\tphy->index < A52_G308_MAX_PHY ? atomic_read(&a52_g308_clamp_release[phy->index]) : -1);\n\t}\n\n\treturn 0;\n}\n'''
    return repl(text, old_clamp, new_clamp, 'persistent clamp latch')


def patch_pll(text: str) -> str:
    if MARK in text:
        return text
    if 'struct mdss_pll_resources' not in text or 'mdss_pll_probe' not in text:
        raise SystemExit('Phase308G PLL driver shape not recognized')

    anchor = 'static int mdss_pll_probe(struct platform_device *pdev)\n'
    helper = r'''/* A52_PHASE308G_GOLDEN_PLL_LOCK_CLAMP_REFERENCE_V1
 * Read-only view of the known-good TouchGrass 10-nm DSI PLL provider.
 * PLL_COMMON_STATUS_ONE bit0 is the provider's actual lock criterion.
 */
#define A52_G308_PLL_COMMON_STATUS_ONE 0x1a0
#define A52_G308_PLL_SYSTEM_MUXES      0x024
#define A52_G308_PLL_OUTDIV_RATE       0x140
#define A52_G308_PHY_CMN_CLK_CFG0      0x010
#define A52_G308_PHY_CMN_CLK_CFG1      0x014
#define A52_G308_PHY_CMN_RBUF_CTRL     0x01c
#define A52_G308_PLL_MAX               2

static struct mdss_pll_resources *a52_g308_pll_res[A52_G308_PLL_MAX];

void a52_g308_pll_snapshot(unsigned int index, unsigned int point)
{
	struct mdss_pll_resources *pll;
	u32 lock, mux, outdiv, cfg0, cfg1, rbuf;

	if (index >= A52_G308_PLL_MAX) {
		pr_info("TG308 LX q=%u i=%u x=idx\n", point, index);
		return;
	}
	pll = READ_ONCE(a52_g308_pll_res[index]);
	if (!pll || !pll->pll_base || !pll->phy_base) {
		pr_info("TG308 LX q=%u i=%u x=map\n", point, index);
		return;
	}

	lock = readl_relaxed(pll->pll_base + A52_G308_PLL_COMMON_STATUS_ONE);
	mux = readl_relaxed(pll->pll_base + A52_G308_PLL_SYSTEM_MUXES);
	outdiv = readl_relaxed(pll->pll_base + A52_G308_PLL_OUTDIV_RATE);
	cfg0 = readl_relaxed(pll->phy_base + A52_G308_PHY_CMN_CLK_CFG0);
	cfg1 = readl_relaxed(pll->phy_base + A52_G308_PHY_CMN_CLK_CFG1);
	rbuf = readl_relaxed(pll->phy_base + A52_G308_PHY_CMN_RBUF_CTRL);

	pr_info("TG308 L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x\n",
		point, index, pll->pll_on, pll->handoff_resources,
		pll->resource_enable, pll->resource_ref_cnt, lock);
	pr_info("TG308 V q=%u vc=%lld ca=%lu c0=%x c1=%x od=%x\n",
		point, pll->vco_current_rate, pll->vco_cached_rate,
		pll->cached_cfg0, pll->cached_cfg1, pll->cached_outdiv);
	pr_info("TG308 M q=%u m=%x o=%x c0=%x c1=%x rb=%x\n",
		point, mux, outdiv, cfg0, cfg1, rbuf);
}

'''
    text = repl(text, anchor, helper + anchor, 'PLL snapshot helper')

    call = 'rc = mdss_pll_clock_register(pdev, pll_res);'
    pos = text.find(call)
    if pos < 0:
        raise SystemExit('Phase308G mdss_pll_clock_register call missing')
    ret = text.find('\n\treturn rc;', pos)
    if ret < 0:
        raise SystemExit('Phase308G PLL probe success return missing')
    registration = r'''
	if (!rc && pll_res->pll_interface_type == MDSS_DSI_PLL_10NM &&
	    pll_res->index < A52_G308_PLL_MAX) {
		WRITE_ONCE(a52_g308_pll_res[pll_res->index], pll_res);
		pr_info("TG308 R i=%u p=1\n", pll_res->index);
	}
'''
    return text[:ret] + registration + text[ret:]


def validate(phy: str, pll: str) -> None:
    alltxt = phy + pll
    required = [
        MARK,
        'A52_G308_V3_LNX_TX_DCTRL(n) (0x22c + (0x80 * (n)))',
        'TG308 T q=%u %x %x %x %x %x ce=%d cr=%d',
        'TG308 K i=%u e=%u ce=%d cr=%d',
        'a52_g308_pll_snapshot(index, point);',
        'A52_G308_PLL_COMMON_STATUS_ONE 0x1a0',
        'TG308 L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x',
        'TG308 V q=%u vc=%lld ca=%lu c0=%x c1=%x od=%x',
        'TG308 M q=%u m=%x o=%x c0=%x c1=%x rb=%x',
        'TG308 R i=%u p=1',
        P307,
    ]
    for token in required:
        if token not in alltxt:
            raise SystemExit('Phase308G missing token: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    phy = ns.root / PHY
    pll = ns.root / PLL
    for p in (phy, pll):
        if not p.is_file():
            raise SystemExit('Phase308G missing source: ' + str(p))
    if not ns.check_only:
        phy.write_text(patch_phy(phy.read_text()))
        pll.write_text(patch_pll(pll.read_text()))
    validate(phy.read_text(), pll.read_text())
    print('Phase308G Golden PLL lock/handoff + persistent clamp reference: PASS')


if __name__ == '__main__':
    main()
