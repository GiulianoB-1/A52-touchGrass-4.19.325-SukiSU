#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def install_driver(root: Path, touchgrass: Path) -> None:
    src = touchgrass / "drivers/regulator/qpnp-amoled-regulator.c"
    dst = root / "drivers/regulator/qpnp-amoled-regulator.c"
    if not src.is_file():
        raise SystemExit(f"missing TouchGrass source: {src}")
    shutil.copyfile(src, dst)

    text = dst.read_text(encoding="utf-8")
    include_anchor = "#include <linux/regulator/machine.h>\n"
    text = one(
        text,
        include_anchor,
        include_anchor + "\nextern void a52_ackfr_record(const char *fmt, ...);\n",
        "AMOLED recorder declaration",
    )
    text = one(
        text,
        "\tnode = pdev->dev.of_node;\n"
        "\tif (!node) {\n"
        "\t\tpr_err(\"No nodes defined\\n\");\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n",
        "\tnode = pdev->dev.of_node;\n"
        "\ta52_ackfr_record(\"AMOLED probe enter dev=%s node=%s parent=%s\",\n"
        "\t\tdev_name(&pdev->dev), node ? node->full_name : \"none\",\n"
        "\t\tpdev->dev.parent ? dev_name(pdev->dev.parent) : \"none\");\n"
        "\tif (!node) {\n"
        "\t\tpr_err(\"No nodes defined\\n\");\n"
        "\t\ta52_ackfr_record(\"AMOLED probe exit rc=%d stage=no-node\", -ENODEV);\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n",
        "AMOLED probe entry",
    )
    text = one(
        text,
        "\tchip = devm_kzalloc(&pdev->dev, sizeof(*chip), GFP_KERNEL);\n"
        "\tif (!chip)\n"
        "\t\treturn -ENOMEM;\n",
        "\tchip = devm_kzalloc(&pdev->dev, sizeof(*chip), GFP_KERNEL);\n"
        "\tif (!chip) {\n"
        "\t\ta52_ackfr_record(\"AMOLED probe exit rc=%d stage=alloc\", -ENOMEM);\n"
        "\t\treturn -ENOMEM;\n"
        "\t}\n",
        "AMOLED allocation trace",
    )
    text = one(
        text,
        "\tchip->regmap = dev_get_regmap(pdev->dev.parent, NULL);\n"
        "\tif (!chip->regmap) {\n"
        "\t\tdev_err(&pdev->dev, \"Failed to get the regmap handle\\n\");\n"
        "\t\trc = -EINVAL;\n"
        "\t\tgoto error;\n"
        "\t}\n",
        "\tchip->regmap = dev_get_regmap(pdev->dev.parent, NULL);\n"
        "\tif (!chip->regmap) {\n"
        "\t\tdev_err(&pdev->dev, \"Failed to get the regmap handle\\n\");\n"
        "\t\trc = -EINVAL;\n"
        "\t\ta52_ackfr_record(\"AMOLED probe stage=regmap rc=%d\", rc);\n"
        "\t\tgoto error;\n"
        "\t}\n"
        "\ta52_ackfr_record(\"AMOLED probe stage=regmap rc=0\");\n",
        "AMOLED regmap trace",
    )
    text = one(
        text,
        "\trc = qpnp_amoled_parse_dt(chip);\n"
        "\tif (rc < 0) {\n"
        "\t\tdev_err(chip->dev, \"Failed to parse DT params rc=%d\\n\", rc);\n"
        "\t\tgoto error;\n"
        "\t}\n",
        "\trc = qpnp_amoled_parse_dt(chip);\n"
        "\ta52_ackfr_record(\"AMOLED probe stage=parse-dt rc=%d\", rc);\n"
        "\tif (rc < 0) {\n"
        "\t\tdev_err(chip->dev, \"Failed to parse DT params rc=%d\\n\", rc);\n"
        "\t\tgoto error;\n"
        "\t}\n",
        "AMOLED parse trace",
    )
    text = one(
        text,
        "\trc = qpnp_amoled_hw_init(chip);\n"
        "\tif (rc < 0)\n"
        "\t\tdev_err(chip->dev, \"Failed to initialize HW rc=%d\\n\", rc);\n\n"
        "error:\n"
        "\treturn rc;\n",
        "\trc = qpnp_amoled_hw_init(chip);\n"
        "\ta52_ackfr_record(\"AMOLED probe stage=register-rails rc=%d\", rc);\n"
        "\tif (rc < 0)\n"
        "\t\tdev_err(chip->dev, \"Failed to initialize HW rc=%d\\n\", rc);\n\n"
        "error:\n"
        "\ta52_ackfr_record(\"AMOLED probe exit rc=%d\", rc);\n"
        "\treturn rc;\n",
        "AMOLED completion trace",
    )
    dst.write_text(text, encoding="utf-8")


def patch_kconfig(root: Path) -> None:
    path = root / "drivers/regulator/Kconfig"
    text = path.read_text(encoding="utf-8")
    block = """config REGULATOR_QPNP_AMOLED
\ttristate \"Qualcomm QPNP AMOLED regulator support\"
\tdepends on SPMI && MFD_SPMI_PMIC
\thelp
\t  Support the OLEDB, AB and IBB power rails used by Qualcomm
\t  SPMI PMIC based AMOLED display panels.

"""
    anchor = "config REGULATOR_QCOM_LABIBB\n"
    if "config REGULATOR_QPNP_AMOLED\n" not in text:
        text = one(text, anchor, block + anchor, "AMOLED Kconfig insertion")
    path.write_text(text, encoding="utf-8")


def patch_makefile(root: Path) -> None:
    path = root / "drivers/regulator/Makefile"
    text = path.read_text(encoding="utf-8")
    line = "obj-$(CONFIG_REGULATOR_QPNP_AMOLED) += qpnp-amoled-regulator.o\n"
    anchor = "obj-$(CONFIG_REGULATOR_QCOM_LABIBB) += qcom-labibb-regulator.o\n"
    if line not in text:
        text = one(text, anchor, line + anchor, "AMOLED Makefile insertion")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    args = parser.parse_args()

    install_driver(args.root, args.touchgrass)
    patch_kconfig(args.root)
    patch_makefile(args.root)
    print("phase186 TouchGrass AMOLED power-chain provider applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
