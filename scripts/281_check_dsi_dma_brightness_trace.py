#!/usr/bin/env python3
from __future__ import annotations
import argparse
import difflib
from pathlib import Path


def read(p: Path) -> str:
    if not p.is_file():
        raise SystemExit(f'missing audit input: {p}')
    return p.read_text()


def added_lines(before: str, after: str) -> list[str]:
    out = []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith('+ '):
            out.append(line[2:])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    for name in ['before_dsi','after_dsi','before_common','after_common','before_panel','after_panel',
                 'before_smmu','after_smmu','before_recorder','after_recorder']:
        ap.add_argument('--' + name.replace('_','-'), dest=name, type=Path, required=True)
    args = ap.parse_args()

    bd, ad = read(args.before_dsi), read(args.after_dsi)
    bc, ac = read(args.before_common), read(args.after_common)
    bp, apanel = read(args.before_panel), read(args.after_panel)
    bs, ass = read(args.before_smmu), read(args.after_smmu)
    br, ar = read(args.before_recorder), read(args.after_recorder)

    if bs != ass:
        raise SystemExit('Phase281 audit: arm-smmu.c changed unexpectedly')
    if br != ar:
        raise SystemExit('Phase281 audit: persistent recorder changed unexpectedly')
    if bd == ad or bc == ac or bp == apanel:
        raise SystemExit('Phase281 audit: an intended source did not change')

    for line in added_lines(bd, ad):
        for forbidden in ['writel(', 'writel_relaxed(', 'iowrite', 'writeq(', 'writeb(', 'DSI_W32(']:
            if forbidden in line:
                raise SystemExit('Phase281 DSI audit: register write added: ' + line.strip())

    required_dsi = [
        'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1',
        'P276 281R0 q=%u %x %x %x %x %x %x',
        'P276 281R1 q=%u %x %x %x %x %x %x',
        'P276 281R2 q=2 %x %x %x %x',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 0);',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 1);',
        'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);',
    ]
    required_common = [
        'A52_PHASE281_BRIGHTNESS_MAPPING_TRACE_V1',
        'P276 281BE l=%d c=%d o=%d',
        'P276 281BM l=%d i=%d c=%d g=%x',
        'P276 281BT r=%d l=%d g=%x',
        'P276 281BO f=%d t=%d',
    ]
    required_panel = [
        'A52_PHASE281_EARLY_50PCT_BRIGHTNESS_V1',
        'vdd->br_info.common_br.bl_level = 128;',
        'P276 281BI l=%d',
        'P276 281BV i=%d g=%x %02x%02x%02x',
    ]
    for token in required_dsi:
        if token not in ad:
            raise SystemExit('Phase281 audit missing DSI token: ' + token)
    for token in required_common:
        if token not in ac:
            raise SystemExit('Phase281 audit missing common brightness token: ' + token)
    for token in required_panel:
        if token not in apanel:
            raise SystemExit('Phase281 audit missing panel brightness token: ' + token)

    if 'vdd->br_info.common_br.bl_level = 255;' not in bp:
        raise SystemExit('Phase281 audit: expected Phase280 default 255 not present before patch')
    if 'vdd->br_info.common_br.bl_level = 255;' in apanel:
        raise SystemExit('Phase281 audit: old 255 default survived')

    q2 = ad.index('a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);')
    latch = ad.index('P276 280Z q=2')
    if q2 > latch:
        raise SystemExit('Phase281 audit: q2 raw DSI snapshot is after retention latch')

    print('Phase281 non-perturbation + brightness-scope audit: PASS')


if __name__ == '__main__':
    main()
