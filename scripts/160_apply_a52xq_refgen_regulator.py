#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

KCONFIG_REL = Path("drivers/regulator/Kconfig")
MAKEFILE_REL = Path("drivers/regulator/Makefile")
DRIVER_REL = Path("drivers/regulator/refgen.c")
RECORDER_HEADER_REL = Path("include/linux/a52_ack_secure_flight_recorder.h")
REPORT_NAME = "phase26-a52-refgen-regulator-report.json"
MARKER = "A52_REFGEN_DISPLAY_SUPPLY_V1"

KCONFIG_BLOCK = r'''
config REGULATOR_REFGEN
	bool "Qualcomm Technologies, Inc. REFGEN regulator driver"
	depends on OF
	default y if ARCH_QCOM
	help
	  This driver controls the REFGEN reference-bias generator used by
	  internal Qualcomm PHY blocks.  The Galaxy A52 5G stock device tree
	  exposes a qcom,refgen-kona-regulator provider consumed by DSI.
'''.strip("\n")

DRIVER_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * Qualcomm REFGEN regulator.
 *
 * Downstream implementation ported for the Galaxy A52 5G ACK 5.10
 * display bring-up.  The stock DTB exposes qcom,refgen-kona-regulator
 * as the DSI refgen-supply provider.
 */

#include <linux/bitops.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/platform_device.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/slab.h>
#include <linux/types.h>

#include <linux/a52_ack_secure_flight_recorder.h>

#define A52_REFGEN_DISPLAY_SUPPLY_V1

#define REFGEN_REG_BIAS_EN               0x08
#define REFGEN_BIAS_EN_MASK              GENMASK(2, 0)
#define REFGEN_BIAS_EN_ENABLE            0x7
#define REFGEN_BIAS_EN_DISABLE           0x6

#define REFGEN_REG_BG_CTRL               0x14
#define REFGEN_BG_CTRL_MASK              GENMASK(2, 1)
#define REFGEN_BG_CTRL_ENABLE            0x6
#define REFGEN_BG_CTRL_DISABLE           0x4

#define REFGEN_REG_PWRDWN_CTRL5          0x80
#define REFGEN_PWRDWN_CTRL5_MASK         BIT(0)
#define REFGEN_PWRDWN_CTRL5_ENABLE       BIT(0)
#define REFGEN_PWRDWN_CTRL5_DISABLE      0

struct refgen {
	struct regulator_desc rdesc;
	struct regulator_dev *rdev;
	void __iomem *addr;
};

static void refgen_masked_writel(u32 val, u32 mask, void __iomem *addr)
{
	u32 reg;

	reg = readl_relaxed(addr);
	reg = (reg & ~mask) | (val & mask);
	writel_relaxed(reg, addr);
}

static int refgen_enable(struct regulator_dev *rdev)
{
	struct refgen *vreg = rdev_get_drvdata(rdev);
	u32 bg_before, bias_before, bg_after, bias_after;

	bg_before = readl_relaxed(vreg->addr + REFGEN_REG_BG_CTRL);
	bias_before = readl_relaxed(vreg->addr + REFGEN_REG_BIAS_EN);
	refgen_masked_writel(REFGEN_BG_CTRL_ENABLE, REFGEN_BG_CTRL_MASK,
			       vreg->addr + REFGEN_REG_BG_CTRL);
	writel_relaxed(REFGEN_BIAS_EN_ENABLE,
		       vreg->addr + REFGEN_REG_BIAS_EN);
	bg_after = readl_relaxed(vreg->addr + REFGEN_REG_BG_CTRL);
	bias_after = readl_relaxed(vreg->addr + REFGEN_REG_BIAS_EN);
	a52_ackfr_record("REFGEN generic_enable bg=0x%x->0x%x bias=0x%x->0x%x",
			  bg_before, bg_after, bias_before, bias_after);

	return 0;
}

static int refgen_disable(struct regulator_dev *rdev)
{
	struct refgen *vreg = rdev_get_drvdata(rdev);
	u32 bg_before, bias_before, bg_after, bias_after;

	bg_before = readl_relaxed(vreg->addr + REFGEN_REG_BG_CTRL);
	bias_before = readl_relaxed(vreg->addr + REFGEN_REG_BIAS_EN);
	writel_relaxed(REFGEN_BIAS_EN_DISABLE,
		       vreg->addr + REFGEN_REG_BIAS_EN);
	refgen_masked_writel(REFGEN_BG_CTRL_DISABLE, REFGEN_BG_CTRL_MASK,
			       vreg->addr + REFGEN_REG_BG_CTRL);
	bg_after = readl_relaxed(vreg->addr + REFGEN_REG_BG_CTRL);
	bias_after = readl_relaxed(vreg->addr + REFGEN_REG_BIAS_EN);
	a52_ackfr_record("REFGEN generic_disable bg=0x%x->0x%x bias=0x%x->0x%x",
			  bg_before, bg_after, bias_before, bias_after);

	return 0;
}

static int refgen_is_enabled(struct regulator_dev *rdev)
{
	struct refgen *vreg = rdev_get_drvdata(rdev);
	u32 bg, bias;
	int enabled;

	bg = readl_relaxed(vreg->addr + REFGEN_REG_BG_CTRL);
	bias = readl_relaxed(vreg->addr + REFGEN_REG_BIAS_EN);
	enabled = ((bg & REFGEN_BG_CTRL_MASK) == REFGEN_BG_CTRL_ENABLE) &&
		  ((bias & REFGEN_BIAS_EN_MASK) == REFGEN_BIAS_EN_ENABLE);
	a52_ackfr_record("REFGEN generic_state bg=0x%x bias=0x%x enabled=%d",
			  bg, bias, enabled);

	return enabled;
}

static const struct regulator_ops refgen_ops = {
	.enable = refgen_enable,
	.disable = refgen_disable,
	.is_enabled = refgen_is_enabled,
};

static int refgen_kona_enable(struct regulator_dev *rdev)
{
	struct refgen *vreg = rdev_get_drvdata(rdev);
	u32 before, after;

	before = readl_relaxed(vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	refgen_masked_writel(REFGEN_PWRDWN_CTRL5_ENABLE,
			       REFGEN_PWRDWN_CTRL5_MASK,
			       vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	after = readl_relaxed(vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	a52_ackfr_record("REFGEN kona_enable raw=0x%x->0x%x enabled=%u",
			  before, after,
			  !!(after & REFGEN_PWRDWN_CTRL5_MASK));

	return 0;
}

static int refgen_kona_disable(struct regulator_dev *rdev)
{
	struct refgen *vreg = rdev_get_drvdata(rdev);
	u32 before, after;

	before = readl_relaxed(vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	refgen_masked_writel(REFGEN_PWRDWN_CTRL5_DISABLE,
			       REFGEN_PWRDWN_CTRL5_MASK,
			       vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	after = readl_relaxed(vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	a52_ackfr_record("REFGEN kona_disable raw=0x%x->0x%x enabled=%u",
			  before, after,
			  !!(after & REFGEN_PWRDWN_CTRL5_MASK));

	return 0;
}

static int refgen_kona_is_enabled(struct regulator_dev *rdev)
{
	struct refgen *vreg = rdev_get_drvdata(rdev);
	u32 raw;
	int enabled;

	raw = readl_relaxed(vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	enabled = (raw & REFGEN_PWRDWN_CTRL5_MASK) ==
		  REFGEN_PWRDWN_CTRL5_ENABLE;
	a52_ackfr_record("REFGEN kona_state raw=0x%x enabled=%d", raw, enabled);

	return enabled;
}

static const struct regulator_ops refgen_kona_ops = {
	.enable = refgen_kona_enable,
	.disable = refgen_kona_disable,
	.is_enabled = refgen_kona_is_enabled,
};

static const struct of_device_id refgen_match_table[] = {
	{ .compatible = "qcom,refgen-regulator", .data = &refgen_ops },
	{ .compatible = "qcom,refgen-sdm845-regulator", .data = &refgen_ops },
	{ .compatible = "qcom,refgen-kona-regulator", .data = &refgen_kona_ops },
	{ }
};
MODULE_DEVICE_TABLE(of, refgen_match_table);

static int refgen_probe(struct platform_device *pdev)
{
	struct regulator_config config = { };
	struct regulator_init_data *init_data;
	struct device *dev = &pdev->dev;
	const struct of_device_id *match;
	struct resource *res;
	struct refgen *vreg;
	u32 raw = 0;
	int rc;

	match = of_match_device(refgen_match_table, dev);
	a52_ackfr_record("REFGEN probe enter compat=%s",
			  match ? match->compatible : "none");

	vreg = devm_kzalloc(dev, sizeof(*vreg), GFP_KERNEL);
	if (!vreg) {
		a52_ackfr_record("REFGEN probe fail stage=alloc rc=%d", -ENOMEM);
		return -ENOMEM;
	}

	if (!dev->of_node) {
		a52_ackfr_record("REFGEN probe fail stage=of_node rc=%d", -ENODEV);
		return -ENODEV;
	}

	vreg->rdesc.ops = of_device_get_match_data(dev);
	if (!vreg->rdesc.ops) {
		a52_ackfr_record("REFGEN probe fail stage=match rc=%d", -ENODEV);
		return -ENODEV;
	}

	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res || !res->start) {
		a52_ackfr_record("REFGEN probe fail stage=resource rc=%d", -EINVAL);
		return -EINVAL;
	}

	vreg->addr = devm_ioremap_resource(dev, res);
	if (IS_ERR(vreg->addr)) {
		rc = PTR_ERR(vreg->addr);
		a52_ackfr_record("REFGEN probe fail stage=ioremap rc=%d", rc);
		return rc;
	}

	if (match && match->data == &refgen_kona_ops)
		raw = readl_relaxed(vreg->addr + REFGEN_REG_PWRDWN_CTRL5);
	a52_ackfr_record("REFGEN mapped start=0x%llx size=%llu raw=0x%x",
			  (unsigned long long)res->start,
			  (unsigned long long)resource_size(res), raw);

	init_data = of_get_regulator_init_data(dev, dev->of_node,
					       &vreg->rdesc);
	if (!init_data) {
		a52_ackfr_record("REFGEN probe fail stage=init_data rc=%d", -ENOMEM);
		return -ENOMEM;
	}

	if (!init_data->constraints.name) {
		a52_ackfr_record("REFGEN probe fail stage=name rc=%d", -EINVAL);
		return -EINVAL;
	}

	if (of_get_property(dev->of_node, "parent-supply", NULL))
		init_data->supply_regulator = "parent";

	vreg->rdesc.name = "refgen";
	vreg->rdesc.id = pdev->id;
	vreg->rdesc.owner = THIS_MODULE;
	vreg->rdesc.type = REGULATOR_VOLTAGE;

	config.dev = dev;
	config.init_data = init_data;
	config.driver_data = vreg;
	config.of_node = dev->of_node;

	vreg->rdev = devm_regulator_register(dev, &vreg->rdesc, &config);
	if (IS_ERR(vreg->rdev)) {
		rc = PTR_ERR(vreg->rdev);
		a52_ackfr_record("REFGEN probe fail stage=register rc=%d", rc);
		return rc;
	}

	rc = vreg->rdesc.ops->is_enabled(vreg->rdev);
	a52_ackfr_record("REFGEN probe ready initial_enabled=%d", rc);
	dev_info(dev, "A52 REFGEN regulator registered, initial state=%d\n", rc);

	return 0;
}

static struct platform_driver refgen_driver = {
	.probe = refgen_probe,
	.driver = {
		.name = "qcom,refgen-regulator",
		.of_match_table = refgen_match_table,
	},
};

static int __init refgen_init(void)
{
	int rc;

	rc = platform_driver_register(&refgen_driver);
	a52_ackfr_record("REFGEN driver_register rc=%d", rc);
	return rc;
}
arch_initcall(refgen_init);

static void __exit refgen_exit(void)
{
	platform_driver_unregister(&refgen_driver);
}
module_exit(refgen_exit);

MODULE_LICENSE("GPL v2");
MODULE_DESCRIPTION("Qualcomm REFGEN regulator with A52 display diagnostics");
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def patch_kconfig(path: Path) -> bool:
    text = read(path)
    if "config REGULATOR_REFGEN" in text:
        if KCONFIG_BLOCK not in text:
            raise SystemExit("an unexpected REGULATOR_REFGEN definition already exists")
        return False

    anchors = (
        "config REGULATOR_RPMH\n",
        "config REGULATOR_QCOM_RPMH\n",
        "config REGULATOR_QCOM_SMD_RPM\n",
    )
    for anchor in anchors:
        if anchor in text:
            text = text.replace(anchor, KCONFIG_BLOCK + "\n\n" + anchor, 1)
            write(path, text)
            return True

    end = text.rfind("endmenu")
    if end < 0:
        raise SystemExit("regulator Kconfig anchor missing")
    text = text[:end] + KCONFIG_BLOCK + "\n\n" + text[end:]
    write(path, text)
    return True


def patch_makefile(path: Path) -> bool:
    text = read(path)
    line = "obj-$(CONFIG_REGULATOR_REFGEN) += refgen.o\n"
    if line in text:
        return False

    anchors = (
        "obj-$(CONFIG_REGULATOR_RPMH) += rpmh-regulator.o\n",
        "obj-$(CONFIG_REGULATOR_QCOM_RPMH) += qcom-rpmh-regulator.o\n",
        "obj-$(CONFIG_REGULATOR_QCOM_SMD_RPM) += qcom_smd-regulator.o\n",
    )
    for anchor in anchors:
        if anchor in text:
            text = text.replace(anchor, line + anchor, 1)
            write(path, text)
            return True

    text += "\n" + line
    write(path, text)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Port and instrument the Qualcomm REFGEN regulator for A52 display"
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.gki.resolve()
    output = args.output.resolve()
    for rel in (KCONFIG_REL, MAKEFILE_REL, RECORDER_HEADER_REL):
        if not (root / rel).is_file():
            raise SystemExit(f"required source file missing: {rel}")

    recorder_header = read(root / RECORDER_HEADER_REL)
    if "a52_ackfr_record" not in recorder_header:
        raise SystemExit("A52 recorder API is not available")

    driver_path = root / DRIVER_REL
    if driver_path.exists():
        existing = read(driver_path)
        if MARKER not in existing:
            raise SystemExit("unexpected existing drivers/regulator/refgen.c")
        driver_changed = False
    else:
        write(driver_path, DRIVER_SOURCE)
        driver_changed = True

    kconfig_changed = patch_kconfig(root / KCONFIG_REL)
    makefile_changed = patch_makefile(root / MAKEFILE_REL)

    driver = read(driver_path)
    required_driver_tokens = (
        MARKER,
        '"qcom,refgen-kona-regulator"',
        "REFGEN_REG_PWRDWN_CTRL5",
        "refgen_kona_enable",
        "refgen_kona_disable",
        "refgen_kona_is_enabled",
        'a52_ackfr_record("REFGEN probe enter',
        'a52_ackfr_record("REFGEN kona_enable',
        'a52_ackfr_record("REFGEN kona_disable',
        "arch_initcall(refgen_init)",
    )
    missing = [token for token in required_driver_tokens if token not in driver]
    if missing:
        raise SystemExit("REFGEN driver audit failed: " + ", ".join(missing))

    kconfig = read(root / KCONFIG_REL)
    makefile = read(root / MAKEFILE_REL)
    if kconfig.count("config REGULATOR_REFGEN") != 1:
        raise SystemExit("REGULATOR_REFGEN Kconfig count mismatch")
    if makefile.count("obj-$(CONFIG_REGULATOR_REFGEN) += refgen.o") != 1:
        raise SystemExit("REGULATOR_REFGEN Makefile count mismatch")

    report = {
        "status": "a52-refgen-display-supply-v1-staged",
        "hardware_validated": False,
        "hypothesis": "missing qcom,refgen-kona-regulator provider for DSI refgen-supply",
        "stock_dtb_compatible": "qcom,refgen-kona-regulator",
        "config": "CONFIG_REGULATOR_REFGEN=y",
        "files": {
            str(DRIVER_REL): {"changed": driver_changed},
            str(KCONFIG_REL): {"changed": kconfig_changed},
            str(MAKEFILE_REL): {"changed": makefile_changed},
        },
        "instrumentation": {
            "probe": True,
            "register": True,
            "enable": True,
            "disable": True,
            "is_enabled": True,
            "payload_capture": False,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    write(output / REPORT_NAME, json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
