#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
MARK_CTRL = 'A52_PHASE286_GOLDEN_FDR_DMA_CHAIN_V1'
MARK_HW = 'A52_PHASE286_LOWLEVEL_SW_TRIGGER_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_ctrl(text: str) -> str:
    if MARK_CTRL in text:
        return text
    for required in [
        'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1',
        'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1',
        'a52_p276r_deep_active()',
        'P276 281R0 q=%u %x %x %x %x %x %x',
        'P276 280Z q=2',
    ]:
        if required not in text:
            raise SystemExit('Phase286 prerequisite missing from dsi_ctrl.c: ' + required)

    # Cross-object read-only gate for the common HW layer. It deliberately
    # reuses the already hardware-proven Phase276 deep-window gate so low-level
    # records are emitted only around the late failing display transaction.
    anchor = 'static void dsi_ctrl_dma_cmd_wait_for_done(struct work_struct *work)\n'
    helper = '''/* %s\n * Passive Golden-FDR causal recorder for the DSI command-DMA chain.\n * No command flags, register writes, waits, resets, or recovery behavior are\n * changed by this phase. The HW common layer calls the gate below only to\n * decide whether to emit a retained record after its normal register writes.\n */\nbool a52_p286_dma_trace_active(void)\n{\n\treturn a52_p276r_deep_active();\n}\n\nstatic void dsi_ctrl_dma_cmd_wait_for_done(struct work_struct *work)\n''' % MARK_CTRL
    text = replace_one(text, anchor, helper, 'Phase286 gate/helper')

    # Stage A: message identity and both flag domains before tx-mode policy can
    # alter controller flags. Raw masks let us decode LAST/DEFER/FETCH/ASYNC.
    old = '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D M s=0 f=%x mt=%u l=%u", flags ? *flags : 0, (unsigned int)msg->type, (unsigned int)msg->tx_len);\n'''
    new = old + '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P286 A c=%d mf=%x f=%x t=%u l=%u",\n\t\t\tdsi_ctrl->cell_index, msg->flags, flags ? *flags : 0,\n\t\t\t(unsigned int)msg->type, (unsigned int)msg->tx_len);\n'''
    text = replace_one(text, old, new, 'Phase286 message entry')

    # Stage B: after WAIT_FOR_TRIGGER/LAST_COMMAND have been translated into
    # low-level hw_flags and immediately before command transport selection.
    old = '''\thw_flags |= (flags & DSI_CTRL_CMD_DEFER_TRIGGER) ?\n\t\t\tDSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER : 0;\n'''
    new = old + '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P286 B c=%d f=%x h=%x pm=%d ve=%d",\n\t\t\tdsi_ctrl->cell_index, flags, hw_flags,\n\t\t\tdsi_ctrl->host_config.panel_mode,\n\t\t\tdsi_ctrl->current_state.vid_engine_state);\n'''
    text = replace_one(text, old, new, 'Phase286 kickoff hw_flags')

    # Stage D: exact deferred-trigger gate. A missing LAST_COMMAND returns
    # success in golden TouchGrass without ever touching the SW trigger.
    old = '''\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY, flags);\n\t/* Dont trigger the command if this is not the last ocmmand */\n\tif (!(flags & DSI_CTRL_CMD_LAST_COMMAND))\n\t\treturn rc;\n'''
    new = '''\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY, flags);\n\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P286 D c=%d f=%x last=%d bm=%d b=%d",\n\t\t\tdsi_ctrl->cell_index, flags,\n\t\t\t!!(flags & DSI_CTRL_CMD_LAST_COMMAND),\n\t\t\t!!(flags & DSI_CTRL_CMD_BROADCAST_MASTER),\n\t\t\t!!(flags & DSI_CTRL_CMD_BROADCAST));\n\t/* Dont trigger the command if this is not the last ocmmand */\n\tif (!(flags & DSI_CTRL_CMD_LAST_COMMAND)) {\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 DX c=%d reason=nolast",\n\t\t\t\tdsi_ctrl->cell_index);\n\t\treturn rc;\n\t}\n'''
    text = replace_one(text, old, new, 'Phase286 LAST_COMMAND gate')

    # Stage E1: non-master direct trigger. Record after the normal call so the
    # recorder cannot delay the actual SW trigger write.
    old = '''\tif (!(flags & DSI_CTRL_CMD_BROADCAST_MASTER))\n\t\tdsi_hw_ops.trigger_command_dma(&dsi_ctrl->hw);\n'''
    new = '''\tif (!(flags & DSI_CTRL_CMD_BROADCAST_MASTER)) {\n\t\tdsi_hw_ops.trigger_command_dma(&dsi_ctrl->hw);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 E c=%d k=slave", dsi_ctrl->cell_index);\n\t}\n'''
    text = replace_one(text, old, new, 'Phase286 non-master trigger')

    # Stage E2: scheduled video-mode master trigger. Log the actual line that
    # permitted the trigger, again only after trigger_command_dma() returned.
    old = '''\t\t\t\t\tdsi_hw_ops.trigger_command_dma(\n\t\t\t\t\t\t&dsi_ctrl->hw);\n\t\t\t\t\tlocal_irq_restore(flag);\n'''
    new = '''\t\t\t\t\tdsi_hw_ops.trigger_command_dma(\n\t\t\t\t\t\t&dsi_ctrl->hw);\n\t\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\t\ta52_ackfr_record("P286 E c=%d k=sched cl=%u sl=%u lb=%u",\n\t\t\t\t\t\t\tdsi_ctrl->cell_index, cur_line, schedule_line,\n\t\t\t\t\t\t\tlatency_by_line);\n\t\t\t\t\tlocal_irq_restore(flag);\n'''
    text = replace_one(text, old, new, 'Phase286 scheduled master trigger')

    old = '''\t\t} else\n\t\t\tdsi_hw_ops.trigger_command_dma(&dsi_ctrl->hw);\n\n\t\tif (flags & DSI_CTRL_CMD_ASYNC_WAIT) {\n'''
    new = '''\t\t} else {\n\t\t\tdsi_hw_ops.trigger_command_dma(&dsi_ctrl->hw);\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P286 E c=%d k=master", dsi_ctrl->cell_index);\n\t\t}\n\n\t\tif (flags & DSI_CTRL_CMD_ASYNC_WAIT) {\n'''
    text = replace_one(text, old, new, 'Phase286 unscheduled master trigger')

    # Stage F: completion wait result and raw interrupt state at the timeout
    # boundary. Existing Phase281 q2 snapshots retain the wider register set.
    old = '''\tret = wait_for_completion_timeout(\n\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n'''
    new = old + '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P286 W c=%d r=%d irq=%d",\n\t\t\tdsi_ctrl->cell_index, ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n'''
    text = replace_one(text, old, new, 'Phase286 wait result')

    old = '''\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (status & mask) {\n'''
    new = '''\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 T c=%d st=%x done=%d irq=%d",\n\t\t\t\tdsi_ctrl->cell_index, status, !!(status & mask),\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tif (status & mask) {\n'''
    text = replace_one(text, old, new, 'Phase286 timeout raw interrupt')

    # Stage G: IRQ actually observed DMA_DONE, before the atomic/completion are
    # changed. This distinguishes HW completion from lost IRQ/completion paths.
    old = '''\tif (status & DSI_CMD_MODE_DMA_DONE) {\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n'''
    new = '''\tif (status & DSI_CMD_MODE_DMA_DONE) {\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 G c=%d st=%x irq0=%d",\n\t\t\t\tdsi_ctrl->cell_index, status,\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n'''
    text = replace_one(text, old, new, 'Phase286 DMA_DONE ISR')
    return text


def patch_hw(text: str) -> str:
    if MARK_HW in text:
        return text
    for required in [
        'void dsi_ctrl_hw_cmn_kickoff_command(',
        'void dsi_ctrl_hw_cmn_trigger_command_dma(',
        'DSI_W32(ctrl, DSI_DMA_CMD_OFFSET, cmd->offset);',
        'DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);',
    ]:
        if required not in text:
            raise SystemExit('Phase286 prerequisite missing from dsi_ctrl_hw_cmn.c: ' + required)

    inc = '#include "sde_dbg.h"\n'
    decl = '''#include "sde_dbg.h"\n\n/* %s */\nextern bool a52_p286_dma_trace_active(void);\nextern void a52_ackfr_record(const char *fmt, ...);\n''' % MARK_HW
    text = replace_one(text, inc, decl, 'Phase286 HW declarations')

    # Memory-backed kickoff: record only after DMA offset/length are already
    # programmed and wmb() has completed. For immediate mode the trigger write
    # remains first, then the FDR marker, preserving trigger timing.
    old = '''\t/* wait for writes to complete before kick off */\n\twmb();\n\n\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n'''
    new = '''\t/* wait for writes to complete before kick off */\n\twmb();\n\n\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\tif (a52_p286_dma_trace_active())\n\t\t\ta52_ackfr_record("P286 HK c=%d o=%x l=%x f=%x sw=1",\n\t\t\t\tctrl->index, cmd->offset, cmd->length, flags);\n\t} else if (a52_p286_dma_trace_active()) {\n\t\ta52_ackfr_record("P286 HK c=%d o=%x l=%x f=%x sw=0",\n\t\t\t\tctrl->index, cmd->offset, cmd->length, flags);\n\t}\n}\n'''
    text = replace_one(text, old, new, 'Phase286 memory kickoff trigger')

    # Deferred trigger: marker is emitted strictly after the golden write.
    old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n}\n'''
    new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P286 HT c=%d sw=1", ctrl->index);\n}\n'''
    text = replace_one(text, old, new, 'Phase286 deferred SW trigger')
    return text


def validate_ctrl(text: str) -> None:
    required = [
        MARK_CTRL,
        'bool a52_p286_dma_trace_active(void)',
        'P286 A c=%d mf=%x f=%x t=%u l=%u',
        'P286 B c=%d f=%x h=%x pm=%d ve=%d',
        'P286 D c=%d f=%x last=%d bm=%d b=%d',
        'P286 DX c=%d reason=nolast',
        'P286 E c=%d k=slave',
        'P286 E c=%d k=sched cl=%u sl=%u lb=%u',
        'P286 E c=%d k=master',
        'P286 W c=%d r=%d irq=%d',
        'P286 T c=%d st=%x done=%d irq=%d',
        'P286 G c=%d st=%x irq0=%d',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);',
        'P276 280Z q=2',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase286 dsi_ctrl marker missing: ' + token)


def validate_hw(text: str) -> None:
    required = [
        MARK_HW,
        'P286 HK c=%d o=%x l=%x f=%x sw=1',
        'P286 HK c=%d o=%x l=%x f=%x sw=0',
        'P286 HT c=%d sw=1',
        'DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase286 HW marker missing: ' + token)
    # The deferred trigger marker must occur after the register write.
    fn = text.index('void dsi_ctrl_hw_cmn_trigger_command_dma(')
    write = text.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);', fn)
    rec = text.index('P286 HT c=%d sw=1', fn)
    if rec < write:
        raise SystemExit('Phase286 HW trigger marker occurs before SW trigger write')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    ctrl = args.root / CTRL
    hw = args.root / HW
    for p in [ctrl, hw]:
        if not p.is_file():
            raise SystemExit(f'missing source: {p}')
    ct = ctrl.read_text()
    ht = hw.read_text()
    if not args.check_only:
        ct = patch_ctrl(ct)
        ht = patch_hw(ht)
        ctrl.write_text(ct)
        hw.write_text(ht)
    validate_ctrl(ct)
    validate_hw(ht)
    print('Phase286 Golden-FDR DSI DMA causal-chain recorder: PASS')


if __name__ == '__main__':
    main()
