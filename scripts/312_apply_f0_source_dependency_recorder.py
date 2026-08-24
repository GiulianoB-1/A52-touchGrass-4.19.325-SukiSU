#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
DISP_REL = Path('drivers/clk/qcom/dispcc-lagoon.c')
PHY_MARK = 'A52_PHASE312_GKI_F0_PHY_DEPENDENCY_RECORDER_V1'
DISP_MARK = 'A52_PHASE312_GKI_DISPCC_MISC_CMD_RECORDER_V1'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase312 {label}: expected one match, found {n}')
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if PHY_MARK in text:
        return text
    for required in (
        'A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1',
        'A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1',
        'P276 308T q=%u %x %x %x %x %x',
    ):
        if required not in text:
            raise SystemExit('Phase312 PHY inherited dependency missing: ' + required)

    old = '''#define A52_P308_V3_LNX_TX_DCTRL(n) (0x22c + (0x80 * (n)))
extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);
'''
    new = '''#define A52_P308_V3_LNX_TX_DCTRL(n) (0x22c + (0x80 * (n)))

/* A52_PHASE312_GKI_F0_PHY_DEPENDENCY_RECORDER_V1
 * Source-derived read-only coverage of the v3 state that normal
 * dsi_phy_hw_v3_0_enable()/lane_settings() programs but continuous splash
 * can inherit from the bootloader. Phase307 already records the common block
 * and Phase308 records TX_DCTRL, so Phase312 adds only timing + remaining
 * per-lane electrical/config state. Extra records are emitted at q0 only.
 */
#define A52_P312_V3_TIMING(n)       (0x0ac + (0x4 * (n)))
#define A52_P312_V3_LNX_CFG0(n)     (0x200 + (0x80 * (n)))
#define A52_P312_V3_LNX_CFG1(n)     (0x204 + (0x80 * (n)))
#define A52_P312_V3_LNX_CFG2(n)     (0x208 + (0x80 * (n)))
#define A52_P312_V3_LNX_CFG3(n)     (0x20c + (0x80 * (n)))
#define A52_P312_V3_LNX_PIN_SWAP(n) (0x214 + (0x80 * (n)))
#define A52_P312_V3_LNX_HSTX(n)     (0x218 + (0x80 * (n)))
#define A52_P312_V3_LNX_OFF_TOP(n)  (0x21c + (0x80 * (n)))
#define A52_P312_V3_LNX_OFF_BOT(n)  (0x220 + (0x80 * (n)))
#define A52_P312_V3_LNX_LPTX(n)     (0x224 + (0x80 * (n)))
#define A52_P312_V3_LNX_LPRX(n)     (0x228 + (0x80 * (n)))
extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);
'''
    text = replace_once(text, old, new, 'PHY register definitions')

    text = replace_once(text, '\tu32 ver;\n', '\tu32 ver, lane;\n', 'PHY q0 lane iterator')

    old = '''\ta52_ackfr_record("P276 308T q=%u %x %x %x %x %x", point,
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(0)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(1)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(2)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(3)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(4)));
\ta52_p308_pll_snapshot(index, point);
'''
    new = '''\ta52_ackfr_record("P276 308T q=%u %x %x %x %x %x", point,
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(0)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(1)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(2)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(3)),
\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(4)));
\tif (point == 0) {
\t\t/* HW timing versus the exact software timing cfg that normal v3
\t\t * enable would write. dsi_phy_enable() calculates/populates cfg
\t\t * before its continuous-splash skip.
\t\t */
\t\ta52_ackfr_record("P276 312T0 %x %x %x %x %x %x",
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(0)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(1)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(2)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(3)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(4)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(5)));
\t\ta52_ackfr_record("P276 312T1 %x %x %x %x %x %x",
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(6)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(7)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(8)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(9)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(10)),
\t\t\treadl_relaxed(base + A52_P312_V3_TIMING(11)));
\t\ta52_ackfr_record("P276 312TE0 %x %x %x %x %x %x",
\t\t\tphy->cfg.timing.lane_v3[0], phy->cfg.timing.lane_v3[1],
\t\t\tphy->cfg.timing.lane_v3[2], phy->cfg.timing.lane_v3[3],
\t\t\tphy->cfg.timing.lane_v3[4], phy->cfg.timing.lane_v3[5]);
\t\ta52_ackfr_record("P276 312TE1 %x %x %x %x %x %x",
\t\t\tphy->cfg.timing.lane_v3[6], phy->cfg.timing.lane_v3[7],
\t\t\tphy->cfg.timing.lane_v3[8], phy->cfg.timing.lane_v3[9],
\t\t\tphy->cfg.timing.lane_v3[10], phy->cfg.timing.lane_v3[11]);

\t\tfor (lane = 0; lane < DSI_LANE_MAX; lane++) {
\t\t\ta52_ackfr_record("P276 312L0 l=%u %x %x %x %x %x", lane,
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_CFG0(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_CFG1(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_CFG2(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_CFG3(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_PIN_SWAP(lane)));
\t\t\ta52_ackfr_record("P276 312L1 l=%u %x %x %x %x %x", lane,
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_HSTX(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_OFF_TOP(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_OFF_BOT(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_LPTX(lane)),
\t\t\t\treadl_relaxed(base + A52_P312_V3_LNX_LPRX(lane)));
\t\t\ta52_ackfr_record("P276 312LE l=%u %x %x %x %x %x %x", lane,
\t\t\t\tphy->cfg.lanecfg.lane[lane][0],
\t\t\t\tphy->cfg.lanecfg.lane[lane][1],
\t\t\t\tphy->cfg.lanecfg.lane[lane][2],
\t\t\t\tphy->cfg.lanecfg.lane[lane][3],
\t\t\t\tphy->cfg.strength.lane[lane][0],
\t\t\t\tphy->cfg.strength.lane[lane][1]);
\t\t}
\t}
\ta52_p308_pll_snapshot(index, point);
'''
    return replace_once(text, old, new, 'exact-F0 source dependency snapshot')


def patch_disp(text: str) -> str:
    if DISP_MARK in text:
        return text
    if 'A52_PHASE310_GKI_LAGOON_DISPCC_SNAPSHOT_V1' not in text:
        raise SystemExit('Phase312 DISP_CC requires inherited Phase310 observer')

    old = '#define A52_P310_DISP_ESC0_CFG          0x10e4\n'
    new = old + '''
/* A52_PHASE312_GKI_DISPCC_MISC_CMD_RECORDER_V1
 * A52 DSI ctrl hw v2.4 reuses v2.2 phy_reset_config/config_clk_gating ops.
 * Both operate on DISP_CC MISC_CMD; Phase310 did not record this register.
 */
#define A52_P312_DISP_MISC_CMD          0x0000
'''
    text = replace_once(text, old, new, 'DISP_CC MISC_CMD definition')

    text = replace_once(
        text,
        '\tunsigned int ec = ~0U, ef = ~0U;\n',
        '\tunsigned int ec = ~0U, ef = ~0U, misc = ~0U;\n',
        'DISP_CC MISC_CMD variable')

    old = '''\tif (regmap_read(regmap, A52_P310_DISP_ESC0_CMD, &ec)) rc = -EIO;
\tif (regmap_read(regmap, A52_P310_DISP_ESC0_CFG, &ef)) rc = -EIO;

\ta52_ackfr_record("P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x",
'''
    new = '''\tif (regmap_read(regmap, A52_P310_DISP_ESC0_CMD, &ec)) rc = -EIO;
\tif (regmap_read(regmap, A52_P310_DISP_ESC0_CFG, &ef)) rc = -EIO;
\tif (point == 0 && regmap_read(regmap, A52_P312_DISP_MISC_CMD, &misc))
\t\trc = -EIO;

\ta52_ackfr_record("P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x",
'''
    text = replace_once(text, old, new, 'DISP_CC MISC_CMD read')

    old = '''\ta52_ackfr_record("P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x",
\t\tpoint, pc, pf, bc, bf, ec, ef);
}
'''
    new = '''\ta52_ackfr_record("P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x",
\t\tpoint, pc, pf, bc, bf, ec, ef);
\tif (point == 0)
\t\ta52_ackfr_record("P276 312D q=%u rc=%d m=%x b0=%u b5=%u b7=%u b9=%u",
\t\t\tpoint, rc, misc, (misc >> 0) & 1, (misc >> 5) & 1,
\t\t\t(misc >> 7) & 1, (misc >> 9) & 1);
}
'''
    return replace_once(text, old, new, 'DISP_CC MISC_CMD record')


def validate(phy: str, disp: str) -> None:
    required = [
        PHY_MARK,
        DISP_MARK,
        'A52_P312_V3_TIMING(n)       (0x0ac + (0x4 * (n)))',
        'A52_P312_V3_LNX_CFG0(n)     (0x200 + (0x80 * (n)))',
        'A52_P312_V3_LNX_LPRX(n)     (0x228 + (0x80 * (n)))',
        'P276 312T0 %x %x %x %x %x %x',
        'P276 312T1 %x %x %x %x %x %x',
        'P276 312TE0 %x %x %x %x %x %x',
        'P276 312TE1 %x %x %x %x %x %x',
        'P276 312L0 l=%u %x %x %x %x %x',
        'P276 312L1 l=%u %x %x %x %x %x',
        'P276 312LE l=%u %x %x %x %x %x %x',
        'A52_P312_DISP_MISC_CMD          0x0000',
        'regmap_read(regmap, A52_P312_DISP_MISC_CMD, &misc)',
        'P276 312D q=%u rc=%d m=%x b0=%u b5=%u b7=%u b9=%u',
        'P276 308T q=%u %x %x %x %x %x',
        'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x',
    ]
    combined = phy + disp
    for token in required:
        if token not in combined:
            raise SystemExit('Phase312 required token missing: ' + token)
    if phy.count(PHY_MARK) != 1:
        raise SystemExit(f'Phase312 PHY marker count {phy.count(PHY_MARK)} != 1')
    if disp.count(DISP_MARK) != 1:
        raise SystemExit(f'Phase312 DISP marker count {disp.count(DISP_MARK)} != 1')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()

    phy_path = ns.root / PHY_REL
    disp_path = ns.root / DISP_REL
    for p in (phy_path, disp_path):
        if not p.is_file():
            raise SystemExit('Phase312 source missing: ' + str(p))

    if not ns.check_only:
        phy_path.write_text(patch_phy(phy_path.read_text()))
        disp_path.write_text(patch_disp(disp_path.read_text()))

    validate(phy_path.read_text(), disp_path.read_text())
    print('Phase312 source-derived exact-F0 dependency recorder: PASS')


if __name__ == '__main__':
    main()
