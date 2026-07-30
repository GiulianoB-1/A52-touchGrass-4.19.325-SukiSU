#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_driver_core(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = one(
        text,
        'static bool a52_legacy_fw_devlink_consumer(const struct device *dev)\n'
        '{\n'
        '\tconst char *name;\n\n'
        '\tif (!dev)\n'
        '\t\treturn false;\n'
        '\tname = dev_name(dev);\n'
        '\treturn name && (!strcmp(name, "1d84000.ufshc") ||\n'
        '\t\t\t!strcmp(name, "f100000.pinctrl"));\n'
        '}\n',
        'static bool a52_legacy_fw_devlink_consumer(const struct device *dev)\n'
        '{\n'
        '\tconst char *name;\n\n'
        '\tif (!dev)\n'
        '\t\treturn false;\n'
        '\tname = dev_name(dev);\n'
        '\treturn name && !strcmp(name, "1d84000.ufshc");\n'
        '}\n',
        "restrict legacy supplier override to UFS",
    )

    text = one(
        text,
        '\tif (ret == -EPROBE_DEFER && dev->of_node &&\n'
        '\t    of_device_is_compatible(dev->of_node, "qcom,dsi-ctrl-hw-v2.4"))\n'
        '\t\ta52_ackfr_record("DISP RP defer-preserved dev=%s rc=%d",\n'
        '\t\t\tdev_name(dev), ret);\n'
        '\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev) &&\n'
        '\t    !(dev->of_node && of_device_is_compatible(dev->of_node,\n'
        '\t\t\t\t\t\t "qcom,dsi-ctrl-hw-v2.4"))) {\n'
        '\t\tunsigned int kept = 0;\n'
        '\t\tunsigned int dropped = 0;\n\n'
        '\t\ta52_device_links_force_probe(dev, &kept, &dropped);\n'
        '\t\ta52_ackfr_record("DISP RP bypass dev=%s kept=%u drop=%u",\n'
        '\t\t\tdev_name(dev), kept, dropped);\n'
        '\t\tret = 0;\n'
        '\t}\n',
        '\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev))\n'
        '\t\ta52_ackfr_record("DISP RP defer-preserved dev=%s rc=%d",\n'
        '\t\t\tdev_name(dev), ret);\n',
        "remove display supplier bypass",
    )

    path.write_text(text, encoding="utf-8")


def patch_display_audit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = one(
        text,
        '/* Existing phase-177 helper. Phase 180 invokes it only on three display nodes. */\n'
        'extern void a52_device_links_force_probe(struct device *dev,\n'
        '\t\t\t\t\t unsigned int *kept,\n'
        '\t\t\t\t\t unsigned int *dropped);\n\n',
        '',
        "remove display force-probe declaration",
    )

    text = one(
        text,
        '/* Probe dependency order: controller, display aggregator, then SDE/DRM. */\n'
        'static const unsigned int retry_order[] = { 2, 1, 0 };\n\n',
        '',
        "remove display retry order",
    )

    text = one(
        text,
        'static void retry_compat(const struct a52_bind_target *target,\n'
        '\t\t\t unsigned int pass, bool force_links)\n'
        '{\n'
        '\tstruct device_node *node = NULL;\n'
        '\tunsigned int index = 0;\n\n'
        '\tfor_each_compatible_node(node, NULL, target->compatible) {\n'
        '\t\tstruct platform_device *pdev = of_find_device_by_node(node);\n'
        '\t\tunsigned int kept = 0, dropped = 0;\n'
        '\t\tint match = -ENOENT;\n'
        '\t\tint rc = -ENODEV;\n\n'
        '\t\tif (!pdev) {\n'
        '\t\t\ta52_ackfr_record("DISP RETRY p=%u c=%s n=%u force=%u pdev=0 rc=%d",\n'
        '\t\t\t\tpass, target->tag, index, force_links, rc);\n'
        '\t\t\tindex++;\n'
        '\t\t\tcontinue;\n'
        '\t\t}\n\n'
        '\t\tmatch = target_driver_match(target, pdev, pass);\n'
        '\t\tif (!pdev->dev.driver && match > 0) {\n'
        '\t\t\tif (force_links)\n'
        '\t\t\t\ta52_device_links_force_probe(&pdev->dev, &kept, &dropped);\n'
        '\t\t\trc = device_attach(&pdev->dev);\n'
        '\t\t} else if (pdev->dev.driver) {\n'
        '\t\t\trc = 1;\n'
        '\t\t} else {\n'
        '\t\t\trc = 0;\n'
        '\t\t}\n\n'
        '\t\ta52_ackfr_record("DISP RETRY p=%u c=%s n=%u force=%u match=%d kept=%u drop=%u rc=%d drv=%s",\n'
        '\t\t\tpass, target->tag, index, force_links, match, kept, dropped,\n'
        '\t\t\trc, bound_driver(pdev));\n'
        '\t\tput_device(&pdev->dev);\n'
        '\t\tindex++;\n'
        '\t}\n'
        '}\n\n'
        'static void retry_all(unsigned int pass, bool force_links)\n'
        '{\n'
        '\tunsigned int i;\n\n'
        '\tfor (i = 0; i < ARRAY_SIZE(retry_order); i++)\n'
        '\t\tretry_compat(&targets[retry_order[i]], pass, force_links);\n'
        '}\n',
        '',
        "remove forced display retry",
    )

    text = one(
        text,
        '\t/* First retry is normal. Second retry removes only unresolved managed links. */\n'
        '\tif (pass == 1)\n'
        '\t\tretry_all(pass, false);\n'
        '\telse if (pass == 2)\n'
        '\t\tretry_all(pass, true);\n',
        '',
        "remove display retry schedule",
    )

    text = one(
        text,
        'a52_ackfr_record("DISP CORE phase=180 audit=start retry=normal,force");',
        'a52_ackfr_record("DISP CORE phase=187 audit=read-only");',
        "phase187 audit marker",
    )

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    patch_driver_core(args.root / "drivers/base/dd.c")
    patch_display_audit(args.root / "drivers/a52_display/180_a52_display_bind_audit.c")
    print("phase187 normal deferred-probe safety restoration applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
