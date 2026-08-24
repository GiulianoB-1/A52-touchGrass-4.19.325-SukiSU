#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

DISP_REL = Path('drivers/clk/qcom/dispcc-lagoon.c')
PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
MARK = 'A52_PHASE310_GKI_LAGOON_DISPCC_SNAPSHOT_V1'
P310 = 'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase310 DISP_CC {label}: expected one match, found {n}')
    return text.replace(old, new, 1)


def function_span(text: str, name: str) -> tuple[int, int, int]:
    pat = re.compile(r'(?m)^(?:static\s+)?int\s+' + re.escape(name) + r'\s*\([^;]*?\)\s*\{')
    matches = list(pat.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f'Phase310 DISP_CC {name}: expected one definition, found {len(matches)}')
    m = matches[0]
    brace = text.find('{', m.start(), m.end())
    depth = 0
    i = brace
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
                    return m.start(), brace, i + 1
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
    raise SystemExit(f'Phase310 DISP_CC {name}: unterminated body')


def inject_before_last_return(text: str, name: str, code: str) -> str:
    start, _brace, end = function_span(text, name)
    body = text[start:end]
    anchor = '\n\treturn ret;\n'
    pos = body.rfind(anchor)
    if pos < 0:
        raise SystemExit(f'Phase310 DISP_CC {name}: final return ret missing')
    body = body[:pos] + code + body[pos:]
    return text[:start] + body + text[end:]


def patch_disp(text: str) -> str:
    if MARK in text:
        return text
    if 'disp_cc_lagoon_probe' not in text or 'qcom_cc_really_probe' not in text:
        raise SystemExit('Phase310 DISP_CC Lagoon source shape not recognized')

    inc = '#include "vdd-level-lagoon.h"\n'
    if inc not in text:
        raise SystemExit('Phase310 DISP_CC include anchor missing')
    text = text.replace(
        inc,
        inc + '#include <linux/a52_ack_secure_flight_recorder.h>\n',
        1,
    )

    anchor = 'static DEFINE_VDD_REGULATORS(vdd_cx, VDD_NUM, 1, vdd_corner);\n'
    helper = r'''/* A52_PHASE310_GKI_LAGOON_DISPCC_SNAPSHOT_V1
 * Read-only physical view of the Lagoon display clock controller at the
 * exact F0 q0/q1/q2 points. Branch registers expose enable/halt state while
 * RCG CMD/CFG registers expose the active source/mux/divider configuration.
 */
#define A52_P310_DISP_PCLK0_BRANCH      0x100c
#define A52_P310_DISP_BYTE0_BRANCH      0x102c
#define A52_P310_DISP_BYTE0_INTF_BRANCH 0x1030
#define A52_P310_DISP_ESC0_BRANCH       0x1034
#define A52_P310_DISP_PCLK0_CMD         0x1064
#define A52_P310_DISP_PCLK0_CFG         0x1068
#define A52_P310_DISP_BYTE0_CMD         0x10c4
#define A52_P310_DISP_BYTE0_CFG         0x10c8
#define A52_P310_DISP_ESC0_CMD          0x10e0
#define A52_P310_DISP_ESC0_CFG          0x10e4

static struct regmap *a52_p310_dispcc_regmap;

void a52_p310_dispcc_snapshot(unsigned int point)
{
	struct regmap *regmap = READ_ONCE(a52_p310_dispcc_regmap);
	unsigned int p = ~0U, b = ~0U, i = ~0U, e = ~0U;
	unsigned int pc = ~0U, pf = ~0U, bc = ~0U, bf = ~0U;
	unsigned int ec = ~0U, ef = ~0U;
	int rc = 0;

	if (!regmap) {
		a52_ackfr_record("P276 310DX q=%u map=0", point);
		return;
	}
	if (regmap_read(regmap, A52_P310_DISP_PCLK0_BRANCH, &p)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_BYTE0_BRANCH, &b)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_BYTE0_INTF_BRANCH, &i)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_ESC0_BRANCH, &e)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_PCLK0_CMD, &pc)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_PCLK0_CFG, &pf)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_BYTE0_CMD, &bc)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_BYTE0_CFG, &bf)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_ESC0_CMD, &ec)) rc = -EIO;
	if (regmap_read(regmap, A52_P310_DISP_ESC0_CFG, &ef)) rc = -EIO;

	a52_ackfr_record("P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x",
		point, rc, p, b, i, e);
	a52_ackfr_record("P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x",
		point, pc, pf, bc, bf, ec, ef);
}
EXPORT_SYMBOL_GPL(a52_p310_dispcc_snapshot);

'''
    text = replace_once(text, anchor, helper + anchor, 'physical snapshot helper')

    code = '''\n\tWRITE_ONCE(a52_p310_dispcc_regmap, regmap);\n\ta52_ackfr_record("P276 310DR p=1");\n'''
    return inject_before_last_return(text, 'disp_cc_lagoon_probe', code)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    if P310 not in text:
        raise SystemExit('Phase310 DISP_CC requires consolidated Phase310 observer first')

    old = ('extern void a52_p310_clk_snapshot(unsigned int index, unsigned int point);\n'
           'extern void a52_p310_pll_lifecycle_snapshot(unsigned int index, unsigned int point);\n\n')
    new = (old[:-1] +
           'extern void a52_p310_dispcc_snapshot(unsigned int point);\n\n')
    text = replace_once(text, old, new, 'snapshot declaration')

    old = '''\ta52_p310_clk_snapshot(index, point);\n\ta52_p310_pll_lifecycle_snapshot(index, point);\n'''
    new = '''\ta52_p310_clk_snapshot(index, point);\n\ta52_p310_dispcc_snapshot(point);\n\ta52_p310_pll_lifecycle_snapshot(index, point);\n'''
    text = replace_once(text, old, new, 'exact-F0 physical snapshot call')

    # Put the marker in PHY too so check-only/idempotence is explicit.
    marker_anchor = 'extern void a52_p310_dispcc_snapshot(unsigned int point);\n'
    text = text.replace(marker_anchor,
        marker_anchor + '/* ' + MARK + ' */\n', 1)
    return text


def validate(disp: str, phy: str) -> None:
    combined = disp + phy
    required = [
        MARK,
        'A52_P310_DISP_PCLK0_BRANCH      0x100c',
        'A52_P310_DISP_BYTE0_BRANCH      0x102c',
        'A52_P310_DISP_BYTE0_INTF_BRANCH 0x1030',
        'A52_P310_DISP_ESC0_BRANCH       0x1034',
        'A52_P310_DISP_PCLK0_CMD         0x1064',
        'A52_P310_DISP_BYTE0_CMD         0x10c4',
        'A52_P310_DISP_ESC0_CMD          0x10e0',
        'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x',
        'P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x',
        'P276 310DR p=1',
        'regmap_read(regmap, A52_P310_DISP_BYTE0_BRANCH, &b)',
        'a52_p310_dispcc_snapshot(point);',
        P310,
    ]
    for token in required:
        if token not in combined:
            raise SystemExit('Phase310 DISP_CC required token missing: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()

    disp_path = ns.root / DISP_REL
    phy_path = ns.root / PHY_REL
    for p in (disp_path, phy_path):
        if not p.is_file():
            raise SystemExit('Phase310 DISP_CC source missing: ' + str(p))

    if not ns.check_only:
        disp_path.write_text(patch_disp(disp_path.read_text()))
        phy_path.write_text(patch_phy(phy_path.read_text()))

    validate(disp_path.read_text(), phy_path.read_text())
    print('Phase310 Lagoon DISP_CC physical q0/q1/q2 snapshot: PASS')


if __name__ == '__main__':
    main()
