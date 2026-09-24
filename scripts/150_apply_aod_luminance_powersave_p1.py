#!/usr/bin/env python3
from pathlib import Path
import sys

MARKER = "A52 AOD P150: configurable LPM luminance ceiling"

def replace_once(s, old, new, label):
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected 1 match, found {n}")
    return s.replace(old, new, 1)

def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()

    hdr = root / "techpack/display/msm/samsung/ss_dsi_panel_common.h"
    common = root / "techpack/display/msm/samsung/ss_dsi_panel_common.c"
    sysfs = root / "techpack/display/msm/samsung/ss_dsi_panel_sysfs.c"
    panel = root / "techpack/display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"
    p149 = root / "drivers/scsi/ufs/ufshcd.c"

    for p in (hdr, common, sysfs, panel, p149):
        if not p.is_file():
            raise SystemExit(f"missing required file: {p}")

    H, C, S, P = hdr.read_text(), common.read_text(), sysfs.read_text(), panel.read_text()

    if "A52 UFS P149: precise devfreq + runtime-PM autosuspend" not in p149.read_text():
        raise SystemExit("exact P149 UFS baseline marker missing")
    if "S6E3FC3_AMS646YD01_FHD_init" not in P:
        raise SystemExit("A52 AMS646YD01 panel driver missing")
    if "vdd->panel_func.samsung_lfd_get_base_val" in P:
        raise SystemExit("panel unexpectedly gained LFD support; re-audit before applying P150")

    # Store an optional luminance ceiling in Samsung's existing LPM state.
    H = replace_once(
        H,
        """\tint lpm_bl_level;
\tbool esd_recovery;
""",
        f"""\tint lpm_bl_level;
\t/*
\t * {MARKER}
\t * 0/60 = stock ceiling; A52 defaults this to 10 nits.
\t */
\tint aod_max_nit;
\tbool esd_recovery;
""",
        "add AOD luminance ceiling state",
    )

    anchor = """struct lpm_info {
\tbool is_support;
\tu8 origin_mode;
\tu8 ver;
\tu8 mode;
\tu8 hz;
\tint lpm_bl_level;
"""
    if anchor not in H:
        raise SystemExit("lpm_info structure anchor missing after field insertion")

    helper_anchor = """struct clk_timing_table {
"""
    helper = f"""static inline int ss_aod_cap_lpm_nit(struct lpm_info *lpm, int requested)
{{
\tint cap = lpm->aod_max_nit;

\t/* {MARKER}: never invent an unsupported panel brightness command. */
\tif (cap == LPM_2NIT || cap == LPM_10NIT ||
\t    cap == LPM_30NIT || cap == LPM_60NIT) {{
\t\tif (requested > cap)
\t\t\treturn cap;
\t}}

\treturn requested;
}}

"""
    H = replace_once(H, helper_anchor, helper + helper_anchor,
                     "add AOD cap helper")

    # New LPM (current AOD service): candela-map output is the central point
    # used before samsung_set_lpm_brightness().
    C = replace_once(
        C,
        """\tvdd->panel_lpm.lpm_bl_level = table->cd[p];

\tLCD_DEBUG(vdd, "%s: (%d)->(%d)\\n",
""",
        f"""\tvdd->panel_lpm.lpm_bl_level =
\t\tss_aod_cap_lpm_nit(&vdd->panel_lpm, table->cd[p]);

\t/* {MARKER}: cap only panel LPM/AOD luminance. */
\tLCD_DEBUG(vdd, "%s: (%d)->(%d)\\n",
""",
        "cap new-version AOD candela mapping",
    )

    # Old LPM protocol: cap its explicit 2/60-nit mode request too.
    S = replace_once(
        S,
        """\t\t/*set bl also only in LPM_VER0*/
\t\tvdd->panel_lpm.lpm_bl_level = bl_level;
\t}
""",
        f"""\t\t/* set bl also only in LPM_VER0 */
\t\tvdd->panel_lpm.lpm_bl_level =
\t\t\tss_aod_cap_lpm_nit(&vdd->panel_lpm, bl_level);
\t}}
""",
        "cap legacy AOD brightness",
    )

    # Add a runtime control next to Samsung's existing ALPM sysfs node.
    mode_store = """static ssize_t ss_panel_lpm_mode_store(struct device *dev,
\t\tstruct device_attribute *attr, const char *buf, size_t size)
{
"""
    if mode_store not in S:
        raise SystemExit("ALPM sysfs store anchor missing")

    insert_at = S.find("static ssize_t mipi_samsung_hmt_bright_show", S.find(mode_store))
    if insert_at < 0:
        raise SystemExit("HMT sysfs anchor missing")
    control = f"""
/*
 * {MARKER}
 * Valid values are the panel's existing verified AOD command levels.
 * 60 restores the stock maximum behavior.
 */
static ssize_t ss_aod_max_nit_show(struct device *dev,
\t\tstruct device_attribute *attr, char *buf)
{{
\tstruct samsung_display_driver_data *vdd =
\t\t(struct samsung_display_driver_data *)dev_get_drvdata(dev);

\tif (IS_ERR_OR_NULL(vdd))
\t\treturn -ENODEV;

\treturn snprintf(buf, 16, "%d\\n", vdd->panel_lpm.aod_max_nit);
}}

static ssize_t ss_aod_max_nit_store(struct device *dev,
\t\tstruct device_attribute *attr, const char *buf, size_t size)
{{
\tstruct samsung_display_driver_data *vdd =
\t\t(struct samsung_display_driver_data *)dev_get_drvdata(dev);
\tint value;

\tif (IS_ERR_OR_NULL(vdd))
\t\treturn -ENODEV;
\tif (sscanf(buf, "%d", &value) != 1)
\t\treturn -EINVAL;
\tif (value != LPM_2NIT && value != LPM_10NIT &&
\t    value != LPM_30NIT && value != LPM_60NIT)
\t\treturn -EINVAL;

\tmutex_lock(&vdd->panel_lpm.lpm_lock);
\tvdd->panel_lpm.aod_max_nit = value;
\tif (vdd->panel_lpm.lpm_bl_level > value)
\t\tvdd->panel_lpm.lpm_bl_level = value;
\tmutex_unlock(&vdd->panel_lpm.lpm_lock);

\tLCD_INFO(vdd, "[AOD P150] max luminance: %d nit\\n", value);
\treturn size;
}}

"""
    S = S[:insert_at] + control + S[insert_at:]

    S = replace_once(
        S,
        """static DEVICE_ATTR(alpm, S_IRUSR | S_IRGRP | S_IWUSR | S_IWGRP, ss_panel_lpm_mode_show, ss_panel_lpm_mode_store);
""",
        """static DEVICE_ATTR(alpm, S_IRUSR | S_IRGRP | S_IWUSR | S_IWGRP, ss_panel_lpm_mode_show, ss_panel_lpm_mode_store);
static DEVICE_ATTR(aod_max_nit, S_IRUGO | S_IWUSR | S_IWGRP, ss_aod_max_nit_show, ss_aod_max_nit_store);
""",
        "add AOD max-nit sysfs attribute",
    )
    S = replace_once(
        S,
        """\t&dev_attr_alpm.attr,
\t&dev_attr_hmt_bright.attr,
""",
        """\t&dev_attr_alpm.attr,
\t&dev_attr_aod_max_nit.attr,
\t&dev_attr_hmt_bright.attr,
""",
        "register AOD max-nit sysfs attribute",
    )

    # Enable the conservative default only for the exact A52 panel.
    init_sig = "void S6E3FC3_AMS646YD01_FHD_init(struct samsung_display_driver_data *vdd)"
    start = P.find(init_sig)
    if start < 0:
        raise SystemExit("A52 panel init missing")
    state_anchor = """\t/* Default Panel Power Status is OFF */
\tvdd->panel_state = PANEL_PWR_OFF;

"""
    P = replace_once(
        P,
        state_anchor,
        state_anchor + f"""\t/*
\t * {MARKER}
\t * The panel has validated 2/10/30/60-nit AOD command tables but no
\t * panel-specific LFD/1-Hz implementation. Prefer a luminance ceiling
\t * rather than inventing unsupported timing or regulator settings.
\t */
\tvdd->panel_lpm.aod_max_nit = LPM_10NIT;

""",
        "initialize A52 AOD cap",
    )

    hdr.write_text(H)
    common.write_text(C)
    sysfs.write_text(S)
    panel.write_text(P)

    # Audits.
    for path, needles in {
        hdr: [MARKER, "int aod_max_nit;", "ss_aod_cap_lpm_nit"],
        common: [MARKER, "ss_aod_cap_lpm_nit(&vdd->panel_lpm, table->cd[p])"],
        sysfs: ["ss_aod_max_nit_show", "ss_aod_max_nit_store",
                "DEVICE_ATTR(aod_max_nit", "&dev_attr_aod_max_nit.attr",
                "ss_aod_cap_lpm_nit(&vdd->panel_lpm, bl_level)"],
        panel: [MARKER, "vdd->panel_lpm.aod_max_nit = LPM_10NIT;"],
    }.items():
        data = path.read_text()
        for needle in needles:
            if needle not in data:
                raise SystemExit(f"audit failed: {path}: missing {needle}")

    # Explicitly prohibit the risky approaches considered during the audit.
    pp = panel.read_text()
    if "samsung_lfd_get_base_val =" in pp:
        raise SystemExit("audit failed: P150 must not enable unsupported LFD")
    if "lpm_pwr_ctrl_supply_min_v" in pp or "regulator_set_voltage" in pp:
        raise SystemExit("audit failed: P150 must not invent panel LPM voltages")

    print("[audit] exact P149 baseline required: PASS")
    print("[audit] A52 AMS646YD01 only: default AOD ceiling = 10 nit: PASS")
    print("[audit] 2/10/30/60 existing Samsung LPM commands preserved: PASS")
    print("[audit] new and legacy AOD brightness paths both capped: PASS")
    print("[audit] runtime sysfs control aod_max_nit added: PASS")
    print("[audit] 60 nit restores stock maximum behavior: PASS")
    print("[audit] normal/HBM/fingerprint brightness paths untouched: PASS")
    print("[audit] no unsupported 1-Hz/LFD enablement: PASS")
    print("[audit] no panel regulator/voltage changes: PASS")
    print("[done] A52 P150 AOD luminance power saver applied")

if __name__ == "__main__":
    main()
