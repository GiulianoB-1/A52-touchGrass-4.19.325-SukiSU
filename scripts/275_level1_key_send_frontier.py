#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root>")

ROOT = Path(sys.argv[1])
REC = ROOT / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PANEL = ROOT / "drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
WRAP = ROOT / "drivers/a52_display/msm/samsung/ss_wrapper_common.c"
REC_MARKER = "A52_PHASE275_LEVEL1_KEY_SEND_FRONTIER_V1"
PANEL_MARKER = "A52_PHASE275_SS_SEND_CMD_FRONTIER_V1"
WRAP_MARKER = "A52_PHASE275_DSI_TX_WRAPPER_FRONTIER_V1"
TARGET = "TX_LEVEL1_KEY_ENABLE"


def replace_once(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{what}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if REC_MARKER in text:
        return text
    text = replace_once(
        text,
        'return !strncmp(message, "P274 ", 5) ||\n',
        'return !strncmp(message, "P275 ", 5) ||\n'
        '       !strncmp(message, "P274 ", 5) ||\n',
        "critical P275 admission",
    )
    text = replace_once(
        text,
        'if (strncmp(fmt, "P274", 4) &&\n',
        'if (strncmp(fmt, "P275", 4) &&\n'
        '    strncmp(fmt, "P274", 4) &&\n',
        "format P275 admission",
    )
    anchor = 'static unsigned long a52_r274_frontier_start_jiffies;\n'
    marker = (
        '/* ' + REC_MARKER + '\n'
        ' * Phase274 hardware isolated the first non-returning panel operation to\n'
        ' * ss_send_cmd(TX_LEVEL1_KEY_ENABLE). P275 admits sparse target-only\n'
        ' * checkpoints while preserving the R48/RS48 wire format.\n'
        ' */\n'
    )
    return replace_once(text, anchor, marker + anchor, "recorder phase marker")


def patch_panel(text: str) -> str:
    if PANEL_MARKER in text:
        return text

    include = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if include not in text:
        raise RuntimeError("panel recorder include missing")

    anchor = 'int ss_send_cmd(struct samsung_display_driver_data *vdd,\n\t\tint type)\n{\n'
    marker = (
        '/* ' + PANEL_MARKER + '\n'
        ' * Target-only checkpoints for TX_LEVEL1_KEY_ENABLE. No lock ordering,\n'
        ' * command selection, PM behavior, payload, or return semantics change.\n'
        ' */\n'
    )
    text = replace_once(text, anchor, marker + anchor, "ss_send_cmd marker")

    text = replace_once(
        text,
        '\tint rc = 0;\n\n\tif (IS_ERR_OR_NULL(vdd)) {\n',
        '\tint rc = 0;\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S E ty=%d", type);\n\n'
        '\tif (IS_ERR_OR_NULL(vdd)) {\n',
        "ss_send_cmd entry",
    )

    text = replace_once(
        text,
        '\t}\n\n\t/* Make not to turn on the panel power when ub_con_det gpio is high (ub is not connected) */\n'
        '\tif (unlikely(vdd->is_factory_mode)) {\n',
        '\t}\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S F ty=%d p=0 fm=%u", type, vdd->is_factory_mode);\n'
        '\t/* Make not to turn on the panel power when ub_con_det gpio is high (ub is not connected) */\n'
        '\tif (unlikely(vdd->is_factory_mode)) {\n',
        "factory pre",
    )

    text = replace_once(
        text,
        '\t}\n\n\tif (!ss_panel_attach_get(vdd)) {\n',
        '\t}\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S F ty=%d p=1", type);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S H ty=%d p=0", type);\n'
        '\tif (!ss_panel_attach_get(vdd)) {\n',
        "factory post / attach pre",
    )

    text = replace_once(
        text,
        '\t\treturn -EAGAIN;\n\t}\n\n\t/* Skip to lock vdd_lock for commands that has exclusive_pass token\n',
        '\t\treturn -EAGAIN;\n\t}\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S H ty=%d p=1", type);\n\n'
        '\t/* Skip to lock vdd_lock for commands that has exclusive_pass token\n',
        "attach post",
    )

    old = (
        '\tset = ss_get_cmds(vdd, type);\n'
        '\ta52_ackfr_record("DISP SS_CMD start i=%d type=%d name=%s count=%u state=%u ps=%d",\n'
        '\t\tvdd->ndx, type, ss_get_cmd_name(type), set ? set->count : 0,\n'
        '\t\tset ? set->state : 0, vdd->panel_state);\n\n'
        '\tif (likely(!vdd->exclusive_tx.enable || !set->exclusive_pass)) {\n'
        '\t\tmutex_lock(&vdd->vdd_lock);\n'
        '\t\tis_vdd_locked = true;\n'
        '\t}\n\n'
    )
    new = (
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S G ty=%d p=0", type);\n'
        '\tset = ss_get_cmds(vdd, type);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S G ty=%d p=1 ok=%u", type, !IS_ERR_OR_NULL(set));\n'
        '\ta52_ackfr_record("DISP SS_CMD start i=%d type=%d name=%s count=%u state=%u ps=%d",\n'
        '\t\tvdd->ndx, type, ss_get_cmd_name(type), set ? set->count : 0,\n'
        '\t\tset ? set->state : 0, vdd->panel_state);\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S A ty=%d ex=%u xp=%u", type,\n'
        '\t\t\tvdd->exclusive_tx.enable, set->exclusive_pass);\n\n'
        '\tif (likely(!vdd->exclusive_tx.enable || !set->exclusive_pass)) {\n'
        f'\t\tif (type == {TARGET})\n'
        '\t\t\ta52_ackfr_record("P275 S L ty=%d p=0", type);\n'
        '\t\tmutex_lock(&vdd->vdd_lock);\n'
        f'\t\tif (type == {TARGET})\n'
        '\t\t\ta52_ackfr_record("P275 S L ty=%d p=1", type);\n'
        '\t\tis_vdd_locked = true;\n'
        '\t}\n\n'
    )
    text = replace_once(text, old, new, "get_cmds / vdd_lock checkpoints")

    text = replace_once(
        text,
        '\tif (ss_wait_for_pm_resume(vdd))\n\t\tgoto error;\n\n',
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S M ty=%d p=0", type);\n'
        '\tif (ss_wait_for_pm_resume(vdd))\n'
        '\t\tgoto error;\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S M ty=%d p=1", type);\n\n',
        "pm resume checkpoints",
    )

    text = replace_once(
        text,
        '\tss_wrapper_dsi_panel_tx_cmd_set(panel, type);\n\n',
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S W ty=%d p=0", type);\n'
        '\tss_wrapper_dsi_panel_tx_cmd_set(panel, type);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S W ty=%d p=1", type);\n\n',
        "wrapper checkpoints",
    )

    text = replace_once(
        text,
        '\tss_print_rx_buf(vdd, type);\n'
        '\ta52_ackfr_record("DISP SS_CMD done i=%d type=%d rc=%d ps=%d",\n'
        '\t\tvdd->ndx, type, rc, vdd->panel_state);\n\n'
        '\treturn rc;\n}\n',
        '\tss_print_rx_buf(vdd, type);\n'
        '\ta52_ackfr_record("DISP SS_CMD done i=%d type=%d rc=%d ps=%d",\n'
        '\t\tvdd->ndx, type, rc, vdd->panel_state);\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 S Z ty=%d rc=%d", type, rc);\n\n'
        '\treturn rc;\n}\n',
        "ss_send_cmd exit",
    )
    return text


def patch_wrapper(text: str) -> str:
    if WRAP_MARKER in text:
        return text

    include = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    if include not in text:
        text = replace_once(
            text,
            '#include "ss_wrapper_common.h"\n',
            '#include "ss_wrapper_common.h"\n' + include,
            "wrapper recorder include",
        )

    anchor = 'int __mockable ss_wrapper_dsi_panel_tx_cmd_set(struct dsi_panel *panel, int type)\n{\n'
    marker = (
        '/* ' + WRAP_MARKER + '\n'
        ' * Target-only checkpoints around DSI clock and panel TX primitives.\n'
        ' * Observational only: command order and return handling are untouched.\n'
        ' */\n'
    )
    text = replace_once(text, anchor, marker + anchor, "wrapper marker")

    text = replace_once(
        text,
        '\tstruct samsung_display_driver_data *vdd = display->panel->panel_private;\n\n'
        '\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n',
        '\tstruct samsung_display_driver_data *vdd = display->panel->panel_private;\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W E ty=%d", type);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W C ty=%d p=0 on=1", type);\n'
        '\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n',
        "wrapper clock-on pre",
    )

    text = replace_once(
        text,
        '\t\t\t\tDSI_ALL_CLKS, DSI_CLK_ON);\n'
        '\tif (rc) {\n',
        '\t\t\t\tDSI_ALL_CLKS, DSI_CLK_ON);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W C ty=%d p=1 on=1 rc=%d", type, rc);\n'
        '\tif (rc) {\n',
        "wrapper clock-on post",
    )

    text = replace_once(
        text,
        '\trc = dsi_panel_tx_cmd_set(panel, type);\n\n'
        '\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n',
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W T ty=%d p=0", type);\n'
        '\trc = dsi_panel_tx_cmd_set(panel, type);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W T ty=%d p=1 rc=%d", type, rc);\n\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W C ty=%d p=0 on=0", type);\n'
        '\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n',
        "panel tx checkpoints",
    )

    text = replace_once(
        text,
        '\t\t\tDSI_ALL_CLKS, DSI_CLK_OFF);\n'
        '\tif (rc) {\n',
        '\t\t\tDSI_ALL_CLKS, DSI_CLK_OFF);\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W C ty=%d p=1 on=0 rc=%d", type, rc);\n'
        '\tif (rc) {\n',
        "wrapper clock-off post",
    )

    text = replace_once(
        text,
        'error:\n\treturn rc;\n}\n',
        'error:\n'
        f'\tif (type == {TARGET})\n'
        '\t\ta52_ackfr_record("P275 W Z ty=%d rc=%d", type, rc);\n'
        '\treturn rc;\n}\n',
        "wrapper exit",
    )
    return text


REC.write_text(patch_recorder(REC.read_text(encoding="utf-8", errors="strict")), encoding="utf-8")
PANEL.write_text(patch_panel(PANEL.read_text(encoding="utf-8", errors="strict")), encoding="utf-8")
WRAP.write_text(patch_wrapper(WRAP.read_text(encoding="utf-8", errors="strict")), encoding="utf-8")
print("phase275 LEVEL1-key send frontier staged")
