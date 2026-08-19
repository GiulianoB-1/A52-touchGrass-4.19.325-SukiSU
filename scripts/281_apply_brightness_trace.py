#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

COMMON = Path('drivers/a52_display/msm/samsung/ss_dsi_panel_common.c')
PANEL = Path('drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c')
MARK = 'A52_PHASE281_BRIGHTNESS_MAPPING_TRACE_V1'
MARK_DIM = 'A52_PHASE281_EARLY_50PCT_BRIGHTNESS_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


def patch_common(text: str) -> str:
    if MARK in text:
        return text

    text = replace_one(
        text,
        '#include "ss_dsi_panel_common.h"\n#include <linux/preempt.h>\n',
        '#include "ss_dsi_panel_common.h"\n#include <linux/a52_ack_secure_flight_recorder.h>\n#include <linux/preempt.h>\n\n/* ' + MARK + ' */\n',
        'Phase281 common include/marker')

    start = text.index('int ss_brightness_dcs(struct samsung_display_driver_data *vdd, int level, int backlight_origin)')
    end = text.index('int ss_brightness_dcs_hmt(', start)
    region = text[start:end]

    region = replace_one(region, '\tint ret = 0;\n', '\tint ret = 0;\n\tint tx_ret = 0;\n', 'Phase281 tx_ret')
    region = replace_one(
        region,
        '\tstatic int backup_bl_level, backup_acl;\n',
        '\tstatic int backup_bl_level, backup_acl;\n\n'
        '\ta52_ackfr_record("P276 281BE l=%d c=%d o=%d", level,\n'
        '\t\tvdd->br_info.common_br.bl_level, backlight_origin);\n',
        'Phase281 brightness entry trace')

    marker = '\n\tif (cmd_cnt > 0) {\n\t\t/* setting tx cmds cmt */\n'
    insert = ('\n\ta52_ackfr_record("P276 281BM l=%d i=%d c=%d g=%x",\n'
              '\t\tvdd->br_info.common_br.bl_level,\n'
              '\t\tvdd->br_info.common_br.cd_idx,\n'
              '\t\tvdd->br_info.common_br.cd_level,\n'
              '\t\tvdd->br_info.common_br.gm2_wrdisbv);\n'
              '\n\tif (cmd_cnt > 0) {\n\t\t/* setting tx cmds cmt */\n')
    region = replace_one(region, marker, insert, 'Phase281 brightness mapped trace')

    call = '\t\t\t\tss_send_cmd(vdd, TX_BRIGHT_CTRL);\n'
    repl = ('\t\t\t\ttx_ret = ss_send_cmd(vdd, TX_BRIGHT_CTRL);\n'
            '\t\t\t\ta52_ackfr_record("P276 281BT r=%d l=%d g=%x", tx_ret,\n'
            '\t\t\t\t\tvdd->br_info.common_br.bl_level,\n'
            '\t\t\t\t\tvdd->br_info.common_br.gm2_wrdisbv);\n')
    if region.count(call) != 2:
        raise SystemExit(f'Phase281 TX_BRIGHT_CTRL calls: expected 2, found {region.count(call)}')
    region = region.replace(call, repl)
    text = text[:start] + region + text[end:]

    old = '''\t\tif (bd && vdd->br_info.common_br.bl_level != bd->props.brightness) {\n\t\t\tLCD_INFO(vdd, "update bl_level: %d -> %d\\n",\n\t\t\t\tvdd->br_info.common_br.bl_level, bd->props.brightness);\n\t\t\tvdd->br_info.common_br.bl_level = bd->props.brightness;\n\t\t}\n'''
    new = '''\t\tif (bd && vdd->br_info.common_br.bl_level != bd->props.brightness) {\n\t\t\tLCD_INFO(vdd, "update bl_level: %d -> %d\\n",\n\t\t\t\tvdd->br_info.common_br.bl_level, bd->props.brightness);\n\t\t\ta52_ackfr_record("P276 281BO f=%d t=%d",\n\t\t\t\tvdd->br_info.common_br.bl_level, bd->props.brightness);\n\t\t\tvdd->br_info.common_br.bl_level = bd->props.brightness;\n\t\t}\n'''
    text = replace_one(text, old, new, 'Phase281 brightness override trace')
    return text


def patch_panel(text: str) -> str:
    if MARK in text and MARK_DIM in text:
        return text

    text = replace_one(
        text,
        '#include "ss_dsi_panel_S6E3FC3_AMS646YD01.h"\n#include "ss_dsi_mdnie_S6E3FC3_AMS646YD01.h"\n',
        '#include "ss_dsi_panel_S6E3FC3_AMS646YD01.h"\n#include "ss_dsi_mdnie_S6E3FC3_AMS646YD01.h"\n#include <linux/a52_ack_secure_flight_recorder.h>\n\n/* ' + MARK + ' */\n',
        'Phase281 panel include/marker')

    start = text.index('static struct dsi_panel_cmd_set *ss_brightness_gamma_mode2_normal')
    end = text.index('static struct dsi_panel_cmd_set *ss_brightness_gamma_mode2_hbm', start)
    region = text[start:end]
    region = replace_one(
        region,
        '\tint finger_mask_update_delay;\n',
        '\tint finger_mask_update_delay;\n\tint a52_p281_wrdisbv_idx = -1;\n',
        'Phase281 WRDISBV index declaration')

    replacements = [
        ('\t\tpcmds->cmds[5].ss_txbuf[2] = get_bit(vdd->br_info.common_br.gm2_wrdisbv, 0, 8);\n',
         '\t\tpcmds->cmds[5].ss_txbuf[2] = get_bit(vdd->br_info.common_br.gm2_wrdisbv, 0, 8);\n\t\ta52_p281_wrdisbv_idx = 5;\n'),
        ('\t\tpcmds->cmds[2].ss_txbuf[2] = get_bit(vdd->br_info.common_br.gm2_wrdisbv, 0, 8);\n',
         '\t\tpcmds->cmds[2].ss_txbuf[2] = get_bit(vdd->br_info.common_br.gm2_wrdisbv, 0, 8);\n\t\ta52_p281_wrdisbv_idx = 2;\n'),
        ('\t\tpcmds->cmds[3].ss_txbuf[2] = get_bit(vdd->br_info.common_br.gm2_wrdisbv, 0, 8);\n',
         '\t\tpcmds->cmds[3].ss_txbuf[2] = get_bit(vdd->br_info.common_br.gm2_wrdisbv, 0, 8);\n\t\ta52_p281_wrdisbv_idx = 3;\n'),
    ]
    for old, new in replacements:
        region = replace_one(region, old, new, 'Phase281 WRDISBV command index')

    old = '\t*level_key = LEVEL_KEY_NONE;\n'
    new = '''\tif (a52_p281_wrdisbv_idx >= 0 &&\n\t\t\tpcmds->cmds[a52_p281_wrdisbv_idx].ss_txbuf &&\n\t\t\tpcmds->cmds[a52_p281_wrdisbv_idx].msg.tx_len >= 3)\n\t\ta52_ackfr_record("P276 281BV i=%d g=%x %02x%02x%02x",\n\t\t\ta52_p281_wrdisbv_idx, vdd->br_info.common_br.gm2_wrdisbv,\n\t\t\tpcmds->cmds[a52_p281_wrdisbv_idx].ss_txbuf[0],\n\t\t\tpcmds->cmds[a52_p281_wrdisbv_idx].ss_txbuf[1],\n\t\t\tpcmds->cmds[a52_p281_wrdisbv_idx].ss_txbuf[2]);\n\n\t*level_key = LEVEL_KEY_NONE;\n'''
    region = replace_one(region, old, new, 'Phase281 WRDISBV payload trace')
    text = text[:start] + region + text[end:]

    old = '''\t/* default brightness */\n\tvdd->br_info.common_br.bl_level = 255;\n'''
    new = '''\t/* default brightness */\n\t/* A52_PHASE281_EARLY_50PCT_BRIGHTNESS_V1: logical 128/255 boot default. */\n\tvdd->br_info.common_br.bl_level = 128;\n\ta52_ackfr_record("P276 281BI l=%d", vdd->br_info.common_br.bl_level);\n'''
    text = replace_one(text, old, new, 'Phase281 early brightness default')
    return text


def validate_common(text: str) -> None:
    required = [MARK, 'P276 281BE l=%d c=%d o=%d', 'P276 281BM l=%d i=%d c=%d g=%x',
                'P276 281BT r=%d l=%d g=%x', 'P276 281BO f=%d t=%d',
                'int tx_ret = 0;']
    for token in required:
        if token not in text:
            raise SystemExit('Phase281 common brightness marker missing: ' + token)
    if text.count('tx_ret = ss_send_cmd(vdd, TX_BRIGHT_CTRL);') != 2:
        raise SystemExit('Phase281 brightness tx result capture count mismatch')


def validate_panel(text: str) -> None:
    required = [MARK, MARK_DIM, 'vdd->br_info.common_br.bl_level = 128;',
                'P276 281BI l=%d', 'P276 281BV i=%d g=%x %02x%02x%02x',
                'a52_p281_wrdisbv_idx = 5;', 'a52_p281_wrdisbv_idx = 2;',
                'a52_p281_wrdisbv_idx = 3;']
    for token in required:
        if token not in text:
            raise SystemExit('Phase281 panel brightness marker missing: ' + token)
    if 'vdd->br_info.common_br.bl_level = 255;' in text:
        raise SystemExit('Phase281 panel still contains old 255 default brightness')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    common = args.root / COMMON
    panel = args.root / PANEL
    if not common.is_file():
        raise SystemExit(f'missing source: {common}')
    if not panel.is_file():
        raise SystemExit(f'missing source: {panel}')
    ctext = common.read_text()
    ptext = panel.read_text()
    if not args.check_only:
        ctext = patch_common(ctext)
        ptext = patch_panel(ptext)
        common.write_text(ctext)
        panel.write_text(ptext)
    validate_common(ctext)
    validate_panel(ptext)
    print('Phase281 Samsung brightness mapping + early 50% trace: PASS')


if __name__ == '__main__':
    main()
