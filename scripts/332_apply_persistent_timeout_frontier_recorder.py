#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1'

REC_OLD = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
\t      fmt[3] == '6' && fmt[4] == ' ' && fmt[5] == '3' &&
\t      fmt[6] == '3' && fmt[7] == '1'))
\t\treturn;
"""
REC_NEW = """\t/* A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1
\t * Phase331 admitted only P276 331* after the Phase280 freeze. Phase332
\t * additionally admits P276 332* so the persistent exact-F0 GDM latch can
\t * carry timeout-frontier breadcrumbs across workqueue/task boundaries.
\t */
\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
\t      fmt[3] == '6' && fmt[4] == ' ' && fmt[5] == '3' &&
\t      fmt[6] == '3' && (fmt[7] == '1' || fmt[7] == '2')))
\t\treturn;
"""

HEAD_OLD = """\t\tif (a52_p276r_deep_active()) {
\t\t\ta52_ackfr_record("P276 280Z q=2");
\t\t\ta52_ackfr_retain_timeout_snapshot();
\t\t\ta52_ackfr_record("P276 331A q=2 st=%x m=%x", status, mask);
\t\t}
"""
HEAD_NEW = """\t\tif (a52_p293_gdm_armed(dsi_ctrl)) {
\t\t\ta52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
\t\t\t\t(unsigned int)a52_p276r_deep_active(), status, mask);
\t\t\ta52_ackfr_record("P276 280Z q=2");
\t\t\ta52_ackfr_retain_timeout_snapshot();
\t\t\ta52_ackfr_record("P276 332B q=2 retained=1");
\t\t}
"""

REPL = (
    ('if (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331B q=2 b=1 st=%x", status);',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\ta52_ackfr_record("P276 332C q=2 b=1 st=%x", status);'),
    ('if (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331C q=2 b=1 st=%x", status);',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\ta52_ackfr_record("P276 332D q=2 b=1 st=%x", status);'),
    ('if (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331B q=2 b=0 st=%x", status);',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\ta52_ackfr_record("P276 332C q=2 b=0 st=%x", status);'),
    ('if (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331D q=2 ss=1 v=%u", !!vdd);',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\ta52_ackfr_record("P276 332E q=2 ss=1 v=%u", !!vdd);'),
    ('if (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331E q=2 gpio=1");',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\ta52_ackfr_record("P276 332F q=2 gpio=1");'),
    ('if (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 331F q=2 dump=0");',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\t\ta52_ackfr_record("P276 332G q=2 dump=0");'),
    ('if (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 331G q=2 dump=1");',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\t\ta52_ackfr_record("P276 332H q=2 dump=1");'),
    ('if (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331H q=2 b=0");',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\t\ta52_ackfr_record("P276 332I q=2 b=0");'),
    ('if (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 331I q=2 dis=0");',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\ta52_ackfr_record("P276 332J q=2 dis=0");'),
    ('if (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 331J q=2 dis=1");',
     'if (a52_p293_gdm_armed(dsi_ctrl))\n\t\t\ta52_ackfr_record("P276 332K q=2 dis=1");'),
)

def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        raise SystemExit(f'Phase332 {label}: expected 1 match, found {n}')
    return s.replace(old, new, 1)

def patch_ctrl(s: str) -> str:
    if MARK in s:
        return s
    if 'A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1' not in s:
        raise SystemExit('Phase332 requires Phase331 source')
    s = one(s, HEAD_OLD, HEAD_NEW, 'persistent timeout head')
    for i, (old, new) in enumerate(REPL):
        s = one(s, old, new, f'frontier gate {i}')
    anchor = '/* A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1 */\n'
    s = one(s, anchor, anchor + '/* ' + MARK + ' */\n', 'source marker')
    return s

def patch_rec(s: str) -> str:
    if MARK in s:
        return s
    if 'A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1' not in s:
        raise SystemExit('Phase332 recorder requires Phase331')
    s = one(s, REC_OLD, REC_NEW, 'post-retention namespace')
    return s

def validate(c: str, r: str) -> None:
    for x in (
        MARK,
        'P276 332A q=2 g=1 d=%u st=%x m=%x',
        'P276 332B q=2 retained=1',
        'P276 332C q=2 b=1 st=%x',
        'P276 332D q=2 b=1 st=%x',
        'P276 332C q=2 b=0 st=%x',
        'P276 332E q=2 ss=1 v=%u',
        'P276 332F q=2 gpio=1',
        'P276 332G q=2 dump=0',
        'P276 332H q=2 dump=1',
        'P276 332I q=2 b=0',
        'P276 332J q=2 dis=0',
        'P276 332K q=2 dis=1',
    ):
        if x not in c:
            raise SystemExit('Phase332 ctrl token missing: ' + x)
    for x in ('A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1', MARK,
              "fmt[7] == '1' || fmt[7] == '2'"):
        if x not in r:
            raise SystemExit('Phase332 recorder token missing: ' + x)
    if c.count('a52_ackfr_retain_timeout_snapshot();') != 1:
        raise SystemExit('Phase332 retention call count changed')
    if not (c.index('P276 332A q=2') < c.index('P276 280Z q=2') <
            c.index('a52_ackfr_retain_timeout_snapshot();') < c.index('P276 332B q=2')):
        raise SystemExit('Phase332 retention ordering invalid')

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    cp = ns.root / CTRL
    rp = ns.root / REC
    if not cp.is_file() or not rp.is_file():
        raise SystemExit('Phase332 source missing')
    c, r = cp.read_text(), rp.read_text()
    if not ns.check_only:
        cp.write_text(patch_ctrl(c))
        rp.write_text(patch_rec(r))
    validate(cp.read_text(), rp.read_text())
    print('Phase332 persistent GDM timeout frontier recorder: PASS')

if __name__ == '__main__':
    main()
