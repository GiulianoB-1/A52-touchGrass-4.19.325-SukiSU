#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
PHY = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
MARK = 'A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text

    text = replace_one(
        text,
        '#include "dsi_phy.h"\n',
        '#include "dsi_phy.h"\n'
        '#include <linux/a52_ack_secure_flight_recorder.h>\n'
        'extern bool a52_p276r_deep_active(void);\n',
        'Phase283 PHY recorder include')

    anchor = 'static DEFINE_MUTEX(dsi_phy_list_lock);\n'
    helper = r'''static DEFINE_MUTEX(dsi_phy_list_lock);

/* A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1
 * Read-only shared-path snapshot after Phase282 proved that both system-memory
 * command fetch and the Golden FIFO/TPG transport time out. The v4.x register
 * offsets below are taken directly from TouchGrass dsi_phy_hw_v4_0.c. No PHY
 * or DSI register is written.
 */
#define A52_P283_PHY_CLK_CFG0       0x010
#define A52_P283_PHY_CLK_CFG1       0x014
#define A52_P283_PHY_GLBL_CTRL      0x018
#define A52_P283_PHY_VREG_CTRL0     0x020
#define A52_P283_PHY_CTRL0          0x024
#define A52_P283_PHY_CTRL1          0x028
#define A52_P283_PHY_CTRL2          0x02c
#define A52_P283_PHY_CTRL3          0x030
#define A52_P283_PHY_PLL_CTRL       0x03c
#define A52_P283_PHY_LANE_CTRL0     0x0a0
#define A52_P283_PHY_LANE_CTRL1     0x0a4
#define A52_P283_PHY_LANE_CTRL2     0x0a8
#define A52_P283_PHY_LANE_CTRL3     0x0ac
#define A52_P283_PHY_LANE_CTRL4     0x0b0
#define A52_P283_PHY_VREG_CTRL1     0x110
#define A52_P283_PHY_CTRL4          0x114
#define A52_P283_PHY_STATUS         0x140
#define A52_P283_PHY_LANE_STATUS0   0x148
#define A52_P283_PHY_LANE_STATUS1   0x14c

void a52_p283_phy_snapshot(unsigned int index, unsigned int point)
{
	struct dsi_phy_list_item *item;
	struct msm_dsi_phy *phy = NULL;
	void __iomem *base;
	u32 ver;

	if (!a52_p276r_deep_active())
		return;

	mutex_lock(&dsi_phy_list_lock);
	list_for_each_entry(item, &dsi_phy_list, list) {
		if (item->phy && item->phy->index == index) {
			phy = item->phy;
			break;
		}
	}
	mutex_unlock(&dsi_phy_list_lock);

	if (!phy || !phy->ver_info || !phy->hw.base) {
		a52_ackfr_record("P276 283PX q=%u i=%u x=0", point, index);
		return;
	}

	ver = phy->ver_info->version;
	if (ver != DSI_PHY_VERSION_4_0 && ver != DSI_PHY_VERSION_4_1) {
		a52_ackfr_record("P276 283PX q=%u i=%u v=%u", point, index, ver);
		return;
	}

	base = phy->hw.base;
	a52_ackfr_record("P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x", point,
		ver, phy->power_state, phy->dsi_phy_state,
		readl_relaxed(base + A52_P283_PHY_PLL_CTRL),
		readl_relaxed(base + A52_P283_PHY_STATUS),
		readl_relaxed(base + A52_P283_PHY_LANE_STATUS0),
		readl_relaxed(base + A52_P283_PHY_LANE_STATUS1));
	a52_ackfr_record("P276 283P1 q=%u %x %x %x %x %x %x", point,
		readl_relaxed(base + A52_P283_PHY_CLK_CFG0),
		readl_relaxed(base + A52_P283_PHY_CLK_CFG1),
		readl_relaxed(base + A52_P283_PHY_GLBL_CTRL),
		readl_relaxed(base + A52_P283_PHY_VREG_CTRL0),
		readl_relaxed(base + A52_P283_PHY_VREG_CTRL1),
		readl_relaxed(base + A52_P283_PHY_CTRL0));
	a52_ackfr_record("P276 283P2 q=%u %x %x %x %x %x", point,
		readl_relaxed(base + A52_P283_PHY_CTRL1),
		readl_relaxed(base + A52_P283_PHY_CTRL2),
		readl_relaxed(base + A52_P283_PHY_CTRL3),
		readl_relaxed(base + A52_P283_PHY_CTRL4),
		readl_relaxed(base + A52_P283_PHY_LANE_CTRL0));
	a52_ackfr_record("P276 283P3 q=%u %x %x %x %x", point,
		readl_relaxed(base + A52_P283_PHY_LANE_CTRL1),
		readl_relaxed(base + A52_P283_PHY_LANE_CTRL2),
		readl_relaxed(base + A52_P283_PHY_LANE_CTRL3),
		readl_relaxed(base + A52_P283_PHY_LANE_CTRL4));
}

'''
    return replace_one(text, anchor, helper, 'Phase283 PHY helper insertion')


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    for required in [
        'A52_PHASE282_GOLDEN_FIFO_AB_V1',
        'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1',
        'P276 282A m=fifo f=%x',
        'P276 D K s=6 p=0',
        'P276 D K s=6 p=1',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);',
    ]:
        if required not in text:
            raise SystemExit('Phase283 DSI prerequisite missing: ' + required)

    text = replace_one(
        text,
        '#include <linux/clk.h>\n',
        '#include <linux/clk.h>\n#include <linux/clk-provider.h>\n',
        'Phase283 clock framework introspection include')

    text = replace_one(
        text,
        'static atomic_t a52_p282_fifo_inflight = ATOMIC_INIT(0);\n',
        'static atomic_t a52_p282_fifo_inflight = ATOMIC_INIT(0);\n'
        '\n/* ' + MARK + '\n'
        ' * Phase282 proved the Golden FIFO transport times out too. Phase283\n'
        ' * therefore traces only the shared controller/clock/PHY path.\n'
        ' */\n'
        'extern void a52_p283_phy_snapshot(unsigned int index, unsigned int point);\n'
        '\nstatic unsigned int a52_p283_clk_enabled(struct clk *clk)\n'
        '{\n'
        '\treturn clk ? (__clk_is_enabled(clk) ? 1 : 0) : 2;\n'
        '}\n'
        '\nstatic unsigned long a52_p283_clk_rate(struct clk *clk)\n'
        '{\n'
        '\treturn clk ? clk_get_rate(clk) : 0;\n'
        '}\n'
        '\nstatic void a52_p283_shared_snapshot(struct dsi_ctrl *dsi_ctrl,\n'
        '\t\tunsigned int point)\n'
        '{\n'
        '\tvoid __iomem *base;\n'
        '\tstruct dsi_ctrl_state_info *s;\n'
        '\tu32 ce = 0;\n'
        '\n\tif (!a52_p276r_deep_active() || !dsi_ctrl || !dsi_ctrl->hw.base)\n'
        '\t\treturn;\n'
        '\n\tbase = dsi_ctrl->hw.base;\n'
        '\ts = &dsi_ctrl->current_state;\n'
        '\ta52_ackfr_record("P276 283S q=%u v=%u p=%u c=%u m=%u h=%u t=%u", point,\n'
        '\t\tdsi_ctrl->version, s->power_state, s->controller_state,\n'
        '\t\ts->cmd_engine_state, s->host_initialized, s->tpg_enabled);\n'
        '\ta52_ackfr_record("P276 283R0 q=%u %x %x %x %x %x %x", point,\n'
        '\t\treadl_relaxed(base + DSI_CTRL),\n'
        '\t\treadl_relaxed(base + DSI_TRIG_CTRL),\n'
        '\t\treadl_relaxed(base + DSI_TEST_PATTERN_GEN_CTRL),\n'
        '\t\treadl_relaxed(base + DSI_TPG_DMA_FIFO_STATUS),\n'
        '\t\treadl_relaxed(base + DSI_DMA_CMD_LENGTH),\n'
        '\t\treadl_relaxed(base + DSI_INT_CTRL));\n'
        '\ta52_ackfr_record("P276 283R1 q=%u %x %x %x %x %x %x", point,\n'
        '\t\treadl_relaxed(base + DSI_STATUS),\n'
        '\t\treadl_relaxed(base + DSI_FIFO_STATUS),\n'
        '\t\treadl_relaxed(base + DSI_LANE_STATUS),\n'
        '\t\treadl_relaxed(base + DSI_CLK_CTRL),\n'
        '\t\treadl_relaxed(base + DSI_CLK_STATUS),\n'
        '\t\treadl_relaxed(base + DSI_PHY_SW_RESET));\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.core_clks.mdp_core_clk) << 0;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.core_clks.iface_clk) << 2;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.core_clks.core_mmss_clk) << 4;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.core_clks.bus_clk) << 6;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.core_clks.mnoc_clk) << 8;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.hs_link_clks.byte_clk) << 10;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.hs_link_clks.pixel_clk) << 12;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk) << 14;\n'
        '\tce |= a52_p283_clk_enabled(dsi_ctrl->clk_info.lp_link_clks.esc_clk) << 16;\n'
        '\ta52_ackfr_record("P276 283C0 q=%u e=%x", point, ce);\n'
        '\ta52_ackfr_record("P276 283C1 q=%u b=%lx p=%lx", point,\n'
        '\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.hs_link_clks.byte_clk),\n'
        '\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.hs_link_clks.pixel_clk));\n'
        '\ta52_ackfr_record("P276 283C2 q=%u i=%lx e=%lx", point,\n'
        '\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk),\n'
        '\t\ta52_p283_clk_rate(dsi_ctrl->clk_info.lp_link_clks.esc_clk));\n'
        '\ta52_p283_phy_snapshot(dsi_ctrl->cell_index, point);\n'
        '}\n',
        'Phase283 DSI helper insertion')

    text = replace_one(
        text,
        'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=6 p=0");',
        'a52_p283_shared_snapshot(dsi_ctrl, 0);\n\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=6 p=0");',
        'Phase283 FIFO q0 shared snapshot')

    text = replace_one(
        text,
        'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=6 p=1");',
        'if (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=6 p=1");\n\t\t\ta52_p283_shared_snapshot(dsi_ctrl, 1);',
        'Phase283 FIFO q1 shared snapshot')

    text = replace_one(
        text,
        '\t\ta52_p281_dsi_dma_snapshot(dsi_ctrl, 2);\n',
        '\t\ta52_p283_shared_snapshot(dsi_ctrl, 2);\n'
        '\t\ta52_p281_dsi_dma_snapshot(dsi_ctrl, 2);\n',
        'Phase283 q2 shared snapshot')
    return text


def validate(dsi: str, phy: str) -> None:
    required_dsi = [
        MARK,
        '#include <linux/clk-provider.h>',
        'extern void a52_p283_phy_snapshot',
        'P276 283S q=%u v=%u p=%u c=%u m=%u h=%u t=%u',
        'P276 283R0 q=%u %x %x %x %x %x %x',
        'P276 283R1 q=%u %x %x %x %x %x %x',
        'P276 283C0 q=%u e=%x',
        'P276 283C1 q=%u b=%lx p=%lx',
        'P276 283C2 q=%u i=%lx e=%lx',
        'a52_p283_shared_snapshot(dsi_ctrl, 0);',
        'a52_p283_shared_snapshot(dsi_ctrl, 1);',
        'a52_p283_shared_snapshot(dsi_ctrl, 2);',
        'P276 282F a=0 t=1',
        'P276 280Z q=2',
    ]
    required_phy = [
        MARK,
        'void a52_p283_phy_snapshot',
        'P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x',
        'P276 283P1 q=%u %x %x %x %x %x %x',
        'P276 283P2 q=%u %x %x %x %x %x',
        'P276 283P3 q=%u %x %x %x %x',
        'DSI_PHY_VERSION_4_0',
        'DSI_PHY_VERSION_4_1',
    ]
    for token in required_dsi:
        if token not in dsi:
            raise SystemExit('Phase283 DSI marker missing: ' + token)
    for token in required_phy:
        if token not in phy:
            raise SystemExit('Phase283 PHY marker missing: ' + token)
    if dsi.index('a52_p283_shared_snapshot(dsi_ctrl, 2);') > dsi.index('P276 282Z q=2'):
        raise SystemExit('Phase283 q2 snapshot must precede Phase282/280 retention freeze')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    dsi_path = args.root / DSI
    phy_path = args.root / PHY
    if not dsi_path.is_file() or not phy_path.is_file():
        raise SystemExit('Phase283 DSI/PHY source missing')
    dsi = dsi_path.read_text()
    phy = phy_path.read_text()
    if not args.check_only:
        dsi = patch_dsi(dsi)
        phy = patch_phy(phy)
        dsi_path.write_text(dsi)
        phy_path.write_text(phy)
    validate(dsi, phy)
    print('Phase283 shared DSI engine/clock/PHY trace: PASS')


if __name__ == '__main__':
    main()
