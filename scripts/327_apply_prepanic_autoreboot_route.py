#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE327_PREPANIC_AUTOREBOOT_ROUTE_V2'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase327 {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    required = [
        'A52_PHASE326_RECORDER_FINALIZE_AUTOREBOOT_V1',
        'bool a52_p326_q2_recorded = false;',
        'a52_p326_q2_recorded = true;',
        'a52_p326_schedule_reboot();',
        'a52_ackfr_retain_timeout_snapshot();',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase327 requires Phase326 token: ' + token)

    old = '''\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n\t\t}\n\t\tif (status & mask) {\n'''
    new = '''\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n\t\t}\n\n\t\t/* A52_PHASE327_PREPANIC_AUTOREBOOT_ROUTE_V2\n\t\t * Phase326 proved the q2/retention path is reached but the later done:\n\t\t * finalizer is not, because the Samsung debug-failure path below can\n\t\t * enter SDE_DBG_DUMP(..., "panic"). Once the exact target q2 snapshot\n\t\t * and retention latch are complete, route directly to done so the\n\t\t * existing Phase326 one-shot 3 s reboot work can actually execute.\n\t\t * No display/clock/PHY behavior before the verdict is changed.\n\t\t */\n\t\tif (a52_p326_q2_recorded) {\n\t\t\ta52_ackfr_record("P276 327R retained q2=1 route=done");\n\t\t\tgoto done;\n\t\t}\n\n\t\tif (status & mask) {\n'''
    return one(text, old, new, 'post-retention route')


def validate(text: str) -> None:
    required = [
        MARK,
        'P276 327R retained q2=1 route=done',
        'if (a52_p326_q2_recorded) {',
        'goto done;',
        'P276 326R final q2=1 delay_ms=%u',
        'P276 326R reboot now',
        'kernel_restart("a52_phase326_capture_done")',
        'a52_ackfr_retain_timeout_snapshot();',
        'SDE_DBG_DUMP("all", "dbg_bus", "vbif_dbg_bus", "panic");',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase327 required token missing: ' + token)
    if text.count(MARK) != 1:
        raise SystemExit(f'Phase327 marker count must be 1, got {text.count(MARK)}')
    if text.count('P276 327R retained q2=1 route=done') != 1:
        raise SystemExit('Phase327 route marker must be unique')

    q2 = text.index('a52_p326_q2_recorded = true;')
    retain = text.index('a52_ackfr_retain_timeout_snapshot();', q2)
    route = text.index('P276 327R retained q2=1 route=done', retain)
    panic = text.index('SDE_DBG_DUMP("all", "dbg_bus", "vbif_dbg_bus", "panic");', route)
    done = text.index('\ndone:\n', route)
    arm = text.index('a52_p326_schedule_reboot();', done)
    if not q2 < retain < route < panic < done < arm:
        raise SystemExit('Phase327 ordering invariant failed')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    p = ns.root / TARGET
    if not p.is_file():
        raise SystemExit('Phase327 source missing: ' + str(p))
    text = p.read_text()
    if not ns.check_only:
        text = patch(text)
        p.write_text(text)
    validate(p.read_text())
    print('Phase327 retained-q2 pre-panic route to Phase326 autoreboot: PASS')


if __name__ == '__main__':
    main()
