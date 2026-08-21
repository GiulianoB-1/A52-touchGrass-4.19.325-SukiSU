#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE295_F05A5A_ADMISSION_WITNESS_V1'
PHASE293 = 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase295 {label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


def behavioral_counts(text: str) -> dict[str, int]:
    return {k: text.count(k) for k in [
        'DSI_W32(', 'writel(', 'writel_relaxed(', 'clk_set_rate(',
        'wait_for_completion_timeout(', 'msleep(', 'usleep_range(',
        'DSI_CTRL_CMD_FIFO_STORE', 'DSI_CTRL_CMD_FETCH_MEMORY',
    ]}


def patch(text: str) -> str:
    if MARK in text:
        return text
    if PHASE293 not in text:
        raise SystemExit('Phase295 requires the exact Phase293 passive GDM source')

    state = 'static atomic_t a52_p293_gdm_state = ATOMIC_INIT(0); /* 0 idle, 1 exact target */\n'
    state_new = '''/* A52_PHASE295_F05A5A_ADMISSION_WITNESS_V1
 * One-shot passive admission witness. It observes the first ctrl0 DSI message
 * whose payload begins F0 5A 5A, regardless of incoming controller flags,
 * MIPI flags, packet type or total length. It writes only to the existing A52
 * flight recorder and does not mutate the DSI message or controller state.
 */
static atomic_t a52_p295_f05a5a_seen = ATOMIC_INIT(0);
static atomic_t a52_p293_gdm_state = ATOMIC_INIT(0); /* 0 idle, 1 exact target */
'''
    text = one(text, state, state_new, 'witness state insertion')

    old = '''\tconst u8 *p;\n\n\tif (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||\n\t    *flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||\n\t    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)\n\t\treturn;\n\tif (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)\n\t\treturn;\n\tp = msg->tx_buf;\n'''
    new = '''\tconst u8 *p;\n\n\tif (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||\n\t    msg->tx_len < 3 || !msg->tx_buf)\n\t\treturn;\n\tp = msg->tx_buf;\n\tif (p[0] == 0xf0 && p[1] == 0x5a && p[2] == 0x5a &&\n\t    atomic_cmpxchg(&a52_p295_f05a5a_seen, 0, 1) == 0)\n\t\ta52_ackfr_record("GDM W00 c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x",\n\t\t\t*flags, msg->flags, msg->type, (unsigned int)msg->tx_len,\n\t\t\tp[0], p[1], p[2]);\n\n\t/* Preserve the Phase293 exact-target admission semantics unchanged. */\n\tif (*flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||\n\t    msg->type != 0x29 || msg->tx_len != 3)\n\t\treturn;\n\tif (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)\n\t\treturn;\n'''
    text = one(text, old, new, 'admission witness insertion')
    return text


def validate(text: str) -> None:
    for token in [
        PHASE293, MARK,
        'GDM W00 c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x',
        'GDM S00 c=0 in=%x mf=%x t=%x l=%u',
        'GDM DONE success=0 target=0/8/20/29/3',
        'P276 280Z q=2',
    ]:
        if token not in text:
            raise SystemExit('Phase295 validation missing: ' + token)
    if text.count('a52_p295_f05a5a_seen') != 2:
        raise SystemExit('Phase295 witness atomic use count changed unexpectedly')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    cp = args.root / CTRL
    if not cp.is_file():
        raise SystemExit('Phase295 reconstructed dsi_ctrl.c missing')
    before = cp.read_text()
    counts_before = behavioral_counts(before)
    if not args.check_only:
        cp.write_text(patch(before))
    after = cp.read_text()
    validate(after)
    if not args.check_only:
        counts_after = behavioral_counts(after)
        # One additional textual FETCH_MEMORY comparison is expected; all
        # behavioral/write/wait/clock/FIFO token counts must remain unchanged.
        expected = dict(counts_before)
        expected['DSI_CTRL_CMD_FETCH_MEMORY'] += 1
        if counts_after != expected:
            raise SystemExit(f'Phase295 behavioral-token count changed: {counts_before} -> {counts_after}')
    print('Phase295 passive F05A5A admission witness: PASS')


if __name__ == '__main__':
    main()
