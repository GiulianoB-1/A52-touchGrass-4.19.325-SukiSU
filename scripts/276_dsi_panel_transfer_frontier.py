#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root>")

ROOT = Path(sys.argv[1])
REC = ROOT / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
DSI = ROOT / "drivers/a52_display/msm/dsi/dsi_panel.c"
REC_MARKER = "A52_PHASE276_DSI_PANEL_TRANSFER_FRONTIER_V1"
DSI_MARKER = "A52_PHASE276_DSI_PANEL_TX_FRONTIER_V1"
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
        'return !strncmp(message, "P275 ", 5) ||\n',
        'return !strncmp(message, "P276 ", 5) ||\n'
        '       !strncmp(message, "P275 ", 5) ||\n',
        "critical P276 admission",
    )
    text = replace_once(
        text,
        'if (strncmp(fmt, "P275", 4) &&\n',
        'if (strncmp(fmt, "P276", 4) &&\n'
        '    strncmp(fmt, "P275", 4) &&\n',
        "format P276 admission",
    )
    anchor = "static unsigned long a52_r274_frontier_start_jiffies;\n"
    marker = (
        "/* " + REC_MARKER + "\n"
        " * Phase275 hardware reached dsi_panel_tx_cmd_set(TX_LEVEL1_KEY_ENABLE)\n"
        " * after successful PM and DSI clock-on. P276 admits target-only internal\n"
        " * command-lock and host-transfer checkpoints. R48/RS48 stays unchanged.\n"
        " */\n"
    )
    return replace_once(text, anchor, marker + anchor, "recorder marker")


def patch_dsi(text: str) -> str:
    if DSI_MARKER in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        text = replace_once(
            text,
            '#include "sde_dbg.h"\n',
            '#include "sde_dbg.h"\n' + inc,
            "dsi recorder include",
        )

    sig = (
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n"
        "int dsi_panel_tx_cmd_set(struct dsi_panel *panel,\n"
        "\t\t\t\tint type)\n"
    )
    marker = (
        "/* " + DSI_MARKER + "\n"
        " * Target-only instrumentation for TX_LEVEL1_KEY_ENABLE. No command-set\n"
        " * selection, mutex behavior, flags, retries, payloads, waits, or returns\n"
        " * are changed.\n"
        " */\n"
    )
    text = replace_once(text, sig, marker + sig, "dsi panel tx marker")

    text = replace_once(
        text,
        "\tint retry = 5;\n\n"
        "\t/* Null check before use*/\n",
        "\tint retry = 5;\n\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T E ty=%d", type);\n\n'
        "\t/* Null check before use*/\n",
        "entry checkpoint",
    )

    text = replace_once(
        text,
        "\t/* ss_get_cmds() gets proper QCT cmds or SS cmds for panel revision. */\n"
        "\tset = ss_get_cmds(vdd, type);\n\n"
        "\tcmds = set->cmds;\n"
        "\tcount = set->count;\n"
        "\tstate = set->state;\n",
        "\t/* ss_get_cmds() gets proper QCT cmds or SS cmds for panel revision. */\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T S ty=%d p=0", type);\n'
        "\tset = ss_get_cmds(vdd, type);\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T S ty=%d p=1 ok=%u", type, !IS_ERR_OR_NULL(set));\n\n'
        "\tcmds = set->cmds;\n"
        "\tcount = set->count;\n"
        "\tstate = set->state;\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T A n=%x s=%x e=%x p=%x",\n'
        "\t\t\tcount, state, vdd->exclusive_tx.enable, set->exclusive_pass);\n",
        "command set checkpoint",
    )

    text = replace_once(
        text,
        "\tif (unlikely(vdd->exclusive_tx.enable &&\n"
        "\t\t\t!set->exclusive_pass)) {\n"
        '\t\tLCD_INFO(vdd, "[SDE] %s: wait.. cmd[%d]=%s\\n", __func__,\n',
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T X ty=%d p=0 ex=%u xp=%u", type,\n'
        "\t\t\tvdd->exclusive_tx.enable, set->exclusive_pass);\n"
        "\tif (unlikely(vdd->exclusive_tx.enable &&\n"
        "\t\t\t!set->exclusive_pass)) {\n"
        f"\t\tif (type == {TARGET})\n"
        '\t\t\ta52_ackfr_record("P276 T X ty=%d p=1", type);\n'
        '\t\tLCD_INFO(vdd, "[SDE] %s: wait.. cmd[%d]=%s\\n", __func__,\n',
        "exclusive wait pre",
    )

    text = replace_once(
        text,
        "\t\twait_event(vdd->exclusive_tx.ex_tx_waitq,\n"
        "\t\t\t\t!vdd->exclusive_tx.enable);\n"
        '\t\tLCD_INFO(vdd, "[SDE] %s: pass, cmd[%d]=%s\\n", __func__,\n',
        "\t\twait_event(vdd->exclusive_tx.ex_tx_waitq,\n"
        "\t\t\t\t!vdd->exclusive_tx.enable);\n"
        f"\t\tif (type == {TARGET})\n"
        '\t\t\ta52_ackfr_record("P276 T X ty=%d p=2", type);\n'
        '\t\tLCD_INFO(vdd, "[SDE] %s: pass, cmd[%d]=%s\\n", __func__,\n',
        "exclusive wait post",
    )

    text = replace_once(
        text,
        "\t}\n"
        "\tmutex_lock(&vdd->cmd_lock);\n"
        "#else\n",
        "\t}\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T L ty=%d p=0 h=%u", type,\n'
        "\t\t\tmutex_is_locked(&vdd->cmd_lock));\n"
        "\tmutex_lock(&vdd->cmd_lock);\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T L ty=%d p=1", type);\n'
        "#else\n",
        "cmd_lock checkpoints",
    )

    text = replace_once(
        text,
        "\tSDE_EVT32(type, state, count);\n\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n",
        "\tSDE_EVT32(type, state, count);\n\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n"
        f"\tif (type == {TARGET}) {{\n"
        '\t\tif (cmds && count) {\n'
        '\t\t\ta52_ackfr_record("P276 T M0 n=%x s=%x mt=%x l=%x",\n'
        "\t\t\t\tcount, state, (unsigned int)cmds->msg.type,\n"
        "\t\t\t\t(unsigned int)cmds->msg.tx_len);\n"
        '\t\t\ta52_ackfr_record("P276 T M1 f=%x c=%x z=%x w=%x",\n'
        "\t\t\t\t(unsigned int)cmds->msg.flags,\n"
        "\t\t\t\t(unsigned int)cmds->msg.ctrl,\n"
        "\t\t\t\t(unsigned int)cmds->last_command,\n"
        "\t\t\t\t(unsigned int)cmds->post_wait_ms);\n"
        "\t\t} else\n"
        '\t\t\ta52_ackfr_record("P276 T M0 n=%x s=%x e=1", count, state);\n'
        "\t}\n",
        "message metadata",
    )

    text = replace_once(
        text,
        "\tfor (i = 0; i < count; i++) {\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n",
        "\tfor (i = 0; i < count; i++) {\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n"
        f"\t\tif (type == {TARGET})\n"
        '\t\t\ta52_ackfr_record("P276 T I i=%d p=0", i);\n'
        "#endif\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n",
        "loop checkpoint",
    )

    text = replace_once(
        text,
        "\t\tlen = ops->transfer(panel->host, &cmds->msg);\n"
        "\t\tif (len < 0) {\n",
        f"\t\tif (type == {TARGET})\n"
        '\t\t\ta52_ackfr_record("P276 T O i=%d p=0 mt=%u tl=%u fl=%x", i,\n'
        "\t\t\t\t(unsigned int)cmds->msg.type,\n"
        "\t\t\t\t(unsigned int)cmds->msg.tx_len,\n"
        "\t\t\t\t(unsigned int)cmds->msg.flags);\n"
        "\t\tlen = ops->transfer(panel->host, &cmds->msg);\n"
        f"\t\tif (type == {TARGET})\n"
        '\t\t\ta52_ackfr_record("P276 T O i=%d p=1 len=%zd", i, len);\n'
        "\t\tif (len < 0) {\n",
        "host transfer checkpoints",
    )

    text = replace_once(
        text,
        "\t\tif (cmds->post_wait_ms)\n"
        "\t\t\tusleep_range(cmds->post_wait_ms*1000,\n"
        "\t\t\t\t\t((cmds->post_wait_ms*1000)+10));\n"
        "\t\tcmds++;\n",
        "\t\tif (cmds->post_wait_ms)\n"
        "\t\t\tusleep_range(cmds->post_wait_ms*1000,\n"
        "\t\t\t\t\t((cmds->post_wait_ms*1000)+10));\n"
        f"\t\tif (type == {TARGET})\n"
        '\t\t\ta52_ackfr_record("P276 T I i=%d p=1", i);\n'
        "\t\tcmds++;\n",
        "loop completion",
    )

    text = replace_once(
        text,
        "error:\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n"
        "\tmutex_unlock(&vdd->cmd_lock);\n"
        "#endif\n"
        "\treturn rc;\n"
        "}\n",
        "error:\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T Z ty=%d p=0 rc=%d", type, rc);\n'
        "\tmutex_unlock(&vdd->cmd_lock);\n"
        f"\tif (type == {TARGET})\n"
        '\t\ta52_ackfr_record("P276 T Z ty=%d p=1 rc=%d", type, rc);\n'
        "#endif\n"
        "\treturn rc;\n"
        "}\n",
        "exit checkpoints",
    )
    return text


REC.write_text(patch_recorder(REC.read_text(encoding="utf-8", errors="strict")), encoding="utf-8")
DSI.write_text(patch_dsi(DSI.read_text(encoding="utf-8", errors="strict")), encoding="utf-8")
print("phase276 dsi_panel_tx_cmd_set frontier staged")
