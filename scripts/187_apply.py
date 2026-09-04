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


def optional_transition(
    text: str,
    old: str,
    new: str,
    symbol: str,
    label: str,
) -> str:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        return text.replace(old, new, 1)
    if old_count == 0 and new_count == 1:
        print(f"{label}: already in Phase187 state")
        return text
    if old_count == 0 and new_count == 0 and symbol not in text:
        print(f"{label}: Run37 preimage has no Run40-only helper; already compliant")
        return text
    raise SystemExit(
        f"{label}: unsupported preimage old={old_count} new={new_count} "
        f"symbol_count={text.count(symbol)}"
    )


def remove_display_bypass(text: str) -> str:
    old_run40 = (
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev) &&\n"
        "\t    !(dev->of_node && of_device_is_compatible(dev->of_node,\n"
        "\t\t\t\t\t\t \"qcom,dsi-ctrl-hw-v2.4\"))) {\n"
        "\t\tunsigned int kept = 0;\n"
        "\t\tunsigned int dropped = 0;\n\n"
        "\t\ta52_device_links_force_probe(dev, &kept, &dropped);\n"
        "\t\ta52_ackfr_record(\"DISP RP bypass dev=%s kept=%u drop=%u\",\n"
        "\t\t\tdev_name(dev), kept, dropped);\n"
        "\t\tret = 0;\n"
        "\t}\n"
    )
    old_phase181 = (
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev)) {\n"
        "\t\tunsigned int kept = 0;\n"
        "\t\tunsigned int dropped = 0;\n\n"
        "\t\ta52_device_links_force_probe(dev, &kept, &dropped);\n"
        "\t\ta52_ackfr_record(\"DISP RP bypass dev=%s kept=%u drop=%u\",\n"
        "\t\t\tdev_name(dev), kept, dropped);\n"
        "\t\tret = 0;\n"
        "\t}\n"
    )
    new = (
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev))\n"
        "\t\ta52_ackfr_record(\"DISP RP defer-normal dev=%s rc=%d\",\n"
        "\t\t\tdev_name(dev), ret);\n"
    )
    counts = {
        "run40": text.count(old_run40),
        "phase181": text.count(old_phase181),
        "new": text.count(new),
    }
    if counts == {"run40": 1, "phase181": 0, "new": 0}:
        return text.replace(old_run40, new, 1)
    if counts == {"run40": 0, "phase181": 1, "new": 0}:
        print("remove display supplier bypass: accepted Phase181 preimage")
        return text.replace(old_phase181, new, 1)
    if counts == {"run40": 0, "phase181": 0, "new": 1}:
        print("remove display supplier bypass: already in Phase187 state")
        return text
    raise SystemExit(f"remove display supplier bypass: unsupported preimage {counts}")


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

    old_legacy = (
        "static bool a52_legacy_fw_devlink_consumer(const struct device *dev)\n"
        "{\n"
        "\tconst char *name;\n\n"
        "\tif (!dev)\n"
        "\t\treturn false;\n"
        "\tname = dev_name(dev);\n"
        "\treturn name && (!strcmp(name, \"1d84000.ufshc\") ||\n"
        "\t\t\t!strcmp(name, \"f100000.pinctrl\"));\n"
        "}\n"
    )
    new_legacy = (
        "static bool a52_legacy_fw_devlink_consumer(const struct device *dev)\n"
        "{\n"
        "\tconst char *name;\n\n"
        "\tif (!dev)\n"
        "\t\treturn false;\n"
        "\tname = dev_name(dev);\n"
        "\treturn name && !strcmp(name, \"1d84000.ufshc\");\n"
        "}\n"
    )
    text = optional_transition(
        text,
        old_legacy,
        new_legacy,
        "a52_legacy_fw_devlink_consumer",
        "remove TLMM from legacy supplier bypass",
    )

    old_preprobe = (
        "static bool a52_run40_preprobe_target(const struct device *dev)\n"
        "{\n"
        "\tconst char *name = dev ? dev_name(dev) : NULL;\n\n"
        "\treturn name && (!strcmp(name, \"1d84000.ufshc\") ||\n"
        "\t\t\t!strcmp(name, \"f100000.pinctrl\"));\n"
        "}\n"
    )
    new_preprobe = (
        "static bool a52_run40_preprobe_target(const struct device *dev)\n"
        "{\n"
        "\tconst char *name = dev ? dev_name(dev) : NULL;\n\n"
        "\treturn name && !strcmp(name, \"1d84000.ufshc\");\n"
        "}\n"
    )
    text = optional_transition(
        text,
        old_preprobe,
        new_preprobe,
        "a52_run40_preprobe_target",
        "remove TLMM from legacy preprobe tracing target",
    )

    text = remove_display_bypass(text)
    path.write_text(text, encoding="utf-8")


def patch_display_audit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = one(
        text,
        "/* Existing phase-177 helper. Phase 180 invokes it only on three display nodes. */\n"
        "extern void a52_device_links_force_probe(struct device *dev,\n"
        "\t\t\t\t\t unsigned int *kept,\n"
        "\t\t\t\t\t unsigned int *dropped);\n\n",
        "",
        "remove display force-probe declaration",
    )

    text = one(
        text,
        "/* Probe dependency order: controller, display aggregator, then SDE/DRM. */\n"
        "static const unsigned int retry_order[] = { 2, 1, 0 };\n\n",
        "",
        "remove retry order",
    )

    retry_pattern = (
        r"static void retry_compat\(.*?\n}\n\n"
        r"static void retry_all\(.*?\n}\n\n"
    )
    text, count = re.subn(retry_pattern, "", text, count=1, flags=re.DOTALL)
    if count != 1:
        raise SystemExit(f"remove retry implementation: expected one match, found {count}")

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
