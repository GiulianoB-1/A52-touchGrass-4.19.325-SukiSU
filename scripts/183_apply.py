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


def patch_driver_core(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev)) {\n"
    new = (
        "\tif (ret == -EPROBE_DEFER && dev->of_node &&\n"
        "\t    of_device_is_compatible(dev->of_node, \"qcom,dsi-ctrl-hw-v2.4\"))\n"
        "\t\ta52_ackfr_record(\"DISP RP defer-preserved dev=%s rc=%d\",\n"
        "\t\t\tdev_name(dev), ret);\n"
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev) &&\n"
        "\t    !(dev->of_node && of_device_is_compatible(dev->of_node,\n"
        "\t\t\t\t\t\t \"qcom,dsi-ctrl-hw-v2.4\"))) {\n"
    )
    text = one(text, old, new, "preserve DSI controller deferral")
    path.write_text(text, encoding="utf-8")


def add_recorder_decl(text: str) -> str:
    declaration = "extern void a52_ackfr_record(const char *fmt, ...);\n"
    if declaration in text:
        return text
    includes = list(re.finditer(r"^#include[^\n]*\n", text, flags=re.MULTILINE))
    if not includes:
        raise SystemExit("dispcc includes: no include block found")
    pos = includes[-1].end()
    return text[:pos] + "\n" + declaration + text[pos:]


def patch_dispcc(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = add_recorder_decl(text)

    text = one(
        text,
        "static int disp_cc_lagoon_probe(struct platform_device *pdev)\n"
        "{\n"
        "\tstruct regmap *regmap;\n"
        "\tint ret;\n",
        "static int disp_cc_lagoon_probe(struct platform_device *pdev)\n"
        "{\n"
        "\tstruct regmap *regmap;\n"
        "\tint ret;\n\n"
        "\ta52_ackfr_record(\"DISPCC probe enter dev=%s node=%s\",\n"
        "\t\tdev_name(&pdev->dev), pdev->dev.of_node ?\n"
        "\t\tpdev->dev.of_node->full_name : \"none\");\n",
        "dispcc probe entry",
    )

    text = one(
        text,
        "\tvdd_cx.regulator[0] = devm_regulator_get(&pdev->dev, \"vdd_cx\");\n",
        "\ta52_ackfr_record(\"DISPCC step=vdd_cx enter\");\n"
        "\tvdd_cx.regulator[0] = devm_regulator_get(&pdev->dev, \"vdd_cx\");\n"
        "\ta52_ackfr_record(\"DISPCC step=vdd_cx exit rc=%ld\",\n"
        "\t\tIS_ERR(vdd_cx.regulator[0]) ? PTR_ERR(vdd_cx.regulator[0]) : 0L);\n",
        "dispcc regulator",
    )

    text = one(
        text,
        "\tregmap = qcom_cc_map(pdev, &disp_cc_lagoon_desc);\n",
        "\ta52_ackfr_record(\"DISPCC step=map enter\");\n"
        "\tregmap = qcom_cc_map(pdev, &disp_cc_lagoon_desc);\n"
        "\ta52_ackfr_record(\"DISPCC step=map exit rc=%ld\",\n"
        "\t\tIS_ERR(regmap) ? PTR_ERR(regmap) : 0L);\n",
        "dispcc map",
    )

    text = one(
        text,
        "\tclk_fabia_pll_configure(&disp_cc_pll0, regmap, &disp_cc_pll0_config);\n",
        "\ta52_ackfr_record(\"DISPCC step=pll enter\");\n"
        "\tclk_fabia_pll_configure(&disp_cc_pll0, regmap, &disp_cc_pll0_config);\n"
        "\ta52_ackfr_record(\"DISPCC step=pll exit\");\n",
        "dispcc pll",
    )

    text = one(
        text,
        "\tret = qcom_cc_really_probe(pdev, &disp_cc_lagoon_desc, regmap);\n",
        "\ta52_ackfr_record(\"DISPCC step=register enter\");\n"
        "\tret = qcom_cc_really_probe(pdev, &disp_cc_lagoon_desc, regmap);\n"
        "\ta52_ackfr_record(\"DISPCC step=register exit rc=%d\", ret);\n",
        "dispcc clock registration",
    )

    text = one(
        text,
        "\tdev_info(&pdev->dev, \"Registered DISP CC clocks\\n\");\n\n"
        "\treturn ret;\n",
        "\tdev_info(&pdev->dev, \"Registered DISP CC clocks\\n\");\n"
        "\ta52_ackfr_record(\"DISPCC probe done rc=%d\", ret);\n\n"
        "\treturn ret;\n",
        "dispcc probe completion",
    )

    text = one(
        text,
        "static int __init disp_cc_lagoon_init(void)\n"
        "{\n"
        "\treturn platform_driver_register(&disp_cc_lagoon_driver);\n"
        "}\n",
        "static int __init disp_cc_lagoon_init(void)\n"
        "{\n"
        "\tint rc;\n\n"
        "\ta52_ackfr_record(\"DISPCC init enter\");\n"
        "\trc = platform_driver_register(&disp_cc_lagoon_driver);\n"
        "\ta52_ackfr_record(\"DISPCC init exit rc=%d\", rc);\n"
        "\treturn rc;\n"
        "}\n",
        "dispcc init",
    )

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    patch_driver_core(args.root / "drivers/base/dd.c")
    patch_dispcc(args.root / "drivers/clk/qcom/dispcc-lagoon.c")
    print("phase183 dispcc enable/probe instrumentation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
