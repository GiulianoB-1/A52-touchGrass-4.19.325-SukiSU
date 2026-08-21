#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REL = Path("techpack/display/msm/dsi/dsi_ctrl.c")
MARK = "A52_GOLDEN_DMA_DONE_REFERENCE_V1"


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text

    text = once(
        text,
        '#include "dsi_ctrl_hw.h"\n#include "dsi_clk.h"\n',
        '#include "dsi_ctrl_hw.h"\n#include "dsi_ctrl_reg.h"\n#include "dsi_hw.h"\n#include "dsi_clk.h"\n',
        "read-only register headers",
    )

    helper = r'''/* A52_GOLDEN_DMA_DONE_REFERENCE_V1
 * Single-shot, read-only reference recorder for the proven-working
 * TouchGrass/Golden-FDR DSI DMA path. It arms only on the exact command
 * signature observed at the GKI DMA_DONE failure frontier:
 *   ctrl=0, incoming ctrl flags=0x20, msg flags=0x8, type=0x29, tx_len=3.
 *
 * IMPORTANT: this block performs only RAM stores, clk_get_rate(), MMIO reads,
 * and bounded pr_info() after the normal completion path. It adds no DSI
 * writes, triggers, waits, resets, recovery, clock programming, panel packets,
 * or brightness changes.
 */
struct a52_gdm_snapshot {
	u32 in_flags, msg_flags, msg_type, tx_len;
	u8 payload[3];
	u32 selected_flags, hw_flags, panel_mode;
	u32 power_state, host_initialized, ctrl_state, cmd_state, vid_state;
	u32 clk_target_b, clk_target_p, clk_target_i, clk_target_e;
	unsigned long clk_actual_b, clk_actual_p, clk_actual_i, clk_actual_e;
	u32 arm0_int, arm0_status, arm0_lane, arm0_clk;
	int arm0_irq;
	u32 arm1_int, arm1_status, arm1_lane, arm1_clk;
	int arm1_irq;
	u32 pre_dma_ctrl, pre_dma_off, pre_dma_len, pre_fifo_ctrl;
	u32 pre_trig_ctrl, pre_sw_trig, pre_clk_ctrl;
	u32 post_status, post_fifo, post_lane, post_clk, post_tpg_fifo, post_int;
	u32 isr_seen, isr_status, isr_int, isr_err_lo, isr_err_hi;
	int isr_irq0;
	int wait_ret, wait_irq;
	u32 wait_int, wait_status;
	u32 final_status, final_fifo, final_lane, final_clk;
	u32 final_ack, final_timeout, final_phy, final_ctrl, final_tpg_fifo;
};

static atomic_t a52_gdm_state = ATOMIC_INIT(0); /* 0 idle, 1 armed, 2 dumped */
static struct a52_gdm_snapshot a52_gdm;

static bool a52_gdm_armed(struct dsi_ctrl *dsi_ctrl)
{
	return dsi_ctrl && dsi_ctrl->cell_index == 0 &&
		atomic_read(&a52_gdm_state) == 1;
}

static unsigned long a52_gdm_clk_rate(struct clk *clk)
{
	return clk ? clk_get_rate(clk) : 0;
}

static void a52_gdm_arm(struct dsi_ctrl *dsi_ctrl,
		const struct mipi_dsi_msg *msg, u32 flags)
{
	const u8 *p;

	if (!dsi_ctrl || !msg || dsi_ctrl->cell_index != 0 ||
	    flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return;

	if (atomic_cmpxchg(&a52_gdm_state, 0, 1) != 0)
		return;

	memset(&a52_gdm, 0, sizeof(a52_gdm));
	p = msg->tx_buf;
	a52_gdm.in_flags = flags;
	a52_gdm.msg_flags = msg->flags;
	a52_gdm.msg_type = msg->type;
	a52_gdm.tx_len = msg->tx_len;
	a52_gdm.payload[0] = p[0];
	a52_gdm.payload[1] = p[1];
	a52_gdm.payload[2] = p[2];
}

static void a52_gdm_capture_selected(struct dsi_ctrl *dsi_ctrl, u32 flags)
{
	struct dsi_ctrl_state_info *s;

	if (!a52_gdm_armed(dsi_ctrl))
		return;

	s = &dsi_ctrl->current_state;
	a52_gdm.selected_flags = flags;
	a52_gdm.panel_mode = dsi_ctrl->host_config.panel_mode;
	a52_gdm.power_state = s->power_state;
	a52_gdm.host_initialized = s->host_initialized;
	a52_gdm.ctrl_state = s->controller_state;
	a52_gdm.cmd_state = s->cmd_engine_state;
	a52_gdm.vid_state = s->vid_engine_state;

	a52_gdm.clk_target_b = dsi_ctrl->clk_freq.byte_clk_rate;
	a52_gdm.clk_target_p = dsi_ctrl->clk_freq.pix_clk_rate;
	a52_gdm.clk_target_i = dsi_ctrl->clk_freq.byte_intf_clk_rate;
	a52_gdm.clk_target_e = dsi_ctrl->clk_freq.esc_clk_rate;
	a52_gdm.clk_actual_b = a52_gdm_clk_rate(dsi_ctrl->clk_info.hs_link_clks.byte_clk);
	a52_gdm.clk_actual_p = a52_gdm_clk_rate(dsi_ctrl->clk_info.hs_link_clks.pixel_clk);
	a52_gdm.clk_actual_i = a52_gdm_clk_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk);
	a52_gdm.clk_actual_e = a52_gdm_clk_rate(dsi_ctrl->clk_info.lp_link_clks.esc_clk);
}

static void a52_gdm_capture_hw_flags(struct dsi_ctrl *dsi_ctrl, u32 hw_flags)
{
	if (a52_gdm_armed(dsi_ctrl))
		a52_gdm.hw_flags = hw_flags;
}

static void a52_gdm_capture_arm0(struct dsi_ctrl *dsi_ctrl)
{
	if (!a52_gdm_armed(dsi_ctrl))
		return;
	a52_gdm.arm0_irq = atomic_read(&dsi_ctrl->dma_irq_trig);
	a52_gdm.arm0_int = DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL);
	a52_gdm.arm0_status = DSI_R32(&dsi_ctrl->hw, DSI_STATUS);
	a52_gdm.arm0_lane = DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS);
	a52_gdm.arm0_clk = DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS);
}

static void a52_gdm_capture_arm1(struct dsi_ctrl *dsi_ctrl)
{
	if (!a52_gdm_armed(dsi_ctrl))
		return;
	a52_gdm.arm1_irq = atomic_read(&dsi_ctrl->dma_irq_trig);
	a52_gdm.arm1_int = DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL);
	a52_gdm.arm1_status = DSI_R32(&dsi_ctrl->hw, DSI_STATUS);
	a52_gdm.arm1_lane = DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS);
	a52_gdm.arm1_clk = DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS);
}

static void a52_gdm_capture_pre(struct dsi_ctrl *dsi_ctrl)
{
	if (!a52_gdm_armed(dsi_ctrl))
		return;
	a52_gdm.pre_dma_ctrl = DSI_R32(&dsi_ctrl->hw, DSI_COMMAND_MODE_DMA_CTRL);
	a52_gdm.pre_dma_off = DSI_R32(&dsi_ctrl->hw, DSI_DMA_CMD_OFFSET);
	a52_gdm.pre_dma_len = DSI_R32(&dsi_ctrl->hw, DSI_DMA_CMD_LENGTH);
	a52_gdm.pre_fifo_ctrl = DSI_R32(&dsi_ctrl->hw, DSI_DMA_FIFO_CTRL);
	a52_gdm.pre_trig_ctrl = DSI_R32(&dsi_ctrl->hw, DSI_TRIG_CTRL);
	a52_gdm.pre_sw_trig = DSI_R32(&dsi_ctrl->hw, DSI_CMD_MODE_DMA_SW_TRIGGER);
	a52_gdm.pre_clk_ctrl = DSI_R32(&dsi_ctrl->hw, DSI_CLK_CTRL);
}

static void a52_gdm_capture_post(struct dsi_ctrl *dsi_ctrl)
{
	if (!a52_gdm_armed(dsi_ctrl))
		return;
	a52_gdm.post_status = DSI_R32(&dsi_ctrl->hw, DSI_STATUS);
	a52_gdm.post_fifo = DSI_R32(&dsi_ctrl->hw, DSI_FIFO_STATUS);
	a52_gdm.post_lane = DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS);
	a52_gdm.post_clk = DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS);
	a52_gdm.post_tpg_fifo = DSI_R32(&dsi_ctrl->hw, DSI_TPG_DMA_FIFO_STATUS);
	a52_gdm.post_int = DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL);
}

static void a52_gdm_capture_wait_final(struct dsi_ctrl *dsi_ctrl, int ret)
{
	if (!a52_gdm_armed(dsi_ctrl))
		return;
	a52_gdm.wait_ret = ret;
	a52_gdm.wait_irq = atomic_read(&dsi_ctrl->dma_irq_trig);
	a52_gdm.wait_int = DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL);
	a52_gdm.wait_status = DSI_R32(&dsi_ctrl->hw, DSI_STATUS);
	a52_gdm.final_status = a52_gdm.wait_status;
	a52_gdm.final_fifo = DSI_R32(&dsi_ctrl->hw, DSI_FIFO_STATUS);
	a52_gdm.final_lane = DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS);
	a52_gdm.final_clk = DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS);
	a52_gdm.final_ack = DSI_R32(&dsi_ctrl->hw, DSI_ACK_ERR_STATUS);
	a52_gdm.final_timeout = DSI_R32(&dsi_ctrl->hw, DSI_TIMEOUT_STATUS);
	a52_gdm.final_phy = DSI_R32(&dsi_ctrl->hw, DSI_DLN0_PHY_ERR);
	a52_gdm.final_ctrl = DSI_R32(&dsi_ctrl->hw, DSI_CTRL);
	a52_gdm.final_tpg_fifo = DSI_R32(&dsi_ctrl->hw, DSI_TPG_DMA_FIFO_STATUS);
}

static void a52_gdm_dump(struct dsi_ctrl *dsi_ctrl)
{
	u32 success;

	if (!a52_gdm_armed(dsi_ctrl))
		return;

	success = READ_ONCE(a52_gdm.isr_seen) && a52_gdm.wait_irq;
	pr_info("GDM S00 c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x\n",
		a52_gdm.in_flags, a52_gdm.msg_flags, a52_gdm.msg_type,
		a52_gdm.tx_len, a52_gdm.payload[0], a52_gdm.payload[1],
		a52_gdm.payload[2]);
	pr_info("GDM S01 sel=%x hw=%x pm=%u pwr=%u hi=%u ce=%u me=%u ve=%u\n",
		a52_gdm.selected_flags, a52_gdm.hw_flags, a52_gdm.panel_mode,
		a52_gdm.power_state, a52_gdm.host_initialized, a52_gdm.ctrl_state,
		a52_gdm.cmd_state, a52_gdm.vid_state);
	pr_info("GDM S02 ct=%u,%u,%u,%u ca=%lu,%lu,%lu,%lu\n",
		a52_gdm.clk_target_b, a52_gdm.clk_target_p,
		a52_gdm.clk_target_i, a52_gdm.clk_target_e,
		a52_gdm.clk_actual_b, a52_gdm.clk_actual_p,
		a52_gdm.clk_actual_i, a52_gdm.clk_actual_e);
	pr_info("GDM S03 a0 irq=%d int=%08x st=%08x ln=%08x ck=%08x\n",
		a52_gdm.arm0_irq, a52_gdm.arm0_int, a52_gdm.arm0_status,
		a52_gdm.arm0_lane, a52_gdm.arm0_clk);
	pr_info("GDM S04 a1 irq=%d int=%08x st=%08x ln=%08x ck=%08x\n",
		a52_gdm.arm1_irq, a52_gdm.arm1_int, a52_gdm.arm1_status,
		a52_gdm.arm1_lane, a52_gdm.arm1_clk);
	pr_info("GDM S05 pre dc=%08x off=%08x len=%08x fc=%08x tr=%08x sw=%08x cc=%08x\n",
		a52_gdm.pre_dma_ctrl, a52_gdm.pre_dma_off, a52_gdm.pre_dma_len,
		a52_gdm.pre_fifo_ctrl, a52_gdm.pre_trig_ctrl, a52_gdm.pre_sw_trig,
		a52_gdm.pre_clk_ctrl);
	pr_info("GDM S06 post st=%08x fs=%08x ln=%08x ck=%08x tg=%08x int=%08x\n",
		a52_gdm.post_status, a52_gdm.post_fifo, a52_gdm.post_lane,
		a52_gdm.post_clk, a52_gdm.post_tpg_fifo, a52_gdm.post_int);
	pr_info("GDM S07 isr seen=%u st=%08x int=%08x er=%08x:%08x irq0=%d\n",
		READ_ONCE(a52_gdm.isr_seen), a52_gdm.isr_status, a52_gdm.isr_int,
		a52_gdm.isr_err_hi, a52_gdm.isr_err_lo, a52_gdm.isr_irq0);
	pr_info("GDM S08 wait ret=%d irq=%d int=%08x st=%08x\n",
		a52_gdm.wait_ret, a52_gdm.wait_irq, a52_gdm.wait_int,
		a52_gdm.wait_status);
	pr_info("GDM S09 fin st=%08x fs=%08x ln=%08x ck=%08x ack=%08x to=%08x phy=%08x ctl=%08x tg=%08x\n",
		a52_gdm.final_status, a52_gdm.final_fifo, a52_gdm.final_lane,
		a52_gdm.final_clk, a52_gdm.final_ack, a52_gdm.final_timeout,
		a52_gdm.final_phy, a52_gdm.final_ctrl, a52_gdm.final_tpg_fifo);
	pr_info("GDM DONE success=%u target=0/8/20/29/3\n", success);
	atomic_set(&a52_gdm_state, 2);
}

'''
    text = once(
        text,
        '#define TICKS_IN_MICRO_SECOND    1000000\n\n',
        '#define TICKS_IN_MICRO_SECOND    1000000\n\n' + helper,
        "helper block",
    )

    text = once(
        text,
        '\t/* Select the tx mode to transfer the command */\n\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n',
        '\t/* Arm only on the exact GKI-failing command signature, before Golden chooses tx mode. */\n'
        '\ta52_gdm_arm(dsi_ctrl, msg, *flags);\n\n'
        '\t/* Select the tx mode to transfer the command */\n\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n',
        "target arm",
    )

    text = once(
        text,
        '\tdsi_ctrl_validate_msg_flags(dsi_ctrl, msg, flags);\n\n\tif (dsi_ctrl->dma_wait_queued)\n',
        '\tdsi_ctrl_validate_msg_flags(dsi_ctrl, msg, flags);\n'
        '\ta52_gdm_capture_selected(dsi_ctrl, *flags);\n\n'
        '\tif (dsi_ctrl->dma_wait_queued)\n',
        "selected path capture",
    )

    text = once(
        text,
        '\tif ((msg->flags & MIPI_DSI_MSG_LASTCOMMAND))\n\t\thw_flags |= DSI_CTRL_CMD_LAST_COMMAND;\n\n\tif (flags & DSI_CTRL_CMD_DEFER_TRIGGER) {\n',
        '\tif ((msg->flags & MIPI_DSI_MSG_LASTCOMMAND))\n\t\thw_flags |= DSI_CTRL_CMD_LAST_COMMAND;\n\n'
        '\ta52_gdm_capture_hw_flags(dsi_ctrl, hw_flags);\n\n'
        '\tif (flags & DSI_CTRL_CMD_DEFER_TRIGGER) {\n',
        "hw flags capture",
    )

    text = once(
        text,
        '\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\n\t\tdsi_ctrl_enable_status_interrupt(dsi_ctrl,\n\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE, NULL);\n\t\treinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);\n\n\t\tif (flags & DSI_CTRL_CMD_FETCH_MEMORY) {\n',
        '\t\ta52_gdm_capture_arm0(dsi_ctrl);\n'
        '\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\n\t\tdsi_ctrl_enable_status_interrupt(dsi_ctrl,\n\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE, NULL);\n\t\treinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);\n'
        '\t\ta52_gdm_capture_arm1(dsi_ctrl);\n'
        '\t\ta52_gdm_capture_pre(dsi_ctrl);\n\n'
        '\t\tif (flags & DSI_CTRL_CMD_FETCH_MEMORY) {\n',
        "DMA arm and pre-kick captures",
    )

    text = once(
        text,
        '\t\t} else if (flags & DSI_CTRL_CMD_FIFO_STORE) {\n\t\t\tdsi_hw_ops.kickoff_fifo_command(&dsi_ctrl->hw,\n\t\t\t\t\t\t\t      cmd,\n\t\t\t\t\t\t\t      hw_flags);\n\t\t}\n\t\tif (flags & DSI_CTRL_CMD_ASYNC_WAIT) {\n',
        '\t\t} else if (flags & DSI_CTRL_CMD_FIFO_STORE) {\n\t\t\tdsi_hw_ops.kickoff_fifo_command(&dsi_ctrl->hw,\n\t\t\t\t\t\t\t      cmd,\n\t\t\t\t\t\t\t      hw_flags);\n\t\t}\n'
        '\t\ta52_gdm_capture_post(dsi_ctrl);\n'
        '\t\tif (flags & DSI_CTRL_CMD_ASYNC_WAIT) {\n',
        "post-kick capture",
    )

    text = once(
        text,
        '\tif (status & DSI_CMD_MODE_DMA_DONE) {\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n',
        '\tif (status & DSI_CMD_MODE_DMA_DONE) {\n'
        '\t\tif (a52_gdm_armed(dsi_ctrl)) {\n'
        '\t\t\ta52_gdm.isr_status = status;\n'
        '\t\t\ta52_gdm.isr_int = DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL);\n'
        '\t\t\ta52_gdm.isr_err_lo = (u32)errors;\n'
        '\t\t\ta52_gdm.isr_err_hi = (u32)(errors >> 32);\n'
        '\t\t\ta52_gdm.isr_irq0 = atomic_read(&dsi_ctrl->dma_irq_trig);\n'
        '\t\t\tWRITE_ONCE(a52_gdm.isr_seen, 1);\n'
        '\t\t}\n'
        '\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n',
        "DMA_DONE ISR capture",
    )

    text = once(
        text,
        'done:\n\tdsi_ctrl->dma_wait_queued = false;\n}\n\nstatic int dsi_ctrl_check_state',
        'done:\n'
        '\tif (a52_gdm_armed(dsi_ctrl)) {\n'
        '\t\ta52_gdm_capture_wait_final(dsi_ctrl, ret);\n'
        '\t\ta52_gdm_dump(dsi_ctrl);\n'
        '\t}\n'
        '\tdsi_ctrl->dma_wait_queued = false;\n'
        '}\n\nstatic int dsi_ctrl_check_state',
        "completion dump",
    )

    return text


def audit(text: str) -> None:
    required = [
        MARK,
        "GDM S00",
        "GDM S01",
        "GDM S02",
        "GDM S03",
        "GDM S04",
        "GDM S05",
        "GDM S06",
        "GDM S07",
        "GDM S08",
        "GDM S09",
        "GDM DONE",
        "a52_gdm_arm(dsi_ctrl, msg, *flags);",
        "a52_gdm_capture_arm0(dsi_ctrl);",
        "a52_gdm_capture_arm1(dsi_ctrl);",
        "a52_gdm_capture_pre(dsi_ctrl);",
        "a52_gdm_capture_post(dsi_ctrl);",
        "WRITE_ONCE(a52_gdm.isr_seen, 1);",
        "a52_gdm_capture_wait_final(dsi_ctrl, ret);",
    ]
    for token in required:
        if token not in text:
            raise SystemExit(f"missing recorder token: {token}")

    block = text[text.index("/* " + MARK):text.index("#define DSI_CTRL_DEBUG")]
    forbidden = [
        "DSI_W32(", "writel(", "writel_relaxed(", "clk_set_rate(",
        "wait_for_completion_timeout(", "trigger_command_dma(",
        "reset_cmd_fifo(", "soft_reset(", "msleep(", "usleep_range(",
    ]
    for token in forbidden:
        if token in block:
            raise SystemExit(f"recorder helper contains forbidden behavior primitive: {token}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    path = args.root / REL
    text = path.read_text(encoding="utf-8")
    if args.check_only:
        audit(text)
        print("Golden DMA_DONE reference recorder source audit: PASS")
        return 0

    new = patch(text)
    audit(new)
    if new == text and MARK not in text:
        raise SystemExit("patch produced no change")
    path.write_text(new, encoding="utf-8")
    print("Golden DMA_DONE reference recorder applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
