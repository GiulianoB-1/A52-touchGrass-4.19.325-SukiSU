#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK_REC = 'A52_PHASE289_STICKY_FIFO_SNAPSHOT_V1'
MARK_DSI = 'A52_PHASE289_TARGET_TIMEOUT_RETENTION_V1'
MARK_HW = 'A52_PHASE289_FIFO_CAUSAL_SLOTS_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


REC_BLOCK = r'''
/* A52_PHASE289_STICKY_FIFO_SNAPSHOT_V1
 * Dedicated one-write slots for the single Phase282 FIFO-routed diagnostic
 * transaction. This is intentionally independent of the Phase286 circular
 * final-32 tail. Each causal boundary can be captured at most once, then the
 * fixed snapshot is replayed as the final recorder records immediately before
 * the inherited Phase280 timeout retention latch is frozen.
 *
 * Stage map: 0=TARGET, 1=F0, 2=F1, 3=F2, 4=F3, 5=F4(actual SW trigger),
 *            6=W(wait result), 7=G(DMA_DONE IRQ), 8=T(timeout status).
 */
#define A52_P289_SLOTS 9U
#define A52_P289_VALUES 5U

struct a52_p289_sample {
	u32 v[A52_P289_VALUES];
	u8 n;
};

static struct a52_p289_sample a52_p289_slots[A52_P289_SLOTS];
static unsigned long a52_p289_valid;
static DEFINE_SPINLOCK(a52_p289_lock);
static atomic_t a52_p289_flushed = ATOMIC_INIT(0);

void a52_p289_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4)
{
	const u32 v[A52_P289_VALUES] = { v0, v1, v2, v3, v4 };
	unsigned long flags;

	if (stage >= A52_P289_SLOTS || !n || n > A52_P289_VALUES)
		return;
	spin_lock_irqsave(&a52_p289_lock, flags);
	if (!(a52_p289_valid & (1UL << stage))) {
		a52_p289_slots[stage].n = (u8)n;
		memcpy(a52_p289_slots[stage].v, v, n * sizeof(v[0]));
		a52_p289_valid |= 1UL << stage;
	}
	spin_unlock_irqrestore(&a52_p289_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_p289_snapshot_record);

void a52_p289_flush_timeout_snapshot(void)
{
	struct a52_p289_sample s[A52_P289_SLOTS];
	unsigned long flags, valid;

	if (atomic_cmpxchg(&a52_p289_flushed, 0, 1))
		return;
	spin_lock_irqsave(&a52_p289_lock, flags);
	valid = a52_p289_valid;
	memcpy(s, a52_p289_slots, sizeof(s));
	spin_unlock_irqrestore(&a52_p289_lock, flags);

	a52_ackfr_record("P289 RH v=%lx", valid);
	if (valid & (1UL << 0))
		a52_ackfr_record("P289 TARGET c=%x f=%x t=%x l=%x",
			s[0].v[0], s[0].v[1], s[0].v[2], s[0].v[3]);
	if (valid & (1UL << 1))
		a52_ackfr_record("P289 F0 c=%x s=%x f=%x cfg=%x",
			s[1].v[0], s[1].v[1], s[1].v[2], s[1].v[3]);
	if (valid & (1UL << 2))
		a52_ackfr_record("P289 F1 c=%x tg=%x w0=%x w1=%x",
			s[2].v[0], s[2].v[1], s[2].v[2], s[2].v[3]);
	if (valid & (1UL << 3))
		a52_ackfr_record("P289 F2 c=%x st=%x fs=%x tg=%x",
			s[3].v[0], s[3].v[1], s[3].v[2], s[3].v[3]);
	if (valid & (1UL << 4))
		a52_ackfr_record("P289 F3 c=%x dc=%x dl=%x fs=%x in=%x",
			s[4].v[0], s[4].v[1], s[4].v[2], s[4].v[3], s[4].v[4]);
	if (valid & (1UL << 5))
		a52_ackfr_record("P289 F4 c=%x sw=%x st=%x fs=%x in=%x",
			s[5].v[0], s[5].v[1], s[5].v[2], s[5].v[3], s[5].v[4]);
	if (valid & (1UL << 6))
		a52_ackfr_record("P289 W c=%x r=%x irq=%x",
			s[6].v[0], s[6].v[1], s[6].v[2]);
	if (valid & (1UL << 7))
		a52_ackfr_record("P289 G c=%x st=%x irq0=%x",
			s[7].v[0], s[7].v[1], s[7].v[2]);
	if (valid & (1UL << 8))
		a52_ackfr_record("P289 T c=%x st=%x done=%x irq=%x",
			s[8].v[0], s[8].v[1], s[8].v[2], s[8].v[3]);
}
EXPORT_SYMBOL_GPL(a52_p289_flush_timeout_snapshot);
'''


def patch_rec(text: str) -> str:
    if MARK_REC in text:
        return text
    for token in [
        'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1',
        'A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1',
        'A52_PHASE288B_RETAINED_FIFO_CHAIN_V1',
        'static void a52_p288_capture_fmt(const char *fmt, va_list src)',
        'return !strncmp(message, "P288 ", 5) ||',
        'if (strncmp(fmt, "P288", 4) &&',
    ]:
        if token not in text:
            raise SystemExit('Phase289 recorder prerequisite missing: ' + token)

    anchor = 'static void a52_p288_capture_fmt(const char *fmt, va_list src)\n'
    text = one(text, anchor, REC_BLOCK + '\n' + anchor,
               'Phase289 dedicated snapshot insertion')
    text = one(text,
        'return !strncmp(message, "P288 ", 5) ||',
        'return !strncmp(message, "P289 ", 5) ||\n       !strncmp(message, "P288 ", 5) ||',
        'critical P289 admission')
    text = one(text,
        'if (strncmp(fmt, "P288", 4) &&',
        'if (strncmp(fmt, "P289", 4) &&\n    strncmp(fmt, "P288", 4) &&',
        'focused P289 admission')
    return text


def patch_dsi(text: str) -> str:
    if MARK_DSI in text:
        return text
    for token in [
        'A52_PHASE282_GOLDEN_FIFO_AB_V1',
        'A52_PHASE286_GOLDEN_FDR_DMA_CHAIN_V1',
        'static atomic_t a52_p282_fifo_inflight = ATOMIC_INIT(0);',
        'P276 282A m=fifo f=%x',
        'P286 W c=%d r=%d irq=%d',
        'P286 T c=%d st=%x done=%d irq=%d',
        'P286 G c=%d st=%x irq0=%d',
        'a52_p286_flush_timeout_chain();',
        'P276 280Z q=2',
        'a52_ackfr_retain_timeout_snapshot();',
    ]:
        if token not in text:
            raise SystemExit('Phase289 DSI prerequisite missing: ' + token)

    decl_anchor = 'extern void a52_ackfr_retain_timeout_snapshot(void);\n'
    decl = decl_anchor + '''/* A52_PHASE289_TARGET_TIMEOUT_RETENTION_V1 */
extern void a52_p289_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4);
extern void a52_p289_flush_timeout_snapshot(void);
'''
    text = one(text, decl_anchor, decl, 'Phase289 DSI declarations')

    inflight = 'static atomic_t a52_p282_fifo_inflight = ATOMIC_INIT(0);\n'
    helper = inflight + '''
bool a52_p289_fifo_trace_active(void)
{
	return atomic_read(&a52_p282_fifo_inflight) != 0;
}
'''
    text = one(text, inflight, helper, 'Phase289 target-active helper')

    target_old = '''\t\tatomic_set(&a52_p282_fifo_inflight, 1);\n\t\ta52_ackfr_record("P276 282A m=fifo f=%x", *flags);\n'''
    target_new = target_old + '''\t\ta52_p289_snapshot_record(0, 4, (u32)dsi_ctrl->cell_index,
\t\t\t(u32)*flags, (u32)msg->type, (u32)msg->tx_len, 0);\n'''
    text = one(text, target_old, target_new, 'Phase289 TARGET slot')

    w_old = '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P286 W c=%d r=%d irq=%d",\n\t\t\tdsi_ctrl->cell_index, ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n'''
    w_new = w_old + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(6, 3, (u32)dsi_ctrl->cell_index,
\t\t\t(u32)ret, (u32)atomic_read(&dsi_ctrl->dma_irq_trig), 0, 0);\n'''
    text = one(text, w_old, w_new, 'Phase289 wait slot')

    t_old = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 T c=%d st=%x done=%d irq=%d",\n\t\t\t\tdsi_ctrl->cell_index, status, !!(status & mask),\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n'''
    t_new = t_old + '''\t\tif (a52_p289_fifo_trace_active())
\t\t\ta52_p289_snapshot_record(8, 4, (u32)dsi_ctrl->cell_index,
\t\t\t\t(u32)status, (u32)!!(status & mask),
\t\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig), 0);\n'''
    text = one(text, t_old, t_new, 'Phase289 timeout slot')

    g_old = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 G c=%d st=%x irq0=%d",\n\t\t\t\tdsi_ctrl->cell_index, status,\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n'''
    g_new = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 G c=%d st=%x irq0=%d",\n\t\t\t\tdsi_ctrl->cell_index, status,\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tif (a52_p289_fifo_trace_active())
\t\t\ta52_p289_snapshot_record(7, 3, (u32)dsi_ctrl->cell_index,
\t\t\t\t(u32)status, (u32)atomic_read(&dsi_ctrl->dma_irq_trig), 0, 0);\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n'''
    text = one(text, g_old, g_new, 'Phase289 DMA_DONE IRQ slot')

    freeze_old = '''\t\t\ta52_p286_flush_timeout_chain();\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n'''
    freeze_new = '''\t\t\ta52_p286_flush_timeout_chain();\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\tif (a52_p289_fifo_trace_active())
\t\t\t\ta52_p289_flush_timeout_snapshot();\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n'''
    text = one(text, freeze_old, freeze_new,
               'Phase289 final replay immediately before retention freeze')
    return text


def patch_hw(text: str) -> str:
    if MARK_HW in text:
        return text
    for token in [
        'A52_PHASE286_LOWLEVEL_SW_TRIGGER_V1',
        'A52_PHASE288_FIFO_CAUSAL_CHAIN_V1',
        'extern bool a52_p286_dma_trace_active(void);',
        'extern void a52_ackfr_record(const char *fmt, ...);',
        'void dsi_ctrl_hw_cmn_kickoff_fifo_command(',
        'void dsi_ctrl_hw_cmn_trigger_command_dma(',
        'P288 F0 c=%d s=%u f=%x cfg=%x',
        'P288 F4 c=%d sw=%u st=%x fs=%x in=%x',
    ]:
        if token not in text:
            raise SystemExit('Phase289 HW prerequisite missing: ' + token)

    decl_old = '''extern bool a52_p286_dma_trace_active(void);\nextern void a52_ackfr_record(const char *fmt, ...);\n'''
    decl_new = decl_old + '''/* A52_PHASE289_FIFO_CAUSAL_SLOTS_V1 */
extern bool a52_p289_fifo_trace_active(void);
extern void a52_p289_snapshot_record(unsigned int stage, unsigned int n,
		u32 v0, u32 v1, u32 v2, u32 v3, u32 v4);
'''
    text = one(text, decl_old, decl_new, 'Phase289 HW declarations')

    f0_old = '''\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P288 F0 c=%d s=%u f=%x cfg=%x",\n\t\t\tctrl->index, cmd->size, flags,\n\t\t\t(cmd->en_broadcast ? 1U : 0U) |\n\t\t\t(cmd->is_master ? 2U : 0U) |\n\t\t\t(cmd->use_lpm ? 4U : 0U));\n'''
    f0_new = f0_old + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(1, 4, (u32)ctrl->index, (u32)cmd->size,
\t\t\t(u32)flags, (cmd->en_broadcast ? 1U : 0U) |
\t\t\t(cmd->is_master ? 2U : 0U) | (cmd->use_lpm ? 4U : 0U), 0);\n'''
    text = one(text, f0_old, f0_new, 'Phase289 F0 slot')

    f1_old = '''\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P288 F1 c=%d tg=%x w0=%x w1=%x",\n\t\t\tctrl->index, DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL),\n\t\t\t(cmd->command && cmd->size >= 4) ? cmd->command[0] : 0,\n\t\t\t(cmd->command && cmd->size >= 8) ? cmd->command[1] : 0);\n'''
    f1_new = f1_old + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(2, 4, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL),
\t\t\t(cmd->command && cmd->size >= 4) ? cmd->command[0] : 0,
\t\t\t(cmd->command && cmd->size >= 8) ? cmd->command[1] : 0, 0);\n'''
    text = one(text, f1_old, f1_new, 'Phase289 F1 slot')

    f2_old = '''\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P288 F2 c=%d st=%x fs=%x tg=%x",\n\t\t\tctrl->index, DSI_R32(ctrl, DSI_STATUS),\n\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS),\n\t\t\tDSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL));\n'''
    f2_new = f2_old + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(3, 4, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL), 0);\n'''
    text = one(text, f2_old, f2_new, 'Phase289 F2 slot')

    f3_old = '''\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P288 F3 c=%d dc=%x dl=%x fs=%x in=%x",\n\t\t\tctrl->index, DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),\n\t\t\tDSI_R32(ctrl, DSI_DMA_CMD_LENGTH),\n\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));\n'''
    f3_new = f3_old + '''\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(4, 5, (u32)ctrl->index,
\t\t\t(u32)DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),
\t\t\t(u32)DSI_R32(ctrl, DSI_DMA_CMD_LENGTH),
\t\t\t(u32)DSI_R32(ctrl, DSI_FIFO_STATUS), (u32)DSI_R32(ctrl, DSI_INT_CTRL));\n'''
    text = one(text, f3_old, f3_new, 'Phase289 F3 slot')

    f4_imm_old = '''\t\tif (a52_p286_dma_trace_active())\n\t\t\ta52_ackfr_record("P288 F4 c=%d sw=%u st=%x fs=%x in=%x",\n\t\t\t\tctrl->index, 1U, DSI_R32(ctrl, DSI_STATUS),\n\t\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));\n'''
    f4_imm_new = f4_imm_old + '''\t\tif (a52_p289_fifo_trace_active())
\t\t\ta52_p289_snapshot_record(5, 5, (u32)ctrl->index, 1U,
\t\t\t\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
\t\t\t\t(u32)DSI_R32(ctrl, DSI_INT_CTRL));\n'''
    text = one(text, f4_imm_old, f4_imm_new, 'Phase289 immediate actual trigger slot')

    trig_old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P286 HT c=%d sw=1", ctrl->index);\n}\n'''
    trig_new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P286 HT c=%d sw=1", ctrl->index);\n\tif (a52_p289_fifo_trace_active())
\t\ta52_p289_snapshot_record(5, 5, (u32)ctrl->index, 1U,
\t\t\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
\t\t\t(u32)DSI_R32(ctrl, DSI_INT_CTRL));\n}\n'''
    text = one(text, trig_old, trig_new, 'Phase289 deferred actual trigger slot')
    return text


def validate(rec: str, dsi: str, hw: str) -> None:
    for token in [
        MARK_REC, '#define A52_P289_SLOTS 9U',
        'a52_p289_snapshot_record(unsigned int stage, unsigned int n,',
        'a52_p289_flush_timeout_snapshot(void)',
        'P289 RH v=%lx', 'P289 TARGET c=%x f=%x t=%x l=%x',
        'P289 F0 c=%x s=%x f=%x cfg=%x',
        'P289 F1 c=%x tg=%x w0=%x w1=%x',
        'P289 F2 c=%x st=%x fs=%x tg=%x',
        'P289 F3 c=%x dc=%x dl=%x fs=%x in=%x',
        'P289 F4 c=%x sw=%x st=%x fs=%x in=%x',
        'P289 W c=%x r=%x irq=%x', 'P289 G c=%x st=%x irq0=%x',
        'P289 T c=%x st=%x done=%x irq=%x',
        'return !strncmp(message, "P289 ", 5)', 'strncmp(fmt, "P289", 4)',
    ]:
        if token not in rec:
            raise SystemExit('Phase289 recorder marker missing: ' + token)

    for token in [
        MARK_DSI, 'bool a52_p289_fifo_trace_active(void)',
        'a52_p289_snapshot_record(0, 4,', 'a52_p289_snapshot_record(6, 3,',
        'a52_p289_snapshot_record(7, 3,', 'a52_p289_snapshot_record(8, 4,',
        'a52_p289_flush_timeout_snapshot();',
    ]:
        if token not in dsi:
            raise SystemExit('Phase289 DSI marker missing: ' + token)
    if not (dsi.index('P286 T c=%d st=%x done=%d irq=%d') <
            dsi.index('a52_p289_snapshot_record(8, 4,') <
            dsi.index('a52_p289_flush_timeout_snapshot();') <
            dsi.index('a52_ackfr_retain_timeout_snapshot();')):
        raise SystemExit('Phase289 timeout capture/replay/freeze ordering invalid')

    for token in [MARK_HW, 'a52_p289_snapshot_record(1, 4,',
                  'a52_p289_snapshot_record(2, 4,',
                  'a52_p289_snapshot_record(3, 4,',
                  'a52_p289_snapshot_record(4, 5,']:
        if token not in hw:
            raise SystemExit('Phase289 HW marker missing: ' + token)
    if hw.count('a52_p289_snapshot_record(5, 5,') != 2:
        raise SystemExit('Phase289 F4 must cover immediate and deferred actual trigger writes')
    fifo = hw.index('void dsi_ctrl_hw_cmn_kickoff_fifo_command(')
    reset = hw.index('\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo(', fifo)
    ffn = hw[fifo:reset]
    prod = ffn.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);')
    snap = ffn.index('a52_p289_snapshot_record(5, 5,', prod)
    if snap < prod:
        raise SystemExit('Phase289 immediate F4 is not after production SW trigger')
    trig = hw.index('void dsi_ctrl_hw_cmn_trigger_command_dma(')
    tw = hw.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);', trig)
    ts = hw.index('a52_p289_snapshot_record(5, 5,', tw)
    if ts < tw:
        raise SystemExit('Phase289 deferred F4 is not after production SW trigger')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    rp, dp, hp = args.root / REC, args.root / DSI, args.root / HW
    for p in [rp, dp, hp]:
        if not p.is_file():
            raise SystemExit('missing source: ' + str(p))
    r, d, h = rp.read_text(), dp.read_text(), hp.read_text()
    if not args.check_only:
        r, d, h = patch_rec(r), patch_dsi(d), patch_hw(h)
        rp.write_text(r); dp.write_text(d); hp.write_text(h)
    validate(r, d, h)
    print('Phase289 sticky FIFO timeout snapshot + final pre-freeze replay: PASS')


if __name__ == '__main__':
    main()
