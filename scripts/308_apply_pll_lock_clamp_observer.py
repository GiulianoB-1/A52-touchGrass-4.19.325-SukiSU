#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY_REL = Path("drivers/a52_display/msm/dsi/dsi_phy.c")
MARK = "A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1"
P307 = "A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase308 {label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def find_pll_driver(root: Path) -> Path:
    preferred = root / "drivers/clk/qcom/mdss/mdss-pll.c"
    if preferred.is_file():
        return preferred
    matches = [p for p in root.rglob("mdss-pll.c") if "drivers" in p.parts]
    if len(matches) != 1:
        raise SystemExit(
            "Phase308 could not uniquely locate mdss-pll.c: "
            + ", ".join(str(p) for p in matches)
        )
    return matches[0]


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    if P307 not in text:
        raise SystemExit("Phase308 requires inherited Phase307 PHY observer")

    old = '''#define A52_P307_V3_LANE_STATUS1    0x0f8

void a52_p307_phy_snapshot(unsigned int index, unsigned int point)
'''
    new = '''#define A52_P307_V3_LANE_STATUS1    0x0f8

/* A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1
 * v3 lane register layout is source-gated in 308_ci_build.sh against both
 * reconstructed GKI and pinned touchGrass. Reads only, no timing insertion.
 */
#define A52_P308_V3_LNX_TX_DCTRL(n) (0x22c + (0x80 * (n)))
extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);

static void a52_p308_tx_dctrl_snapshot(struct msm_dsi_phy *phy,
		unsigned int enable, unsigned int point)
{
	void __iomem *base;

	if (!phy || !phy->ver_info || !phy->hw.base ||
	    phy->ver_info->version != DSI_PHY_VERSION_3_0)
		return;

	base = phy->hw.base;
	a52_ackfr_record("P276 308K i=%u e=%u q=%u %x %x %x %x %x",
		phy->index, enable, point,
		readl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(0)),
		readl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(1)),
		readl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(2)),
		readl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(3)),
		readl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(4)));
}

void a52_p307_phy_snapshot(unsigned int index, unsigned int point)
'''
    text = replace_once(text, old, new, "Phase307 helper extension")

    old_tail = '''\ta52_ackfr_record("P276 307P3 q=%u %x %x %x", point,
\t\treadl_relaxed(base + A52_P307_V3_LANE_CTRL2),
\t\treadl_relaxed(base + A52_P307_V3_LANE_CTRL3),
\t\treadl_relaxed(base + A52_P307_V3_LANE_CTRL4));
}
'''
    new_tail = '''\ta52_ackfr_record("P276 307P3 q=%u %x %x %x", point,
\t\treadl_relaxed(base + A52_P307_V3_LANE_CTRL2),
\t\treadl_relaxed(base + A52_P307_V3_LANE_CTRL3),
\t\treadl_relaxed(base + A52_P307_V3_LANE_CTRL4));
\ta52_ackfr_record("P276 308T q=%u %x %x %x %x %x", point,
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(0)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(1)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(2)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(3)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(4)));
\ta52_p308_pll_snapshot(index, point);
}
'''
    text = replace_once(text, old_tail, new_tail, "exact-F0 TX_DCTRL/PLL snapshot")

    old_clamp = '''int dsi_phy_set_clamp_state(struct msm_dsi_phy *phy, bool enable)
{
\tif (!phy)
\t\treturn -EINVAL;

\tDSI_PHY_DBG(phy, "enable=%d\\n", enable);

\tif (phy->hw.ops.clamp_ctrl)
\t\tphy->hw.ops.clamp_ctrl(&phy->hw, enable);

\treturn 0;
}
'''
    new_clamp = '''int dsi_phy_set_clamp_state(struct msm_dsi_phy *phy, bool enable)
{
\tif (!phy)
\t\treturn -EINVAL;

\tDSI_PHY_DBG(phy, "enable=%d\\n", enable);
\ta52_p308_tx_dctrl_snapshot(phy, enable, 0);

\tif (phy->hw.ops.clamp_ctrl)
\t\tphy->hw.ops.clamp_ctrl(&phy->hw, enable);

\ta52_p308_tx_dctrl_snapshot(phy, enable, 1);
\treturn 0;
}
'''
    return replace_once(text, old_clamp, new_clamp, "clamp pre/post observer")


def patch_pll(text: str) -> str:
    if MARK in text:
        return text
    if "struct mdss_pll_resources" not in text or "mdss_pll_probe" not in text:
        raise SystemExit("Phase308 PLL driver shape not recognized")

    inc = '#include "mdss-pll.h"\n'
    if inc not in text:
        raise SystemExit("Phase308 mdss-pll.h include anchor missing")
    text = text.replace(
        inc,
        inc + '#include <linux/a52_ack_secure_flight_recorder.h>\n',
        1,
    )

    anchor = "static int mdss_pll_probe(struct platform_device *pdev)\n"
    helper = '''/* A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1
 * Recorder-only view of the already-mapped MDSS DSI 10-nm PLL provider.
 * PLL_COMMON_STATUS_ONE bit0 is the provider's own lock criterion.
 */
#define A52_P308_PLL_COMMON_STATUS_ONE 0x1a0
#define A52_P308_PLL_SYSTEM_MUXES      0x024
#define A52_P308_PLL_OUTDIV_RATE       0x140
#define A52_P308_PHY_CMN_CLK_CFG0      0x010
#define A52_P308_PHY_CMN_CLK_CFG1      0x014
#define A52_P308_PHY_CMN_RBUF_CTRL     0x01c
#define A52_P308_PLL_MAX               2

static struct mdss_pll_resources *a52_p308_pll_res[A52_P308_PLL_MAX];

void a52_p308_pll_snapshot(unsigned int index, unsigned int point)
{
\tstruct mdss_pll_resources *pll;
\tu32 lock, mux, outdiv, cfg0, cfg1, rbuf;

\tif (index >= A52_P308_PLL_MAX) {
\t\ta52_ackfr_record("P276 308LX q=%u i=%u x=idx", point, index);
\t\treturn;
\t}

\tpll = READ_ONCE(a52_p308_pll_res[index]);
\tif (!pll || !pll->pll_base || !pll->phy_base) {
\t\ta52_ackfr_record("P276 308LX q=%u i=%u x=map", point, index);
\t\treturn;
\t}

\tlock = readl_relaxed(pll->pll_base + A52_P308_PLL_COMMON_STATUS_ONE);
\tmux = readl_relaxed(pll->pll_base + A52_P308_PLL_SYSTEM_MUXES);
\toutdiv = readl_relaxed(pll->pll_base + A52_P308_PLL_OUTDIV_RATE);
\tcfg0 = readl_relaxed(pll->phy_base + A52_P308_PHY_CMN_CLK_CFG0);
\tcfg1 = readl_relaxed(pll->phy_base + A52_P308_PHY_CMN_CLK_CFG1);
\trbuf = readl_relaxed(pll->phy_base + A52_P308_PHY_CMN_RBUF_CTRL);

\ta52_ackfr_record("P276 308L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x",
\t\tpoint, index, pll->pll_on, pll->handoff_resources,
\t\tpll->resource_enable, pll->resource_ref_cnt, lock);
\ta52_ackfr_record("P276 308V q=%u vc=%lld ca=%lu c0=%x c1=%x od=%x",
\t\tpoint, pll->vco_current_rate, pll->vco_cached_rate,
\t\tpll->cached_cfg0, pll->cached_cfg1, pll->cached_outdiv);
\ta52_ackfr_record("P276 308M q=%u m=%x o=%x c0=%x c1=%x rb=%x",
\t\tpoint, mux, outdiv, cfg0, cfg1, rbuf);
}

'''
    text = replace_once(text, anchor, helper + anchor, "PLL snapshot helper")

    call = "rc = mdss_pll_clock_register(pdev, pll_res);"
    pos = text.find(call)
    if pos < 0:
        raise SystemExit("Phase308 mdss_pll_clock_register call missing")
    ret = text.find("\n\treturn rc;", pos)
    if ret < 0:
        raise SystemExit("Phase308 PLL probe success return missing")
    registration = '''
\tif (pll_res->pll_interface_type == MDSS_DSI_PLL_10NM &&
\t    pll_res->index < A52_P308_PLL_MAX) {
\t\tWRITE_ONCE(a52_p308_pll_res[pll_res->index], pll_res);
\t\ta52_ackfr_record("P276 308R i=%u p=1", pll_res->index);
\t}
'''
    text = text[:ret] + registration + text[ret:]
    return text


def validate(phy: str, pll: str) -> None:
    combined = phy + pll
    required = [
        MARK,
        "A52_P308_V3_LNX_TX_DCTRL(n) (0x22c + (0x80 * (n)))",
        "P276 308K i=%u e=%u q=%u %x %x %x %x %x",
        "P276 308T q=%u %x %x %x %x %x",
        "a52_p308_pll_snapshot(index, point);",
        "A52_P308_PLL_COMMON_STATUS_ONE 0x1a0",
        "P276 308L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x",
        "P276 308V q=%u vc=%lld ca=%lu c0=%x c1=%x od=%x",
        "P276 308M q=%u m=%x o=%x c0=%x c1=%x rb=%x",
        "P276 308R i=%u p=1",
        "pll_res->pll_interface_type == MDSS_DSI_PLL_10NM",
        "P276 307P0 q=%u v=%u p=%u s=%u %x %x %x %x",
    ]
    for token in required:
        if token not in combined:
            raise SystemExit("Phase308 required token missing: " + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    phy_path = args.root / PHY_REL
    if not phy_path.is_file():
        raise SystemExit("Phase308 PHY source missing: " + str(phy_path))
    pll_path = find_pll_driver(args.root)

    if not args.check_only:
        phy_path.write_text(patch_phy(phy_path.read_text()))
        pll_path.write_text(patch_pll(pll_path.read_text()))

    validate(phy_path.read_text(), pll_path.read_text())
    print("Phase308 PLL lock/handoff + TX_DCTRL/clamp observer: PASS")
    print("Phase308 PLL driver: " + str(pll_path))


if __name__ == "__main__":
    main()
