#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
MARK = 'A52_PHASE287_DSI_DMA_FETCH_PROVENANCE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    for token in ['A52_PHASE286_GOLDEN_FDR_DMA_CHAIN_V1', 'P286 A c=%d mf=%x f=%x t=%u l=%u']:
        if token not in text:
            raise SystemExit('Phase287 requires Phase286: ' + token)

    # Exact memory-backed command-buffer provenance immediately after the
    # normal GEM sync and before CPU copies the encoded packet bytes.
    old = '''\t\tcmdbuf = (u8 *)(dsi_ctrl->vaddr);\n\n\t\tmsm_gem_sync(dsi_ctrl->tx_cmd_buf);\n\t\tfor (cnt = 0; cnt < length; cnt++)\n'''
    new = '''\t\tcmdbuf = (u8 *)(dsi_ctrl->vaddr);\n\n\t\tmsm_gem_sync(dsi_ctrl->tx_cmd_buf);\n\t\t/* %s */\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P287 M0 c=%d i=%llx va=%llx pre=%u add=%u",\n\t\t\t\tdsi_ctrl->cell_index,\n\t\t\t\t(unsigned long long)dsi_ctrl->cmd_buffer_iova,\n\t\t\t\t(unsigned long long)(uintptr_t)dsi_ctrl->vaddr,\n\t\t\t\tdsi_ctrl->cmd_len, length);\n\t\tfor (cnt = 0; cnt < length; cnt++)\n''' % MARK
    text = replace_one(text, old, new, 'Phase287 post-sync provenance')

    # The final DMA descriptor length is only committed on LASTCOMMAND.
    old = '''\t\t\tcmd_mem.length = dsi_ctrl->cmd_len;\n\t\t\tdsi_ctrl->cmd_len = 0;\n'''
    new = '''\t\t\tcmd_mem.length = dsi_ctrl->cmd_len;\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P287 M1 c=%d i=%llx len=%u last=1",\n\t\t\t\t\tdsi_ctrl->cell_index,\n\t\t\t\t\t(unsigned long long)cmd_mem.offset, cmd_mem.length);\n\t\t\tdsi_ctrl->cmd_len = 0;\n'''
    text = replace_one(text, old, new, 'Phase287 final DMA descriptor')

    # Capture the first 8 bytes from the actual mapped command buffer after the
    # copy, not merely from the temporary packet buffer. This proves CPU-side
    # contents at the address paired with the DMA IOVA.
    old = '''\t\tdsi_ctrl->cmd_len += length;\n\n\t\tif (!(msg->flags & MIPI_DSI_MSG_LASTCOMMAND)) {\n'''
    new = '''\t\tdsi_ctrl->cmd_len += length;\n\t\tif (a52_p276r_deep_active() && dsi_ctrl->cmd_len >= 8)\n\t\t\ta52_ackfr_record("P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x",\n\t\t\t\tdsi_ctrl->cell_index, cmdbuf[0], cmdbuf[1], cmdbuf[2], cmdbuf[3],\n\t\t\t\tcmdbuf[4], cmdbuf[5], cmdbuf[6], cmdbuf[7]);\n\n\t\tif (!(msg->flags & MIPI_DSI_MSG_LASTCOMMAND)) {\n'''
    text = replace_one(text, old, new, 'Phase287 mapped bytes')
    return text


def patch_hw(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE286_LOWLEVEL_SW_TRIGGER_V1' not in text:
        raise SystemExit('Phase287 requires Phase286 low-level trigger trace')

    # For immediate kickoff, all register reads happen only after the normal SW
    # trigger and after Phase286's post-trigger marker, so trigger timing is not
    # delayed by this deeper discriminator.
    old = '''\t\tif (a52_p286_dma_trace_active())\n\t\t\ta52_ackfr_record("P286 HK c=%d o=%x l=%x f=%x sw=1",\n\t\t\t\tctrl->index, cmd->offset, cmd->length, flags);\n'''
    new = old + '''\t\tif (a52_p286_dma_trace_active())\n\t\t\ta52_ackfr_record("P287 R c=%d ro=%x rl=%x ax=%x vb=%x",\n\t\t\t\tctrl->index, DSI_R32(ctrl, DSI_DMA_CMD_OFFSET),\n\t\t\t\tDSI_R32(ctrl, DSI_DMA_CMD_LENGTH),\n\t\t\t\tDSI_R32(ctrl, DSI_AXI2AHB_CTRL), DSI_R32(ctrl, DSI_VBIF_CTRL));\n'''
    text = replace_one(text, old, new, 'Phase287 immediate post-trigger readback')

    # Deferred path readback, likewise strictly after the SW trigger write.
    old = '''\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P286 HT c=%d sw=1", ctrl->index);\n}\n'''
    new = '''\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P286 HT c=%d sw=1", ctrl->index);\n\tif (a52_p286_dma_trace_active())\n\t\ta52_ackfr_record("P287 R c=%d ro=%x rl=%x ax=%x vb=%x",\n\t\t\tctrl->index, DSI_R32(ctrl, DSI_DMA_CMD_OFFSET),\n\t\t\tDSI_R32(ctrl, DSI_DMA_CMD_LENGTH),\n\t\t\tDSI_R32(ctrl, DSI_AXI2AHB_CTRL), DSI_R32(ctrl, DSI_VBIF_CTRL));\n}\n'''
    text = replace_one(text, old, new, 'Phase287 deferred post-trigger readback')
    # Place the phase marker in a comment without changing includes/behavior.
    text = text.replace('/* A52_PHASE286_LOWLEVEL_SW_TRIGGER_V1 */',
                        '/* A52_PHASE286_LOWLEVEL_SW_TRIGGER_V1\n * %s */' % MARK, 1)
    return text


def validate(ctrl: str, hw: str) -> None:
    for token in [MARK, 'P287 M0 c=%d i=%llx va=%llx pre=%u add=%u',
                  'P287 M1 c=%d i=%llx len=%u last=1',
                  'P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x']:
        if token not in ctrl and token not in hw:
            raise SystemExit('Phase287 marker missing: ' + token)
    if hw.count('P287 R c=%d ro=%x rl=%x ax=%x vb=%x') != 2:
        raise SystemExit('Phase287 requires immediate and deferred register readback sites')
    fn = hw.index('void dsi_ctrl_hw_cmn_trigger_command_dma(')
    write = hw.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);', fn)
    rec = hw.index('P287 R c=%d ro=%x rl=%x ax=%x vb=%x', fn)
    if rec < write:
        raise SystemExit('Phase287 deferred readback occurs before SW trigger write')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    cp, hp = args.root / CTRL, args.root / HW
    if not cp.is_file() or not hp.is_file():
        raise SystemExit('missing Phase287 source files')
    ct, ht = cp.read_text(), hp.read_text()
    if not args.check_only:
        ct, ht = patch_ctrl(ct), patch_hw(ht)
        cp.write_text(ct); hp.write_text(ht)
    validate(ct, ht)
    print('Phase287 DSI DMA fetch provenance recorder: PASS')


if __name__ == '__main__':
    main()
