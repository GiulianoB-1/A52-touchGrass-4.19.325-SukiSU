#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
MARK_CTRL = 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1'
MARK_HW = 'A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1'

FETCH_MEMORY_CLEAR = '*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY'


def assert_no_fetch_memory_fifo_reroute(text: str) -> None:
    """Allow only the stock secure-session FETCH_MEMORY -> FIFO safeguard."""
    if text.count(FETCH_MEMORY_CLEAR) != 1:
        raise SystemExit(
            'Phase293 refuses later behavioral lineage: unexpected '
            f'FETCH_MEMORY clear count ({text.count(FETCH_MEMORY_CLEAR)})'
        )

    fn_start = text.find('void dsi_message_setup_tx_mode(')
    fn_end = text.find('\nint dsi_message_validate_tx_mode(', fn_start)
    if fn_start < 0 or fn_end < 0:
        raise SystemExit('Phase293 stock dsi_message_setup_tx_mode boundary missing')
    fn = text[fn_start:fn_end]
    secure = fn.find('if (dsi_ctrl->secure_mode) {')
    clear = fn.find(FETCH_MEMORY_CLEAR)
    fifo = fn.find('*flags |= DSI_CTRL_CMD_FIFO_STORE', clear)
    ret = fn.find('return;', fifo)
    if not (0 <= secure < clear < fifo < ret):
        raise SystemExit(
            'Phase293 refuses later behavioral lineage: stock secure-mode '
            'FETCH_MEMORY -> FIFO safeguard moved or altered'
        )


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase293 {label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


def behavioral_counts(text: str) -> dict[str, int]:
    return {k: text.count(k) for k in [
        'DSI_W32(', 'writel(', 'writel_relaxed(', 'clk_set_rate(',
        'wait_for_completion_timeout(', 'msleep(', 'usleep_range(',
        'DSI_CTRL_CMD_FIFO_STORE',
    ]}


def patch_ctrl(text: str) -> str:
    if MARK_CTRL in text:
        return text
    if 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' not in text:
        raise SystemExit('Phase293 requires exact Phase280 retained-timeout DSI source')
    for forbidden in [
        'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1',
        'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1',
        'A52_PHASE292_DSI_CHAIN_TAPS_V1',
    ]:
        if forbidden in text:
            raise SystemExit('Phase293 refuses later behavioral lineage: ' + forbidden)
    assert_no_fetch_memory_fifo_reroute(text)

    text = one(text, '#include "dsi_ctrl_hw.h"\n',
               '#include "dsi_ctrl_hw.h"\n#include "dsi_ctrl_reg.h"\n#include "dsi_hw.h"\n',
               'read-only register headers')

    anchor = 'static DEFINE_MUTEX(dsi_ctrl_list_lock);\n\n'
    helper = r'''static DEFINE_MUTEX(dsi_ctrl_list_lock);

/* A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1
 * Passive mirror of the hardware-validated Golden GDM recorder on the clean
 * Phase280 GKI memory-fetch path. The only operations added here are RAM
 * atomics, MMIO reads and writes to the existing A52 flight recorder. No DSI
 * register write, command flag mutation, wait, reset, recovery, clock change,
 * panel packet or brightness operation is added.
 */
static atomic_t a52_p293_gdm_state = ATOMIC_INIT(0); /* 0 idle, 1 exact target */

static bool a52_p293_gdm_armed(struct dsi_ctrl *dsi_ctrl)
{
	return dsi_ctrl && dsi_ctrl->cell_index == 0 &&
		atomic_read(&a52_p293_gdm_state) == 1;
}

bool a52_p293_gdm_trace_active(void)
{
	return atomic_read(&a52_p293_gdm_state) == 1;
}

static void a52_p293_gdm_try_arm(struct dsi_ctrl *dsi_ctrl,
		const struct mipi_dsi_msg *msg, u32 *flags)
{
	const u8 *p;

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
	    *flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return;
	if (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)
		return;
	p = msg->tx_buf;
	a52_ackfr_record("GDM S00 c=0 in=%x mf=%x t=%x l=%u",
		*flags, msg->flags, msg->type, (unsigned int)msg->tx_len);
	a52_ackfr_record("GDM S00p p=%02x%02x%02x", p[0], p[1], p[2]);
}

static void a52_p293_gdm_arm_snapshot(struct dsi_ctrl *dsi_ctrl, unsigned int stage)
{
	if (!a52_p293_gdm_armed(dsi_ctrl) || !dsi_ctrl->hw.base)
		return;
	a52_ackfr_record(stage ? "GDM S04 irq=%d in=%x st=%x" : "GDM S03 irq=%d in=%x st=%x",
		atomic_read(&dsi_ctrl->dma_irq_trig),
		DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
		DSI_R32(&dsi_ctrl->hw, DSI_STATUS));
	a52_ackfr_record(stage ? "GDM S04b ln=%x ck=%x" : "GDM S03b ln=%x ck=%x",
		DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
		DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS));
}

'''
    text = one(text, anchor, helper, 'helper insertion')

    target = '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D M s=0 f=%x mt=%u l=%u", flags ? *flags : 0, (unsigned int)msg->type, (unsigned int)msg->tx_len);\n'''
    text = one(text, target, target + '\ta52_p293_gdm_try_arm(dsi_ctrl, msg, flags);\n', 'exact target arm')

    hwflags = '''\thw_flags |= (flags & DSI_CTRL_CMD_DEFER_TRIGGER) ?\n\t\t\tDSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER : 0;\n'''
    state = r'''	if (a52_p293_gdm_armed(dsi_ctrl)) {
		a52_ackfr_record("GDM S01 sel=%x hw=%x pm=%u pwr=%u",
			flags, hw_flags, dsi_ctrl->host_config.panel_mode,
			dsi_ctrl->current_state.power_state);
		a52_ackfr_record("GDM S01b hi=%u ce=%u me=%u ve=%u",
			dsi_ctrl->current_state.host_initialized,
			dsi_ctrl->current_state.controller_state,
			dsi_ctrl->current_state.cmd_engine_state,
			dsi_ctrl->current_state.vid_engine_state);
		a52_ackfr_record("GDM S02 ct=%u,%u,%u,%u ca=na",
			dsi_ctrl->clk_freq.byte_clk_rate,
			dsi_ctrl->clk_freq.pix_clk_rate,
			dsi_ctrl->clk_freq.byte_intf_clk_rate,
			dsi_ctrl->clk_freq.esc_clk_rate);
	}
'''
    text = one(text, hwflags, hwflags + state, 'selected flags/state/target clocks')

    irq_arm = '''\t\tdsi_ctrl_mask_overflow(dsi_ctrl, true);\n\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\n\t\tdsi_ctrl_enable_status_interrupt(dsi_ctrl,\n\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE, NULL);\n\t\treinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);\n'''
    irq_arm_rec = '''\t\tdsi_ctrl_mask_overflow(dsi_ctrl, true);\n\n\t\ta52_p293_gdm_arm_snapshot(dsi_ctrl, 0);\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\n\t\tdsi_ctrl_enable_status_interrupt(dsi_ctrl,\n\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE, NULL);\n\t\treinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);\n\t\ta52_p293_gdm_arm_snapshot(dsi_ctrl, 1);\n'''
    text = one(text, irq_arm, irq_arm_rec, 'command-DMA IRQ-arm snapshots')

    wait = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n'''
    wait_rec = r'''	if (a52_p293_gdm_armed(dsi_ctrl)) {
		a52_ackfr_record("GDM S08 ret=%d irq=%d in=%x st=%x", ret,
			atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			DSI_R32(&dsi_ctrl->hw, DSI_STATUS));
	}
'''
    text = one(text, wait, wait + wait_rec, 'completion wait result')

    isr = '''\tif (status & DSI_CMD_MODE_DMA_DONE) {\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n'''
    isr_new = r'''	if (status & DSI_CMD_MODE_DMA_DONE) {
		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_ackfr_record("GDM S07 seen=1 st=%x in=%x irq0=%d", status,
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
				atomic_read(&dsi_ctrl->dma_irq_trig));
			a52_ackfr_record("GDM S07e ack=%x to=%x",
				DSI_R32(&dsi_ctrl->hw, DSI_ACK_ERR_STATUS),
				DSI_R32(&dsi_ctrl->hw, DSI_TIMEOUT_STATUS));
		}
		atomic_set(&dsi_ctrl->dma_irq_trig, 1);
'''
    text = one(text, isr, isr_new, 'DMA_DONE ISR')

    retain = '''\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n\t\t}\n'''
    final = r'''		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_ackfr_record("GDM S09 st=%x fs=%x ln=%x ck=%x",
				DSI_R32(&dsi_ctrl->hw, DSI_STATUS),
				DSI_R32(&dsi_ctrl->hw, DSI_FIFO_STATUS),
				DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
				DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS));
			a52_ackfr_record("GDM S09e ack=%x to=%x phy=%x ctl=%x",
				DSI_R32(&dsi_ctrl->hw, DSI_ACK_ERR_STATUS),
				DSI_R32(&dsi_ctrl->hw, DSI_TIMEOUT_STATUS),
				DSI_R32(&dsi_ctrl->hw, DSI_DLN0_PHY_ERR),
				DSI_R32(&dsi_ctrl->hw, DSI_CTRL));
			a52_ackfr_record("GDM DONE success=0 target=0/8/20/29/3");
		}
'''
    text = one(text, retain, final + retain, 'final snapshot before Phase280 latch')
    return text


def patch_hw(text: str) -> str:
    if MARK_HW in text:
        return text
    for token in [
        'void dsi_ctrl_hw_cmn_kickoff_command(',
        'void dsi_ctrl_hw_cmn_trigger_command_dma(',
        'DSI_W32(ctrl, DSI_DMA_CMD_OFFSET, cmd->offset);',
        'DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);',
    ]:
        if token not in text:
            raise SystemExit('Phase293 HW prerequisite missing: ' + token)

    inc = '#include "sde_dbg.h"\n'
    decl = '''#include "sde_dbg.h"\n\n/* A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1 */\nextern bool a52_p293_gdm_trace_active(void);\nextern void a52_ackfr_record(const char *fmt, ...);\n'''
    text = one(text, inc, decl, 'HW declarations')

    old = '''\t/* wait for writes to complete before kick off */\n\twmb();\n\n\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n'''
    new = r'''	/* wait for writes to complete before kick off */
	wmb();

	if (a52_p293_gdm_trace_active()) {
		a52_ackfr_record("GDM S05 dc=%x off=%x len=%x fc=%x",
			DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),
			DSI_R32(ctrl, DSI_DMA_CMD_OFFSET),
			DSI_R32(ctrl, DSI_DMA_CMD_LENGTH),
			DSI_R32(ctrl, DSI_DMA_FIFO_CTRL));
		a52_ackfr_record("GDM S05b tr=%x sw=%x cc=%x",
			DSI_R32(ctrl, DSI_TRIG_CTRL),
			DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER),
			DSI_R32(ctrl, DSI_CLK_CTRL));
	}

	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		if (a52_p293_gdm_trace_active()) {
			a52_ackfr_record("GDM S06 st=%x fs=%x ln=%x ck=%x",
				DSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_FIFO_STATUS),
				DSI_R32(ctrl, DSI_LANE_STATUS), DSI_R32(ctrl, DSI_CLK_STATUS));
			a52_ackfr_record("GDM S06b tg=%x in=%x",
				DSI_R32(ctrl, DSI_TPG_DMA_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));
		}
	}
}
'''
    text = one(text, old, new, 'memory kickoff snapshots')

    trig = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n'''
    trig_new = r'''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
	DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
	if (a52_p293_gdm_trace_active()) {
		a52_ackfr_record("GDM S06 st=%x fs=%x ln=%x ck=%x",
			DSI_R32(ctrl, DSI_STATUS), DSI_R32(ctrl, DSI_FIFO_STATUS),
			DSI_R32(ctrl, DSI_LANE_STATUS), DSI_R32(ctrl, DSI_CLK_STATUS));
		a52_ackfr_record("GDM S06b tg=%x in=%x",
			DSI_R32(ctrl, DSI_TPG_DMA_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));
	}
}
'''
    text = one(text, trig, trig_new, 'deferred trigger post snapshot')
    return text


def validate(ctrl: str, hw: str) -> None:
    for token in [
        MARK_CTRL, 'GDM S00 c=0 in=%x mf=%x t=%x l=%u', 'GDM S00p p=%02x%02x%02x',
        'GDM S01 sel=%x hw=%x pm=%u pwr=%u', 'GDM S02 ct=%u,%u,%u,%u ca=na',
        'GDM S03 irq=%d in=%x st=%x', 'GDM S04 irq=%d in=%x st=%x',
        'GDM S07 seen=1 st=%x in=%x irq0=%d', 'GDM S08 ret=%d irq=%d in=%x st=%x',
        'GDM S09 st=%x fs=%x ln=%x ck=%x', 'GDM DONE success=0 target=0/8/20/29/3',
        'P276 280Z q=2', 'a52_ackfr_retain_timeout_snapshot();',
    ]:
        if token not in ctrl:
            raise SystemExit('Phase293 dsi_ctrl validation missing: ' + token)
    for token in [MARK_HW, 'GDM S05 dc=%x off=%x len=%x fc=%x',
                  'GDM S05b tr=%x sw=%x cc=%x', 'GDM S06 st=%x fs=%x ln=%x ck=%x',
                  'GDM S06b tg=%x in=%x']:
        if token not in hw:
            raise SystemExit('Phase293 HW validation missing: ' + token)
    assert_no_fetch_memory_fifo_reroute(ctrl)
    if ctrl.index('GDM S09 st=%x fs=%x ln=%x ck=%x') > ctrl.index('P276 280Z q=2'):
        raise SystemExit('Phase293 final GDM snapshot is after Phase280 retention latch')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    cp, hp = args.root / CTRL, args.root / HW
    if not cp.is_file() or not hp.is_file():
        raise SystemExit('Phase293 reconstructed DSI sources missing')
    c0, h0 = cp.read_text(), hp.read_text()
    bc, bh = behavioral_counts(c0), behavioral_counts(h0)
    if not args.check_only:
        cp.write_text(patch_ctrl(c0))
        hp.write_text(patch_hw(h0))
    c1, h1 = cp.read_text(), hp.read_text()
    validate(c1, h1)
    if not args.check_only:
        ac, ah = behavioral_counts(c1), behavioral_counts(h1)
        if bc != ac:
            raise SystemExit(f'Phase293 dsi_ctrl behavioral-token count changed: {bc} -> {ac}')
        if bh != ah:
            raise SystemExit(f'Phase293 dsi_ctrl_hw behavioral-token count changed: {bh} -> {ah}')
    print('Phase293 passive GKI DMA_DONE reference recorder: PASS')


if __name__ == '__main__':
    main()
