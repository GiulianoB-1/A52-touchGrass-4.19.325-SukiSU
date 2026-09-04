#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_core(path: Path) -> None:
    text = path.read_text()

    recorder_decl = "extern void a52_ackfr_record(const char *fmt, ...);\n"
    link_decl = (
        "void a52_device_links_force_probe(struct device *dev,\n"
        "                                  unsigned int *kept,\n"
        "                                  unsigned int *dropped);\n"
    )

    if text.count(recorder_decl + link_decl) == 1:
        pass
    elif text.count(link_decl) == 1:
        text = text.replace(link_decl, recorder_decl + link_decl, 1)
    else:
        definition = re.compile(
            r"(?m)^void\s+a52_device_links_force_probe\s*\(\s*"
            r"struct\s+device\s*\*\s*dev\s*,\s*"
            r"unsigned\s+int\s*\*\s*kept\s*,\s*"
            r"unsigned\s+int\s*\*\s*dropped\s*\)\s*\n\s*\{"
        )
        matches = list(definition.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                "link declaration: unsupported preimage "
                f"rec+decl={text.count(recorder_decl + link_decl)} "
                f"decl={text.count(link_decl)} definitions={len(matches)} "
                f"name_count={text.count('a52_device_links_force_probe')}"
            )
        text = text[:matches[0].start()] + recorder_decl + link_decl + text[matches[0].start():]

    text = one(text,
        "\tunsigned int local_dropped = 0;\n",
        "\tunsigned int local_dropped = 0;\n\tunsigned int link_index = 0;\n",
        "link index")
    old = """\tdevice_links_write_lock();
\tlist_for_each_entry_safe(link, ln, &dev->links.suppliers, c_node) {
\t\tif (!(link->flags & DL_FLAG_MANAGED))
\t\t\tcontinue;

\t\tif (link->status != DL_STATE_AVAILABLE) {
\t\t\tdevice_link_drop_managed(link);
\t\t\tlocal_dropped++;
\t\t\tcontinue;
\t\t}

\t\tWRITE_ONCE(link->status, DL_STATE_CONSUMER_PROBE);
\t\tlocal_kept++;
\t}
"""
    new = """\ta52_ackfr_record("DISP LINK begin c=%s", dev_name(dev));
\tdevice_links_write_lock();
\tlist_for_each_entry_safe(link, ln, &dev->links.suppliers, c_node) {
\t\tconst char *sname, *sdrv, *sof, *action;

\t\tif (!(link->flags & DL_FLAG_MANAGED))
\t\t\tcontinue;
\t\tsname = link->supplier ? dev_name(link->supplier) : "none";
\t\tsdrv = link->supplier && link->supplier->driver &&
\t\t\tlink->supplier->driver->name ? link->supplier->driver->name : "none";
\t\tsof = link->supplier && link->supplier->of_node ?
\t\t\tlink->supplier->of_node->full_name : "none";
\t\taction = link->status == DL_STATE_AVAILABLE ? "keep" : "drop";
\t\ta52_ackfr_record("DISP LINK n=%u s=%s st=%u fl=0x%x act=%s",
\t\t\tlink_index, sname, (unsigned int)link->status, link->flags, action);
\t\ta52_ackfr_record("DISP LINK n=%u of=%s drv=%s", link_index, sof, sdrv);
\t\tlink_index++;

\t\tif (link->status != DL_STATE_AVAILABLE) {
\t\t\tdevice_link_drop_managed(link);
\t\t\tlocal_dropped++;
\t\t\tcontinue;
\t\t}

\t\tWRITE_ONCE(link->status, DL_STATE_CONSUMER_PROBE);
\t\tlocal_kept++;
\t}
"""
    text = one(text, old, new, "link loop")
    text = one(text,
        "\tdevice_links_write_unlock();\n\n\tif (kept)\n",
        "\tdevice_links_write_unlock();\n"
        "\ta52_ackfr_record(\"DISP LINK end c=%s kept=%u drop=%u\",\n"
        "\t\tdev_name(dev), local_kept, local_dropped);\n\n\tif (kept)\n",
        "link completion")
    path.write_text(text)


def patch_ctrl(path: Path) -> None:
    text = path.read_text()
    replacements = [
        ("\tid = of_match_node(msm_dsi_of_match, pdev->dev.of_node);\n\tif (!id) {\n",
         "\ta52_ackfr_record(\"DISP CTRL step=match enter\");\n"
         "\tid = of_match_node(msm_dsi_of_match, pdev->dev.of_node);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=match exit ok=%u\", id != NULL);\n\tif (!id) {\n", "match"),
        ("\titem = devm_kzalloc(&pdev->dev, sizeof(*item), GFP_KERNEL);\n\tif (!item)\n\t\treturn -ENOMEM;\n\n"
         "\tdsi_ctrl = devm_kzalloc(&pdev->dev, sizeof(*dsi_ctrl), GFP_KERNEL);\n\tif (!dsi_ctrl)\n\t\treturn -ENOMEM;\n",
         "\ta52_ackfr_record(\"DISP CTRL step=item_alloc enter\");\n"
         "\titem = devm_kzalloc(&pdev->dev, sizeof(*item), GFP_KERNEL);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=item_alloc exit ok=%u\", item != NULL);\n\tif (!item)\n\t\treturn -ENOMEM;\n\n"
         "\ta52_ackfr_record(\"DISP CTRL step=ctrl_alloc enter\");\n"
         "\tdsi_ctrl = devm_kzalloc(&pdev->dev, sizeof(*dsi_ctrl), GFP_KERNEL);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=ctrl_alloc exit ok=%u\", dsi_ctrl != NULL);\n\tif (!dsi_ctrl)\n\t\treturn -ENOMEM;\n", "allocations"),
        ("\tspin_lock_init(&dsi_ctrl->irq_info.irq_lock);\n\n\trc = dsi_ctrl_dts_parse(dsi_ctrl, pdev->dev.of_node);\n",
         "\tspin_lock_init(&dsi_ctrl->irq_info.irq_lock);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=state_init done ver=%u\", (unsigned int)dsi_ctrl->version);\n\n"
         "\ta52_ackfr_record(\"DISP CTRL step=dts enter\");\n\trc = dsi_ctrl_dts_parse(dsi_ctrl, pdev->dev.of_node);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=dts exit rc=%d\", rc);\n", "dts"),
        ("\trc = dsi_ctrl_init_regmap(pdev, dsi_ctrl);\n\tif (rc) {\n",
         "\ta52_ackfr_record(\"DISP CTRL step=regmap enter\");\n\trc = dsi_ctrl_init_regmap(pdev, dsi_ctrl);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=regmap exit rc=%d\", rc);\n\tif (rc) {\n", "regmap"),
        ("\trc = dsi_ctrl_clocks_init(pdev, dsi_ctrl);\n\tif (rc) {\n",
         "\ta52_ackfr_record(\"DISP CTRL step=clocks enter\");\n\trc = dsi_ctrl_clocks_init(pdev, dsi_ctrl);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=clocks exit rc=%d\", rc);\n\tif (rc) {\n", "clocks"),
        ("\trc = dsi_ctrl_supplies_init(pdev, dsi_ctrl);\n\tif (rc) {\n",
         "\ta52_ackfr_record(\"DISP CTRL step=supplies enter\");\n\trc = dsi_ctrl_supplies_init(pdev, dsi_ctrl);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=supplies exit rc=%d\", rc);\n\tif (rc) {\n", "supplies"),
        ("\trc = dsi_catalog_ctrl_setup(&dsi_ctrl->hw, dsi_ctrl->version,\n\t\tdsi_ctrl->cell_index, dsi_ctrl->phy_isolation_enabled,\n\t\tdsi_ctrl->null_insertion_enabled);\n\tif (rc) {\n",
         "\ta52_ackfr_record(\"DISP CTRL step=catalog enter i=%d\", dsi_ctrl->cell_index);\n"
         "\trc = dsi_catalog_ctrl_setup(&dsi_ctrl->hw, dsi_ctrl->version,\n\t\tdsi_ctrl->cell_index, dsi_ctrl->phy_isolation_enabled,\n\t\tdsi_ctrl->null_insertion_enabled);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=catalog exit rc=%d\", rc);\n\tif (rc) {\n", "catalog"),
        ("\trc = dsi_ctrl_axi_bus_client_init(pdev, dsi_ctrl);\n\tif (rc)\n",
         "\ta52_ackfr_record(\"DISP CTRL step=axi enter\");\n\trc = dsi_ctrl_axi_bus_client_init(pdev, dsi_ctrl);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=axi exit rc=%d\", rc);\n\tif (rc)\n", "axi"),
        ("\tif (dsi_ctrl->hw.ops.map_mdp_regs)\n\t\tdsi_ctrl->hw.ops.map_mdp_regs(pdev, &dsi_ctrl->hw);\n\n\titem->ctrl = dsi_ctrl;\n",
         "\ta52_ackfr_record(\"DISP CTRL step=mdp enter has=%u\", dsi_ctrl->hw.ops.map_mdp_regs != NULL);\n"
         "\tif (dsi_ctrl->hw.ops.map_mdp_regs)\n\t\tdsi_ctrl->hw.ops.map_mdp_regs(pdev, &dsi_ctrl->hw);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=mdp exit\");\n\n\titem->ctrl = dsi_ctrl;\n", "mdp"),
        ("\tmutex_lock(&dsi_ctrl_list_lock);\n\tlist_add(&item->list, &dsi_ctrl_list);\n\tmutex_unlock(&dsi_ctrl_list_lock);\n\n\tmutex_init(&dsi_ctrl->ctrl_lock);\n",
         "\ta52_ackfr_record(\"DISP CTRL step=list enter\");\n\tmutex_lock(&dsi_ctrl_list_lock);\n"
         "\tlist_add(&item->list, &dsi_ctrl_list);\n\tmutex_unlock(&dsi_ctrl_list_lock);\n"
         "\ta52_ackfr_record(\"DISP CTRL step=list exit\");\n\n\tmutex_init(&dsi_ctrl->ctrl_lock);\n", "list"),
        ("\tdsi_ctrl->pdev = pdev;\n\tplatform_set_drvdata(pdev, dsi_ctrl);\n\tDSI_CTRL_INFO(dsi_ctrl, \"Probe successful\\n\");\n",
         "\tdsi_ctrl->pdev = pdev;\n\ta52_ackfr_record(\"DISP CTRL step=drvdata enter\");\n"
         "\tplatform_set_drvdata(pdev, dsi_ctrl);\n\ta52_ackfr_record(\"DISP CTRL step=drvdata exit\");\n"
         "\tDSI_CTRL_INFO(dsi_ctrl, \"Probe successful\\n\");\n", "drvdata"),
    ]
    for old, new, label in replacements:
        text = one(text, old, new, label)
    path.write_text(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    root = ap.parse_args().root
    patch_core(root / "drivers/base/core.c")
    patch_ctrl(root / "drivers/a52_display/msm/dsi/dsi_ctrl.c")
    print("phase182 supplier identity and DSI checkpoints applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
