#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE282_GOLDEN_FIFO_AB_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    for required in [
        'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1',
        'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1',
        'P276 281R0 q=%u %x %x %x %x %x %x',
        'P276 280Z q=2',
    ]:
        if required not in text:
            raise SystemExit('Phase282 prerequisite missing: ' + required)

    text = replace_one(
        text,
        'static DEFINE_MUTEX(dsi_ctrl_list_lock);\n',
        'static DEFINE_MUTEX(dsi_ctrl_list_lock);\n'
        '\n/* ' + MARK + '\n'
        ' * Golden TouchGrass uses the controller FIFO/TPG path as a supported\n'
        ' * alternative to system-memory command fetch (for example in secure\n'
        ' * mode). Phase282 redirects exactly one deep-window 0x29 Generic Long\n'
        ' * Write with tx_len=3 to FIFO_STORE, then all later commands use the\n'
        ' * untouched normal policy. This creates an in-boot FIFO-vs-memory-DMA\n'
        ' * A/B test while preserving the Phase281 q0/q1/q2 snapshots.\n'
        ' */\n'
        'static atomic_t a52_p282_fifo_once = ATOMIC_INIT(0);\n'
        'static atomic_t a52_p282_fifo_inflight = ATOMIC_INIT(0);\n',
        'Phase282 globals')

    old = '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D M s=0 f=%x mt=%u l=%u", flags ? *flags : 0, (unsigned int)msg->type, (unsigned int)msg->tx_len);\n\n\t/* Select the tx mode to transfer the command */\n\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n\n\t/* Validate the mode before sending the command */\n'''
    new = '''\tif (a52_p276r_deep_active())\n\t\ta52_ackfr_record("P276 D M s=0 f=%x mt=%u l=%u", flags ? *flags : 0, (unsigned int)msg->type, (unsigned int)msg->tx_len);\n\tif (a52_p276r_deep_active() && msg->tx_buf && msg->tx_len == 3) {\n\t\tconst u8 *a52_p282_tx = msg->tx_buf;\n\t\ta52_ackfr_record("P276 282P t=%02x f=%x b=%02x%02x%02x",\n\t\t\tmsg->type, flags ? *flags : 0, a52_p282_tx[0],\n\t\t\ta52_p282_tx[1], a52_p282_tx[2]);\n\t}\n\n\t/* Select the tx mode to transfer the command */\n\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n\n\t/*\n\t * One-shot Golden A/B probe. 0x29 == MIPI_DSI_GENERIC_LONG_WRITE.\n\t * Keep the same packet construction, trigger and completion machinery;\n\t * only bypass external command-buffer memory fetch for this one command.\n\t */\n\tif (a52_p276r_deep_active() && msg->type == 0x29 &&\n\t\t\tmsg->tx_len == 3 && (*flags & DSI_CTRL_CMD_FETCH_MEMORY) &&\n\t\t\tatomic_cmpxchg(&a52_p282_fifo_once, 0, 1) == 0) {\n\t\t*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;\n\t\t*flags |= DSI_CTRL_CMD_FIFO_STORE;\n\t\tatomic_set(&a52_p282_fifo_inflight, 1);\n\t\ta52_ackfr_record("P276 282A m=fifo f=%x", *flags);\n\t}\n\n\t/* Validate the mode before sending the command */\n'''
    text = replace_one(text, old, new, 'Phase282 payload + one-shot mode override')

    old = '''\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D M s=3 p=1 r=%d", rc);\n\tif (rc) {\n\t\tDSI_CTRL_ERR(dsi_ctrl, "failed to copy message, rc=%d\\n", rc);\n\t\tgoto error;\n\t}\n\n\tif ((msg->flags & MIPI_DSI_MSG_LASTCOMMAND))\n'''
    new = '''\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D M s=3 p=1 r=%d", rc);\n\tif (rc) {\n\t\tDSI_CTRL_ERR(dsi_ctrl, "failed to copy message, rc=%d\\n", rc);\n\t\tgoto error;\n\t}\n\tif (a52_p276r_deep_active() && buffer && length == 8)\n\t\ta52_ackfr_record("P276 282E %02x%02x%02x%02x%02x%02x%02x%02x",\n\t\t\tbuffer[0], buffer[1], buffer[2], buffer[3],\n\t\t\tbuffer[4], buffer[5], buffer[6], buffer[7]);\n\n\tif ((msg->flags & MIPI_DSI_MSG_LASTCOMMAND))\n'''
    text = replace_one(text, old, new, 'Phase282 encoded packet trace')

    old = '''\t\tif (a52_p276r_deep_active() && dsi_hw_ops.get_error_status)\n\t\t\ta52_ackfr_record("P276 H E e=%llx",\n\t\t\t\t(unsigned long long)dsi_hw_ops.get_error_status(&dsi_ctrl->hw));\n\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n'''
    new = '''\t\tif (a52_p276r_deep_active() && dsi_hw_ops.get_error_status)\n\t\t\ta52_ackfr_record("P276 H E e=%llx",\n\t\t\t\t(unsigned long long)dsi_hw_ops.get_error_status(&dsi_ctrl->hw));\n\t\tif (a52_p276r_deep_active() &&\n\t\t\t\tatomic_read(&a52_p282_fifo_inflight))\n\t\t\ta52_ackfr_record("P276 282F a=0 t=1");\n\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 282Z q=2");\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n'''
    text = replace_one(text, old, new, 'Phase282 timeout result before retention')

    old = '''\t\t\tdsi_ctrl_dma_cmd_wait_for_done(&dsi_ctrl->dma_cmd_wait);\n\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K w=1");\n\t\t}\n\n\t\tdsi_ctrl_mask_overflow(dsi_ctrl, false);\n'''
    new = '''\t\t\tdsi_ctrl_dma_cmd_wait_for_done(&dsi_ctrl->dma_cmd_wait);\n\t\t\tif (a52_p276r_deep_active() &&\n\t\t\t\t\tatomic_xchg(&a52_p282_fifo_inflight, 0))\n\t\t\t\ta52_ackfr_record("P276 282F a=%d t=0",\n\t\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K w=1");\n\t\t}\n\n\t\tdsi_ctrl_mask_overflow(dsi_ctrl, false);\n'''
    text = replace_one(text, old, new, 'Phase282 FIFO success result')
    return text


def validate(text: str) -> None:
    required = [
        MARK,
        'static atomic_t a52_p282_fifo_once = ATOMIC_INIT(0);',
        'P276 282P t=%02x f=%x b=%02x%02x%02x',
        'msg->type == 0x29',
        '*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;',
        '*flags |= DSI_CTRL_CMD_FIFO_STORE;',
        'P276 282A m=fifo f=%x',
        'P276 282E %02x%02x%02x%02x%02x%02x%02x%02x',
        'P276 282F a=0 t=1',
        'P276 282F a=%d t=0',
        'P276 282Z q=2',
        'P276 280Z q=2',
        'a52_ackfr_retain_timeout_snapshot();',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase282 marker missing: ' + token)
    if text.index('P276 282Z q=2') > text.index('P276 280Z q=2'):
        raise SystemExit('Phase282 marker must precede inherited Phase280 freeze marker')
    if text.index('a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);') > text.index('P276 282Z q=2'):
        raise SystemExit('Phase281 pristine q2 snapshot must precede Phase282 timeout marker')


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
    print('Phase282 Golden FIFO-vs-memory DMA A/B probe: PASS')


if __name__ == '__main__':
    main()
