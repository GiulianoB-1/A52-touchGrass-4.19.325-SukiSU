#!/usr/bin/env python3
from pathlib import Path
import argparse

DISPLAY = Path('techpack/display/msm/dsi/dsi_display.c')
MARK = 'A52_GOLDEN_TOUCHGRASS_CLOCK_CHAIN_ORIGIN_V2'


def once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected one match, found {n}')
    return text.replace(old, new, 1)


def patch_display(text: str) -> str:
    if MARK in text:
        return text

    text = once(
        text,
        '#define DSI_CLOCK_BITRATE_RADIX 10\n#define MAX_TE_SOURCE_ID  2\n',
        '#define DSI_CLOCK_BITRATE_RADIX 10\n#define MAX_TE_SOURCE_ID  2\n\n'
        '/* ' + MARK + ': bounded trace of the rate producer only. */\n'
        'static unsigned int tgref_derive_logs;\n',
        'derive marker/counter')

    text = once(
        text,
        '\t\tSDE_EVT32(i, bit_rate, byte_clk_rate, pclk_rate);\n\n'
        '\t\tctrl->clk_freq.byte_clk_rate = byte_clk_rate;\n'
        '\t\tctrl->clk_freq.byte_intf_clk_rate = byte_intf_clk_rate;\n'
        '\t\tctrl->clk_freq.pix_clk_rate = pclk_rate;\n'
        '\t\trc = dsi_clk_set_link_frequencies(display->dsi_clk_handle,\n'
        '\t\t\tctrl->clk_freq, ctrl->cell_index);\n',
        '\t\tSDE_EVT32(i, bit_rate, byte_clk_rate, pclk_rate);\n\n'
        '\t\tif (tgref_derive_logs < 8) {\n'
        '\t\t\tpr_info("TGREF DERIVE c=%d phy=%u in=%u bit=%llu lane=%llu lanes=%u bpp=%u div=%u b=%llu i=%llu p=%llu\\n",\n'
        '\t\t\t\tctrl->cell_index, host_cfg->phy_type, bit_clk_rate,\n'
        '\t\t\t\t(unsigned long long)bit_rate,\n'
        '\t\t\t\t(unsigned long long)bit_rate_per_lane,\n'
        '\t\t\t\tnum_of_lanes, bpp, host_cfg->byte_intf_clk_div,\n'
        '\t\t\t\t(unsigned long long)byte_clk_rate,\n'
        '\t\t\t\t(unsigned long long)byte_intf_clk_rate,\n'
        '\t\t\t\t(unsigned long long)pclk_rate);\n'
        '\t\t\ttgref_derive_logs++;\n'
        '\t\t}\n\n'
        '\t\tctrl->clk_freq.byte_clk_rate = byte_clk_rate;\n'
        '\t\tctrl->clk_freq.byte_intf_clk_rate = byte_intf_clk_rate;\n'
        '\t\tctrl->clk_freq.pix_clk_rate = pclk_rate;\n'
        '\t\trc = dsi_clk_set_link_frequencies(display->dsi_clk_handle,\n'
        '\t\t\tctrl->clk_freq, ctrl->cell_index);\n',
        'derived-rate trace')

    return text


def validate(text: str) -> None:
    for token in [MARK, 'TGREF DERIVE', 'bit_rate_per_lane',
                  'host_cfg->byte_intf_clk_div']:
        if token not in text:
            raise SystemExit('missing display marker: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    path = args.root / DISPLAY
    text = path.read_text()
    if not args.check_only:
        text = patch_display(text)
        path.write_text(text)
    validate(text)
    print('Golden TouchGrass clock-chain origin V2 instrumentation validated')


if __name__ == '__main__':
    main()
