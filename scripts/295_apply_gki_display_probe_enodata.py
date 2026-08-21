#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE295_DISPLAY_PROBE_ENODATA_V1"
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

    text = one(
        text,
        REC_INCLUDE,
        REC_INCLUDE + "\n/* " + MARK + "\n"
        " * Passive Phase293 follow-up. The preserved hardware capture proved\n"
        " * dsi_display_dev_probe returns -ENODATA before the target memory-DMA\n"
        " * transaction is admitted. These probes only read existing software\n"
        " * state and append R48/RS48 records through the existing recorder.\n"
        " */\n",
        "marker insertion",
    )

    # dsi_display_dev_probe -> dsi_display_init boundary.
    old = '''\tplatform_set_drvdata(pdev, display);\n\n\n\t/* initialize display in firmware callback */\n'''
    new = '''\tplatform_set_drvdata(pdev, display);\n\ta52_ackfr_record("P276 295P s=0 i=%d be=%d pn=%d fr=%d",\n\t\tindex, boot_disp->boot_disp_en, !!panel_node, firm_req);\n\n\n\t/* initialize display in firmware callback */\n'''
    text = one(text, old, new, "probe pre-init snapshot")

    old = '''\tif (!firm_req) {\n\t\trc = dsi_display_init(display);\n\t\tif (rc)\n\t\t\tgoto end;\n\t}\n'''
    new = '''\tif (!firm_req) {\n\t\trc = dsi_display_init(display);\n\t\ta52_ackfr_record("P276 295P s=1 rc=%d", rc);\n\t\tif (rc)\n\t\t\tgoto end;\n\t}\n'''
    text = one(text, old, new, "probe init return")

    # dsi_display_init -> _dev_init -> component_add.
    old = '''\tmutex_init(&display->display_lock);\n\n\trc = _dsi_display_dev_init(display);\n\tif (rc) {\n'''
    new = '''\tmutex_init(&display->display_lock);\n\n\ta52_ackfr_record("P276 295I s=0");\n\trc = _dsi_display_dev_init(display);\n\ta52_ackfr_record("P276 295I s=1 rc=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "display init dev-init return")

    old = '''\trc = component_add(&pdev->dev, &dsi_display_comp_ops);\n\tif (rc)\n\t\tDSI_ERR("component add failed, rc=%d\\n", rc);\n'''
    new = '''\ta52_ackfr_record("P276 295I s=2");\n\trc = component_add(&pdev->dev, &dsi_display_comp_ops);\n\ta52_ackfr_record("P276 295I s=3 rc=%d", rc);\n\tif (rc)\n\t\tDSI_ERR("component add failed, rc=%d\\n", rc);\n'''
    text = one(text, old, new, "component add return")

    # _dsi_display_dev_init -> parse_dt -> res_init.
    old = '''\tif (display->fw && display->parser)\n\t\tdisplay->parser_node = dsi_parser_get_head_node(\n\t\t\t\tdisplay->parser, display->fw->data,\n\t\t\t\tdisplay->fw->size);\n\n\trc = dsi_display_parse_dt(display);\n'''
    new = '''\tif (display->fw && display->parser)\n\t\tdisplay->parser_node = dsi_parser_get_head_node(\n\t\t\t\tdisplay->parser, display->fw->data,\n\t\t\t\tdisplay->fw->size);\n\n\ta52_ackfr_record("P276 295D s=0 fw=%d pn=%d", !!display->fw,\n\t\t!!display->panel_node);\n\trc = dsi_display_parse_dt(display);\n\ta52_ackfr_record("P276 295D s=1 rc=%d cc=%d", rc, display->ctrl_count);\n'''
    text = one(text, old, new, "dev-init parse return")

    old = '''\trc = dsi_display_res_init(display);\n\tif (rc) {\n'''
    new = '''\trc = dsi_display_res_init(display);\n\ta52_ackfr_record("P276 295D s=2 rc=%d", rc);\n\tif (rc) {\n'''
    text = one(text, old, new, "dev-init resource return")

    # dsi_display_parse_dt: counts and resolved ctrl/phy handles.
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

    # dsi_display_res_init: identify the exact acquisition that propagates rc.
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

    # dsi_display_clocks_init: enough detail if the resource failure is a clk.
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    display_path = root / DISPLAY
    ctrl_path = root / CTRL
    if not display_path.is_file() or not ctrl_path.is_file():
        raise SystemExit("Phase295 active A52 display source missing")
    ctrl = ctrl_path.read_text(encoding="utf-8", errors="strict")
    if "A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1" not in ctrl:
        raise SystemExit("Phase295 requires reconstructed Phase293 controller source")

    original = display_path.read_text(encoding="utf-8", errors="strict")
    if args.check_only:
        verify(original)
        print("Phase295 passive display-probe ENODATA audit: PASS")
        return 0

    patched = patch(original)
    if patched == original:
        raise SystemExit("Phase295 patch unexpectedly made no change")
    display_path.write_text(patched, encoding="utf-8")
    print("Phase295 passive display-probe ENODATA instrumentation staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
