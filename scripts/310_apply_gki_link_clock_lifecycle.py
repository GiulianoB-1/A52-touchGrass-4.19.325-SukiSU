#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

CLK_REL = Path('drivers/a52_display/msm/dsi/dsi_clk_manager.c')
PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
MARK = 'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V1'
P309 = 'A52_PHASE309_GKI_CLAMP_RELEASE_LATCH_V1'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase310 {label}: expected exactly one match, found {n}')
    return text.replace(old, new, 1)


def function_span(text: str, name: str) -> tuple[int, int, int]:
    pat = re.compile(r'(?m)^(?:static\s+)?(?:int|void)\s+' + re.escape(name) + r'\s*\([^;]*?\)\s*\{')
    matches = list(pat.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f'Phase310 {name}: expected one definition, found {len(matches)}')
    m = matches[0]
    open_brace = text.find('{', m.start(), m.end())
    depth = 0
    i = open_brace
    state = 'code'
    quote = ''
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ''
        if state == 'code':
            if c == '/' and n == '*':
                state = 'block'; i += 2; continue
            if c == '/' and n == '/':
                state = 'line'; i += 2; continue
            if c in ('\"', "'"):
                state = 'string'; quote = c; i += 1; continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return m.start(), open_brace, i + 1
            i += 1
        elif state == 'block':
            if c == '*' and n == '/':
                state = 'code'; i += 2
            else:
                i += 1
        elif state == 'line':
            if c == '\n':
                state = 'code'
            i += 1
        else:
            if c == '\\':
                i += 2
            elif c == quote:
                state = 'code'; i += 1
            else:
                i += 1
    raise SystemExit(f'Phase310 {name}: unterminated body')


def inject_entry(text: str, name: str, code: str) -> str:
    _start, brace, _end = function_span(text, name)
    return text[:brace + 1] + '\n' + code + text[brace + 1:]


def rewrite_returns_rc(text: str, name: str, before: str) -> str:
    start, _brace, end = function_span(text, name)
    body = text[start:end]
    count = body.count('\treturn rc;')
    if count < 1:
        raise SystemExit(f'Phase310 {name}: no return rc anchor')
    body = body.replace('\treturn rc;', before + '\n\treturn rc;')
    return text[:start] + body + text[end:]


def patch_clk(text: str) -> str:
    if MARK in text:
        return text
    if 'dsi_clk_update_link_clk_state' not in text or 'dsi_link_hs_clk_set_rate' not in text:
        raise SystemExit('Phase310 DSI clock manager shape not recognized')

    inc = '#include "dsi_clk.h"\n'
    if inc not in text:
        raise SystemExit('Phase310 dsi_clk.h include anchor missing')
    text = text.replace(
        inc,
        inc + '#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        1,
    )

    anchor = 'struct dsi_clk_mngr {\n'
    helper = '''/* A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V1
 * Sticky software-only history for the DSI link-clock path. This records
 * whether the existing set-rate/prepare/enable/start/update functions ran,
 * whether continuous splash skipped HS set-rate, and their last rc values.
 * It does not add or remove any clk operation.
 */
static atomic_t a52_p310_sr_in;
static atomic_t a52_p310_sr_run;
static atomic_t a52_p310_sr_skip;
static atomic_t a52_p310_sr_ok;
static atomic_t a52_p310_sr_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_sr_idx = ATOMIC_INIT(-1);
static atomic_t a52_p310_prepare_in;
static atomic_t a52_p310_prepare_ok;
static atomic_t a52_p310_prepare_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_enable_in;
static atomic_t a52_p310_enable_ok;
static atomic_t a52_p310_enable_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_disable_in;
static atomic_t a52_p310_unprepare_in;
static atomic_t a52_p310_hs_start;
static atomic_t a52_p310_hs_stop;
static atomic_t a52_p310_lp_start;
static atomic_t a52_p310_lp_stop;
static atomic_t a52_p310_update_in;
static atomic_t a52_p310_update_ok;
static atomic_t a52_p310_update_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_last_type = ATOMIC_INIT(-1);
static atomic_t a52_p310_last_state = ATOMIC_INIT(-1);
static atomic_t a52_p310_last_enable = ATOMIC_INIT(-1);

void a52_p310_clk_snapshot(unsigned int index, unsigned int point)
{
\ta52_ackfr_record("P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d",
\t\tpoint, index,
\t\tatomic_read(&a52_p310_sr_in), atomic_read(&a52_p310_sr_run),
\t\tatomic_read(&a52_p310_sr_skip), atomic_read(&a52_p310_sr_ok),
\t\tatomic_read(&a52_p310_sr_rc), atomic_read(&a52_p310_sr_idx));
\ta52_ackfr_record("P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d",
\t\tpoint, atomic_read(&a52_p310_prepare_in),
\t\tatomic_read(&a52_p310_prepare_ok), atomic_read(&a52_p310_prepare_rc),
\t\tatomic_read(&a52_p310_enable_in), atomic_read(&a52_p310_enable_ok),
\t\tatomic_read(&a52_p310_enable_rc), atomic_read(&a52_p310_hs_start),
\t\tatomic_read(&a52_p310_hs_stop));
\ta52_ackfr_record("P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d",
\t\tpoint, atomic_read(&a52_p310_disable_in),
\t\tatomic_read(&a52_p310_unprepare_in), atomic_read(&a52_p310_lp_start),
\t\tatomic_read(&a52_p310_lp_stop), atomic_read(&a52_p310_update_in),
\t\tatomic_read(&a52_p310_update_ok), atomic_read(&a52_p310_update_rc),
\t\tatomic_read(&a52_p310_last_type), atomic_read(&a52_p310_last_state),
\t\tatomic_read(&a52_p310_last_enable));
}

'''
    text = replace_once(text, anchor, helper + anchor, 'state helper')

    text = inject_entry(text, 'dsi_link_hs_clk_set_rate',
        '\tatomic_inc(&a52_p310_sr_in);\n\tatomic_set(&a52_p310_sr_idx, index);')

    splash_old = '''\tif (mngr->is_cont_splash_enabled)\n\t\treturn 0;\n\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n'''
    splash_new = '''\tif (mngr->is_cont_splash_enabled) {\n\t\tatomic_inc(&a52_p310_sr_skip);\n\t\treturn 0;\n\t}\n\n\tatomic_inc(&a52_p310_sr_run);\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n'''
    text = replace_once(text, splash_old, splash_new, 'continuous-splash set-rate gate')
    text = rewrite_returns_rc(text, 'dsi_link_hs_clk_set_rate',
        '\tatomic_set(&a52_p310_sr_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_sr_ok);')

    text = inject_entry(text, 'dsi_link_hs_clk_prepare', '\tatomic_inc(&a52_p310_prepare_in);')
    text = rewrite_returns_rc(text, 'dsi_link_hs_clk_prepare',
        '\tatomic_set(&a52_p310_prepare_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_prepare_ok);')

    text = inject_entry(text, 'dsi_link_hs_clk_enable', '\tatomic_inc(&a52_p310_enable_in);')
    text = rewrite_returns_rc(text, 'dsi_link_hs_clk_enable',
        '\tatomic_set(&a52_p310_enable_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_enable_ok);')

    text = inject_entry(text, 'dsi_link_hs_clk_disable', '\tatomic_inc(&a52_p310_disable_in);')
    text = inject_entry(text, 'dsi_link_hs_clk_unprepare', '\tatomic_inc(&a52_p310_unprepare_in);')
    text = inject_entry(text, 'dsi_link_hs_clk_start', '\tatomic_inc(&a52_p310_hs_start);')
    text = inject_entry(text, 'dsi_link_hs_clk_stop', '\tatomic_inc(&a52_p310_hs_stop);')
    text = inject_entry(text, 'dsi_link_lp_clk_start', '\tatomic_inc(&a52_p310_lp_start);')
    text = inject_entry(text, 'dsi_link_lp_clk_stop', '\tatomic_inc(&a52_p310_lp_stop);')

    text = inject_entry(text, 'dsi_clk_update_link_clk_state',
        '\tatomic_inc(&a52_p310_update_in);\n'
        '\tatomic_set(&a52_p310_last_type, (int)l_type);\n'
        '\tatomic_set(&a52_p310_last_state, (int)l_state);\n'
        '\tatomic_set(&a52_p310_last_enable, enable ? 1 : 0);')
    text = rewrite_returns_rc(text, 'dsi_clk_update_link_clk_state',
        '\tatomic_set(&a52_p310_update_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_update_ok);')
    return text


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    if P309 not in text:
        raise SystemExit('Phase310 requires inherited Phase309 observer')

    old = 'extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);\n\n'
    new = old + 'extern void a52_p310_clk_snapshot(unsigned int index, unsigned int point);\n\n'
    text = replace_once(text, old, new, 'Phase310 clock snapshot declaration')

    old_call = '''\ta52_p309_clamp_snapshot(index, point);\n\ta52_p308_pll_snapshot(index, point);\n}\n'''
    new_call = '''\ta52_p309_clamp_snapshot(index, point);\n\ta52_p310_clk_snapshot(index, point);\n\ta52_p308_pll_snapshot(index, point);\n}\n'''
    return replace_once(text, old_call, new_call, 'exact-F0 lifecycle snapshot')


def validate(clk: str, phy: str) -> None:
    alltxt = clk + phy
    required = [
        MARK,
        'P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d',
        'P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d',
        'P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d',
        'atomic_inc(&a52_p310_sr_skip);',
        'atomic_inc(&a52_p310_sr_run);',
        'atomic_inc(&a52_p310_prepare_in);',
        'atomic_inc(&a52_p310_enable_in);',
        'atomic_inc(&a52_p310_hs_start);',
        'atomic_inc(&a52_p310_update_in);',
        'a52_p310_clk_snapshot(index, point);',
        P309,
    ]
    for token in required:
        if token not in alltxt:
            raise SystemExit('Phase310 required token missing: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    clk_path = args.root / CLK_REL
    phy_path = args.root / PHY_REL
    for p in (clk_path, phy_path):
        if not p.is_file():
            raise SystemExit('Phase310 source missing: ' + str(p))

    if not args.check_only:
        clk_path.write_text(patch_clk(clk_path.read_text()))
        phy_path.write_text(patch_phy(phy_path.read_text()))

    validate(clk_path.read_text(), phy_path.read_text())
    print('Phase310 GKI sticky DSI link-clock lifecycle observer: PASS')


if __name__ == '__main__':
    main()
