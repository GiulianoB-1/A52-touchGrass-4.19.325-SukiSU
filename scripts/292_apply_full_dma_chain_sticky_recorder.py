#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
CLK = Path('drivers/a52_display/msm/dsi/dsi_clk_manager.c')

MARK_REC = 'A52_PHASE292_FULL_DMA_STICKY_RECORDER_V1'
MARK_DSI = 'A52_PHASE292_DSI_CHAIN_TAPS_V1'
MARK_HW = 'A52_PHASE292_HW_CHAIN_TAPS_V1'
MARK_CLK = 'A52_PHASE292_CLOCK_CHAIN_TAPS_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase292 {label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


def fn_slice(text: str, start: str, end: str, label: str):
    a = text.find(start)
    if a < 0:
        raise SystemExit(f'Phase292 {label}: start missing')
    b = text.find(end, a + len(start))
    if b < 0:
        raise SystemExit(f'Phase292 {label}: end missing')
    return a, b, text[a:b]


REC_BLOCK = r'''
/* A52_PHASE292_FULL_DMA_STICKY_RECORDER_V1
 * Complete first-write-wins recorder for the single Phase282 FIFO-routed
 * target command. Producers do not stream diagnostic records: they only copy
 * small fixed snapshots into RAM slots. At the inherited Phase280 timeout
 * retention boundary the compact snapshot is replayed three times, then the
 * recorder is frozen. This deliberately hardens retention after the Phase291
 * hardware capture recovered no usable R48 flight-recorder records.
 *
 * 00 TARGET   message identity
 * 01 STATE    driver state gate
 * 02 CLK0     Phase291 zero-handoff target/current clocks
 * 03 CLK1     post-existing-set-rate actual clocks
 * 04 ARM0     before DMA_DONE interrupt arm
 * 05 ARM1     after interrupt arm + completion reinit
 * 06 F0       FIFO command shape
 * 07 F1       TPG/payload head
 * 08 F2       controller/FIFO/lane/clock readiness
 * 09 F3       DMA/trigger/interrupt programming
 * 10 PRETRIG  immediately before production SW_TRIGGER
 * 11 POSTTRIG immediately after production SW_TRIGGER
 * 12 POSTCTL  controller/clock/int/error state after trigger
 * 13 ISR      ISR decision state
 * 14 WAIT0    before completion wait
 * 15 WAIT1    after completion wait
 * 16 TIME0    translated/raw interrupt timeout state
 * 17 TIME1    timeout datapath readiness state
 * 18 TIME2    timeout error/controller state
 * 19 END      reserved
 */
#define A52_P292_SLOTS 20U
#define A52_P292_VALUES 6U
#define A52_P292_REPLAYS 3U

struct a52_p292_sample {
	u32 v[A52_P292_VALUES];
	u8 n;
};

static struct a52_p292_sample a52_p292_slots[A52_P292_SLOTS];
static unsigned long a52_p292_valid;
static DEFINE_SPINLOCK(a52_p292_lock);
static atomic_t a52_p292_flushed = ATOMIC_INIT(0);

void a52_p292_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4, u32 v5)
{
	const u32 v[A52_P292_VALUES] = { v0, v1, v2, v3, v4, v5 };
	unsigned long flags;

	if (stage >= A52_P292_SLOTS || !n || n > A52_P292_VALUES)
		return;
	spin_lock_irqsave(&a52_p292_lock, flags);
	if (!(a52_p292_valid & (1UL << stage))) {
		a52_p292_slots[stage].n = (u8)n;
		memcpy(a52_p292_slots[stage].v, v, n * sizeof(v[0]));
		a52_p292_valid |= 1UL << stage;
	}
	spin_unlock_irqrestore(&a52_p292_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_p292_snapshot_record);

static void a52_p292_emit_slot(unsigned int r, unsigned int stage,
		const struct a52_p292_sample *s)
{
	switch (stage) {
	case 0:
		a52_ackfr_record("P292 S00 r=%u c=%x f=%x t=%x l=%x mf=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 1:
		a52_ackfr_record("P292 S01 r=%u p=%x h=%x c=%x m=%x v=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 2:
		a52_ackfr_record("P292 S02 r=%u b=%x p=%x i=%x ab=%x ap=%x ai=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4], s->v[5]);
		break;
	case 3:
		a52_ackfr_record("P292 S03 r=%u rc=%x ab=%x ap=%x ai=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3]);
		break;
	case 4:
		a52_ackfr_record("P292 S04 r=%u q=%x in=%x st=%x ck=%x ln=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 5:
		a52_ackfr_record("P292 S05 r=%u q=%x in=%x st=%x ck=%x ln=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 6:
		a52_ackfr_record("P292 S06 r=%u c=%x s=%x f=%x cfg=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3]);
		break;
	case 7:
		a52_ackfr_record("P292 S07 r=%u c=%x tg=%x w0=%x w1=%x tf=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 8:
		a52_ackfr_record("P292 S08 r=%u st=%x fs=%x ln=%x ck=%x tf=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 9:
		a52_ackfr_record("P292 S09 r=%u dc=%x dl=%x tr=%x in=%x cc=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 10:
		a52_ackfr_record("P292 S10 r=%u st=%x fs=%x ln=%x ck=%x tf=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 11:
		a52_ackfr_record("P292 S11 r=%u st=%x fs=%x ln=%x ck=%x tf=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 12:
		a52_ackfr_record("P292 S12 r=%u ct=%x cc=%x in=%x tr=%x pe=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 13:
		a52_ackfr_record("P292 S13 r=%u st=%x q=%x in=%x e0=%x e1=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 14:
		a52_ackfr_record("P292 S14 r=%u q=%x in=%x st=%x ln=%x ck=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 15:
		a52_ackfr_record("P292 S15 r=%u ret=%x q=%x st=%x in=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3]);
		break;
	case 16:
		a52_ackfr_record("P292 S16 r=%u st=%x in=%x q=%x done=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3]);
		break;
	case 17:
		a52_ackfr_record("P292 S17 r=%u st=%x fs=%x ln=%x ck=%x tf=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 18:
		a52_ackfr_record("P292 S18 r=%u in=%x ae=%x to=%x pe=%x ct=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4]);
		break;
	case 19:
		a52_ackfr_record("P292 S19 r=%u a=%x b=%x c=%x d=%x e=%x f=%x", r,
			s->v[0], s->v[1], s->v[2], s->v[3], s->v[4], s->v[5]);
		break;
	default:
		break;
	}
}

void a52_p292_flush_timeout_snapshot(void)
{
	struct a52_p292_sample s[A52_P292_SLOTS];
	unsigned long flags, valid;
	unsigned int r, stage;

	if (atomic_cmpxchg(&a52_p292_flushed, 0, 1))
		return;
	spin_lock_irqsave(&a52_p292_lock, flags);
	valid = a52_p292_valid;
	memcpy(s, a52_p292_slots, sizeof(s));
	spin_unlock_irqrestore(&a52_p292_lock, flags);

	for (r = 0; r < A52_P292_REPLAYS; r++) {
		a52_ackfr_record("P292 H r=%u v=%lx", r, valid);
		for (stage = 0; stage < A52_P292_SLOTS; stage++)
			if (valid & (1UL << stage))
				a52_p292_emit_slot(r, stage, &s[stage]);
	}
}
EXPORT_SYMBOL_GPL(a52_p292_flush_timeout_snapshot);
'''


def patch_rec(text: str) -> str:
    if MARK_REC in text:
        return text
    for token in [
        'A52_PHASE289_STICKY_FIFO_SNAPSHOT_V1',
        'static void a52_p288_capture_fmt(const char *fmt, va_list src)',
        'return !strncmp(message, "P289 ", 5)',
        'strncmp(fmt, "P289", 4)',
    ]:
        if token not in text:
            raise SystemExit('Phase292 recorder prerequisite missing: ' + token)
    anchor = 'static void a52_p288_capture_fmt(const char *fmt, va_list src)\n'
    text = one(text, anchor, REC_BLOCK + '\n' + anchor, 'recorder insertion')
    text = one(text,
        'return !strncmp(message, "P289 ", 5) ||',
        'return !strncmp(message, "P292 ", 5) ||\n       !strncmp(message, "P289 ", 5) ||',
        'critical P292 admission')
    text = one(text,
        'if (strncmp(fmt, "P289", 4) &&',
        'if (strncmp(fmt, "P292", 4) &&\n    strncmp(fmt, "P289", 4) &&',
        'focused P292 admission')
    return text


def patch_dsi(text: str) -> str:
    if MARK_DSI in text:
        return text
    for token in [
        'A52_PHASE289_TARGET_TIMEOUT_RETENTION_V1',
        'bool a52_p289_fifo_trace_active(void)',
        'a52_p289_flush_timeout_snapshot();',
        'a52_ackfr_retain_timeout_snapshot();',
        'static void dsi_ctrl_dma_cmd_wait_for_done(',
        'static irqreturn_t dsi_ctrl_isr(',
        'atomic_set(&dsi_ctrl->dma_irq_trig, 0);',
        'reinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);',
    ]:
        if token not in text:
            raise SystemExit('Phase292 DSI prerequisite missing: ' + token)

    # Raw read-only register snapshots in dsi_ctrl.c.
    text = one(text, '#include "dsi_ctrl_hw.h"\n',
               '#include "dsi_ctrl_hw.h"\n#include "dsi_ctrl_reg.h"\n#include "dsi_hw.h"\n',
               'read-only register includes')

    decl = 'extern void a52_p289_flush_timeout_snapshot(void);\n'
    text = one(text, decl, decl + '''/* A52_PHASE292_DSI_CHAIN_TAPS_V1 */
extern void a52_p292_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4, u32 v5);
extern void a52_p292_flush_timeout_snapshot(void);
''', 'DSI declarations')

    target = '''\t\ta52_p289_snapshot_record(0, 4, (u32)dsi_ctrl->cell_index,
\t\t\t(u32)*flags, (u32)msg->type, (u32)msg->tx_len, 0);\n'''
    text = one(text, target, target + '''\t\ta52_p292_snapshot_record(0, 5, (u32)dsi_ctrl->cell_index,
\t\t\t(u32)*flags, (u32)msg->type, (u32)msg->tx_len,
\t\t\t(u32)msg->flags, 0);
\t\ta52_p292_snapshot_record(1, 5,
\t\t\t(u32)dsi_ctrl->current_state.power_state,
\t\t\t(u32)dsi_ctrl->current_state.host_initialized,
\t\t\t(u32)dsi_ctrl->current_state.controller_state,
\t\t\t(u32)dsi_ctrl->current_state.cmd_engine_state,
\t\t\t(u32)dsi_ctrl->current_state.vid_engine_state, 0);\n''', 'TARGET/STATE')

    arm0 = '\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\n'
    text = one(text, arm0, '''\t\tif (a52_p289_fifo_trace_active())
\t\t\ta52_p292_snapshot_record(4, 5,
\t\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS), 0);
''' + arm0, 'ARM0')

    arm1 = '\t\treinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);\n'
    text = one(text, arm1, arm1 + '''\t\tif (a52_p289_fifo_trace_active())
\t\t\ta52_p292_snapshot_record(5, 5,
\t\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS), 0);\n''', 'ARM1')

    # Restrict WAIT anchors to the DMA wait worker, not the video-frame wait.
    wa, wb, wait_fn = fn_slice(text,
        'static void dsi_ctrl_dma_cmd_wait_for_done(',
        '\nstatic int dsi_ctrl_check_state(', 'wait worker')
    wait_entry = '''\tdsi_hw_ops = dsi_ctrl->hw.ops;
\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY);\n'''
    wait_fn = one(wait_fn, wait_entry, wait_entry + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(14, 5,
\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_STATUS),
\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS), 0);\n''', 'WAIT0')
    wait_call = '''\tret = wait_for_completion_timeout(
\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,
\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n'''
    wait_fn = one(wait_fn, wait_call, wait_call + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(15, 4, (u32)ret,
\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\t(u32)dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw),
\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), 0, 0);\n''', 'WAIT1')
    timeout = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {
\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    wait_fn = one(wait_fn, timeout, timeout + '''\t\tif (a52_p289_fifo_trace_active()) {
\t\t\ta52_p292_snapshot_record(16, 4, (u32)status,
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
\t\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\t\t(u32)!!(status & mask), 0, 0);
\t\t\ta52_p292_snapshot_record(17, 5,
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_FIFO_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_TPG_DMA_FIFO_STATUS), 0);
\t\t\ta52_p292_snapshot_record(18, 5,
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_ACK_ERR_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_TIMEOUT_STATUS),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_DLN0_PHY_ERR),
\t\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_CTRL), 0);
\t\t}\n''', 'TIME0/TIME1/TIME2')
    text = text[:wa] + wait_fn + text[wb:]

    ia, ib, isr_fn = fn_slice(text, 'static irqreturn_t dsi_ctrl_isr(',
                               '\nstatic int dsi_ctrl_register_isr(', 'ISR')
    done = '\tif (status & DSI_CMD_MODE_DMA_DONE) {\n'
    isr_fn = one(isr_fn, done, '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(13, 5, (u32)status,
\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\t(u32)DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
\t\t\t(u32)errors, (u32)(errors >> 32), 0);

''' + done, 'ISR slot')
    text = text[:ia] + isr_fn + text[ib:]

    # Phase280 places this freeze before the status branch and therefore before
    # the Samsung timeout debug/panic path. Phase289 is already the final replay;
    # Phase292 becomes the new final, triplicated replay immediately before it.
    freeze = '\t\t\ta52_ackfr_retain_timeout_snapshot();\n'
    text = one(text, freeze,
               '\t\t\ta52_p292_flush_timeout_snapshot();\n' + freeze,
               'final timeout replay')
    return text


def patch_hw(text: str) -> str:
    if MARK_HW in text:
        return text
    for token in [
        'A52_PHASE289_FIFO_CAUSAL_SLOTS_V1',
        'extern bool a52_p289_fifo_trace_active(void);',
        'void dsi_ctrl_hw_cmn_kickoff_fifo_command(',
        'void dsi_ctrl_hw_cmn_trigger_command_dma(',
        'a52_p289_snapshot_record(1, 4,',
        'a52_p289_snapshot_record(5, 5,',
    ]:
        if token not in text:
            raise SystemExit('Phase292 HW prerequisite missing: ' + token)

    decl = '''extern bool a52_p289_fifo_trace_active(void);
extern void a52_p289_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4);\n'''
    text = one(text, decl, decl + '''/* A52_PHASE292_HW_CHAIN_TAPS_V1 */
extern void a52_p292_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4, u32 v5);
''', 'HW declaration')

    f0 = '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(1, 4, (u32)ctrl->index, (u32)cmd->size,
\t\t\t(u32)flags, (cmd->en_broadcast ? 1U : 0U) |
\t\t\t(cmd->is_master ? 2U : 0U) | (cmd->use_lpm ? 4U : 0U), 0);\n'''
    text = one(text, f0, f0 + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(6, 4, (u32)ctrl->index, (u32)cmd->size,
\t\t\t(u32)flags, (cmd->en_broadcast ? 1U : 0U) |
\t\t\t(cmd->is_master ? 2U : 0U) | (cmd->use_lpm ? 4U : 0U), 0, 0);\n''', 'F0')

    f1 = '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(2, 4, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL),
\t\t\t(cmd->command && cmd->size >= 4) ? cmd->command[0] : 0,
\t\t\t(cmd->command && cmd->size >= 8) ? cmd->command[1] : 0, 0);\n'''
    text = one(text, f1, f1 + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(7, 5, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL),
\t\t\t(cmd->command && cmd->size >= 4) ? cmd->command[0] : 0,
\t\t\t(cmd->command && cmd->size >= 8) ? cmd->command[1] : 0,
\t\t\t(u32)DSI_R32(ctrl, DSI_TPG_DMA_FIFO_STATUS), 0);\n''', 'F1')

    f2 = '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(3, 4, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL), 0);\n'''
    text = one(text, f2, f2 + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(8, 5,
\t\t\t(u32)DSI_R32(ctrl, DSI_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_LANE_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_CLK_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_TPG_DMA_FIFO_STATUS), 0);\n''', 'F2')

    f3 = '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(4, 5, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),
\t\t\t(u32)DSI_R32(ctrl, DSI_DMA_CMD_LENGTH),
\t\t\t(u32)DSI_R32(ctrl, DSI_FIFO_STATUS), (u32)DSI_R32(ctrl, DSI_INT_CTRL));\n'''
    text = one(text, f3, f3 + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p292_snapshot_record(9, 5,
\t\t\t(u32)DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),
\t\t\t(u32)DSI_R32(ctrl, DSI_DMA_CMD_LENGTH),
\t\t\t(u32)DSI_R32(ctrl, DSI_TRIG_CTRL),
\t\t\t(u32)DSI_R32(ctrl, DSI_INT_CTRL),
\t\t\t(u32)DSI_R32(ctrl, DSI_CLK_CTRL), 0);\n''', 'F3')

    def wrap_trigger(fn: str, trigger: str, indent: str, label: str) -> str:
        pre = indent + '''if (a52_p289_fifo_trace_active())
''' + indent + '''\ta52_p292_snapshot_record(10, 5,
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_LANE_STATUS), (u32)DSI_R32(ctrl, DSI_CLK_STATUS),
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_TPG_DMA_FIFO_STATUS), 0);
'''
        post = indent + '''if (a52_p289_fifo_trace_active()) {
''' + indent + '''\ta52_p292_snapshot_record(11, 5,
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_LANE_STATUS), (u32)DSI_R32(ctrl, DSI_CLK_STATUS),
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_TPG_DMA_FIFO_STATUS), 0);
''' + indent + '''\ta52_p292_snapshot_record(12, 5,
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_CTRL), (u32)DSI_R32(ctrl, DSI_CLK_CTRL),
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_INT_CTRL), (u32)DSI_R32(ctrl, DSI_TRIG_CTRL),
''' + indent + '''\t\t(u32)DSI_R32(ctrl, DSI_DLN0_PHY_ERR), 0);
''' + indent + '''}
'''
        return one(fn, trigger, pre + trigger + post, label)

    a, b, fifo_fn = fn_slice(text,
        'void dsi_ctrl_hw_cmn_kickoff_fifo_command(',
        '\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo(', 'FIFO function')
    fifo_fn = wrap_trigger(fifo_fn,
        '\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n',
        '\t\t', 'immediate PRE/POST trigger')
    text = text[:a] + fifo_fn + text[b:]

    a, b, trigger_fn = fn_slice(text,
        'void dsi_ctrl_hw_cmn_trigger_command_dma(',
        '\nvoid dsi_ctrl_hw_cmn_clear_rdbk_reg(', 'deferred trigger function')
    trigger_fn = wrap_trigger(trigger_fn,
        '\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n',
        '\t', 'deferred PRE/POST trigger')
    text = text[:a] + trigger_fn + text[b:]
    return text


def patch_clk(text: str) -> str:
    if MARK_CLK in text:
        return text
    for token in [
        'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1',
        'bool a52_targets_valid = l_clks->freq.byte_clk_rate &&',
        'bool a52_zero_handoff = !a52_byte_now || !a52_pixel_now ||',
        'P291 C0 c=%d b=%llx p=%llx i=%llx ab=%lx ap=%lx ai=%lx',
    ]:
        if token not in text:
            raise SystemExit('Phase292 clock prerequisite missing: ' + token)

    anchor = 'static int dsi_core_clk_set_rate('
    text = one(text, anchor, '''/* A52_PHASE292_CLOCK_CHAIN_TAPS_V1 */
extern void a52_p292_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4, u32 v5);

''' + anchor, 'clock declaration')

    guard = '''\t\tif (!a52_targets_valid || !a52_zero_handoff)
\t\t\treturn 0;\n'''
    text = one(text, guard, guard + '''
\t\ta52_p292_snapshot_record(2, 6,
\t\t\t(u32)l_clks->freq.byte_clk_rate,
\t\t\t(u32)l_clks->freq.pix_clk_rate,
\t\t\t(u32)l_clks->freq.byte_intf_clk_rate,
\t\t\t(u32)a52_byte_now, (u32)a52_pixel_now, (u32)a52_intf_now);
''', 'CLK0')

    a, b, hs_fn = fn_slice(text, 'static int dsi_link_hs_clk_set_rate(',
                            '\nstatic int dsi_link_hs_clk_prepare(', 'HS set-rate')
    ret = '\treturn rc;\n'
    pos = hs_fn.rfind(ret)
    if pos < 0:
        raise SystemExit('Phase292 CLK1 return anchor missing')
    hs_fn = hs_fn[:pos] + '''\tif (mngr->is_cont_splash_enabled)
\t\ta52_p292_snapshot_record(3, 4, (u32)rc,
\t\t\t(u32)clk_get_rate(link_hs_clks->byte_clk),
\t\t\t(u32)clk_get_rate(link_hs_clks->pixel_clk),
\t\t\tlink_hs_clks->byte_intf_clk ?
\t\t\t\t(u32)clk_get_rate(link_hs_clks->byte_intf_clk) : 0,
\t\t\t0, 0);
''' + hs_fn[pos:]
    text = text[:a] + hs_fn + text[b:]
    return text


def validate(rec: str, dsi: str, hw: str, clk: str) -> None:
    for token in [MARK_REC, '#define A52_P292_SLOTS 20U',
                  '#define A52_P292_REPLAYS 3U', 'P292 H r=%u v=%lx',
                  'P292 S00', 'P292 S18',
                  'return !strncmp(message, "P292 ", 5)',
                  'strncmp(fmt, "P292", 4)']:
        if token not in rec:
            raise SystemExit('Phase292 recorder validation missing: ' + token)
    for token in [MARK_DSI, 'a52_p292_snapshot_record(0, 5,',
                  'a52_p292_snapshot_record(1, 5,',
                  'a52_p292_snapshot_record(4, 5,',
                  'a52_p292_snapshot_record(5, 5,',
                  'a52_p292_snapshot_record(13, 5,',
                  'a52_p292_snapshot_record(14, 5,',
                  'a52_p292_snapshot_record(15, 4,',
                  'a52_p292_snapshot_record(16, 4,',
                  'a52_p292_snapshot_record(17, 5,',
                  'a52_p292_snapshot_record(18, 5,',
                  'a52_p292_flush_timeout_snapshot();']:
        if token not in dsi:
            raise SystemExit('Phase292 DSI validation missing: ' + token)
    if not (dsi.index('a52_p292_snapshot_record(18, 5,') <
            dsi.index('a52_p289_flush_timeout_snapshot();') <
            dsi.index('a52_p292_flush_timeout_snapshot();') <
            dsi.index('a52_ackfr_retain_timeout_snapshot();')):
        raise SystemExit('Phase292 timeout/P289/P292/freeze ordering invalid')
    for token in [MARK_HW, 'a52_p292_snapshot_record(6, 4,',
                  'a52_p292_snapshot_record(7, 5,',
                  'a52_p292_snapshot_record(8, 5,',
                  'a52_p292_snapshot_record(9, 5,']:
        if token not in hw:
            raise SystemExit('Phase292 HW validation missing: ' + token)
    if hw.count('a52_p292_snapshot_record(10, 5,') != 2:
        raise SystemExit('Phase292 PRETRIG coverage must be exactly two trigger paths')
    if hw.count('a52_p292_snapshot_record(11, 5,') != 2:
        raise SystemExit('Phase292 POSTTRIG coverage must be exactly two trigger paths')
    if hw.count('a52_p292_snapshot_record(12, 5,') != 2:
        raise SystemExit('Phase292 POSTCTL coverage must be exactly two trigger paths')
    for token in [MARK_CLK, 'a52_p292_snapshot_record(2, 6,',
                  'a52_p292_snapshot_record(3, 4,',
                  'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1']:
        if token not in clk:
            raise SystemExit('Phase292 clock validation missing: ' + token)


def self_test() -> None:
    assert 'A52_P292_REPLAYS 3U' in REC_BLOCK
    assert 'if (!(a52_p292_valid & (1UL << stage)))' in REC_BLOCK
    assert 'atomic_cmpxchg(&a52_p292_flushed, 0, 1)' in REC_BLOCK
    assert REC_BLOCK.count('P292 S') == 20
    print('Phase292 sticky recorder self-test: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path)
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test()
        if args.root is None:
            return
    if args.root is None:
        raise SystemExit('--root is required unless only --self-test is used')
    paths = [args.root / p for p in [REC, DSI, HW, CLK]]
    for p in paths:
        if not p.is_file():
            raise SystemExit('Phase292 missing source: ' + str(p))
    old = [p.read_text() for p in paths]
    new = [patch_rec(old[0]), patch_dsi(old[1]), patch_hw(old[2]), patch_clk(old[3])]
    validate(*new)
    if args.check_only:
        print('Phase292 full DMA sticky recorder source audit: PASS')
        return
    for p, o, n in zip(paths, old, new):
        if o != n:
            p.write_text(n)
    print('Phase292 full DMA sticky recorder applied: PASS')


if __name__ == '__main__':
    main()
