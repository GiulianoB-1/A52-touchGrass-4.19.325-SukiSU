#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

DRIVER_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * Minimal Qualcomm legacy GDSC regulator bridge for the Samsung A52 Lagoon DT.
 *
 * The vendor DT exposes gcc_ufs_phy_gdsc as a standalone "qcom,gdsc"
 * regulator. Mainline-style Android common 5.10 only builds the generic power
 * domain GDSC helpers and therefore leaves this standalone provider unbound.
 * Keep this bridge deliberately scoped to gcc_ufs_phy_gdsc.
 */

#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/of_regulator.h>
#include <linux/string.h>

#define A52_GDSC_PWR_ON		BIT(31)
#define A52_GDSC_SW_OVERRIDE	BIT(2)
#define A52_GDSC_HW_CONTROL	BIT(1)
#define A52_GDSC_SW_COLLAPSE	BIT(0)
#define A52_GDSC_TIMEOUT_US	100

struct a52_legacy_gdsc {
	struct device *dev;
	void __iomem *gdscr;
	struct regulator_desc desc;
};

extern void a52_persistent_diag_mark(const char *fmt, ...);

static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)
{
	struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
	u32 val = readl_relaxed(gdsc->gdscr);

	/* Match the downstream vote semantics, not only the physical PWR_ON bit. */
	return !!((val & A52_GDSC_PWR_ON) &&
		  !(val & A52_GDSC_SW_COLLAPSE));
}

static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)
{
	struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
	u32 val;
	int ret;

	val = readl_relaxed(gdsc->gdscr);
	val &= ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE |
		 A52_GDSC_SW_COLLAPSE);
	writel_relaxed(val, gdsc->gdscr);

	/* Downstream waits at least eight XO cycles before checking PWR_ON. */
	mb();
	udelay(1);

	ret = readl_poll_timeout(gdsc->gdscr, val,
				 val & A52_GDSC_PWR_ON,
				 1, A52_GDSC_TIMEOUT_US);
	a52_persistent_diag_mark(
		"A52GDSC ENABLE dev=%s name=%s ret=%d reg=0x%08x\n",
		dev_name(gdsc->dev), gdsc->desc.name, ret, val);

	if (ret)
		dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\n", val);

	return ret;
}

static int a52_legacy_gdsc_disable(struct regulator_dev *rdev)
{
	struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
	u32 val = readl_relaxed(gdsc->gdscr);

	/*
	 * This diagnostic bridge intentionally keeps the boot-critical UFS GDSC
	 * on. The stock kernel can collapse it later, but preserving the rail here
	 * avoids turning a provider-compatibility fix into a new runtime-PM risk.
	 */
	a52_persistent_diag_mark(
		"A52GDSC DISABLE_KEEP_ON dev=%s name=%s reg=0x%08x\n",
		dev_name(gdsc->dev), gdsc->desc.name, val);
	return 0;
}

static const struct regulator_ops a52_legacy_gdsc_ops = {
	.enable = a52_legacy_gdsc_enable,
	.disable = a52_legacy_gdsc_disable,
	.is_enabled = a52_legacy_gdsc_is_enabled,
};

static int a52_legacy_gdsc_probe(struct platform_device *pdev)
{
	struct regulator_config config = { };
	struct a52_legacy_gdsc *gdsc;
	struct regulator_dev *rdev;
	struct resource *res;
	const char *name;
	u32 val;

	if (of_property_read_string(pdev->dev.of_node, "regulator-name", &name))
		return -EINVAL;

	/* Do not claim unrelated standalone GDSCs with a deliberately small shim. */
	if (strcmp(name, "gcc_ufs_phy_gdsc"))
		return -ENODEV;

	gdsc = devm_kzalloc(&pdev->dev, sizeof(*gdsc), GFP_KERNEL);
	if (!gdsc)
		return -ENOMEM;

	gdsc->dev = &pdev->dev;
	/*
	 * The legacy standalone GDSC node describes a register inside the GCC
	 * controller range. The GCC clock driver already owns that parent MMIO
	 * resource, so devm_platform_ioremap_resource() would fail with -EBUSY.
	 * Map the four-byte child register without requesting the overlapping
	 * resource, matching the vendor DT's shared-register layout.
	 */
	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res)
		return -EINVAL;
	gdsc->gdscr = devm_ioremap(&pdev->dev, res->start, resource_size(res));
	if (!gdsc->gdscr)
		return -ENOMEM;

	gdsc->desc.name = name;
	gdsc->desc.of_match = name;
	gdsc->desc.type = REGULATOR_VOLTAGE;
	gdsc->desc.owner = THIS_MODULE;
	gdsc->desc.ops = &a52_legacy_gdsc_ops;

	config.dev = &pdev->dev;
	config.of_node = pdev->dev.of_node;
	config.driver_data = gdsc;

	rdev = devm_regulator_register(&pdev->dev, &gdsc->desc, &config);
	if (IS_ERR(rdev)) {
		int ret = PTR_ERR(rdev);

		if (ret != -EPROBE_DEFER)
			dev_err(&pdev->dev,
				"failed to register %s: %d\n", name, ret);
		return ret;
	}

	platform_set_drvdata(pdev, gdsc);
	val = readl_relaxed(gdsc->gdscr);
	a52_persistent_diag_mark(
		"A52GDSC PROBE dev=%s name=%s reg=0x%08x pwr=%u collapse=%u\n",
		dev_name(&pdev->dev), name, val,
		!!(val & A52_GDSC_PWR_ON), !!(val & A52_GDSC_SW_COLLAPSE));
	dev_info(&pdev->dev, "registered legacy UFS GDSC regulator %s\n", name);

	return 0;
}

static const struct of_device_id a52_legacy_gdsc_match[] = {
	{ .compatible = "qcom,gdsc" },
	{ }
};
MODULE_DEVICE_TABLE(of, a52_legacy_gdsc_match);

static struct platform_driver a52_legacy_gdsc_driver = {
	.probe = a52_legacy_gdsc_probe,
	.driver = {
		.name = "a52-legacy-gdsc-regulator",
		.of_match_table = a52_legacy_gdsc_match,
	},
};

static int __init a52_legacy_gdsc_init(void)
{
	return platform_driver_register(&a52_legacy_gdsc_driver);
}
subsys_initcall(a52_legacy_gdsc_init);

static void __exit a52_legacy_gdsc_exit(void)
{
	platform_driver_unregister(&a52_legacy_gdsc_driver);
}
module_exit(a52_legacy_gdsc_exit);

MODULE_DESCRIPTION("Samsung A52 legacy Qualcomm UFS GDSC regulator bridge");
MODULE_LICENSE("GPL");
'''

MAKEFILE_ANCHOR = "obj-$(CONFIG_REGULATOR) += core.o dummy.o fixed-helper.o helpers.o devres.o\n"
MAKEFILE_ADDITION = (
    MAKEFILE_ANCHOR
    + "obj-$(CONFIG_REGULATOR) += a52-legacy-gdsc-regulator.o\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add a narrowly scoped qcom,gdsc regulator provider for the A52 "
            "UFS vdd-hba supply."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    regulator_dir = gki / "drivers/regulator"
    makefile_path = regulator_dir / "Makefile"
    driver_path = regulator_dir / "a52-legacy-gdsc-regulator.c"

    if not makefile_path.is_file():
        raise SystemExit(f"regulator Makefile is missing: {makefile_path}")
    if driver_path.exists():
        raise SystemExit(f"legacy GDSC bridge already exists: {driver_path}")

    makefile = makefile_path.read_text(encoding="utf-8")
    matches = makefile.count(MAKEFILE_ANCHOR)
    if matches != 1:
        raise SystemExit(
            f"regulator Makefile anchor: expected one match, found {matches}"
        )
    makefile = makefile.replace(MAKEFILE_ANCHOR, MAKEFILE_ADDITION, 1)

    checks = {
        "scoped_provider": 'strcmp(name, "gcc_ufs_phy_gdsc")' in DRIVER_SOURCE,
        "legacy_compatible": '.compatible = "qcom,gdsc"' in DRIVER_SOURCE,
        "hba_vote_enable": "A52_GDSC_SW_COLLAPSE" in DRIVER_SOURCE,
        "overlap_safe_mapping": "devm_ioremap(&pdev->dev, res->start" in DRIVER_SOURCE,
        "persistent_probe_trace": "A52GDSC PROBE" in DRIVER_SOURCE,
        "persistent_enable_trace": "A52GDSC ENABLE" in DRIVER_SOURCE,
        "boot_critical_disable_guard": "A52GDSC DISABLE_KEEP_ON" in DRIVER_SOURCE,
        "makefile_object": "a52-legacy-gdsc-regulator.o" in makefile,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("legacy GDSC staging audit failed: " + ", ".join(failed))

    driver_path.write_text(DRIVER_SOURCE, encoding="utf-8")
    makefile_path.write_text(makefile, encoding="utf-8")

    (output / "a52-legacy-gdsc-regulator.c").write_text(
        DRIVER_SOURCE, encoding="utf-8"
    )
    (output / "patched-regulator-Makefile").write_text(makefile, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "hardware_validated": False,
                "root_cause": (
                    "the live Samsung DT routes UFS vdd-hba-supply to the "
                    "standalone qcom,gdsc regulator gcc_ufs_phy_gdsc, while "
                    "Android common 5.10 has no provider for that legacy node"
                ),
                "scope": "gcc_ufs_phy_gdsc only",
                "policy": (
                    "map the overlapping GCC child register without reserving it, "
                    "enable the HLOS vote, and keep the boot-critical domain on "
                    "for this diagnostic candidate"
                ),
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
