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


def add_recorder_decl(text: str) -> str:
    declaration = "extern void a52_ackfr_record(const char *fmt, ...);\n"
    if declaration in text:
        return text
    includes = list(re.finditer(r"^#include[^\n]*\n", text, flags=re.MULTILINE))
    if not includes:
        raise SystemExit("qcom-pdc includes: no include block found")
    pos = includes[-1].end()
    return text[:pos] + "\n" + declaration + text[pos:]


def patch_pdc(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = add_recorder_decl(text)

    text = one(
        text,
        "static int qcom_pdc_probe(struct platform_device *pdev)\n"
        "{\n"
        "\tstruct device_node *np = pdev->dev.of_node;\n"
        "\tstruct device_node *parent = of_irq_find_parent(np);\n\n"
        "\treturn qcom_pdc_init(np, parent);\n"
        "}\n",
        "static int qcom_pdc_probe(struct platform_device *pdev)\n"
        "{\n"
        "\tstruct device_node *np = pdev->dev.of_node;\n"
        "\tstruct device_node *parent = of_irq_find_parent(np);\n"
        "\tint rc;\n\n"
        "\ta52_ackfr_record(\"PDC probe enter dev=%s node=%s parent=%s\",\n"
        "\t\tdev_name(&pdev->dev), np ? np->full_name : \"none\",\n"
        "\t\tparent ? parent->full_name : \"none\");\n"
        "\trc = qcom_pdc_init(np, parent);\n"
        "\ta52_ackfr_record(\"PDC probe exit rc=%d\", rc);\n"
        "\tof_node_put(parent);\n"
        "\treturn rc;\n"
        "}\n",
        "PDC platform probe trace",
    )

    text = one(
        text,
        "static const struct of_device_id qcom_pdc_match_table[] = {\n"
        "\t{ .compatible = \"qcom,pdc\" },\n"
        "\t{}\n"
        "};\n",
        "static const struct of_device_id qcom_pdc_match_table[] = {\n"
        "\t{ .compatible = \"qcom,pdc\" },\n"
        "\t{ .compatible = \"qcom,lagoon-pdc\" },\n"
        "\t{}\n"
        "};\n",
        "Lagoon PDC compatible",
    )

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    path = args.root / "drivers/irqchip/qcom-pdc.c"
    patch_pdc(path)
    print("phase185 Lagoon PDC compatibility and probe trace applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
