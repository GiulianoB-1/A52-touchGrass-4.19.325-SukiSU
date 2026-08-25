#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c')
MARK = 'A52_PHASE313_V3_TIMING9_HANDOFF_REPAIR_AB_V1'

OLD = '''\treg |= BIT(2);\n\tDSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));\n\twmb(); /* Ensure that the freezeio bit is toggled */\n'''

NEW = '''\treg |= BIT(2);\n\t/* A52_PHASE313_V3_TIMING9_HANDOFF_REPAIR_AB_V1\n\t * Phase312 recorded DSIPHY_CMN_TIMING_CTRL_9=0x12 at the exact first\n\t * Samsung F0 transfer while the already-calculated v3 timing cfg was\n\t * 0x02. Pinned TouchGrass hard-codes lane_v3[9]=0x02 and normal v3 PHY\n\t * enable writes that value directly. Continuous splash skips that normal\n\t * hardware programming, so repair this one inherited field before the\n\t * existing FreezeIO release sequence. The existing following write\n\t * barrier orders this write; do not change release timing/order.\n\t */\n\tDSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);\n\tDSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));\n\twmb(); /* Ensure that the freezeio bit is toggled */\n'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1' not in text:
        raise SystemExit('Phase313: inherited Phase311 DCTRL3 handoff repair missing')
    if '#define DSIPHY_CMN_TIMING_CTRL_9' not in text:
        raise SystemExit('Phase313: DSIPHY_CMN_TIMING_CTRL_9 definition missing')
    if text.count(OLD) != 1:
        raise SystemExit(
            f'Phase313: expected one Phase311 repaired release anchor, found {text.count(OLD)}'
        )
    return text.replace(OLD, NEW, 1)


def validate(text: str) -> None:
    required = [
        MARK,
        'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1',
        'DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);',
        'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));',
        'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg & ~BIT(0));',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase313 required token missing: ' + token)
    if text.count(MARK) != 1:
        raise SystemExit(f'Phase313 expected one marker, found {text.count(MARK)}')
    if text.count('DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);') != 1:
        raise SystemExit('Phase313 expected exactly one timing9 repair write')
    if text.count(NEW) != 1:
        raise SystemExit('Phase313 exact repaired release block not unique')
    if text.count(OLD) != 0:
        raise SystemExit('Phase313 original Phase311-only release block still present')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()

    path = ns.root / PHY_REL
    if not path.is_file():
        raise SystemExit('Phase313 source missing: ' + str(path))

    if not ns.check_only:
        path.write_text(patch(path.read_text()))
    validate(path.read_text())
    print('Phase313 v3 TIMING_CTRL_9 handoff repair A/B: PASS')


if __name__ == '__main__':
    main()
