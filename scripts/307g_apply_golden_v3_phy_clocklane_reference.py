#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('techpack/display/msm/dsi/dsi_ctrl.c')
HW = Path('techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c')
PHY = Path('techpack/display/msm/dsi/dsi_phy.c')
MARK = 'A52_PHASE307_GOLDEN_V3_PHY_CLOCKLANE_REFERENCE_V1'


def repl(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase307G {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    anchor = 'static DEFINE_MUTEX(dsi_phy_list_lock);\n'
    helper = r'''static DEFINE_MUTEX(dsi_phy_list_lock);

/* A52_PHASE307_GOLDEN_V3_PHY_CLOCKLANE_REFERENCE_V1
 * Read-only matched control for known-good TouchGrass 4.19.200.
 * q0 before SW_TRIGGER, q1 after SW_TRIGGER, q2 after completion.
 */
#define A52_G307_V3_CLK_CFG0        0x010
#define A52_G307_V3_CLK_CFG1        0x014
#define A52_G307_V3_GLBL_CTRL       0x018
#define A52_G307_V3_RBUF_CTRL       0x01c
#define A52_G307_V3_VREG_CTRL       0x020
#define A52_G307_V3_CTRL0           0x024
#define A52_G307_V3_CTRL1           0x028
#define A52_G307_V3_CTRL2           0x02c
#define A52_G307_V3_LANE_CFG0       0x030
#define A52_G307_V3_LANE_CFG1       0x034
#define A52_G307_V3_PLL_CTRL        0x038
#define A52_G307_V3_LANE_CTRL0      0x098
#define A52_G307_V3_LANE_CTRL1      0x09c
#define A52_G307_V3_LANE_CTRL2      0x0a0
#define A52_G307_V3_LANE_CTRL3      0x0a4
#define A52_G307_V3_LANE_CTRL4      0x0a8
#define A52_G307_V3_STATUS          0x0ec
#define A52_G307_V3_LANE_STATUS0    0x0f4
#define A52_G307_V3_LANE_STATUS1    0x0f8

void a52_g307_phy_snapshot(unsigned int index, unsigned int point)
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
		pr_info("TG307 PX q=%u i=%u x=0\n", point, index);
		return;
	}
	ver = phy->ver_info->version;
	if (ver != DSI_PHY_VERSION_3_0) {
		pr_info("TG307 PX q=%u i=%u v=%u\n", point, index, ver);
		return;
	}
	base = phy->hw.base;
	pr_info("TG307 P0 q=%u v=%u p=%u s=%u %x %x %x %x\n", point,
		ver, phy->power_state, phy->dsi_phy_state,
		readl_relaxed(base + A52_G307_V3_PLL_CTRL),
		readl_relaxed(base + A52_G307_V3_STATUS),
		readl_relaxed(base + A52_G307_V3_LANE_STATUS0),
		readl_relaxed(base + A52_G307_V3_LANE_STATUS1));
	pr_info("TG307 P1 q=%u %x %x %x %x %x %x\n", point,
		readl_relaxed(base + A52_G307_V3_CLK_CFG0),
		readl_relaxed(base + A52_G307_V3_CLK_CFG1),
		readl_relaxed(base + A52_G307_V3_GLBL_CTRL),
		readl_relaxed(base + A52_G307_V3_RBUF_CTRL),
		readl_relaxed(base + A52_G307_V3_VREG_CTRL),
		readl_relaxed(base + A52_G307_V3_CTRL0));
	pr_info("TG307 P2 q=%u %x %x %x %x %x %x\n", point,
		readl_relaxed(base + A52_G307_V3_CTRL1),
		readl_relaxed(base + A52_G307_V3_CTRL2),
		readl_relaxed(base + A52_G307_V3_LANE_CFG0),
		readl_relaxed(base + A52_G307_V3_LANE_CFG1),
		readl_relaxed(base + A52_G307_V3_LANE_CTRL0),
		readl_relaxed(base + A52_G307_V3_LANE_CTRL1));
	pr_info("TG307 P3 q=%u %x %x %x\n", point,
		readl_relaxed(base + A52_G307_V3_LANE_CTRL2),
		readl_relaxed(base + A52_G307_V3_LANE_CTRL3),
		readl_relaxed(base + A52_G307_V3_LANE_CTRL4));
}

'''
    return repl(text, anchor, helper, 'PHY helper')


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    anchor = 'static DEFINE_MUTEX(dsi_ctrl_list_lock);\n'
    helper = r'''static DEFINE_MUTEX(dsi_ctrl_list_lock);

/* A52_PHASE307_GOLDEN_V3_PHY_CLOCKLANE_REFERENCE_V1 */
static atomic_t a52_g307_state = ATOMIC_INIT(0);
extern void a52_g307_phy_snapshot(unsigned int index, unsigned int point);
extern void a52_g307_hw_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);

bool a52_g307_trace_active(void)
{
	return atomic_read(&a52_g307_state) == 1;
}

static bool a52_g307_armed(struct dsi_ctrl *ctrl)
{
	return ctrl && ctrl->cell_index == 0 && a52_g307_trace_active();
}

static void a52_g307_try_arm(struct dsi_ctrl *ctrl,
		const struct mipi_dsi_msg *msg, u32 flags)
{
	const u8 *p;
	if (!ctrl || !msg || ctrl->cell_index != 0 ||
	    flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return;
	p = msg->tx_buf;
	if (p[0] != 0xF0 || p[1] != 0x5A || p[2] != 0x5A)
		return;
	if (atomic_cmpxchg(&a52_g307_state, 0, 1) != 0)
		return;
	pr_info("TG307 ARM c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x\n",
		flags, msg->flags, msg->type, (unsigned int)msg->tx_len,
		p[0], p[1], p[2]);
}

'''
    text = repl(text, anchor, helper, 'controller arm helper')

    old_arm = '''\tdsi_ctrl_validate_msg_flags(dsi_ctrl, msg, flags);\n\n\tif (dsi_ctrl->dma_wait_queued)\n'''
    new_arm = '''\tdsi_ctrl_validate_msg_flags(dsi_ctrl, msg, flags);\n\ta52_g307_try_arm(dsi_ctrl, msg, *flags);\n\n\tif (dsi_ctrl->dma_wait_queued)\n'''
    text = repl(text, old_arm, new_arm, 'exact target arm')

    old_wait = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n'''
    new_wait = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (a52_g307_armed(dsi_ctrl)) {\n\t\ta52_g307_hw_snapshot(&dsi_ctrl->hw, 2);\n\t\tpr_info("TG307 C q=2 ret=%d irq=%d\\n", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t}\n\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n'''
    return repl(text, old_wait, new_wait, 'q2 completion snapshot')


def patch_hw(text: str) -> str:
    if MARK in text:
        return text
    anchor = '#include "sde_dbg.h"\n'
    helper = r'''#include "sde_dbg.h"

/* A52_PHASE307_GOLDEN_V3_PHY_CLOCKLANE_REFERENCE_V1 */
extern bool a52_g307_trace_active(void);
extern void a52_g307_phy_snapshot(unsigned int index, unsigned int point);
void a52_g307_hw_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);
void a52_g307_hw_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	if (!a52_g307_trace_active() || !ctrl || !ctrl->base)
		return;
	pr_info("TG307 C q=%u st=%x ln=%x ck=%x cc=%x in=%x\n", point,
		DSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_LANE_STATUS),
		DSI_R32(ctrl, DSI_CLK_STATUS), DSI_R32(ctrl, DSI_CLK_CTRL),
		DSI_R32(ctrl, DSI_INT_CTRL));
	a52_g307_phy_snapshot(ctrl->index, point);
}
'''
    text = repl(text, anchor, helper, 'HW snapshot helper')

    old_kick = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n\n/**\n * kickoff_fifo_command()'''
    new_kick = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_g307_hw_snapshot(ctrl, 0);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_g307_hw_snapshot(ctrl, 1);\n\t}\n}\n\n/**\n * kickoff_fifo_command()'''
    text = repl(text, old_kick, new_kick, 'memory q0/q1')

    old_trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n'''
    new_trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_g307_hw_snapshot(ctrl, 0);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_g307_hw_snapshot(ctrl, 1);\n}\n'''
    return repl(text, old_trig, new_trig, 'deferred q0/q1')


def validate(ctrl: str, hw: str, phy: str) -> None:
    need = [MARK, 'TG307 ARM c=0', 'TG307 C q=%u', 'TG307 C q=2',
            'TG307 P0 q=%u', 'TG307 P1 q=%u', 'TG307 P2 q=%u', 'TG307 P3 q=%u',
            'DSI_PHY_VERSION_3_0', 'A52_G307_V3_LANE_STATUS1    0x0f8']
    alltxt = ctrl + hw + phy
    for x in need:
        if x not in alltxt:
            raise SystemExit('Phase307G missing token: ' + x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    paths = [ns.root / CTRL, ns.root / HW, ns.root / PHY]
    for p in paths:
        if not p.is_file(): raise SystemExit('Phase307G missing source: ' + str(p))
    if not ns.check_only:
        paths[0].write_text(patch_ctrl(paths[0].read_text()))
        paths[1].write_text(patch_hw(paths[1].read_text()))
        paths[2].write_text(patch_phy(paths[2].read_text()))
    validate(*(p.read_text() for p in paths))
    print('Phase307 Golden TouchGrass v3 PHY/clock-lane observer: PASS')


if __name__ == '__main__':
    main()
