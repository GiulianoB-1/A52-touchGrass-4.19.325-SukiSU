#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
MARK = 'A52_PHASE288_FIFO_CAUSAL_CHAIN_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    for token in [
        'A52_PHASE286_LOWLEVEL_SW_TRIGGER_V1',
        'A52_PHASE287_DSI_DMA_FETCH_PROVENANCE_V1',
        'extern bool a52_p286_dma_trace_active(void);',
        'extern void a52_ackfr_record(const char *fmt, ...);',
        'void dsi_ctrl_hw_cmn_kickoff_fifo_command(',
        'void dsi_ctrl_hw_cmn_reset_cmd_fifo(',
    ]:
        if token not in text:
            raise SystemExit('Phase288 prerequisite missing: ' + token)

    start = text.index('void dsi_ctrl_hw_cmn_kickoff_fifo_command(')
    end = text.index('\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo(', start)
    fn = text[start:end]

    fn = one(fn,
        '\tu32 *ptr = cmd->command;\n',
        '\tu32 *ptr = cmd->command;\n'
        '\t/* ' + MARK + '\n'
        '\t * Passive causal trace of the already-selected FIFO/TPG transport.\n'
        '\t * All records are read-only and occur around existing production writes.\n'
        '\t */\n'
        '\tif (a52_p286_dma_trace_active())\n'
        '\t\ta52_ackfr_record("P288 F0 c=%d s=%u f=%x cfg=%x",\n'
        '\t\t\tctrl->index, cmd->size, flags,\n'
        '\t\t\t(cmd->en_broadcast ? 1U : 0U) |\n'
        '\t\t\t(cmd->is_master ? 2U : 0U) |\n'
        '\t\t\t(cmd->use_lpm ? 4U : 0U));\n',
        'FIFO entry')

    fn = one(fn,
        '\tDSI_W32(ctrl, DSI_TEST_PATTERN_GEN_CTRL, reg);\n\n\t/*\n\t * Program the FIFO with command buffer.',
        '\tDSI_W32(ctrl, DSI_TEST_PATTERN_GEN_CTRL, reg);\n'
        '\tif (a52_p286_dma_trace_active())\n'
        '\t\ta52_ackfr_record("P288 F1 c=%d tg=%x w0=%x w1=%x",\n'
        '\t\t\tctrl->index, DSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL),\n'
        '\t\t\t(cmd->command && cmd->size >= 4) ? cmd->command[0] : 0,\n'
        '\t\t\t(cmd->command && cmd->size >= 8) ? cmd->command[1] : 0);\n\n'
        '\t/*\n\t * Program the FIFO with command buffer.',
        'TPG setup + encoded words')

    fn = one(fn,
        '\tif ((cmd->size / 4) & 0x1)\n\t\tDSI_W32(ctrl, DSI_TEST_PATTERN_GEN_CMD_DMA_INIT_VAL, 0);\n\n\t/*Set BROADCAST_EN and EMBEDDED_MODE */',
        '\tif ((cmd->size / 4) & 0x1)\n\t\tDSI_W32(ctrl, DSI_TEST_PATTERN_GEN_CMD_DMA_INIT_VAL, 0);\n'
        '\tif (a52_p286_dma_trace_active())\n'
        '\t\ta52_ackfr_record("P288 F2 c=%d st=%x fs=%x tg=%x",\n'
        '\t\t\tctrl->index, DSI_R32(ctrl, DSI_STATUS),\n'
        '\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS),\n'
        '\t\t\tDSI_R32(ctrl, DSI_TEST_PATTERN_GEN_CTRL));\n\n'
        '\t/*Set BROADCAST_EN and EMBEDDED_MODE */',
        'post FIFO fill snapshot')

    old = '''\tDSI_W32(ctrl, DSI_DMA_CMD_LENGTH, (cmd->size & 0xFFFFFFFF));\n\t/* Finish writes before command trigger */\n\twmb();\n\n\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n'''
    new = '''\tDSI_W32(ctrl, DSI_DMA_CMD_LENGTH, (cmd->size & 0xFFFFFFFF));\n\t/* Finish writes before command trigger */\n\twmb();\n\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P288 F3 c=%d dc=%x dl=%x fs=%x in=%x",\n\t\t\tctrl->index, DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL),\n\t\t\tDSI_R32(ctrl, DSI_DMA_CMD_LENGTH),\n\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));\n\n\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\tif (a52_p286_dma_trace_active())\n\t\t\ta52_ackfr_record("P288 F4 c=%d sw=%u st=%x fs=%x in=%x",\n\t\t\t\tctrl->index, 1U, DSI_R32(ctrl, DSI_STATUS),\n\t\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));\n\t} else if (a52_p286_dma_trace_active()) {\n\t\ta52_ackfr_record("P288 F4 c=%d sw=%u st=%x fs=%x in=%x",\n\t\t\tctrl->index, 0U, DSI_R32(ctrl, DSI_STATUS),\n\t\t\tDSI_R32(ctrl, DSI_FIFO_STATUS), DSI_R32(ctrl, DSI_INT_CTRL));\n\t}\n'''
    fn = one(fn, old, new, 'FIFO programmed + trigger causal trace')
    return text[:start] + fn + text[end:]


def validate(text: str) -> None:
    required = [
        MARK,
        'P288 F0 c=%d s=%u f=%x cfg=%x',
        'P288 F1 c=%d tg=%x w0=%x w1=%x',
        'P288 F2 c=%d st=%x fs=%x tg=%x',
        'P288 F3 c=%d dc=%x dl=%x fs=%x in=%x',
        'P288 F4 c=%d sw=%u st=%x fs=%x in=%x',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase288 FIFO marker missing: ' + token)
    if text.count('P288 F4 c=%d sw=%u st=%x fs=%x in=%x') != 2:
        raise SystemExit('Phase288 F4 must cover both immediate and deferred FIFO paths')
    start = text.index('void dsi_ctrl_hw_cmn_kickoff_fifo_command(')
    end = text.index('\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo(', start)
    fn = text[start:end]
    order = [fn.index(x) for x in [
        'P288 F0 c=%d s=%u f=%x cfg=%x',
        'P288 F1 c=%d tg=%x w0=%x w1=%x',
        'P288 F2 c=%d st=%x fs=%x tg=%x',
        'P288 F3 c=%d dc=%x dl=%x fs=%x in=%x',
    ]]
    if order != sorted(order):
        raise SystemExit('Phase288 FIFO causal markers out of order')
    trig = fn.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);')
    f4 = fn.index('P288 F4 c=%d sw=%u st=%x fs=%x in=%x', trig)
    if f4 < trig:
        raise SystemExit('Phase288 immediate F4 must be after the production SW trigger write')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    path = args.root / HW
    if not path.is_file():
        raise SystemExit('missing DSI common HW source')
    text = path.read_text()
    if not args.check_only:
        text = patch(text)
        path.write_text(text)
    validate(text)
    print('Phase288 passive FIFO causal-chain producer hooks: PASS')


if __name__ == '__main__':
    main()
