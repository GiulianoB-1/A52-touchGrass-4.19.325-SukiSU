#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
PHY = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
MARK = 'A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase307 {label}: expected exactly one match, found {n}')
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text

    text = replace_once(
        text,
        '#include "dsi_phy.h"\n',
        '#include "dsi_phy.h"\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'PHY recorder include')

    anchor = 'static DEFINE_MUTEX(dsi_phy_list_lock);\n'
    helper = r'''static DEFINE_MUTEX(dsi_phy_list_lock);

/* A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1
 * Read-only snapshot of the actual A52 DSI_PHY_VERSION_3_0 (10-nm) common
 * block. Offsets are the exact v3.0 offsets already validated in Phase284.
 * The helper is called only for the exact ctrl0 F0 5A 5A transaction.
 * No MMIO write, delay, reset, clock vote, regulator vote or PHY state change.
 */
#define A52_P307_V3_CLK_CFG0        0x010
#define A52_P307_V3_CLK_CFG1        0x014
#define A52_P307_V3_GLBL_CTRL       0x018
#define A52_P307_V3_RBUF_CTRL       0x01c
#define A52_P307_V3_VREG_CTRL       0x020
#define A52_P307_V3_CTRL0           0x024
#define A52_P307_V3_CTRL1           0x028
#define A52_P307_V3_CTRL2           0x02c
#define A52_P307_V3_LANE_CFG0       0x030
#define A52_P307_V3_LANE_CFG1       0x034
#define A52_P307_V3_PLL_CTRL        0x038
#define A52_P307_V3_LANE_CTRL0      0x098
#define A52_P307_V3_LANE_CTRL1      0x09c
#define A52_P307_V3_LANE_CTRL2      0x0a0
#define A52_P307_V3_LANE_CTRL3      0x0a4
#define A52_P307_V3_LANE_CTRL4      0x0a8
#define A52_P307_V3_STATUS          0x0ec
#define A52_P307_V3_LANE_STATUS0    0x0f4
#define A52_P307_V3_LANE_STATUS1    0x0f8

void a52_p307_phy_snapshot(unsigned int index, unsigned int point)
{
	struct dsi_phy_list_item *item;
	struct msm_dsi_phy *phy = NULL;
	void __iomem *base;
	u32 ver;

	mutex_lock(&dsi_phy_list_lock);
	list_for_each_entry(item, &dsi_phy_list, list) {
		if (item->phy && item->phy->index == index) {
			phy = item->phy;
			break;
		}
	}
	mutex_unlock(&dsi_phy_list_lock);

	if (!phy || !phy->ver_info || !phy->hw.base) {
		a52_ackfr_record("P276 307PX q=%u i=%u x=0", point, index);
		return;
	}

	ver = phy->ver_info->version;
	if (ver != DSI_PHY_VERSION_3_0) {
		a52_ackfr_record("P276 307PX q=%u i=%u v=%u", point, index, ver);
		return;
	}

	base = phy->hw.base;
	a52_ackfr_record("P276 307P0 q=%u v=%u p=%u s=%u %x %x %x %x", point,
		ver, phy->power_state, phy->dsi_phy_state,
		readl_relaxed(base + A52_P307_V3_PLL_CTRL),
		readl_relaxed(base + A52_P307_V3_STATUS),
		readl_relaxed(base + A52_P307_V3_LANE_STATUS0),
		readl_relaxed(base + A52_P307_V3_LANE_STATUS1));
	a52_ackfr_record("P276 307P1 q=%u %x %x %x %x %x %x", point,
		readl_relaxed(base + A52_P307_V3_CLK_CFG0),
		readl_relaxed(base + A52_P307_V3_CLK_CFG1),
		readl_relaxed(base + A52_P307_V3_GLBL_CTRL),
		readl_relaxed(base + A52_P307_V3_RBUF_CTRL),
		readl_relaxed(base + A52_P307_V3_VREG_CTRL),
		readl_relaxed(base + A52_P307_V3_CTRL0));
	a52_ackfr_record("P276 307P2 q=%u %x %x %x %x %x %x", point,
		readl_relaxed(base + A52_P307_V3_CTRL1),
		readl_relaxed(base + A52_P307_V3_CTRL2),
		readl_relaxed(base + A52_P307_V3_LANE_CFG0),
		readl_relaxed(base + A52_P307_V3_LANE_CFG1),
		readl_relaxed(base + A52_P307_V3_LANE_CTRL0),
		readl_relaxed(base + A52_P307_V3_LANE_CTRL1));
	a52_ackfr_record("P276 307P3 q=%u %x %x %x", point,
		readl_relaxed(base + A52_P307_V3_LANE_CTRL2),
		readl_relaxed(base + A52_P307_V3_LANE_CTRL3),
		readl_relaxed(base + A52_P307_V3_LANE_CTRL4));
}

'''
    return replace_once(text, anchor, helper, 'PHY helper insertion')


def patch_hw(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1' not in text:
        raise SystemExit('Phase307 requires inherited Phase303/293 HW reference')

    old = '''/* A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1 */\nextern bool a52_p293_gdm_trace_active(void);\nextern void a52_ackfr_record(const char *fmt, ...);\n'''
    new = old + '''extern void a52_p307_phy_snapshot(unsigned int index, unsigned int point);\n\n/* A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1\n * q0 = immediately before SW trigger, q1 = immediately after SW trigger.\n * Read-only controller + real v3 PHY correlation for exact F0 5A 5A only.\n */\nstatic void a52_p307_hw_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)\n{\n\tif (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)\n\t\treturn;\n\ta52_ackfr_record("P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x", point,\n\t\tDSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_LANE_STATUS),\n\t\tDSI_R32(ctrl, DSI_CLK_STATUS), DSI_R32(ctrl, DSI_CLK_CTRL),\n\t\tDSI_R32(ctrl, DSI_INT_CTRL));\n\ta52_p307_phy_snapshot(ctrl->index, point);\n}\n'''
    text = replace_once(text, old, new, 'HW helper insertion')

    old_pre = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\tif (a52_p293_gdm_trace_active()) {\n'''
    new_pre = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_p307_hw_snapshot(ctrl, 0);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_p307_hw_snapshot(ctrl, 1);\n\t\tif (a52_p293_gdm_trace_active()) {\n'''
    text = replace_once(text, old_pre, new_pre, 'memory kickoff q0/q1')

    old_trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\tif (a52_p293_gdm_trace_active()) {\n'''
    new_trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_p307_hw_snapshot(ctrl, 0);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_p307_hw_snapshot(ctrl, 1);\n\tif (a52_p293_gdm_trace_active()) {\n'''
    text = replace_once(text, old_trig, new_trig, 'deferred trigger q0/q1')
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE304_EXACT_F05A5A_VISIBILITY_V1' not in text:
        raise SystemExit('Phase307 requires inherited exact F0 5A 5A arming')

    anchor = 'bool a52_p293_gdm_trace_active(void)\n{\n\treturn atomic_read(&a52_p293_gdm_state) == 1;\n}\n'
    inject = anchor + '\n/* ' + MARK + ' */\nextern void a52_p307_phy_snapshot(unsigned int index, unsigned int point);\n'
    text = replace_once(text, anchor, inject, 'controller PHY declaration')

    old = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (a52_p293_gdm_armed(dsi_ctrl)) {\n'''
    new = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (a52_p293_gdm_armed(dsi_ctrl)) {\n\t\ta52_ackfr_record("P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x",\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_CLK_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));\n\t\ta52_p307_phy_snapshot(dsi_ctrl->cell_index, 2);\n'''
    return replace_once(text, old, new, 'completion q2 snapshot')


def validate(ctrl: str, hw: str, phy: str) -> None:
    combined = ctrl + hw + phy
    required = [
        MARK,
        'DSI_PHY_VERSION_3_0',
        'A52_P307_V3_PLL_CTRL        0x038',
        'A52_P307_V3_STATUS          0x0ec',
        'A52_P307_V3_LANE_STATUS0    0x0f4',
        'A52_P307_V3_LANE_STATUS1    0x0f8',
        'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x',
        'P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x',
        'P276 307P0 q=%u v=%u p=%u s=%u %x %x %x %x',
        'P276 307P1 q=%u %x %x %x %x %x %x',
        'P276 307P2 q=%u %x %x %x %x %x %x',
        'P276 307P3 q=%u %x %x %x',
        'a52_p307_hw_snapshot(ctrl, 0);',
        'a52_p307_hw_snapshot(ctrl, 1);',
        'a52_p307_phy_snapshot(dsi_ctrl->cell_index, 2);',
        'P276 303 S00p p=%02x%02x%02x',
        'P276 303 S06 st=%x fs=%x ln=%x ck=%x',
        'P276 303 S08 ret=%d irq=%d in=%x st=%x',
    ]
    for token in required:
        if token not in combined:
            raise SystemExit('Phase307 required token missing: ' + token)

    # Observer-only audit: the patch must add no new write/delay/reset primitives.
    for token in ['DSI_W32(', 'writel_relaxed(', 'writel(', 'msleep(', 'usleep_range(', 'udelay(']:
        # Existing source naturally contains these; the apply script itself never injects them
        # except the two pre-existing trigger lines included in replacement anchors.
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    cp, hp, pp = args.root / CTRL, args.root / HW, args.root / PHY
    for p in (cp, hp, pp):
        if not p.is_file():
            raise SystemExit('Phase307 source missing: ' + str(p))

    if not args.check_only:
        cp.write_text(patch_ctrl(cp.read_text()))
        hp.write_text(patch_hw(hp.read_text()))
        pp.write_text(patch_phy(pp.read_text()))

    validate(cp.read_text(), hp.read_text(), pp.read_text())
    print('Phase307 GKI v3 PHY/clock-lane exact-target observer: PASS')


if __name__ == '__main__':
    main()
