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


def add_recorder_decl(text: str, label: str) -> str:
    declaration = "extern void a52_ackfr_record(const char *fmt, ...);\n"
    if declaration in text:
        return text
    includes = list(re.finditer(r"^#include[^\n]*\n", text, flags=re.MULTILINE))
    if not includes:
        raise SystemExit(f"{label}: no include block found")
    pos = includes[-1].end()
    return text[:pos] + "\n" + declaration + text[pos:]


def patch_driver_core(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = one(
        text,
        "\treturn name && (!strcmp(name, \"1d84000.ufshc\") ||\n"
        "\t\t\t!strcmp(name, \"f100000.pinctrl\"));\n",
        "\treturn name && !strcmp(name, \"1d84000.ufshc\");\n",
        "remove TLMM from legacy supplier bypass",
    )

    text = one(
        text,
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev) &&\n"
        "\t    !(dev->of_node && of_device_is_compatible(dev->of_node,\n"
        "\t\t\t\t\t\t \"qcom,dsi-ctrl-hw-v2.4\"))) {\n"
        "\t\tunsigned int kept = 0;\n"
        "\t\tunsigned int dropped = 0;\n\n"
        "\t\ta52_device_links_force_probe(dev, &kept, &dropped);\n"
        "\t\ta52_ackfr_record(\"DISP RP bypass dev=%s kept=%u drop=%u\",\n"
        "\t\t\tdev_name(dev), kept, dropped);\n"
        "\t\tret = 0;\n"
        "\t}\n",
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev))\n"
        "\t\ta52_ackfr_record(\"DISP RP defer-normal dev=%s rc=%d\",\n"
        "\t\t\tdev_name(dev), ret);\n",
        "remove display supplier bypass",
    )

    path.write_text(text, encoding="utf-8")


def patch_display_audit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = one(
        text,
        "\t\tif (!pdev->dev.driver && match > 0) {\n"
        "\t\t\tif (force_links)\n"
        "\t\t\t\ta52_device_links_force_probe(&pdev->dev, &kept, &dropped);\n"
        "\t\t\trc = device_attach(&pdev->dev);\n",
        "\t\tif (!pdev->dev.driver && match > 0) {\n"
        "\t\t\tif (force_links)\n"
        "\t\t\t\ta52_ackfr_record(\"DISP RETRY force-disabled p=%u c=%s\",\n"
        "\t\t\t\t\tpass, target->tag);\n"
        "\t\t\trc = device_attach(&pdev->dev);\n",
        "disable dormant forced-link retry",
    )

    text = one(
        text,
        "\taudit_all(pass);\n"
        "\t/* First retry is normal. Second retry removes only unresolved managed links. */\n"
        "\tif (pass == 1)\n"
        "\t\tretry_all(pass, false);\n"
        "\telse if (pass == 2)\n"
        "\t\tretry_all(pass, true);\n"
        "\taudit_all(pass + 100);\n\n"
        "\tif (pass < 4)\n"
        "\t\tschedule_delayed_work(&audit_work,\n"
        "\t\t\tmsecs_to_jiffies(pass == 1 ? 2000 : pass == 2 ? 8000 : 20000));\n",
        "\taudit_all(pass);\n"
        "\ta52_ackfr_record(\"DISP CORE phase=187 observe-only pass=%u\", pass);\n",
        "make display audit observe-only",
    )

    text = one(
        text,
        "\ta52_ackfr_record(\"DISP CORE phase=180 audit=start retry=normal,force\");\n",
        "\ta52_ackfr_record(\"DISP CORE phase=187 audit=observe-only normal-defer\");\n",
        "audit mode marker",
    )

    path.write_text(text, encoding="utf-8")


def patch_lagoon_pinctrl(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = add_recorder_decl(text, "pinctrl-lagoon includes")

    old = (
        "static int lagoon_pinctrl_probe(struct platform_device *pdev)\n"
        "{\n"
        "\treturn msm_pinctrl_probe(pdev, &lagoon_pinctrl);\n"
        "}\n"
    )
    new = (
        "static int lagoon_pinctrl_probe(struct platform_device *pdev)\n"
        "{\n"
        "\tint rc;\n\n"
        "\ta52_ackfr_record(\"PINCTRL Lagoon probe enter dev=%s node=%s\",\n"
        "\t\tdev_name(&pdev->dev), pdev->dev.of_node ?\n"
        "\t\tpdev->dev.of_node->full_name : \"none\");\n"
        "\trc = msm_pinctrl_probe(pdev, &lagoon_pinctrl);\n"
        "\ta52_ackfr_record(\"PINCTRL Lagoon probe exit rc=%d bound=%s\", rc,\n"
        "\t\tpdev->dev.driver && pdev->dev.driver->name ?\n"
        "\t\tpdev->dev.driver->name : \"none\");\n"
        "\treturn rc;\n"
        "}\n"
    )
    text = one(text, old, new, "Lagoon pinctrl probe trace")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    patch_driver_core(args.root / "drivers/base/dd.c")
    patch_display_audit(args.root / "drivers/a52_secure/a52_display_bind_audit.c")
    patch_lagoon_pinctrl(args.root / "drivers/pinctrl/qcom/pinctrl-lagoon.c")
    print("phase187 normal deferred-probe restoration applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
