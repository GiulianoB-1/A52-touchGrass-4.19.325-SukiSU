#!/usr/bin/env python3
from pathlib import Path
import argparse

MARK = 'A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1'
CTRL_REL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy.c')


def replace_one(text, old, new, why):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase284: expected exactly one {why} anchor, found {n}')
    return text.replace(old, new, 1)


def apply_ctrl(path: Path):
    text = path.read_text()
    if MARK in text:
        return

    anchor = '''static unsigned long a52_p283_clk_rate(struct clk *clk)\n{\n\treturn clk ? clk_get_rate(clk) : 0;\n}\n\n'''
    inject = anchor + '''/* A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1\n * Read-only continuation of Phase283. Capture the configured link rates and\n * the actual RCG/selected-source/parent clock chain. No clock state is changed.\n */\nstatic u32 a52_p284_rate32(unsigned long rate)\n{\n\treturn rate > 0xffffffffUL ? 0xffffffffU : (u32)rate;\n}\n\nstatic u32 a52_p284_u64_rate32(u64 rate)\n{\n\treturn rate > 0xffffffffULL ? 0xffffffffU : (u32)rate;\n}\n\nstatic u32 a52_p284_clk_rate32(struct clk *clk)\n{\n\treturn a52_p284_rate32(a52_p283_clk_rate(clk));\n}\n\n'''
    text = replace_one(text, anchor, inject, 'clock helper')

    anchor2 = '''\ta52_ackfr_record("P276 283C2 q=%u i=%lx e=%lx", point,\n\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk),\n\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.lp_link_clks.esc_clk));\n\ta52_p283_phy_snapshot(dsi_ctrl->cell_index, point);\n'''
    inject2 = '''\ta52_ackfr_record("P276 283C2 q=%u i=%lx e=%lx", point,\n\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk),\n\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.lp_link_clks.esc_clk));\n\t{\n\t\tstruct clk *bc = dsi_ctrl->clk_info.hs_link_clks.byte_clk;\n\t\tstruct clk *pc = dsi_ctrl->clk_info.hs_link_clks.pixel_clk;\n\t\tstruct clk *bp = bc ? clk_get_parent(bc) : NULL;\n\t\tstruct clk *pp = pc ? clk_get_parent(pc) : NULL;\n\t\tstruct clk *bg = bp ? clk_get_parent(bp) : NULL;\n\t\tstruct clk *pg = pp ? clk_get_parent(pp) : NULL;\n\t\tu32 en = 0;\n\n\t\ta52_ackfr_record("P276 284C0 q=%u %x %x %x %x", point,\n\t\t\ta52_p284_u64_rate32(dsi_ctrl->clk_freq.byte_clk_rate),\n\t\t\ta52_p284_u64_rate32(dsi_ctrl->clk_freq.pix_clk_rate),\n\t\t\ta52_p284_u64_rate32(dsi_ctrl->clk_freq.byte_intf_clk_rate),\n\t\t\ta52_p284_u64_rate32(dsi_ctrl->clk_freq.esc_clk_rate));\n\t\ta52_ackfr_record("P276 284C1 q=%u %x %x %x %x", point,\n\t\t\ta52_p284_clk_rate32(dsi_ctrl->clk_info.rcg_clks.byte_clk),\n\t\t\ta52_p284_clk_rate32(dsi_ctrl->clk_info.rcg_clks.pixel_clk),\n\t\t\ta52_p284_clk_rate32(dsi_ctrl->clk_info.pll_op_clks.byte_clk),\n\t\t\ta52_p284_clk_rate32(dsi_ctrl->clk_info.pll_op_clks.pixel_clk));\n\t\ta52_ackfr_record("P276 284C2 q=%u %x %x %x %x %x %x", point,\n\t\t\ta52_p284_clk_rate32(bc), a52_p284_clk_rate32(bp),\n\t\t\ta52_p284_clk_rate32(bg), a52_p284_clk_rate32(pc),\n\t\t\ta52_p284_clk_rate32(pp), a52_p284_clk_rate32(pg));\n\t\ten |= a52_p283_clk_enabled(dsi_ctrl->clk_info.rcg_clks.byte_clk) << 0;\n\t\ten |= a52_p283_clk_enabled(dsi_ctrl->clk_info.rcg_clks.pixel_clk) << 2;\n\t\ten |= a52_p283_clk_enabled(dsi_ctrl->clk_info.pll_op_clks.byte_clk) << 4;\n\t\ten |= a52_p283_clk_enabled(dsi_ctrl->clk_info.pll_op_clks.pixel_clk) << 6;\n\t\ten |= a52_p283_clk_enabled(bp) << 8;\n\t\ten |= a52_p283_clk_enabled(bg) << 10;\n\t\ten |= a52_p283_clk_enabled(pp) << 12;\n\t\ten |= a52_p283_clk_enabled(pg) << 14;\n\t\ta52_ackfr_record("P276 284C3 q=%u e=%x", point, en);\n\t}\n\ta52_p283_phy_snapshot(dsi_ctrl->cell_index, point);\n'''
    text = replace_one(text, anchor2, inject2, 'Phase283 C2 tail')
    path.write_text(text)


def apply_phy(path: Path):
    text = path.read_text()
    if MARK in text:
        return

    anchor = '''#define A52_P283_PHY_LANE_STATUS1   0x14c\n\n'''
    inject = anchor + '''/* A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1\n * Snapdragon 720G/Lagoon reports DSI_PHY_VERSION_3_0. These offsets are the\n * exact 10-nm v3.0 common/lane/status offsets used by TouchGrass. Read-only.\n */\n#define A52_P284_V3_CLK_CFG0        0x010\n#define A52_P284_V3_CLK_CFG1        0x014\n#define A52_P284_V3_GLBL_CTRL       0x018\n#define A52_P284_V3_RBUF_CTRL       0x01c\n#define A52_P284_V3_VREG_CTRL       0x020\n#define A52_P284_V3_CTRL0           0x024\n#define A52_P284_V3_CTRL1           0x028\n#define A52_P284_V3_CTRL2           0x02c\n#define A52_P284_V3_LANE_CFG0       0x030\n#define A52_P284_V3_LANE_CFG1       0x034\n#define A52_P284_V3_PLL_CTRL        0x038\n#define A52_P284_V3_LANE_CTRL0      0x098\n#define A52_P284_V3_LANE_CTRL1      0x09c\n#define A52_P284_V3_LANE_CTRL2      0x0a0\n#define A52_P284_V3_LANE_CTRL3      0x0a4\n#define A52_P284_V3_LANE_CTRL4      0x0a8\n#define A52_P284_V3_STATUS          0x0ec\n#define A52_P284_V3_LANE_STATUS0    0x0f4\n#define A52_P284_V3_LANE_STATUS1    0x0f8\n\n'''
    text = replace_one(text, anchor, inject, 'PHY define tail')

    anchor2 = '''\tver = phy->ver_info->version;\n\tif (ver != DSI_PHY_VERSION_4_0 && ver != DSI_PHY_VERSION_4_1) {\n'''
    inject2 = '''\tver = phy->ver_info->version;\n\tif (ver == DSI_PHY_VERSION_3_0) {\n\t\tbase = phy->hw.base;\n\t\ta52_ackfr_record("P276 284P0 q=%u %u %x %x %x %x", point, ver,\n\t\t\treadl_relaxed(base + A52_P284_V3_PLL_CTRL),\n\t\t\treadl_relaxed(base + A52_P284_V3_STATUS),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_STATUS0),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_STATUS1));\n\t\ta52_ackfr_record("P276 284P1 q=%u %x %x %x %x %x %x", point,\n\t\t\treadl_relaxed(base + A52_P284_V3_CLK_CFG0),\n\t\t\treadl_relaxed(base + A52_P284_V3_CLK_CFG1),\n\t\t\treadl_relaxed(base + A52_P284_V3_GLBL_CTRL),\n\t\t\treadl_relaxed(base + A52_P284_V3_RBUF_CTRL),\n\t\t\treadl_relaxed(base + A52_P284_V3_VREG_CTRL),\n\t\t\treadl_relaxed(base + A52_P284_V3_CTRL0));\n\t\ta52_ackfr_record("P276 284P2 q=%u %x %x %x %x %x %x", point,\n\t\t\treadl_relaxed(base + A52_P284_V3_CTRL1),\n\t\t\treadl_relaxed(base + A52_P284_V3_CTRL2),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CFG0),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CFG1),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CTRL0),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CTRL1));\n\t\ta52_ackfr_record("P276 284P3 q=%u %x %x %x", point,\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CTRL2),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CTRL3),\n\t\t\treadl_relaxed(base + A52_P284_V3_LANE_CTRL4));\n\t\treturn;\n\t}\n\tif (ver != DSI_PHY_VERSION_4_0 && ver != DSI_PHY_VERSION_4_1) {\n'''
    text = replace_one(text, anchor2, inject2, 'PHY version dispatch')
    path.write_text(text)


def check(root: Path):
    ctrl = (root / CTRL_REL).read_text()
    phy = (root / PHY_REL).read_text()
    need_ctrl = [MARK, 'P276 284C0 q=%u %x %x %x %x', 'P276 284C1 q=%u %x %x %x %x',
                 'P276 284C2 q=%u %x %x %x %x %x %x', 'P276 284C3 q=%u e=%x',
                 'clk_freq.byte_intf_clk_rate', 'clk_info.pll_op_clks.byte_clk']
    need_phy = [MARK, 'DSI_PHY_VERSION_3_0', 'A52_P284_V3_PLL_CTRL        0x038',
                'A52_P284_V3_STATUS          0x0ec', 'P276 284P0 q=%u %u %x %x %x %x',
                'P276 284P1 q=%u %x %x %x %x %x %x', 'P276 284P2 q=%u %x %x %x %x %x %x',
                'P276 284P3 q=%u %x %x %x']
    for x in need_ctrl:
        if x not in ctrl:
            raise SystemExit('Phase284 controller check missing: ' + x)
    for x in need_phy:
        if x not in phy:
            raise SystemExit('Phase284 PHY check missing: ' + x)
    print('Phase284 v3 PHY + link clock chain trace: PASS')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    root = Path(ns.root)
    if not ns.check_only:
        apply_ctrl(root / CTRL_REL)
        apply_phy(root / PHY_REL)
    check(root)

if __name__ == '__main__':
    main()
