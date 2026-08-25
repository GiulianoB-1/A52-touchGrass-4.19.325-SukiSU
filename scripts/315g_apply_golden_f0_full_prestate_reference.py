#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path("techpack/display/msm/dsi/dsi_ctrl.c")
HW = Path("techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c")
PHY = Path("techpack/display/msm/dsi/dsi_phy.c")
MARK = "A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1"


def repl(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase315G {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text

    anchor = "static DEFINE_MUTEX(dsi_phy_list_lock);\n"
    helper = r'''static DEFINE_MUTEX(dsi_phy_list_lock);

/* A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1
 * Read-only full q0 prestate from known-working TouchGrass 4.19.200.
 * No PHY/controller/clock/regulator/reset programming is added.
 */
#define A52_G315_CMN_CLK_CFG0             0x010
#define A52_G315_CMN_CLK_CFG1             0x014
#define A52_G315_CMN_GLBL_CTRL            0x018
#define A52_G315_CMN_RBUF_CTRL            0x01c
#define A52_G315_CMN_VREG_CTRL            0x020
#define A52_G315_CMN_CTRL0                0x024
#define A52_G315_CMN_CTRL1                0x028
#define A52_G315_CMN_CTRL2                0x02c
#define A52_G315_CMN_LANE_CFG0            0x030
#define A52_G315_CMN_LANE_CFG1            0x034
#define A52_G315_CMN_PLL_CTRL             0x038
#define A52_G315_CMN_LANE_CTRL0           0x098
#define A52_G315_CMN_LANE_CTRL1           0x09c
#define A52_G315_CMN_LANE_CTRL2           0x0a0
#define A52_G315_CMN_LANE_CTRL3           0x0a4
#define A52_G315_CMN_LANE_CTRL4           0x0a8
#define A52_G315_CMN_TIMING0              0x0ac
#define A52_G315_CMN_PHY_STATUS           0x0ec
#define A52_G315_CMN_LANE_STATUS0         0x0f4
#define A52_G315_CMN_LANE_STATUS1         0x0f8

#define A52_G315_LNX_CFG0(n)              (0x200 + (0x80 * (n)))
#define A52_G315_LNX_CFG1(n)              (0x204 + (0x80 * (n)))
#define A52_G315_LNX_CFG2(n)              (0x208 + (0x80 * (n)))
#define A52_G315_LNX_CFG3(n)              (0x20c + (0x80 * (n)))
#define A52_G315_LNX_TEST_DATAPATH(n)     (0x210 + (0x80 * (n)))
#define A52_G315_LNX_PIN_SWAP(n)          (0x214 + (0x80 * (n)))
#define A52_G315_LNX_HSTX_STR_CTRL(n)     (0x218 + (0x80 * (n)))
#define A52_G315_LNX_OFFSET_TOP_CTRL(n)   (0x21c + (0x80 * (n)))
#define A52_G315_LNX_OFFSET_BOT_CTRL(n)   (0x220 + (0x80 * (n)))
#define A52_G315_LNX_LPTX_STR_CTRL(n)     (0x224 + (0x80 * (n)))
#define A52_G315_LNX_LPRX_CTRL(n)         (0x228 + (0x80 * (n)))
#define A52_G315_LNX_TX_DCTRL(n)          (0x22c + (0x80 * (n)))

void a52_g315_phy_snapshot(unsigned int index)
{
	struct dsi_phy_list_item *item;
	struct msm_dsi_phy *phy = NULL;
	void __iomem *base;
	u32 l;

	mutex_lock(&dsi_phy_list_lock);
	list_for_each_entry(item, &dsi_phy_list, list) {
		if (item->phy && item->phy->index == index) {
			phy = item->phy;
			break;
		}
	}
	mutex_unlock(&dsi_phy_list_lock);

	if (!phy || !phy->ver_info || !phy->hw.base) {
		pr_info("TG315 PX i=%u x=0\n", index);
		return;
	}
	if (phy->ver_info->version != DSI_PHY_VERSION_3_0) {
		pr_info("TG315 PX i=%u v=%u\n", index,
			phy->ver_info->version);
		return;
	}

	base = phy->hw.base;
	pr_info("TG315 P0 %x %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_CMN_CLK_CFG0),
		readl_relaxed(base + A52_G315_CMN_CLK_CFG1),
		readl_relaxed(base + A52_G315_CMN_GLBL_CTRL),
		readl_relaxed(base + A52_G315_CMN_RBUF_CTRL),
		readl_relaxed(base + A52_G315_CMN_VREG_CTRL),
		readl_relaxed(base + A52_G315_CMN_CTRL0));
	pr_info("TG315 P1 %x %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_CMN_CTRL1),
		readl_relaxed(base + A52_G315_CMN_CTRL2),
		readl_relaxed(base + A52_G315_CMN_LANE_CFG0),
		readl_relaxed(base + A52_G315_CMN_LANE_CFG1),
		readl_relaxed(base + A52_G315_CMN_PLL_CTRL),
		readl_relaxed(base + A52_G315_CMN_LANE_CTRL0));
	pr_info("TG315 P2 %x %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_CMN_LANE_CTRL1),
		readl_relaxed(base + A52_G315_CMN_LANE_CTRL2),
		readl_relaxed(base + A52_G315_CMN_LANE_CTRL3),
		readl_relaxed(base + A52_G315_CMN_LANE_CTRL4),
		readl_relaxed(base + A52_G315_CMN_PHY_STATUS),
		readl_relaxed(base + A52_G315_CMN_LANE_STATUS0));
	pr_info("TG315 P3 %x %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_CMN_LANE_STATUS1),
		readl_relaxed(base + A52_G315_LNX_TEST_DATAPATH(0)),
		readl_relaxed(base + A52_G315_LNX_TEST_DATAPATH(1)),
		readl_relaxed(base + A52_G315_LNX_TEST_DATAPATH(2)),
		readl_relaxed(base + A52_G315_LNX_TEST_DATAPATH(3)),
		readl_relaxed(base + A52_G315_LNX_TEST_DATAPATH(4)));
	pr_info("TG315 P4 %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_LNX_TX_DCTRL(0)),
		readl_relaxed(base + A52_G315_LNX_TX_DCTRL(1)),
		readl_relaxed(base + A52_G315_LNX_TX_DCTRL(2)),
		readl_relaxed(base + A52_G315_LNX_TX_DCTRL(3)),
		readl_relaxed(base + A52_G315_LNX_TX_DCTRL(4)));

	pr_info("TG315 T0 %x %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x00),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x04),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x08),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x0c),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x10),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x14));
	pr_info("TG315 T1 %x %x %x %x %x %x\n",
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x18),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x1c),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x20),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x24),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x28),
		readl_relaxed(base + A52_G315_CMN_TIMING0 + 0x2c));

	for (l = 0; l < 5; l++) {
		pr_info("TG315 L%uA %x %x %x %x %x %x\n", l,
			readl_relaxed(base + A52_G315_LNX_CFG0(l)),
			readl_relaxed(base + A52_G315_LNX_CFG1(l)),
			readl_relaxed(base + A52_G315_LNX_CFG2(l)),
			readl_relaxed(base + A52_G315_LNX_CFG3(l)),
			readl_relaxed(base + A52_G315_LNX_PIN_SWAP(l)),
			readl_relaxed(base + A52_G315_LNX_HSTX_STR_CTRL(l)));
		pr_info("TG315 L%uB %x %x %x %x %x\n", l,
			readl_relaxed(base + A52_G315_LNX_OFFSET_TOP_CTRL(l)),
			readl_relaxed(base + A52_G315_LNX_OFFSET_BOT_CTRL(l)),
			readl_relaxed(base + A52_G315_LNX_LPTX_STR_CTRL(l)),
			readl_relaxed(base + A52_G315_LNX_LPRX_CTRL(l)),
			readl_relaxed(base + A52_G315_LNX_TX_DCTRL(l)));
	}
}

'''
    return repl(text, anchor, helper, "PHY full-prestate helper")


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text

    anchor = "static DEFINE_MUTEX(dsi_ctrl_list_lock);\n"
    helper = r'''static DEFINE_MUTEX(dsi_ctrl_list_lock);

/* A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1 */
static atomic_t a52_g315_state = ATOMIC_INIT(0);
extern void a52_g315_full_snapshot(struct dsi_ctrl_hw *ctrl);
extern void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,
				     unsigned int point);

bool a52_g315_trace_active(void)
{
	return atomic_read(&a52_g315_state) == 1;
}

static bool a52_g315_armed(struct dsi_ctrl *ctrl)
{
	return ctrl && ctrl->cell_index == 0 && a52_g315_trace_active();
}

static void a52_g315_try_arm(struct dsi_ctrl *ctrl,
			     const struct mipi_dsi_msg *msg, u32 flags)
{
	const u8 *p;

	if (!ctrl || !msg || ctrl->cell_index != 0 ||
	    flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return;

	p = msg->tx_buf;
	if (p[0] != 0xf0 || p[1] != 0x5a || p[2] != 0x5a)
		return;

	if (atomic_cmpxchg(&a52_g315_state, 0, 1) != 0)
		return;

	pr_info("TG315 ARM c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x\n",
		flags, msg->flags, msg->type, (unsigned int)msg->tx_len,
		p[0], p[1], p[2]);
}

'''
    text = repl(text, anchor, helper, "controller arm helper")

    old_arm = '''\tdsi_ctrl_validate_msg_flags(dsi_ctrl, msg, flags);\n\n\tif (dsi_ctrl->dma_wait_queued)\n'''
    new_arm = '''\tdsi_ctrl_validate_msg_flags(dsi_ctrl, msg, flags);\n\ta52_g315_try_arm(dsi_ctrl, msg, *flags);\n\n\tif (dsi_ctrl->dma_wait_queued)\n'''
    text = repl(text, old_arm, new_arm, "exact target arm")

    old_wait = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n'''
    new_wait = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n\tif (a52_g315_armed(dsi_ctrl)) {\n\t\ta52_g315_launch_snapshot(&dsi_ctrl->hw, 2);\n\t\tpr_info("TG315 DONE ret=%d irq=%d\\n", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tatomic_set(&a52_g315_state, 2);\n\t}\n\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n'''
    return repl(text, old_wait, new_wait, "q2 completion snapshot")


def patch_hw(text: str) -> str:
    if MARK in text:
        return text

    anchor = '#include "sde_dbg.h"\n'
    helper = r'''#include "sde_dbg.h"

/* A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1 */
extern bool a52_g315_trace_active(void);
extern void a52_g315_phy_snapshot(unsigned int index);

void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,
			      unsigned int point)
{
	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;

	pr_info("TG315 Q%u st=%x ln=%x ck=%x in=%x\n", point,
		DSI_R32(ctrl, DSI_STATUS),
		DSI_R32(ctrl, DSI_LANE_STATUS),
		DSI_R32(ctrl, DSI_CLK_STATUS),
		DSI_R32(ctrl, DSI_INT_CTRL));
}

void a52_g315_full_snapshot(struct dsi_ctrl_hw *ctrl)
{
	u32 misc = 0xffffffff;

	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;

	if (ctrl->disp_cc_base)
		misc = DSI_DISP_CC_R32(ctrl, 0x00);

	pr_info("TG315 C0 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_CTRL),
		DSI_R32(ctrl, DSI_STATUS),
		DSI_R32(ctrl, DSI_FIFO_STATUS),
		DSI_R32(ctrl, DSI_CLK_CTRL),
		DSI_R32(ctrl, DSI_CLK_STATUS),
		DSI_R32(ctrl, DSI_LANE_STATUS));
	pr_info("TG315 C1 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_LANE_CTRL),
		DSI_R32(ctrl, DSI_LANE_SWAP_CTRL),
		DSI_R32(ctrl, DSI_LOGICAL_LANE_SWAP_CTRL),
		DSI_R32(ctrl, DSI_TRIG_CTRL),
		DSI_R32(ctrl, DSI_EXT_MUX),
		DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL));
	pr_info("TG315 C2 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_DMA_CMD_OFFSET),
		DSI_R32(ctrl, DSI_DMA_CMD_LENGTH),
		DSI_R32(ctrl, DSI_DMA_FIFO_CTRL),
		DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER),
		DSI_R32(ctrl, DSI_COMMAND_MODE_MDP_CTRL),
		DSI_R32(ctrl, DSI_COMMAND_MODE_MDP_DCS_CMD_CTRL));
	pr_info("TG315 C3 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_ACK_ERR_STATUS),
		DSI_R32(ctrl, DSI_TIMEOUT_STATUS),
		DSI_R32(ctrl, DSI_DLN0_PHY_ERR),
		DSI_R32(ctrl, DSI_INT_CTRL),
		DSI_R32(ctrl, DSI_ERR_INT_MASK0),
		DSI_R32(ctrl, DSI_DEBUG_CTRL));
	pr_info("TG315 C4 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_LP_TIMER_CTRL),
		DSI_R32(ctrl, DSI_HS_TIMER_CTRL),
		DSI_R32(ctrl, DSI_CLKOUT_TIMING_CTRL),
		DSI_R32(ctrl, DSI_EOT_PACKET_CTRL),
		DSI_R32(ctrl, DSI_PHY_SW_RESET),
		DSI_R32(ctrl, DSI_SOFT_RESET));
	pr_info("TG315 C5 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_DYNAMIC_REFRESH_CTRL),
		DSI_R32(ctrl, DSI_DYNAMIC_REFRESH_STATUS),
		DSI_R32(ctrl, DSI_DESKEW_CTRL),
		DSI_R32(ctrl, DSI_DESKEW_DELAY_CTRL),
		DSI_R32(ctrl, DSI_SECURE_DISPLAY_STATUS),
		DSI_R32(ctrl, DSI_SPLIT_LINK));
	pr_info("TG315 C6 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_CPHY_MODE_CTRL),
		DSI_R32(ctrl, DSI_DEBUG_BUS_CTL),
		DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS),
		DSI_R32(ctrl, DSI_READ_BACK_DISABLE_STATUS),
		DSI_R32(ctrl, DSI_DSI_TIMING_DB_MODE),
		DSI_R32(ctrl, DSI_AXI2AHB_CTRL));
	pr_info("TG315 C7 %x %x %x %x %x %x\n",
		DSI_R32(ctrl, DSI_VIDEO_MODE_CTRL),
		DSI_R32(ctrl, DSI_VIDEO_MODE_DATA_CTRL),
		DSI_R32(ctrl, DSI_DMA_NULL_PACKET_DATA),
		DSI_R32(ctrl, DSI_COMMAND_MODE_MDP_IDLE_CTRL),
		DSI_R32(ctrl, DSI_COMMAND_MODE_MDP_CTRL2),
		DSI_R32(ctrl, DSI_COMMAND_MODE_NULL_INSERTION_CTRL));
	pr_info("TG315 M %x\n", misc);

	a52_g315_phy_snapshot(ctrl->index);
}

'''
    text = repl(text, anchor, helper, "HW full-prestate helper")

    old_kick = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n\n/**\n * kickoff_fifo_command()'''
    new_kick = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_g315_full_snapshot(ctrl);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_g315_launch_snapshot(ctrl, 1);\n\t}\n}\n\n/**\n * kickoff_fifo_command()'''
    text = repl(text, old_kick, new_kick, "memory q0/q1")

    old_trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n'''
    new_trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_g315_full_snapshot(ctrl);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_g315_launch_snapshot(ctrl, 1);\n}\n'''
    return repl(text, old_trig, new_trig, "deferred q0/q1")


def validate(ctrl: str, hw: str, phy: str) -> None:
    alltxt = ctrl + hw + phy
    need = [
        MARK,
        "TG315 ARM c=0",
        "TG315 C0 %x %x %x %x %x %x",
        "TG315 C7 %x %x %x %x %x %x",
        "TG315 M %x",
        "TG315 P0 %x %x %x %x %x %x",
        "TG315 P4 %x %x %x %x %x",
        "TG315 T0 %x %x %x %x %x %x",
        "TG315 T1 %x %x %x %x %x %x",
        "TG315 L%uA",
        "TG315 L%uB",
        "TG315 Q%u st=%x ln=%x ck=%x in=%x",
        "TG315 DONE ret=%d irq=%d",
        "atomic_set(&a52_g315_state, 2);",
    ]
    for token in need:
        if token not in alltxt:
            raise SystemExit("Phase315G missing token: " + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    paths = [ns.root / CTRL, ns.root / HW, ns.root / PHY]
    for p in paths:
        if not p.is_file():
            raise SystemExit("Phase315G missing source: " + str(p))

    if not ns.check_only:
        paths[0].write_text(patch_ctrl(paths[0].read_text()))
        paths[1].write_text(patch_hw(paths[1].read_text()))
        paths[2].write_text(patch_phy(paths[2].read_text()))

    validate(*(p.read_text() for p in paths))
    print("Phase315G Golden full first-F0 prestate reference: PASS")


if __name__ == "__main__":
    main()
