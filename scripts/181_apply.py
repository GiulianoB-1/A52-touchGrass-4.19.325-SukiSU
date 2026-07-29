#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    dd = args.root / "drivers/base/dd.c"
    text = dd.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n",
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
        "extern void a52_ackfr_record(const char *fmt, ...);\n",
        "recorder declaration",
    )

    text = replace_once(
        text,
        "\nvoid driver_deferred_probe_add(struct device *dev)\n",
        "\nstatic bool a52_display_probe_device(const struct device *dev)\n"
        "{\n"
        "\tif (!dev || !dev->of_node)\n"
        "\t\treturn false;\n\n"
        "\treturn of_device_is_compatible(dev->of_node, \"qcom,sde-kms\") ||\n"
        "\t       of_device_is_compatible(dev->of_node, \"qcom,dsi-display\") ||\n"
        "\t       of_device_is_compatible(dev->of_node, \"qcom,dsi-ctrl-hw-v2.4\");\n"
        "}\n\n"
        "void driver_deferred_probe_add(struct device *dev)\n",
        "display target helper",
    )

    text = replace_once(
        text,
        "static int really_probe(struct device *dev, struct device_driver *drv)\n"
        "{\n"
        "\tint ret = -EPROBE_DEFER;\n"
        "\tint local_trigger_count = atomic_read(&deferred_trigger_count);\n"
        "\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n"
        "\t\t\t   !drv->suppress_bind_attrs;\n",
        "static int really_probe(struct device *dev, struct device_driver *drv)\n"
        "{\n"
        "\tint ret = -EPROBE_DEFER;\n"
        "\tint local_trigger_count = atomic_read(&deferred_trigger_count);\n"
        "\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n"
        "\t\t\t   !drv->suppress_bind_attrs;\n\n"
        "\tif (a52_display_probe_device(dev))\n"
        "\t\ta52_ackfr_record(\"DISP RP enter dev=%s drv=%s\",\n"
        "\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\");\n",
        "really_probe entry",
    )

    text = replace_once(
        text,
        "\tif (ret == -EPROBE_DEFER)\n"
        "\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);\n",
        "\tif (a52_display_probe_device(dev)) {\n"
        "\t\tconst char *reason = dev->p && dev->p->deferred_probe_reason ?\n"
        "\t\t\tdev->p->deferred_probe_reason : \"-\";\n\n"
        "\t\ta52_ackfr_record(\"DISP RP suppliers dev=%s rc=%d reason=%s\",\n"
        "\t\t\tdev_name(dev), ret, reason);\n"
        "\t}\n"
        "\tif (ret == -EPROBE_DEFER && a52_display_probe_device(dev)) {\n"
        "\t\tunsigned int kept = 0;\n"
        "\t\tunsigned int dropped = 0;\n\n"
        "\t\ta52_device_links_force_probe(dev, &kept, &dropped);\n"
        "\t\ta52_ackfr_record(\"DISP RP bypass dev=%s kept=%u drop=%u\",\n"
        "\t\t\tdev_name(dev), kept, dropped);\n"
        "\t\tret = 0;\n"
        "\t}\n"
        "\tif (ret == -EPROBE_DEFER)\n"
        "\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);\n",
        "supplier gate",
    )

    text = replace_once(
        text,
        "\tret = pinctrl_bind_pins(dev);\n",
        "\tret = pinctrl_bind_pins(dev);\n"
        "\tif (a52_display_probe_device(dev))\n"
        "\t\ta52_ackfr_record(\"DISP RP pinctrl dev=%s rc=%d\", dev_name(dev), ret);\n",
        "pinctrl stage",
    )

    text = replace_once(
        text,
        "\t\tret = dev->bus->dma_configure(dev);\n",
        "\t\tret = dev->bus->dma_configure(dev);\n"
        "\t\tif (a52_display_probe_device(dev))\n"
        "\t\t\ta52_ackfr_record(\"DISP RP dma dev=%s rc=%d\", dev_name(dev), ret);\n",
        "dma stage",
    )

    text = replace_once(
        text,
        "\tret = driver_sysfs_add(dev);\n"
        "\tif (a52_run40_preprobe_target(dev)) {\n",
        "\tret = driver_sysfs_add(dev);\n"
        "\tif (a52_display_probe_device(dev))\n"
        "\t\ta52_ackfr_record(\"DISP RP sysfs dev=%s rc=%d\", dev_name(dev), ret);\n"
        "\tif (a52_run40_preprobe_target(dev)) {\n",
        "sysfs stage",
    )

    text = replace_once(
        text,
        "\t\tret = dev->pm_domain->activate(dev);\n",
        "\t\tret = dev->pm_domain->activate(dev);\n"
        "\t\tif (a52_display_probe_device(dev))\n"
        "\t\t\ta52_ackfr_record(\"DISP RP pm dev=%s rc=%d\", dev_name(dev), ret);\n",
        "pm stage",
    )

    text = replace_once(
        text,
        "\t\tret = dev->bus->probe(dev);\n",
        "\t\tif (a52_display_probe_device(dev))\n"
        "\t\t\ta52_ackfr_record(\"DISP RP busprobe enter dev=%s drv=%s\",\n"
        "\t\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\");\n"
        "\t\tret = dev->bus->probe(dev);\n"
        "\t\tif (a52_display_probe_device(dev))\n"
        "\t\t\ta52_ackfr_record(\"DISP RP busprobe exit dev=%s rc=%d\",\n"
        "\t\t\t\tdev_name(dev), ret);\n",
        "bus probe stage",
    )

    text = replace_once(
        text,
        "\t\tret = drv->probe(dev);\n",
        "\t\tif (a52_display_probe_device(dev))\n"
        "\t\t\ta52_ackfr_record(\"DISP RP drvprobe enter dev=%s drv=%s\",\n"
        "\t\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\");\n"
        "\t\tret = drv->probe(dev);\n"
        "\t\tif (a52_display_probe_device(dev))\n"
        "\t\t\ta52_ackfr_record(\"DISP RP drvprobe exit dev=%s rc=%d\",\n"
        "\t\t\t\tdev_name(dev), ret);\n",
        "driver probe stage",
    )

    text = replace_once(
        text,
        "done:\n\tatomic_dec(&probe_count);\n",
        "done:\n"
        "\tif (a52_display_probe_device(dev))\n"
        "\t\ta52_ackfr_record(\"DISP RP done dev=%s rc=%d bound=%s\",\n"
        "\t\t\tdev_name(dev), ret, dev->driver && dev->driver->name ?\n"
        "\t\t\tdev->driver->name : \"-\");\n"
        "\tatomic_dec(&probe_count);\n",
        "really_probe completion",
    )

    dd.write_text(text, encoding="utf-8")
    print(dd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
