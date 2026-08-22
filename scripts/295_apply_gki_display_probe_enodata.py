#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
PANEL = Path("drivers/a52_display/msm/dsi/dsi_panel.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE295_DISPLAY_PROBE_ENODATA_V1"
PANEL_MARK = "A52_PHASE295_PANEL_GET_ENODATA_R2"
REC_INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>\n"


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase295 {label}: expected exactly 1 anchor, found {count}")
    return text.replace(old, new, 1)


def behavioral_counts(text: str) -> dict[str, int]:
    tokens = (
        "DSI_W32(", "writel(", "writel_relaxed(", "clk_set_rate(",
        "wait_for_completion", "msleep(", "usleep_range(", "udelay(",
        "gpio_set_value(", "dsi_ctrl_cmd_transfer(", "dsi_panel_tx_cmd_set(",
    )
    return {token: text.count(token) for token in tokens}


def verify(text: str) -> None:
    required = (
        MARK,
        'P276 295P s=0 i=%d be=%d pn=%d fr=%d',
        'P276 295P s=1 rc=%d',
        'P276 295I s=0',
        'P276 295I s=1 rc=%d',
        'P276 295I s=2',
        'P276 295I s=3 rc=%d',
        'P276 295D s=0 fw=%d pn=%d',
        'P276 295D s=1 rc=%d cc=%d',
        'P276 295D s=2 rc=%d',
        'P276 295T s=0 cc=%d pc=%u',
        'P276 295T s=1 i=%d ix=%d n=%d',
        'P276 295T s=2 i=%d ix=%d n=%d',
        'P276 295T s=3 rc=%d eb=%d',
        'P276 295R s=0 i=%d e=%d',
        'P276 295R s=1 i=%d e=%d',
        'P276 295R s=2 e=%d',
        'P276 295R s=3 rc=%d',
        'P276 295R s=4 rc=%d',
        'P276 295R s=5 rc=0',
        'P276 295R s=9 rc=%d i=%d',
        'P276 295C s=0 n=%d',
        'P276 295C s=1 i=%d e=%d n=%.24s',
        'P276 295C s=2 rc=0',
        'P276 295C s=9 rc=%d',
    )
    for marker in required:
        if text.count(marker) != 1:
            raise SystemExit(f"Phase295 audit marker count failed: {marker!r}: {text.count(marker)}")
    if REC_INCLUDE not in text:
        raise SystemExit("Phase295 recorder include missing")


def patch(text: str) -> str:
    if MARK in text:
        verify(text)
        return text

    if REC_INCLUDE not in text:
        raise SystemExit("Phase295 requires inherited display lifecycle recorder include")
    if 'A52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_dev_probe");' not in text:
        raise SystemExit("Phase295 requires inherited dsi_display_dev_probe lifecycle scope")

    before_behavior = behavioral_counts(text)

    display_mark_anchor = (
        '#include "dsi_parser.h"\n'
        + REC_INCLUDE
        + "\n#if defined(CONFIG_DISPLAY_SAMSUNG)\n"
    )
    text = one(
        text,
        display_mark_anchor,
        '#include "dsi_parser.h"\n'
        + REC_INCLUDE
        + "\n/* " + MARK + "\n"
        " * Passive Phase293 follow-up. The preserved hardware capture proved\n"
        " * dsi_display_dev_probe returns -ENODATA before the target memory-DMA\n"
        " * transaction is admitted. These probes only read existing software\n"
        " * state and append R48/RS48 records through the existing recorder.\n"
        " */\n\n"
        "#if defined(CONFIG_DISPLAY_SAMSUNG)\n",
        "marker insertion",
    )

    old = '''\tplatform_set_drvdata(pdev, display);\n\n\n\t/* initialize display in firmware callback */\n'''
    new = '''\tplatform_set_drvdata(pdev, display);\n\ta52_ackfr_record("P276 295P s=0 i=%d be=%d pn=%d fr=%d",\n\t\tindex, boot_disp->boot_disp_en, !!panel_node, firm_req);\n\n\n\t/* initialize display in firmware callback */\n'''
    text = one(text, old, new, "probe pre-init snapshot")

    old = '''\tif (!firm_req) {\n\t\trc = dsi_display_init(display);\n\t\tif (rc)\n\t\t\tgoto end;\n\t}\n'''
    new = '''\tif (!firm_req) {\n\t\trc = dsi_display_init(display);\n\t\ta52_ackfr_record("P276 295P s=1 rc=%d", rc);\n\t\tif (rc)\n\t\t\tgoto end;\n\t}\n'''
    text = one(text, old, new, "probe init return")

    old = '''\tmutex_init(&display->display_lock);\n\n\trc = _dsi_display_dev_init(display);\n\tif (rc) {\n'''
    new = '''\tmutex_init(&display->display_lock);\n\n\ta52_ackfr_record("P276 295I s=0");\n\trc = _dsi_display_dev_init(display);\n\ta52_ackfr_record("P276 295I s=1 rc=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "display init dev-init return")

    old = '''\ta52_ackfr_record("DRMCOMP component-add enter dev=%s display=%s",\n\t\t\t dev_name(&pdev->dev), display->name ? display->name : "-");\n\trc = component_add(&pdev->dev, &dsi_display_comp_ops);\n\ta52_ackfr_record("DRMCOMP component-add exit dev=%s rc=%d",\n\t\t\t dev_name(&pdev->dev), rc);\n\tif (rc)\n\t\tDSI_ERR("component add failed, rc=%d\\n", rc);\n'''
    new = '''\ta52_ackfr_record("DRMCOMP component-add enter dev=%s display=%s",\n\t\t\t dev_name(&pdev->dev), display->name ? display->name : "-");\n\ta52_ackfr_record("P276 295I s=2");\n\trc = component_add(&pdev->dev, &dsi_display_comp_ops);\n\ta52_ackfr_record("DRMCOMP component-add exit dev=%s rc=%d",\n\t\t\t dev_name(&pdev->dev), rc);\n\ta52_ackfr_record("P276 295I s=3 rc=%d", rc);\n\tif (rc)\n\t\tDSI_ERR("component add failed, rc=%d\\n", rc);\n'''
    text = one(text, old, new, "component add return")

    old = '''\tif (display->fw && display->parser)\n\t\tdisplay->parser_node = dsi_parser_get_head_node(\n\t\t\t\tdisplay->parser, display->fw->data,\n\t\t\t\tdisplay->fw->size);\n\n\trc = dsi_display_parse_dt(display);\n'''
    new = '''\tif (display->fw && display->parser)\n\t\tdisplay->parser_node = dsi_parser_get_head_node(\n\t\t\t\tdisplay->parser, display->fw->data,\n\t\t\t\tdisplay->fw->size);\n\n\ta52_ackfr_record("P276 295D s=0 fw=%d pn=%d", !!display->fw,\n\t\t!!display->panel_node);\n\trc = dsi_display_parse_dt(display);\n\ta52_ackfr_record("P276 295D s=1 rc=%d cc=%d", rc, display->ctrl_count);\n'''
    text = one(text, old, new, "dev-init parse return")

    old = '''\trc = dsi_display_res_init(display);\n\ta52_ackfr_record("DISP DEV res rc=%d cmd=%u clk=%u", rc,\n\t\tdisplay->cmd_master_idx, display->clk_master_idx);\n\tif (rc) {\n'''
    new = '''\trc = dsi_display_res_init(display);\n\ta52_ackfr_record("DISP DEV res rc=%d cmd=%u clk=%u", rc,\n\t\tdisplay->cmd_master_idx, display->clk_master_idx);\n\ta52_ackfr_record("P276 295D s=2 rc=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "dev-init resource return")

    old = '''\tdisplay->ctrl_count = dsi_display_get_phandle_count(display,\n\t\t\t\t\tdsi_ctrl_name);\n\tphy_count = dsi_display_get_phandle_count(display, dsi_phy_name);\n\n\tDSI_DEBUG("ctrl count=%d, phy count=%d\\n",\n'''
    new = '''\tdisplay->ctrl_count = dsi_display_get_phandle_count(display,\n\t\t\t\t\tdsi_ctrl_name);\n\tphy_count = dsi_display_get_phandle_count(display, dsi_phy_name);\n\ta52_ackfr_record("P276 295T s=0 cc=%d pc=%u",\n\t\tdisplay->ctrl_count, phy_count);\n\n\tDSI_DEBUG("ctrl count=%d, phy count=%d\\n",\n'''
    text = one(text, old, new, "dt count snapshot")

    old = '''\t\tctrl->ctrl_of_node = of_parse_phandle(of_node,\n\t\t\t\t"qcom,dsi-ctrl", index);\n\t\tof_node_put(ctrl->ctrl_of_node);\n\n\t\tindex = dsi_display_get_phandle_index(display, dsi_phy_name,\n'''
    new = '''\t\tctrl->ctrl_of_node = of_parse_phandle(of_node,\n\t\t\t\t"qcom,dsi-ctrl", index);\n\t\tof_node_put(ctrl->ctrl_of_node);\n\t\ta52_ackfr_record("P276 295T s=1 i=%d ix=%d n=%d",\n\t\t\ti, index, !!ctrl->ctrl_of_node);\n\n\t\tindex = dsi_display_get_phandle_index(display, dsi_phy_name,\n'''
    text = one(text, old, new, "dt controller phandle")

    old = '''\t\tctrl->phy_of_node = of_parse_phandle(of_node,\n\t\t\t\t"qcom,dsi-phy", index);\n\t\tof_node_put(ctrl->phy_of_node);\n\t}\n\n\t/* Parse TE data */\n'''
    new = '''\t\tctrl->phy_of_node = of_parse_phandle(of_node,\n\t\t\t\t"qcom,dsi-phy", index);\n\t\tof_node_put(ctrl->phy_of_node);\n\t\ta52_ackfr_record("P276 295T s=2 i=%d ix=%d n=%d",\n\t\t\ti, index, !!ctrl->phy_of_node);\n\t}\n\n\t/* Parse TE data */\n'''
    text = one(text, old, new, "dt phy phandle")

    old = '''\tDSI_DEBUG("success\\n");\nerror:\n\treturn rc;\n}\n\nstatic int dsi_display_res_init(struct dsi_display *display)\n'''
    new = '''\tDSI_DEBUG("success\\n");\nerror:\n\ta52_ackfr_record("P276 295T s=3 rc=%d eb=%d", rc,\n\t\tdisplay->ext_bridge_cnt);\n\treturn rc;\n}\n\nstatic int dsi_display_res_init(struct dsi_display *display)\n'''
    text = one(text, old, new, "dt final return")

    old = '''\t\tctrl->ctrl = dsi_ctrl_get(ctrl->ctrl_of_node);\n\t\tif (IS_ERR_OR_NULL(ctrl->ctrl)) {\n'''
    new = '''\t\tctrl->ctrl = dsi_ctrl_get(ctrl->ctrl_of_node);\n\t\ta52_ackfr_record("P276 295R s=0 i=%d e=%d", i,\n\t\t\tIS_ERR(ctrl->ctrl) ? (int)PTR_ERR(ctrl->ctrl) :\n\t\t\t(ctrl->ctrl ? 0 : -1));\n\t\tif (IS_ERR_OR_NULL(ctrl->ctrl)) {\n'''
    text = one(text, old, new, "resource controller get")

    old = '''\t\tctrl->phy = dsi_phy_get(ctrl->phy_of_node);\n\t\tif (IS_ERR_OR_NULL(ctrl->phy)) {\n'''
    new = '''\t\tctrl->phy = dsi_phy_get(ctrl->phy_of_node);\n\t\ta52_ackfr_record("P276 295R s=1 i=%d e=%d", i,\n\t\t\tIS_ERR(ctrl->phy) ? (int)PTR_ERR(ctrl->phy) :\n\t\t\t(ctrl->phy ? 0 : -1));\n\t\tif (IS_ERR_OR_NULL(ctrl->phy)) {\n'''
    text = one(text, old, new, "resource phy get")

    old = '''\tdisplay->panel = dsi_panel_get(&display->pdev->dev,\n\t\t\t\tdisplay->panel_node,\n\t\t\t\tdisplay->parser_node,\n\t\t\t\tdisplay->display_type,\n\t\t\t\tdisplay->cmdline_topology);\n\tif (IS_ERR_OR_NULL(display->panel)) {\n'''
    new = '''\tdisplay->panel = dsi_panel_get(&display->pdev->dev,\n\t\t\t\tdisplay->panel_node,\n\t\t\t\tdisplay->parser_node,\n\t\t\t\tdisplay->display_type,\n\t\t\t\tdisplay->cmdline_topology);\n\ta52_ackfr_record("P276 295R s=2 e=%d",\n\t\tIS_ERR(display->panel) ? (int)PTR_ERR(display->panel) :\n\t\t(display->panel ? 0 : -1));\n\tif (IS_ERR_OR_NULL(display->panel)) {\n'''
    text = one(text, old, new, "resource panel get")

    old = '''\trc = dsi_display_parse_lane_map(display);\n\tif (rc) {\n'''
    new = '''\trc = dsi_display_parse_lane_map(display);\n\ta52_ackfr_record("P276 295R s=3 rc=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "resource lane map")

    old = '''\trc = dsi_display_clocks_init(display);\n\tif (rc) {\n'''
    new = '''\trc = dsi_display_clocks_init(display);\n\ta52_ackfr_record("P276 295R s=4 rc=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "resource clocks")

    old = '''\treturn 0;\nerror_ctrl_put:\n\tfor (i = i - 1; i >= 0; i--) {\n'''
    new = '''\ta52_ackfr_record("P276 295R s=5 rc=0");\n\treturn 0;\nerror_ctrl_put:\n\ta52_ackfr_record("P276 295R s=9 rc=%d i=%d", rc, i);\n\tfor (i = i - 1; i >= 0; i--) {\n'''
    text = one(text, old, new, "resource final return")

    old = '''\tnum_clk = dsi_display_get_clocks_count(display, dsi_clock_name);\n\n\tDSI_DEBUG("clk count=%d\\n", num_clk);\n'''
    new = '''\tnum_clk = dsi_display_get_clocks_count(display, dsi_clock_name);\n\ta52_ackfr_record("P276 295C s=0 n=%d", num_clk);\n\n\tDSI_DEBUG("clk count=%d\\n", num_clk);\n'''
    text = one(text, old, new, "clock count")

    old = '''\t\tdsi_clk = devm_clk_get(&display->pdev->dev, clk_name);\n\t\tif (IS_ERR_OR_NULL(dsi_clk)) {\n'''
    new = '''\t\tdsi_clk = devm_clk_get(&display->pdev->dev, clk_name);\n\t\ta52_ackfr_record("P276 295C s=1 i=%d e=%d n=%.24s", i,\n\t\t\tIS_ERR(dsi_clk) ? (int)PTR_ERR(dsi_clk) :\n\t\t\t(dsi_clk ? 0 : -1), clk_name ? clk_name : "null");\n\t\tif (IS_ERR_OR_NULL(dsi_clk)) {\n'''
    text = one(text, old, new, "clock acquisition")

    old = '''\treturn 0;\nerror:\n\t(void)dsi_display_clocks_deinit(display);\n\treturn rc;\n}\n\nstatic int dsi_display_clk_ctrl_cb'''
    new = '''\ta52_ackfr_record("P276 295C s=2 rc=0");\n\treturn 0;\nerror:\n\ta52_ackfr_record("P276 295C s=9 rc=%d", rc);\n\t(void)dsi_display_clocks_deinit(display);\n\treturn rc;\n}\n\nstatic int dsi_display_clk_ctrl_cb'''
    text = one(text, old, new, "clock final return")

    after_behavior = behavioral_counts(text)
    if before_behavior != after_behavior:
        raise SystemExit(
            "Phase295 refuses functional display changes; behavioral token counts changed: "
            f"before={before_behavior} after={after_behavior}"
        )

    verify(text)
    return text


def verify_panel(text: str) -> None:
    required = (
        PANEL_MARK,
        'P276 295G s=0 pr=%d bp=%d bl=%d br=%d bv=%u',
        'P276 295G s=1 tp=%d tl=%d tr=%d tv=%u',
        'P276 295G s=2 host=%d',
        'P276 295G s=3 mode=%d pm=%d',
        'P276 295G s=4 phy=%d',
        'P276 295G s=5 gpio=%d',
        'P276 295G s=6 modes=%d n=%d',
        'P276 295G s=7 ok=1',
        'P276 295G s=9 rc=%d',
    )
    for marker in required:
        if text.count(marker) != 1:
            raise SystemExit(
                f"Phase295 panel audit marker count failed: {marker!r}: "
                f"{text.count(marker)}"
            )
    if REC_INCLUDE not in text:
        raise SystemExit("Phase295 panel recorder include missing")


def patch_panel(text: str) -> str:
    if PANEL_MARK in text:
        verify_panel(text)
        return text

    if REC_INCLUDE not in text:
        raise SystemExit("Phase295 requires inherited dsi_panel recorder include")
    if 'A52_ACKFR_SCOPE("DISP", "a52.life.dsi_panel_get");' not in text:
        raise SystemExit("Phase295 requires inherited dsi_panel_get lifecycle scope")

    before_behavior = behavioral_counts(text)

    signature = '''struct dsi_panel *dsi_panel_get(struct device *parent,\n\t\t\t\tstruct device_node *of_node,\n\t\t\t\tstruct device_node *parser_node,\n\t\t\t\tconst char *type,\n\t\t\t\tint topology_override)\n'''
    helper = '''/* A52_PHASE295_PANEL_GET_ENODATA_R2\n * Passive active-parser witness for the dsi_panel_get() -ENODATA frontier.\n * Reads only parser metadata/properties and records them in the preserved\n * flight recorder. No panel, command, GPIO, regulator, clock or timing state\n * is changed.\n */\nstatic void a52_phase295_probe_parser(struct dsi_parser_utils *utils,\n\t\t\t\t      bool parser_node)\n{\n\tstruct property *prop;\n\tu32 value = 0;\n\tint len = -1;\n\tint rc;\n\n\tprop = utils->find_property(utils->data, "qcom,mdss-dsi-bpp", &len);\n\trc = utils->read_u32(utils->data, "qcom,mdss-dsi-bpp", &value);\n\ta52_ackfr_record("P276 295G s=0 pr=%d bp=%d bl=%d br=%d bv=%u",\n\t\tparser_node, !!prop, len, rc, rc ? 0 : value);\n\n\tprop = NULL;\n\tvalue = 0;\n\tlen = -1;\n\tprop = utils->find_property(utils->data,\n\t\t\t"qcom,mdss-dsi-te-dcs-command", &len);\n\trc = utils->read_u32(utils->data,\n\t\t\t"qcom,mdss-dsi-te-dcs-command", &value);\n\ta52_ackfr_record("P276 295G s=1 tp=%d tl=%d tr=%d tv=%u",\n\t\t!!prop, len, rc, rc ? 0 : value);\n}\n\n'''
    text = one(text, signature, helper + signature, "panel helper insertion")

    old = '''\tdsi_panel_update_util(panel, parser_node);\n\tutils = &panel->utils;\n\n\tpanel->name = utils->get_property(utils->data,\n'''
    new = '''\tdsi_panel_update_util(panel, parser_node);\n\tutils = &panel->utils;\n\ta52_phase295_probe_parser(utils, !!parser_node);\n\n\tpanel->name = utils->get_property(utils->data,\n'''
    text = one(text, old, new, "active parser witness")

    old = '''\trc = dsi_panel_parse_host_config(panel);\n\tif (rc) {\n'''
    new = '''\trc = dsi_panel_parse_host_config(panel);\n\ta52_ackfr_record("P276 295G s=2 host=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "host config return")

    old = '''\trc = dsi_panel_parse_panel_mode(panel);\n\tif (rc) {\n'''
    new = '''\trc = dsi_panel_parse_panel_mode(panel);\n\ta52_ackfr_record("P276 295G s=3 mode=%d pm=%d", rc, panel->panel_mode);\n\tif (rc) {\n'''
    text = one(text, old, new, "panel mode return")

    old = '''\trc = dsi_panel_parse_phy_props(panel);\n\tif (rc) {\n'''
    new = '''\trc = dsi_panel_parse_phy_props(panel);\n\ta52_ackfr_record("P276 295G s=4 phy=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "phy props return")

    old = '''\trc = dsi_panel_parse_gpios(panel);\n\tif (rc) {\n'''
    new = '''\trc = dsi_panel_parse_gpios(panel);\n\ta52_ackfr_record("P276 295G s=5 gpio=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "gpio parse return")

    old = '''\trc = dsi_panel_get_mode_count(panel);\n\tif (rc) {\n'''
    new = '''\trc = dsi_panel_get_mode_count(panel);\n\ta52_ackfr_record("P276 295G s=6 modes=%d n=%d",\n\t\trc, panel->num_display_modes);\n\tif (rc) {\n'''
    text = one(text, old, new, "mode count return")

    old = '''\tmutex_init(&panel->panel_lock);\n\n\treturn panel;\nerror:\n\tkfree(panel);\n\treturn ERR_PTR(rc);\n}\n'''
    new = '''\tmutex_init(&panel->panel_lock);\n\n\ta52_ackfr_record("P276 295G s=7 ok=1");\n\treturn panel;\nerror:\n\ta52_ackfr_record("P276 295G s=9 rc=%d", rc);\n\tkfree(panel);\n\treturn ERR_PTR(rc);\n}\n'''
    text = one(text, old, new, "panel final return")

    after_behavior = behavioral_counts(text)
    if before_behavior != after_behavior:
        raise SystemExit(
            "Phase295 refuses functional panel changes; behavioral token counts changed: "
            f"before={before_behavior} after={after_behavior}"
        )

    verify_panel(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    display_path = root / DISPLAY
    panel_path = root / PANEL
    ctrl_path = root / CTRL
    if (not display_path.is_file() or not panel_path.is_file()
            or not ctrl_path.is_file()):
        raise SystemExit("Phase295 active A52 display source missing")

    ctrl = ctrl_path.read_text(encoding="utf-8", errors="strict")
    if "A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1" not in ctrl:
        raise SystemExit("Phase295 requires reconstructed Phase293 controller source")

    display_original = display_path.read_text(encoding="utf-8", errors="strict")
    panel_original = panel_path.read_text(encoding="utf-8", errors="strict")

    if args.check_only:
        verify(display_original)
        verify_panel(panel_original)
        print("Phase295 R2 display/panel ENODATA audit: PASS")
        return 0

    display_patched = patch(display_original)
    panel_patched = patch_panel(panel_original)

    if display_patched == display_original:
        raise SystemExit("Phase295 display patch unexpectedly made no change")
    if panel_patched == panel_original:
        raise SystemExit("Phase295 panel patch unexpectedly made no change")

    display_path.write_text(display_patched, encoding="utf-8")
    panel_path.write_text(panel_patched, encoding="utf-8")
    print("Phase295 R2 display/panel ENODATA instrumentation staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
