#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 AOD P150: cap high-luminance LPM states to 10nit"
PANEL_REL = "techpack/display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def function_block(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise SystemExit(f"unterminated function: {signature}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    panel = root / PANEL_REL
    common = root / "techpack/display/msm/samsung/ss_dsi_panel_common.c"
    ufs = root / "drivers/scsi/ufs/ufshcd.c"
    lpm = root / "drivers/cpuidle/lpm-levels.c"

    for path in (panel, common, ufs, lpm):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    p = panel.read_text()
    cm = common.read_text()

    # Exact P149/P148 runtime boundary.
    if "A52 UFS P149: precise devfreq + runtime-PM autosuspend" not in ufs.read_text():
        raise SystemExit("P149 UFS baseline marker missing")
    if "A52 QCOM LPM P1: raw idle locks + short-idle tick + hotpath cleanup" not in lpm.read_text():
        raise SystemExit("P148 Qualcomm idle baseline marker missing")

    # AOD is already in Samsung panel LPM. Keep its actual 30-Hz LPM path,
    # self-display, ESD handling and all panel voltages untouched.
    if 'Hz : 30Hz' not in cm:
        raise SystemExit("Samsung 30-Hz panel LPM baseline missing")

    for needle in (
        "case LPM_60NIT:",
        "case LPM_30NIT:",
        "case LPM_10NIT:",
        "case LPM_2NIT:",
        "TX_LPM_60NIT_CMD",
        "TX_LPM_30NIT_CMD",
        "TX_LPM_10NIT_CMD",
        "TX_LPM_2NIT_CMD",
        "vdd->panel_func.samsung_update_lpm_ctrl_cmd = ss_update_panel_lpm_ctrl_cmd;",
        "vdd->panel_func.samsung_set_lpm_brightness = ss_set_panel_lpm_brightness;",
    ):
        if needle not in p:
            raise SystemExit(f"AMS646YD01 AOD baseline missing: {needle}")

    anchor = """enum LPMON_CMD_ID {
\tLPM_BL_CMDID_CTRL = 1,
\tLPM_ON_CMDID_BL = 4,
};

"""
    helper = anchor + f"""/*
 * {MARKER}
 *
 * AMS646YD01 already has Samsung's native 2/10/30/60-nit LPM commands and
 * enters panel LPM at 30 Hz.  Do not invent unsupported LFD or regulator
 * voltages.  Instead, preserve 2/10-nit requests and map only the expensive
 * 30/60-nit AOD states to the panel's native 10-nit command.
 *
 * Factory mode deliberately bypasses the cap so Samsung panel diagnostics
 * continue to exercise the original luminance states.
 */
#define A52_AOD_MAX_NIT LPM_10NIT

static int a52_aod_effective_lpm_bl_level(struct samsung_display_driver_data *vdd)
{{
\tint requested = vdd->panel_lpm.lpm_bl_level;

\tif (unlikely(vdd->is_factory_mode))
\t\treturn requested;

\tswitch (requested) {{
\tcase LPM_30NIT:
\tcase LPM_60NIT:
\t\treturn A52_AOD_MAX_NIT;
\tdefault:
\t\treturn requested;
\t}}
}}

"""
    p = replace_once(p, anchor, helper, "AOD saver helper")

    old_set_head = """static void ss_set_panel_lpm_brightness(struct samsung_display_driver_data *vdd)
{
\tstruct dsi_panel_cmd_set *set = ss_get_cmds(vdd, TX_LPM_BL_CMD);
\tstruct dsi_panel_cmd_set *set_lpm_bl;

"""
    new_set_head = """static void ss_set_panel_lpm_brightness(struct samsung_display_driver_data *vdd)
{
\tstruct dsi_panel_cmd_set *set = ss_get_cmds(vdd, TX_LPM_BL_CMD);
\tstruct dsi_panel_cmd_set *set_lpm_bl;
\tint lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);

"""
    p = replace_once(p, old_set_head, new_set_head, "live AOD brightness effective level")
    set_block = function_block(p, "static void ss_set_panel_lpm_brightness(")
    set_new = set_block.replace(
        "switch (vdd->panel_lpm.lpm_bl_level) {",
        "switch (lpm_bl_level) {",
        1,
    )
    old_log = """\tLCD_INFO(vdd, "[Panel LPM] bl_level : %s\\n",
\t\t\t/* Check current brightness level */
\t\t\tvdd->panel_lpm.lpm_bl_level == LPM_2NIT ? "2NIT" :
\t\t\tvdd->panel_lpm.lpm_bl_level == LPM_10NIT ? "10NIT" :
\t\t\tvdd->panel_lpm.lpm_bl_level == LPM_30NIT ? "30NIT" :
\t\t\tvdd->panel_lpm.lpm_bl_level == LPM_60NIT ? "60NIT" : "UNKNOWN");
"""
    new_log = """\tLCD_INFO(vdd, "[Panel LPM] requested=%dnit effective=%dnit%s\\n",
\t\t\tvdd->panel_lpm.lpm_bl_level, lpm_bl_level,
\t\t\t(vdd->panel_lpm.lpm_bl_level != lpm_bl_level) ?
\t\t\t" [A52 AOD saver]" : "");
"""
    if old_log not in set_new:
        raise SystemExit("live AOD brightness log anchor missing")
    set_new = set_new.replace(old_log, new_log, 1)
    p = replace_once(p, set_block, set_new, "live AOD brightness cap")

    old_update_head = """static void ss_update_panel_lpm_ctrl_cmd(struct samsung_display_driver_data *vdd, int enable)
{
\tstruct dsi_panel_cmd_set *set_lpm_on = ss_get_cmds(vdd, TX_LPM_ON);
\tstruct dsi_panel_cmd_set *set_lpm_off = ss_get_cmds(vdd, TX_LPM_OFF);
\tstruct dsi_panel_cmd_set *set_lpm_bl;

"""
    new_update_head = """static void ss_update_panel_lpm_ctrl_cmd(struct samsung_display_driver_data *vdd, int enable)
{
\tstruct dsi_panel_cmd_set *set_lpm_on = ss_get_cmds(vdd, TX_LPM_ON);
\tstruct dsi_panel_cmd_set *set_lpm_off = ss_get_cmds(vdd, TX_LPM_OFF);
\tstruct dsi_panel_cmd_set *set_lpm_bl;
\tint lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);

"""
    p = replace_once(p, old_update_head, new_update_head, "AOD entry effective level")
    update_block = function_block(p, "static void ss_update_panel_lpm_ctrl_cmd(")
    if update_block.count("switch (vdd->panel_lpm.lpm_bl_level) {") != 1:
        raise SystemExit("AOD entry brightness switch shape changed")
    update_new = update_block.replace(
        "switch (vdd->panel_lpm.lpm_bl_level) {",
        "switch (lpm_bl_level) {",
        1,
    )
    update_new = update_new.replace(
        '\tLCD_INFO(vdd, "%s--\\n", __func__);',
        '\tLCD_INFO(vdd, "%s-- requested=%dnit effective=%dnit%s\\n", __func__,\n'
        '\t\t\tvdd->panel_lpm.lpm_bl_level, lpm_bl_level,\n'
        '\t\t\t(vdd->panel_lpm.lpm_bl_level != lpm_bl_level) ?\n'
        '\t\t\t" [A52 AOD saver]" : "");',
        1,
    )
    p = replace_once(p, update_block, update_new, "AOD entry brightness cap")

    panel.write_text(p)

    # Structural audits.
    P = panel.read_text()

    for needle in (
        MARKER,
        "#define A52_AOD_MAX_NIT LPM_10NIT",
        "static int a52_aod_effective_lpm_bl_level(",
        "case LPM_30NIT:",
        "case LPM_60NIT:",
        "return A52_AOD_MAX_NIT;",
        "int lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);",
        "switch (lpm_bl_level) {",
        "[A52 AOD saver]",
    ):
        if needle not in P:
            raise SystemExit(f"audit failed: panel missing {needle}")

    if P.count("int lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);") != 2:
        raise SystemExit("audit failed: cap must cover AOD entry and live brightness updates")
    if P.count("switch (lpm_bl_level) {") != 2:
        raise SystemExit("audit failed: effective AOD level must drive both brightness switches")

    helper_block = function_block(P, "static int a52_aod_effective_lpm_bl_level(")
    if "unlikely(vdd->is_factory_mode)" not in helper_block:
        raise SystemExit("audit failed: factory-mode bypass missing")
    if "case LPM_2NIT:" in helper_block or "case LPM_10NIT:" in helper_block:
        raise SystemExit("audit failed: native 2/10-nit states must remain uncapped")

    # Do not change Samsung's AOD transport/power architecture.
    CM = common.read_text()
    for needle in (
        'Hz : 30Hz',
        "ss_panel_lpm_power_ctrl(vdd, enable);",
        "vdd->self_disp.aod_enter(vdd);",
        "vdd->self_disp.aod_exit(vdd);",
        "vdd->esd_recovery.is_wakeup_source = true;",
    ):
        if needle not in CM:
            raise SystemExit(f"audit failed: Samsung LPM architecture changed/missing: {needle}")

    print("[audit] exact P149 UFS baseline required: PASS")
    print("[audit] exact P148 Qualcomm idle baseline retained: PASS")
    print("[audit] Samsung AOD remains native 30-Hz panel LPM: PASS")
    print("[audit] native 2-nit AOD remains 2 nit: PASS")
    print("[audit] native 10-nit AOD remains 10 nit: PASS")
    print("[audit] 30/60-nit AOD requests are capped to native 10-nit command: PASS")
    print("[audit] factory panel diagnostics bypass the AOD cap: PASS")
    print("[audit] no panel voltage/regulator setting changed: PASS")
    print("[audit] no LFD/refresh command added: PASS")
    print("[audit] self-display and ESD wake/recovery behavior retained: PASS")
    print("[done] A52 P150 AOD power saver applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
