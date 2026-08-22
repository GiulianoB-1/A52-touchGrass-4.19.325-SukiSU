#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HW = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
MARK = 'A52_PHASE303_GDM_VISIBLE_EXACT_F05A5A_V1'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase303 {label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1' not in text:
        raise SystemExit('Phase303 requires inherited Phase293 GDM controller trace')
    if 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' not in text:
        raise SystemExit('Phase303 requires inherited Phase280 timeout retention latch')

    marker_anchor = '/* A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1\n'
    marker_text = (
        '/* ' + MARK + '\n'
        ' * Phase302 hardware reached the first real compositor atomic commit and\n'
        ' * reproduced the retained command-DMA timeout, but Phase293 GDM records\n'
        ' * were silently rejected because they used a non-admitted "GDM" prefix.\n'
        ' * Re-emit those passive MMIO/IRQ snapshots through the already-admitted\n'
        ' * P276 namespace and arm them only for payload F0 5A 5A. No DSI write,\n'
        ' * command flag, wait, timeout, recovery, clock, panel, SMMU or RPMh state\n'
        ' * is changed.\n'
        ' */\n'
    )
    text = replace_once(text, marker_anchor, marker_text + marker_anchor,
                        'marker insertion')

    old_scope = '''\tif (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||\n\t    *flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||\n\t    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)\n\t\treturn;\n\tif (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)\n\t\treturn;\n\tp = msg->tx_buf;\n'''
    new_scope = '''\tif (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||\n\t    *flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||\n\t    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)\n\t\treturn;\n\tp = msg->tx_buf;\n\tif (p[0] != 0xF0 || p[1] != 0x5A || p[2] != 0x5A)\n\t\treturn;\n\tif (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)\n\t\treturn;\n'''
    text = replace_once(text, old_scope, new_scope, 'exact payload scope')

    # The recorder already admits P276 as critical persistent evidence. Phase293
    # used "GDM ..." literals, which compile and execute but are rejected by that
    # admission gate. Change formatting only; all trace call sites remain fixed.
    text = text.replace('"GDM ', '"P276 303 ')
    return text


def patch_hw(text: str) -> str:
    if 'A52_PHASE293_GKI_DMA_DONE_HW_REFERENCE_V1' not in text:
        raise SystemExit('Phase303 requires inherited Phase293 GDM HW trace')
    text = text.replace('"GDM ', '"P276 303 ')
    return text


def validate(ctrl: str, hw: str) -> None:
    combined = ctrl + hw
    required = [
        MARK,
        'p[0] != 0xF0 || p[1] != 0x5A || p[2] != 0x5A',
        'P276 303 S00 c=0 in=%x mf=%x t=%x l=%u',
        'P276 303 S00p p=%02x%02x%02x',
        'P276 303 S03 irq=%d in=%x st=%x',
        'P276 303 S04 irq=%d in=%x st=%x',
        'P276 303 S05 dc=%x off=%x len=%x fc=%x',
        'P276 303 S06 st=%x fs=%x ln=%x ck=%x',
        'P276 303 S07 seen=1 st=%x in=%x irq0=%d',
        'P276 303 S08 ret=%d irq=%d in=%x st=%x',
        'P276 303 S09 st=%x fs=%x ln=%x ck=%x',
        'P276 303 DONE success=0 target=0/8/20/29/3',
        'P276 280Z q=2',
    ]
    for token in required:
        if token not in combined:
            raise SystemExit('Phase303 required token missing: ' + token)
    if 'a52_ackfr_record("GDM ' in combined:
        raise SystemExit('Phase303 still contains recorder-rejected GDM runtime records')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    cp = args.root / CTRL
    hp = args.root / HW
    if not cp.is_file() or not hp.is_file():
        raise SystemExit('Phase303 source files missing')

    ctrl = cp.read_text()
    hw = hp.read_text()
    if not args.check_only:
        ctrl = patch_ctrl(ctrl)
        hw = patch_hw(hw)
        cp.write_text(ctrl)
        hp.write_text(hw)

    validate(cp.read_text(), hp.read_text())
    print('Phase303 exact F0 5A 5A persistent GDM visibility: PASS')


if __name__ == '__main__':
    main()
