#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
MARK = 'A52_PHASE309_GKI_CLAMP_RELEASE_LATCH_V1'
P308 = 'A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase309 {label}: expected exactly one match, found {n}')
    return text.replace(old, new, 1)


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    if P308 not in text:
        raise SystemExit('Phase309 requires inherited Phase308 PLL/clamp observer')

    old = '''extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);\n\nstatic void a52_p308_tx_dctrl_snapshot(struct msm_dsi_phy *phy,\n'''
    new = '''extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);\n\n/* A52_PHASE309_GKI_CLAMP_RELEASE_LATCH_V1\n * Software-only persistent counters. They are incremented only after the\n * existing clamp_ctrl callback returns, so cr>0 proves that the real release\n * callback completed before the exact F0 5A 5A q snapshot. No HW operation.\n */\n#define A52_P309_PHY_MAX 2\nstatic atomic_t a52_p309_clamp_enable[A52_P309_PHY_MAX];\nstatic atomic_t a52_p309_clamp_release[A52_P309_PHY_MAX];\n\nstatic void a52_p309_clamp_snapshot(unsigned int index, unsigned int point)\n{\n\tint ce = -1, cr = -1;\n\n\tif (index < A52_P309_PHY_MAX) {\n\t\tce = atomic_read(&a52_p309_clamp_enable[index]);\n\t\tcr = atomic_read(&a52_p309_clamp_release[index]);\n\t}\n\n\ta52_ackfr_record("P276 309T q=%u i=%u ce=%d cr=%d",\n\t\tpoint, index, ce, cr);\n}\n\nstatic void a52_p308_tx_dctrl_snapshot(struct msm_dsi_phy *phy,\n'''
    text = replace_once(text, old, new, 'counter declaration/helper')

    old_snapshot = '''\ta52_ackfr_record("P276 308T q=%u %x %x %x %x %x", point,\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(0)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(1)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(2)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(3)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(4)));\n\ta52_p308_pll_snapshot(index, point);\n}\n'''
    new_snapshot = '''\ta52_ackfr_record("P276 308T q=%u %x %x %x %x %x", point,\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(0)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(1)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(2)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(3)),\n\t\treadl_relaxed(base + A52_P308_V3_LNX_TX_DCTRL(4)));\n\ta52_p309_clamp_snapshot(index, point);\n\ta52_p308_pll_snapshot(index, point);\n}\n'''
    text = replace_once(text, old_snapshot, new_snapshot, 'exact-F0 counter snapshot')

    old_clamp = '''\tif (phy->hw.ops.clamp_ctrl)\n\t\tphy->hw.ops.clamp_ctrl(&phy->hw, enable);\n\n\ta52_p308_tx_dctrl_snapshot(phy, enable, 1);\n'''
    new_clamp = '''\tif (phy->hw.ops.clamp_ctrl) {\n\t\tphy->hw.ops.clamp_ctrl(&phy->hw, enable);\n\t\tif (phy->index < A52_P309_PHY_MAX) {\n\t\t\tif (enable)\n\t\t\t\tatomic_inc(&a52_p309_clamp_enable[phy->index]);\n\t\t\telse\n\t\t\t\tatomic_inc(&a52_p309_clamp_release[phy->index]);\n\t\t\ta52_ackfr_record("P276 309K i=%u e=%u ce=%d cr=%d",\n\t\t\t\tphy->index, enable ? 1 : 0,\n\t\t\t\tatomic_read(&a52_p309_clamp_enable[phy->index]),\n\t\t\t\tatomic_read(&a52_p309_clamp_release[phy->index]));\n\t\t}\n\t}\n\n\ta52_p308_tx_dctrl_snapshot(phy, enable, 1);\n'''
    return replace_once(text, old_clamp, new_clamp, 'post-callback latch')


def validate(text: str) -> None:
    required = [
        MARK,
        'static atomic_t a52_p309_clamp_enable[A52_P309_PHY_MAX];',
        'static atomic_t a52_p309_clamp_release[A52_P309_PHY_MAX];',
        'atomic_inc(&a52_p309_clamp_release[phy->index]);',
        'P276 309K i=%u e=%u ce=%d cr=%d',
        'P276 309T q=%u i=%u ce=%d cr=%d',
        'a52_p309_clamp_snapshot(index, point);',
        'phy->hw.ops.clamp_ctrl(&phy->hw, enable);',
        'P276 308T q=%u %x %x %x %x %x',
        'a52_p308_pll_snapshot(index, point);',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase309 required token missing: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    phy = args.root / PHY_REL
    if not phy.is_file():
        raise SystemExit('Phase309 PHY source missing: ' + str(phy))

    if not args.check_only:
        phy.write_text(patch_phy(phy.read_text()))

    validate(phy.read_text())
    print('Phase309 GKI persistent clamp-release latch observer: PASS')


if __name__ == '__main__':
    main()
