#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c')
MARK = 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1'

OLD = '''\treg = DSI_R32(phy, DSIPHY_LNX_TX_DCTRL(3));\n\tDSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));\n\twmb(); /* Ensure that the freezeio bit is toggled */\n\tDSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg & ~BIT(0));\n'''

NEW = '''\treg = DSI_R32(phy, DSIPHY_LNX_TX_DCTRL(3));\n\t/* A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1\n\t * Normal v3 lane setup programs lane-3 TX_DCTRL to 0x04. During\n\t * continuous-splash handoff the normal PHY enable path is skipped, and\n\t * Phase310 observed lane-3 TX_DCTRL=0x00 at the exact F0 transaction.\n\t * Preserve the existing two FreezeIO writes and their ordering, but make\n\t * the inherited base state canonical before the existing bit-0 toggle.\n\t * For the observed 0x00 state this changes 0x01->0x00 into 0x05->0x04.\n\t */\n\treg |= BIT(2);\n\tDSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));\n\twmb(); /* Ensure that the freezeio bit is toggled */\n\tDSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg & ~BIT(0));\n'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    if 'u8 tx_dctrl[] = {0x00, 0x00, 0x00, 0x04, 0x01};' not in text:
        raise SystemExit('Phase311: canonical v3 tx_dctrl table missing')
    if text.count(OLD) != 1:
        raise SystemExit(f'Phase311: expected one clamp-release sequence, found {text.count(OLD)}')
    return text.replace(OLD, NEW, 1)


def validate(text: str) -> None:
    required = [
        MARK,
        'u8 tx_dctrl[] = {0x00, 0x00, 0x00, 0x04, 0x01};',
        'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));',
        'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg & ~BIT(0));',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase311 required token missing: ' + token)

    # Validate the repair only at the intended clamp-release site. The source
    # already has unrelated "reg |= BIT(2);" operations elsewhere, so a
    # whole-file count is not a valid uniqueness check for this experiment.
    if text.count(MARK) != 1:
        raise SystemExit(f'Phase311 expected one repair marker, found {text.count(MARK)}')
    if text.count(NEW) != 1:
        raise SystemExit(f'Phase311 expected one exact repaired clamp-release block, found {text.count(NEW)}')
    if text.count(OLD) != 0:
        raise SystemExit(f'Phase311 original clamp-release block still present {text.count(OLD)} time(s)')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()

    path = ns.root / PHY_REL
    if not path.is_file():
        raise SystemExit('Phase311 source missing: ' + str(path))

    if not ns.check_only:
        path.write_text(patch(path.read_text()))
    validate(path.read_text())
    print('Phase311 v3 lane-3 TX_DCTRL handoff repair A/B: PASS')


if __name__ == '__main__':
    main()
