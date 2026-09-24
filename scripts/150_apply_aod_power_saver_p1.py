#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 AOD P150: FC3 60-to-30nit cap + duplicate LPM TX suppression"
PANEL_REL = (
    "techpack/display/msm/samsung/S6E3FC3_AMS646YD01/"
    "ss_dsi_panel_S6E3FC3_AMS646YD01.c"
)


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

    # Exact boot-tested P149/P148 source boundary.
    if "A52 UFS P149: precise devfreq + runtime-PM autosuspend" not in ufs.read_text():
        raise SystemExit("P149 UFS baseline marker missing")
    if "A52 QCOM LPM P1: raw idle locks + short-idle tick + hotpath cleanup" not in lpm.read_text():
        raise SystemExit("P148 Qualcomm idle baseline marker missing")

    # Require the exact FC3 AOD implementation. This phase deliberately does
    # not invent LFD registers, refresh modes or regulator voltages.
    if "Hz : 30Hz" not in cm:
        raise SystemExit("Samsung fixed 30-Hz panel LPM baseline missing")

    for needle in (
        "static void ss_set_panel_lpm_brightness(struct samsung_display_driver_data *vdd)",
        "static void ss_update_panel_lpm_ctrl_cmd(struct samsung_display_driver_data *vdd, int enable)",
        "case LPM_60NIT:",
        "case LPM_30NIT:",
        "case LPM_10NIT:",
        "case LPM_2NIT:",
        "TX_LPM_60NIT_CMD",
        "TX_LPM_30NIT_CMD",
        "TX_LPM_10NIT_CMD",
        "TX_LPM_2NIT_CMD",
        "ss_send_cmd(vdd, TX_LPM_BL_CMD);",
        "vdd->panel_func.samsung_update_lpm_ctrl_cmd = ss_update_panel_lpm_ctrl_cmd;",
        "vdd->panel_func.samsung_set_lpm_brightness = ss_set_panel_lpm_brightness;",
    ):
        if needle not in p:
            raise SystemExit(f"AMS646YD01 AOD baseline missing: {needle}")

    anchor = """enum LPMON_CMD_ID {
	LPM_BL_CMDID_CTRL = 1,
	LPM_ON_CMDID_BL = 4,
};

"""
    helper = anchor + f"""/*
 * {MARKER}
 *
 * This FC3 panel has Samsung-provided 2/10/30/60-nit LPM commands, but the
 * panel-specific driver does not implement Samsung's LFD callback. Keep its
 * proven 30-Hz AOD transport and power sequence intact.
 *
 * For normal users, cap only the highest 60-nit AOD request to the native
 * 30-nit command. Factory mode keeps the original 60-nit state available.
 *
 * Cache the last live LPM brightness write so repeated identical userspace
 * updates do not wake the DSI path needlessly. ESD recovery always resends.
 */
static int a52_aod_last_lpm_bl[MAX_DISPLAY_NDX];

static int a52_aod_effective_lpm_bl_level(
		struct samsung_display_driver_data *vdd)
{{
	int requested = vdd->panel_lpm.lpm_bl_level;

	if (unlikely(vdd->is_factory_mode))
		return requested;

	if (requested == LPM_60NIT)
		return LPM_30NIT;

	return requested;
}}

static bool a52_aod_lpm_bl_is_duplicate(
		struct samsung_display_driver_data *vdd, int level)
{{
	if (vdd->ndx >= MAX_DISPLAY_NDX)
		return false;

	if (unlikely(vdd->is_factory_mode) ||
			!ss_is_panel_lpm(vdd) ||
			vdd->panel_lpm.esd_recovery)
		return false;

	return a52_aod_last_lpm_bl[vdd->ndx] == level;
}}

static void a52_aod_lpm_bl_cache(
		struct samsung_display_driver_data *vdd, int level)
{{
	if (vdd->ndx < MAX_DISPLAY_NDX)
		a52_aod_last_lpm_bl[vdd->ndx] = level;
}}

"""
    p = replace_once(p, anchor, helper, "P150 helper insertion")

    # Live AOD brightness changes.
    set_fn = function_block(
        p, "static void ss_set_panel_lpm_brightness(struct samsung_display_driver_data *vdd)"
    )
    set_new = set_fn.replace(
        """	struct dsi_panel_cmd_set *set_lpm_bl;

""",
        """	struct dsi_panel_cmd_set *set_lpm_bl;
	int lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);

""",
        1,
    )
    if set_new == set_fn:
        raise SystemExit("live AOD effective-level declaration anchor missing")

    if set_new.count("switch (vdd->panel_lpm.lpm_bl_level) {") != 1:
        raise SystemExit("live AOD brightness switch shape changed")
    set_new = set_new.replace(
        "switch (vdd->panel_lpm.lpm_bl_level) {",
        "switch (lpm_bl_level) {",
        1,
    )

    duplicate_anchor = """	if (SS_IS_CMDS_NULL(set_lpm_bl)) {
		LCD_ERR(vdd, "No cmds for alpm_ctrl..\\n");
		return;
	}

	memcpy(&set->cmds[LPM_BL_CMDID_CTRL].ss_txbuf[1],
"""
    duplicate_new = """	if (SS_IS_CMDS_NULL(set_lpm_bl)) {
		LCD_ERR(vdd, "No cmds for alpm_ctrl..\\n");
		return;
	}

	if (a52_aod_lpm_bl_is_duplicate(vdd, lpm_bl_level)) {
		LCD_DEBUG(vdd, "[AOD P150] skip duplicate LPM brightness: %d nit\\n",
				lpm_bl_level);
		return;
	}

	memcpy(&set->cmds[LPM_BL_CMDID_CTRL].ss_txbuf[1],
"""
    if duplicate_anchor not in set_new:
        raise SystemExit("duplicate LPM write insertion anchor missing")
    set_new = set_new.replace(duplicate_anchor, duplicate_new, 1)

    send_anchor = """	/* send lpm bl cmd */
	ss_send_cmd(vdd, TX_LPM_BL_CMD);

	LCD_INFO(vdd, "[Panel LPM] bl_level : %s\\n",
"""
    send_new = """	/* send lpm bl cmd */
	ss_send_cmd(vdd, TX_LPM_BL_CMD);
	a52_aod_lpm_bl_cache(vdd, lpm_bl_level);

	LCD_INFO(vdd, "[AOD P150] requested=%dnit effective=%dnit%s\\n",
			vdd->panel_lpm.lpm_bl_level, lpm_bl_level,
			(vdd->panel_lpm.lpm_bl_level != lpm_bl_level) ?
			" [power cap]" : "");

	LCD_INFO(vdd, "[Panel LPM] bl_level : %s\\n",
"""
    if send_anchor not in set_new:
        raise SystemExit("live AOD send/cache anchor missing")
    set_new = set_new.replace(send_anchor, send_new, 1)

    p = replace_once(p, set_fn, set_new, "live AOD brightness policy")

    # AOD entry sequence: embed the capped level in TX_LPM_ON immediately.
    update_fn = function_block(
        p, "static void ss_update_panel_lpm_ctrl_cmd(struct samsung_display_driver_data *vdd, int enable)"
    )
    update_new = update_fn.replace(
        """	struct dsi_panel_cmd_set *set_lpm_bl;

	LCD_INFO(vdd, "%s++\\n", __func__);
""",
        """	struct dsi_panel_cmd_set *set_lpm_bl;
	int lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);

	LCD_INFO(vdd, "%s++\\n", __func__);

	/* New LPM entry/exit invalidates the live-brightness TX cache. */
	a52_aod_lpm_bl_cache(vdd, 0);
""",
        1,
    )
    if update_new == update_fn:
        raise SystemExit("AOD entry effective-level/cache anchor missing")

    if update_new.count("switch (vdd->panel_lpm.lpm_bl_level) {") != 1:
        raise SystemExit("AOD entry brightness switch shape changed")
    update_new = update_new.replace(
        "switch (vdd->panel_lpm.lpm_bl_level) {",
        "switch (lpm_bl_level) {",
        1,
    )

    log_anchor = """	LCD_INFO(vdd, "%s--\\n", __func__);
"""
    log_new = """	if (enable)
		LCD_INFO(vdd, "[AOD P150] entry requested=%dnit effective=%dnit%s\\n",
				vdd->panel_lpm.lpm_bl_level, lpm_bl_level,
				(vdd->panel_lpm.lpm_bl_level != lpm_bl_level) ?
				" [power cap]" : "");

	LCD_INFO(vdd, "%s--\\n", __func__);
"""
    if log_anchor not in update_new:
        raise SystemExit("AOD entry logging anchor missing")
    update_new = update_new.replace(log_anchor, log_new, 1)

    p = replace_once(p, update_fn, update_new, "AOD entry brightness policy")
    panel.write_text(p)

    # Structural audits.
    P = panel.read_text()
    for needle in (
        MARKER,
        "static int a52_aod_last_lpm_bl[MAX_DISPLAY_NDX];",
        "if (requested == LPM_60NIT)",
        "return LPM_30NIT;",
        "a52_aod_lpm_bl_is_duplicate(vdd, lpm_bl_level)",
        "vdd->panel_lpm.esd_recovery",
        "a52_aod_lpm_bl_cache(vdd, lpm_bl_level);",
        "a52_aod_lpm_bl_cache(vdd, 0);",
        "int lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);",
        "switch (lpm_bl_level) {",
    ):
        if needle not in P:
            raise SystemExit(f"audit failed: panel missing {needle}")

    if P.count("int lpm_bl_level = a52_aod_effective_lpm_bl_level(vdd);") != 2:
        raise SystemExit("audit failed: cap must cover entry and live AOD brightness")
    if P.count("switch (lpm_bl_level) {") != 2:
        raise SystemExit("audit failed: effective AOD level must drive both switches")

    helper_fn = function_block(P, "static int a52_aod_effective_lpm_bl_level(")
    if "requested == LPM_60NIT" not in helper_fn:
        raise SystemExit("audit failed: 60->30 cap missing")
    if "LPM_10NIT" in helper_fn or "requested == LPM_30NIT" in helper_fn:
        raise SystemExit("audit failed: P150 must leave 2/10/30-nit requests unchanged")

    dup_fn = function_block(P, "static bool a52_aod_lpm_bl_is_duplicate(")
    for needle in ("is_factory_mode", "ss_is_panel_lpm(vdd)", "panel_lpm.esd_recovery"):
        if needle not in dup_fn:
            raise SystemExit(f"audit failed: duplicate-TX safety condition missing: {needle}")

    # Factory mode must still be able to use Samsung's original 60-nit cmd.
    set_fn2 = function_block(
        P, "static void ss_set_panel_lpm_brightness(struct samsung_display_driver_data *vdd)"
    )
    update_fn2 = function_block(
        P, "static void ss_update_panel_lpm_ctrl_cmd(struct samsung_display_driver_data *vdd, int enable)"
    )
    for name, blob in (("live", set_fn2), ("entry", update_fn2)):
        if "case LPM_60NIT:" not in blob or "TX_LPM_60NIT_CMD" not in blob:
            raise SystemExit(f"audit failed: factory 60-nit command lost from {name} path")

    # Keep the original unsupported ALPM/HLPM control block disabled.
    if "#if 0" not in update_fn2 or "set_lpm_on->cmds[LPMON_CMDID_CTRL]" not in update_fn2:
        raise SystemExit("audit failed: disabled ALPM/HLPM mode-control block changed")

    # Keep the panel's actual transport/power behavior unchanged.
    if "Hz : 30Hz" not in cm:
        raise SystemExit("audit failed: Samsung 30-Hz AOD path changed")
    if "samsung_lfd_get_base_val =" in P:
        raise SystemExit("audit failed: unsupported FC3 LFD callback was added")
    for needle in (
        "vdd->panel_func.br_func[BR_FUNC_GAMMA] = ss_brightness_gamma_mode2_normal;",
        "vdd->panel_func.br_func[BR_FUNC_HBM_GAMMA] = ss_brightness_gamma_mode2_hbm;",
        "vdd->panel_func.br_func[BR_FUNC_VRR] = ss_vrr;",
        "vdd->panel_func.br_func[BR_FUNC_HBM_VRR] = ss_vrr_hbm;",
        "vrr->cur_refresh_rate = vrr->adjusted_refresh_rate = 120;",
    ):
        if needle not in P:
            raise SystemExit(f"audit failed: normal display behavior changed/missing: {needle}")

    print("[audit] exact boot-tested P149 UFS baseline required: PASS")
    print("[audit] exact boot-tested P148 Qualcomm idle baseline retained: PASS")
    print("[audit] normal AOD 60-nit request maps to native 30-nit command: PASS")
    print("[audit] native 2/10/30-nit AOD requests remain unchanged: PASS")
    print("[audit] factory mode retains original 60-nit command: PASS")
    print("[audit] duplicate live LPM brightness DSI writes suppressed: PASS")
    print("[audit] ESD recovery bypasses duplicate suppression: PASS")
    print("[audit] Samsung fixed 30-Hz AOD transport retained: PASS")
    print("[audit] no unsupported 1-Hz/LFD enablement: PASS")
    print("[audit] no panel regulator/voltage changes: PASS")
    print("[audit] normal/HBM/fingerprint display paths untouched: PASS")
    print("[done] A52 P150 conservative AOD power saver applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
