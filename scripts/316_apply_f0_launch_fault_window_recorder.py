#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HWC = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
PHYV3 = Path('drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c')
MARK = 'A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase316 {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def restore_golden_phy(text: str) -> str:
    if 'A52_PHASE313_V3_TIMING9_HANDOFF_REPAIR_AB_V1' not in text:
        raise SystemExit('Phase316 requires inherited Phase313 source')
    if 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1' not in text:
        raise SystemExit('Phase316 requires inherited Phase311 source')

    text = one(
        text,
        '\tDSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);\n',
        '\t/* Phase316: Golden successful splash handoff keeps TIMING9 inherited (0x12). */\n',
        'remove Phase313 TIMING9 write')

    marker = 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1'
    pos = text.index(marker)
    bit = text.find('\treg |= BIT(2);\n', pos)
    if bit < 0:
        raise SystemExit('Phase316 Phase311 DCTRL3 bit2 write missing')
    text = text[:bit] + ('\t/* Phase316: Golden successful splash handoff keeps lane3 '
                         'TX_DCTRL inherited (0x00). */\n') + text[bit + len('\treg |= BIT(2);\n'):]
    return text


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1' not in text:
        raise SystemExit('Phase316 requires Phase307 q0/q1 hooks')

    anchor = 'static void a52_p307_hw_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)\n{\n'
    helper = r'''/* A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1
 * Exact-F0 q0/q1/q2 read-only fault window. No ack/clear/write/delay/clock
 * action is added. DSI_DISP_CC_R32() is intentionally used here because it
 * reads ctrl->disp_cc_base (the DSI-specific disp_cc_base resource), not the
 * global Lagoon DISP_CC regmap whose offset 0 is PLL0.
 */
void a52_p316_fault_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	u32 misc = 0xffffffff;
	u32 ck;

	if (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)
		return;
	if (ctrl->disp_cc_base)
		misc = DSI_DISP_CC_R32(ctrl, 0x0);
	ck = DSI_R32(ctrl, DSI_CLK_STATUS);

	a52_ackfr_record("P276 316C q=%u st=%x fs=%x ck=%x ln=%x in=%x em=%x", point,
		DSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_FIFO_STATUS), ck,
		DSI_R32(ctrl, DSI_LANE_STATUS), DSI_R32(ctrl, DSI_INT_CTRL),
		DSI_R32(ctrl, DSI_ERR_INT_MASK0));
	a52_ackfr_record("P276 316E q=%u ack=%x to=%x pe=%x ct=%x cc=%x lc=%x", point,
		DSI_R32(ctrl, DSI_ACK_ERR_STATUS), DSI_R32(ctrl, DSI_TIMEOUT_STATUS),
		DSI_R32(ctrl, DSI_DLN0_PHY_ERR), DSI_R32(ctrl, DSI_CTRL),
		DSI_R32(ctrl, DSI_CLK_CTRL), DSI_R32(ctrl, DSI_LANE_CTRL));
	a52_ackfr_record("P276 316D q=%u dc=%x df=%x of=%x le=%x sw=%x tr=%x", point,
		DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),
		DSI_R32(ctrl, DSI_DMA_FIFO_CTRL), DSI_R32(ctrl, DSI_DMA_CMD_OFFSET),
		DSI_R32(ctrl, DSI_DMA_CMD_LENGTH),
		DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER),
		DSI_R32(ctrl, DSI_TRIG_CTRL));
	a52_ackfr_record("P276 316M q=%u m=%x b0=%u b5=%u b7=%u b9=%u", point,
		misc, (misc >> 0) & 1, (misc >> 5) & 1,
		(misc >> 7) & 1, (misc >> 9) & 1);
	a52_ackfr_record("P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u", point,
		ck, (ck >> 7) & 1, (ck >> 10) & 1, (ck >> 12) & 1,
		(ck >> 16) & 1, (ck >> 23) & 1);
}

'''
    text = one(text, anchor, helper + anchor, 'HWC helper insertion')

    old = '''\ta52_ackfr_record("P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x", point,
\t\tDSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_LANE_STATUS),
\t\tDSI_R32(ctrl, DSI_CLK_STATUS), DSI_R32(ctrl, DSI_CLK_CTRL),
\t\tDSI_R32(ctrl, DSI_INT_CTRL));
\ta52_p307_phy_snapshot(ctrl->index, point);
'''
    new = '''\ta52_ackfr_record("P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x", point,
\t\tDSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_LANE_STATUS),
\t\tDSI_R32(ctrl, DSI_CLK_STATUS), DSI_R32(ctrl, DSI_CLK_CTRL),
\t\tDSI_R32(ctrl, DSI_INT_CTRL));
\ta52_p316_fault_snapshot(ctrl, point);
\ta52_p307_phy_snapshot(ctrl->index, point);
'''
    return one(text, old, new, 'q0/q1 extension')


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if 'P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x' not in text:
        raise SystemExit('Phase316 requires Phase307 q2 hook')

    decl_anchor = 'extern void a52_p307_phy_snapshot(unsigned int index, unsigned int point);\n'
    text = one(
        text, decl_anchor,
        decl_anchor + 'extern void a52_p316_fault_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n',
        'q2 helper declaration')

    q2_anchor = '''\t\ta52_ackfr_record("P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x",
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_CLK_CTRL),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
\t\ta52_p307_phy_snapshot(dsi_ctrl->cell_index, 2);
'''
    q2_new = '''\t\ta52_ackfr_record("P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x",
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_CLK_CTRL),
\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
\t\ta52_p316_fault_snapshot(&dsi_ctrl->hw, 2);
\t\ta52_ackfr_record("P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d",
\t\t\tdsi_ctrl->irq_info.irq_stat_mask,
\t\t\t(unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\tdsi_ctrl->irq_info.cmd_dma_done.done,
\t\t\tdsi_ctrl->dma_wait_queued,
\t\t\tdsi_ctrl->error_interrupt_count, ret);
\t\ta52_p307_phy_snapshot(dsi_ctrl->cell_index, 2);
'''
    return one(text, q2_anchor, q2_new, 'q2 extension')


def validate(ctrl: str, hwc: str, phyv3: str) -> None:
    combined = ctrl + hwc + phyv3
    for token in (
        MARK,
        'P276 316C q=%u st=%x fs=%x ck=%x ln=%x in=%x em=%x',
        'P276 316E q=%u ack=%x to=%x pe=%x ct=%x cc=%x lc=%x',
        'P276 316D q=%u dc=%x df=%x of=%x le=%x sw=%x tr=%x',
        'P276 316M q=%u m=%x b0=%u b5=%u b7=%u b9=%u',
        'P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u',
        'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d',
        'a52_p316_fault_snapshot(ctrl, point);',
        'a52_p316_fault_snapshot(&dsi_ctrl->hw, 2);',
    ):
        if token not in combined:
            raise SystemExit('Phase316 required token missing: ' + token)

    if 'DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);' in phyv3:
        raise SystemExit('Phase316 TIMING9 source-default repair still active')

    pos = phyv3.index('A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1')
    tail = phyv3[pos:pos + 1400]
    if '\treg |= BIT(2);\n' in tail:
        raise SystemExit('Phase316 lane3 DCTRL3 source-default repair still active')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()

    cp, hp, pp = ns.root / CTRL, ns.root / HWC, ns.root / PHYV3
    for p in (cp, hp, pp):
        if not p.is_file():
            raise SystemExit('Phase316 source missing: ' + str(p))

    if not ns.check_only:
        pp.write_text(restore_golden_phy(pp.read_text()))
        hp.write_text(patch_hwc(hp.read_text()))
        cp.write_text(patch_ctrl(cp.read_text()))

    validate(cp.read_text(), hp.read_text(), pp.read_text())
    print('Phase316 Golden-parity exact-F0 launch fault-window recorder: PASS')


if __name__ == '__main__':
    main()
