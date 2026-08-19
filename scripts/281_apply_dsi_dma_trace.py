#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' not in text:
        raise SystemExit('Phase280 retention marker missing')

    anchor = 'static DEFINE_MUTEX(dsi_ctrl_list_lock);\n\n'
    helper = r'''static DEFINE_MUTEX(dsi_ctrl_list_lock);

/* A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1
 * Read-only snapshots around the already-proven command-DMA failure boundary.
 * R0 positional fields: DSI_STATUS, DSI_FIFO_STATUS,
 * DSI_COMMAND_MODE_DMA_CTRL, DSI_DMA_FIFO_CTRL,
 * DSI_CMD_MODE_DMA_SW_TRIGGER, DSI_INT_CTRL.
 * R1 positional fields: DSI_ACK_ERR_STATUS, DSI_TIMEOUT_STATUS,
 * DSI_LANE_STATUS, DSI_DLN0_PHY_ERR, DSI_AXI2AHB_CTRL, DSI_VBIF_CTRL.
 * R2 (timeout only): DSI_DMA_CMD_OFFSET, DSI_DMA_CMD_LENGTH,
 * DSI_CLK_CTRL, DSI_CLK_STATUS.
 * No DSI register is written and no control-flow decision is changed.
 */
static void a52_p281_dsi_dma_snapshot(struct dsi_ctrl *dsi_ctrl,
		unsigned int point)
{
	void __iomem *base;

	if (!a52_p276r_deep_active() || !dsi_ctrl || !dsi_ctrl->hw.base)
		return;

	base = dsi_ctrl->hw.base;
	a52_ackfr_record("P276 281R0 q=%u %x %x %x %x %x %x", point,
		readl_relaxed(base + DSI_STATUS),
		readl_relaxed(base + DSI_FIFO_STATUS),
		readl_relaxed(base + DSI_COMMAND_MODE_DMA_CTRL),
		readl_relaxed(base + DSI_DMA_FIFO_CTRL),
		readl_relaxed(base + DSI_CMD_MODE_DMA_SW_TRIGGER),
		readl_relaxed(base + DSI_INT_CTRL));
	a52_ackfr_record("P276 281R1 q=%u %x %x %x %x %x %x", point,
		readl_relaxed(base + DSI_ACK_ERR_STATUS),
		readl_relaxed(base + DSI_TIMEOUT_STATUS),
		readl_relaxed(base + DSI_LANE_STATUS),
		readl_relaxed(base + DSI_DLN0_PHY_ERR),
		readl_relaxed(base + DSI_AXI2AHB_CTRL),
		readl_relaxed(base + DSI_VBIF_CTRL));

	if (point == 2)
		a52_ackfr_record("P276 281R2 q=2 %x %x %x %x",
			readl_relaxed(base + DSI_DMA_CMD_OFFSET),
			readl_relaxed(base + DSI_DMA_CMD_LENGTH),
			readl_relaxed(base + DSI_CLK_CTRL),
			readl_relaxed(base + DSI_CLK_STATUS));
}

'''
    text = replace_one(text, anchor, helper, 'Phase281 DSI helper insertion')

    old = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_p279_display_fault_snapshot(2);\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    new = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_p279_display_fault_snapshot(2);\n\t\ta52_p281_dsi_dma_snapshot(dsi_ctrl, 2);\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    text = replace_one(text, old, new, 'Phase281 q2 snapshot')

    old = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p279_display_fault_snapshot(0);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n'''
    new = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p279_display_fault_snapshot(0);\n\t\t\t\ta52_p281_dsi_dma_snapshot(dsi_ctrl, 0);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n'''
    text = replace_one(text, old, new, 'Phase281 q0 snapshot')

    old = '''\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=1");\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(1);\n'''
    new = '''\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=1");\n\t\t\t\ta52_p281_dsi_dma_snapshot(dsi_ctrl, 1);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(1);\n'''
    text = replace_one(text, old, new, 'Phase281 q1 snapshot')
    return text


def validate(text: str) -> None:
    required = [
        MARK,
        'P276 281R0 q=%u %x %x %x %x %x %x',
        'P276 281R1 q=%u %x %x %x %x %x %x',
        'P276 281R2 q=2 %x %x %x %x',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 0);',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 1);',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);',
        'P276 280Z q=2',
        'a52_ackfr_retain_timeout_snapshot();',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase281 DSI marker missing: ' + token)
    if text.index('a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);') > text.index('P276 280Z q=2'):
        raise SystemExit('Phase281 q2 DSI snapshot occurs after retention latch')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    path = args.root / DSI
    if not path.is_file():
        raise SystemExit(f'missing source: {path}')
    text = path.read_text()
    if not args.check_only:
        text = patch(text)
        path.write_text(text)
    validate(text)
    print('Phase281 DSI DMA consumption trace: PASS')


if __name__ == '__main__':
    main()
