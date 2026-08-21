#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REL = Path('drivers/a52_display/msm/dsi/dsi_clk_manager.c')
MARK = 'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1'
PREREQ = 'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase291: expected exactly one {label} anchor, found {n}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if PREREQ not in text:
        raise SystemExit('Phase291: Phase284 clock-causality prerequisite missing')
    if 'a52_p284_clk_rate' not in text:
        raise SystemExit('Phase291: Phase284 clock-rate helper missing')

    text = replace_one(
        text,
        'static unsigned int a52_p284_apply_logs;\n',
        'static unsigned int a52_p284_apply_logs;\n'
        'static unsigned int a52_p291_recovery_logs;\n',
        'Phase284 apply-log counter')

    old = '''\tif (mngr->is_cont_splash_enabled) {\n\t\tif (a52_p284_apply_logs < 12) {\n\t\t\ta52_ackfr_record("P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx",\n\t\t\t\tindex,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.pix_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate);\n\t\t\ta52_p284_apply_logs++;\n\t\t}\n\t\treturn 0;\n\t}\n'''

    new = '''\t/* A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1\n\t * TouchGrass normally preserves bootloader-programmed HS rates while\n\t * continuous splash owns the display. Phase285 proved that this GKI\n\t * integration can instead arrive here with valid non-zero cached target\n\t * rates while the Linux clock framework reports one or more HS clocks as\n\t * 0 Hz. Preserve the Golden behavior when inherited rates are sane, but\n\t * if the handoff is demonstrably zero-rate, fall through to the existing\n\t * Golden clock-rate programming sequence. Never program a zero cached target.\n\t */\n\tif (mngr->is_cont_splash_enabled) {\n\t\tunsigned long a52_byte_now =\n\t\t\ta52_p284_clk_rate(link_hs_clks->byte_clk);\n\t\tunsigned long a52_pixel_now =\n\t\t\ta52_p284_clk_rate(link_hs_clks->pixel_clk);\n\t\tunsigned long a52_intf_now =\n\t\t\ta52_p284_clk_rate(link_hs_clks->byte_intf_clk);\n\t\tbool a52_targets_valid = l_clks->freq.byte_clk_rate &&\n\t\t\tl_clks->freq.pix_clk_rate &&\n\t\t\t(!link_hs_clks->byte_intf_clk ||\n\t\t\t l_clks->freq.byte_intf_clk_rate);\n\t\tbool a52_zero_handoff = !a52_byte_now || !a52_pixel_now ||\n\t\t\t(link_hs_clks->byte_intf_clk && !a52_intf_now);\n\n\t\tif (a52_p284_apply_logs < 12) {\n\t\t\ta52_ackfr_record("P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx",\n\t\t\t\tindex,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.pix_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate);\n\t\t\ta52_p284_apply_logs++;\n\t\t}\n\n\t\tif (!a52_targets_valid || !a52_zero_handoff)\n\t\t\treturn 0;\n\n\t\tif (a52_p291_recovery_logs < 4) {\n\t\t\ta52_ackfr_record("P291 C0 c=%d b=%llx p=%llx i=%llx ab=%lx ap=%lx ai=%lx",\n\t\t\t\tindex,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.pix_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate,\n\t\t\t\ta52_byte_now, a52_pixel_now, a52_intf_now);\n\t\t\ta52_p291_recovery_logs++;\n\t\t}\n\t}\n'''
    text = replace_one(text, old, new, 'continuous-splash early return')
    return text


def self_test() -> None:
    sample = '''A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1\nstatic unsigned int a52_p284_apply_logs;\nstatic unsigned long a52_p284_clk_rate(void *clk) { return 0; }\n\tif (mngr->is_cont_splash_enabled) {\n\t\tif (a52_p284_apply_logs < 12) {\n\t\t\ta52_ackfr_record("P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx",\n\t\t\t\tindex,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.pix_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate);\n\t\t\ta52_p284_apply_logs++;\n\t\t}\n\t\treturn 0;\n\t}\n'''
    out = patch(sample)
    assert MARK in out
    assert 'a52_targets_valid' in out
    assert 'a52_zero_handoff' in out
    assert 'P291 C0' in out
    assert 'return 0;' in out
    assert out.count('a52_p291_recovery_logs') >= 2
    assert patch(out) == out
    print('Phase291 self-test: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path)
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    if args.root is None:
        raise SystemExit('--root is required unless --self-test is used')

    path = args.root / REL
    text = path.read_text()
    out = patch(text)
    if args.check_only:
        if MARK not in out:
            raise SystemExit('Phase291 marker missing after dry-run patch')
        print('Phase291 check-only: PASS')
        return
    path.write_text(out)
    print(f'Phase291 patched {path}')


if __name__ == '__main__':
    main()
