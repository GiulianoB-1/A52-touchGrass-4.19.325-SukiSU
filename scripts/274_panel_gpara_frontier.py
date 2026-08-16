#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root>")

ROOT = Path(sys.argv[1])
REC = ROOT / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PANEL = ROOT / "drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
REC_MARKER = "A52_PHASE274_FRONTIER_TIMEBASE_FIX_V1"
PANEL_MARKER = "A52_PHASE274_PANEL_GPARA_FRONTIER_V1"


def replace_once(text: str, old: str, new: str, what: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{what}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if REC_MARKER in text:
        return text

    text = replace_once(
        text,
        'return !strncmp(message, "P273 ", 5) ||\n',
        'return !strncmp(message, "P274 ", 5) ||\n'
        '       !strncmp(message, "P273 ", 5) ||\n',
        "critical P274 admission",
    )
    text = replace_once(
        text,
        'if (strncmp(fmt, "P273", 4) &&\n',
        'if (strncmp(fmt, "P274", 4) &&\n'
        '    strncmp(fmt, "P273", 4) &&\n',
        "format P274 admission",
    )

    anchor = 'static void a52_r273_frontier_fn(struct work_struct *work);\n'
    block = (
        '/* ' + REC_MARKER + '\n'
        ' * Phase273 used absolute unsigned jiffies as seconds-since-boot. Linux\n'
        ' * intentionally biases initial_jiffies near wraparound, so the first\n'
        ' * frontier pass looked older than the 900-second horizon and stopped.\n'
        ' * Use wrap-safe elapsed jiffies from the moment the worker is armed.\n'
        ' * Diagnostic timing only; no device or userspace behavior is changed.\n'
        ' */\n'
        'static unsigned long a52_r274_frontier_start_jiffies;\n\n'
    )
    text = replace_once(text, anchor, block + anchor, "frontier timebase marker")

    text = replace_once(
        text,
        '\tunsigned long boot_s = jiffies_to_msecs(jiffies) / 1000U;\n',
        '\tunsigned long elapsed_j = jiffies - a52_r274_frontier_start_jiffies;\n'
        '\tunsigned long boot_s = jiffies_to_msecs(elapsed_j) / 1000U;\n',
        "elapsed jiffies calculation",
    )

    old = (
        '\tschedule_delayed_work(&a52_r273_frontier_work,\n'
        '\t\tmsecs_to_jiffies(A52_R273_SCAN_FAST_MS));\n'
        '\ta52_ackfr_record("P273 START h=%u q=%u/%u s=%u",\n'
    )
    new = (
        '\ta52_r274_frontier_start_jiffies = jiffies;\n'
        '\tschedule_delayed_work(&a52_r273_frontier_work,\n'
        '\t\tmsecs_to_jiffies(A52_R273_SCAN_FAST_MS));\n'
        '\ta52_ackfr_record("P274 START tb=elapsed q=%u/%u s=%u",\n'
        '\t\tA52_R273_SCAN_FAST_MS, A52_R273_SCAN_SLOW_MS,\n'
        '\t\tA52_R273_SUMMARY_S);\n'
        '\ta52_ackfr_record("P273 START h=%u q=%u/%u s=%u",\n'
    )
    text = replace_once(text, old, new, "late-init timebase arm")
    return text


def find_function(text: str, name: str) -> tuple[int, int]:
    m = re.search(r"\bint\s+" + re.escape(name) + r"\s*\([^;]*?\)\s*\{", text, re.S)
    if not m:
        raise RuntimeError(f"missing function {name}")
    start = m.start()
    brace = text.find("{", m.start(), m.end())
    depth = 0
    in_str = in_chr = esc = False
    for i in range(brace, len(text)):
        c = text[i]
        if esc:
            esc = False
        elif c == "\\" and (in_str or in_chr):
            esc = True
        elif c == '"' and not in_chr:
            in_str = not in_str
        elif c == "'" and not in_str:
            in_chr = not in_chr
        elif not in_str and not in_chr:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
    raise RuntimeError(f"unterminated function {name}")


def patch_panel(text: str) -> str:
    if PANEL_MARKER in text:
        return text

    fn_start, fn_end = find_function(text, "ss_panel_data_read_gpara")
    fn = text[fn_start:fn_end]

    static = (
        '/* ' + PANEL_MARKER + '\n'
        ' * Two independent hardware captures reached this function and retained\n'
        ' * its entry as the final display event. Split the exact TouchGrass path\n'
        ' * into observational checkpoints without changing command ordering,\n'
        ' * return values, locks, DSI payloads, or panel state.\n'
        ' */\n'
        'static atomic_t a52_r274_gpara_calls = ATOMIC_INIT(0);\n\n'
    )
    text = text[:fn_start] + static + text[fn_start:]
    fn_start += len(static)
    fn_end += len(static)
    fn = text[fn_start:fn_end]

    fn = replace_once(
        fn,
        '\tint read_pos_cmd_offset = 0;\n\n',
        '\tint read_pos_cmd_offset = 0;\n'
        '\tint a52_r274_call = atomic_inc_return(&a52_r274_gpara_calls);\n\n'
        '\ta52_ackfr_record("P274 G E n=%d ty=%d lk=%x g=%u p=%u",\n'
        '\t\ta52_r274_call, type, level_key, vdd->two_byte_gpara,\n'
        '\t\tvdd->pointing_gpara);\n\n',
        "gpara entry",
    )

    fn = replace_once(
        fn,
        '\tif (SS_IS_CMDS_NULL(set) || !ss_is_read_cmd(set)) {\n'
        '\t\tLCD_ERR(vdd, "invalid set(%d): %s\\n", type, ss_get_cmd_name(type));\n'
        '\t\treturn -EINVAL;\n'
        '\t}\n\n'
        '\t/* enable level key */\n',
        '\tif (SS_IS_CMDS_NULL(set) || !ss_is_read_cmd(set)) {\n'
        '\t\ta52_ackfr_record("P274 G X n=%d e=1 ty=%d", a52_r274_call, type);\n'
        '\t\tLCD_ERR(vdd, "invalid set(%d): %s\\n", type, ss_get_cmd_name(type));\n'
        '\t\treturn -EINVAL;\n'
        '\t}\n\n'
        '\ta52_ackfr_record("P274 G K n=%d p=0 lk=%x", a52_r274_call, level_key);\n'
        '\t/* enable level key */\n',
        "key pre",
    )

    fn = replace_once(
        fn,
        '\tif (level_key & POC_KEY)\n'
        '\t\tss_send_cmd(vdd, TX_POC_KEY_ENABLE);\n\n'
        '\tset->cmds[0].msg.rx_buf = rx_buffer;\n',
        '\tif (level_key & POC_KEY)\n'
        '\t\tss_send_cmd(vdd, TX_POC_KEY_ENABLE);\n'
        '\ta52_ackfr_record("P274 G K n=%d p=1 lk=%x", a52_r274_call, level_key);\n\n'
        '\tset->cmds[0].msg.rx_buf = rx_buffer;\n',
        "key post",
    )

    fn = replace_once(
        fn,
        '\tif (SS_IS_CMDS_NULL(read_pos_cmd)) {\n'
        '\t\tLCD_ERR(vdd, "No cmds for TX_REG_READ_POS.. \\n");\n'
        '\t\treturn -EINVAL;\n'
        '\t}\n\n',
        '\tif (SS_IS_CMDS_NULL(read_pos_cmd)) {\n'
        '\t\ta52_ackfr_record("P274 G X n=%d e=2 ty=%d", a52_r274_call, type);\n'
        '\t\tLCD_ERR(vdd, "No cmds for TX_REG_READ_POS.. \\n");\n'
        '\t\treturn -EINVAL;\n'
        '\t}\n\n',
        "read-pos invalid",
    )

    fn = replace_once(
        fn,
        '\tloop_limit = (orig_rx_len + RX_SIZE_LIMIT - 1) / RX_SIZE_LIMIT;\n\n\n'
        '\tLCD_DEBUG(vdd, "orig_rx_len (%d) , orig_offset (%d) loop_limit (%d)\\n", orig_rx_len, orig_offset, loop_limit);\n',
        '\tloop_limit = (orig_rx_len + RX_SIZE_LIMIT - 1) / RX_SIZE_LIMIT;\n\n'
        '\ta52_ackfr_record("P274 G A n=%d a=%02x l=%d o=%d lp=%d",\n'
        '\t\ta52_r274_call, (unsigned int)(u8)rx_addr, orig_rx_len,\n'
        '\t\torig_offset, loop_limit);\n\n'
        '\tLCD_DEBUG(vdd, "orig_rx_len (%d) , orig_offset (%d) loop_limit (%d)\\n", orig_rx_len, orig_offset, loop_limit);\n',
        "gpara geometry",
    )

    fn = replace_once(
        fn,
        '\t\tss_send_cmd(vdd, TX_REG_READ_POS);\n\n'
        '\t\t/* 2. Set new read length */\n',
        '\t\ta52_ackfr_record("P274 G P n=%d i=%d p=0 o=%d",\n'
        '\t\t\ta52_r274_call, i, new_offset);\n'
        '\t\tss_send_cmd(vdd, TX_REG_READ_POS);\n'
        '\t\ta52_ackfr_record("P274 G P n=%d i=%d p=1 o=%d",\n'
        '\t\t\ta52_r274_call, i, new_offset);\n\n'
        '\t\t/* 2. Set new read length */\n',
        "read-pos send",
    )

    fn = replace_once(
        fn,
        '\t\t/* 3. RX */\n'
        '\t\tss_send_cmd(vdd, type);\n\n'
        '\t\t/* copy to buffer */\n',
        '\t\t/* 3. RX */\n'
        '\t\ta52_ackfr_record("P274 G R n=%d i=%d p=0 l=%d",\n'
        '\t\t\ta52_r274_call, i, new_rx_len);\n'
        '\t\tss_send_cmd(vdd, type);\n'
        '\t\ta52_ackfr_record("P274 G R n=%d i=%d p=1 l=%d",\n'
        '\t\t\ta52_r274_call, i, new_rx_len);\n\n'
        '\t\t/* copy to buffer */\n',
        "panel RX send",
    )

    idx = fn.rfind('\n\treturn 0;\n')
    if idx < 0:
        raise RuntimeError("gpara completion return not found")
    fn = fn[:idx] + (
        '\n\ta52_ackfr_record("P274 G Z n=%d cp=%d", a52_r274_call, copy_pos);'
    ) + fn[idx:]

    text = text[:fn_start] + fn + text[fn_end:]
    return text


REC.write_text(patch_recorder(REC.read_text()), encoding="utf-8")
PANEL.write_text(patch_panel(PANEL.read_text()), encoding="utf-8")
print("phase274 panel GPARA frontier staged")
