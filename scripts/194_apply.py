#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

DRIVER_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * Qualcomm legacy standalone GDSC regulator bridge for Samsung A52 Lagoon DT.
 *
 * The vendor DT exposes two standalone "qcom,gdsc" regulators that Android
 * common 5.10 does not otherwise bind:
 *
 *   - gcc_ufs_phy_gdsc: boot-critical UFS profile, kept on after enable
 *   - mdss_core_gdsc: display profile, normal software collapse supported
 *
 * Keep the profiles explicit. Do not claim unrelated qcom,gdsc nodes.
 */

#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/string.h>

#define A52_GDSC_PWR_ON          BIT(31)
#define A52_GDSC_SW_OVERRIDE     BIT(2)
#define A52_GDSC_HW_CONTROL      BIT(1)
#define A52_GDSC_SW_COLLAPSE     BIT(0)
#define A52_GDSC_TIMEOUT_US      100

enum a52_legacy_gdsc_profile {
    A52_GDSC_PROFILE_UFS,
    A52_GDSC_PROFILE_MDSS,
};

struct a52_legacy_gdsc {
    struct device *dev;
    void __iomem *gdscr;
    struct regulator_desc desc;
    enum a52_legacy_gdsc_profile profile;
    bool support_hw_trigger;
};

extern void a52_persistent_diag_mark(const char *fmt, ...);

static const char *a52_legacy_gdsc_profile_name(
        const struct a52_legacy_gdsc *gdsc)
{
    return gdsc->profile == A52_GDSC_PROFILE_MDSS ? "mdss" : "ufs";
}

static int a52_legacy_gdsc_poll(struct a52_legacy_gdsc *gdsc, bool on,
                               u32 *last)
{
    u32 val;
    int ret;

    if (on)
        ret = readl_poll_timeout(gdsc->gdscr, val,
                                 val & A52_GDSC_PWR_ON,
                                 1, A52_GDSC_TIMEOUT_US);
    else
        ret = readl_poll_timeout(gdsc->gdscr, val,
                                 !(val & A52_GDSC_PWR_ON),
                                 1, A52_GDSC_TIMEOUT_US);
    if (last)
        *last = val;
    return ret;
}

static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 val = readl_relaxed(gdsc->gdscr);

    return !!((val & A52_GDSC_PWR_ON) &&
              !(val & A52_GDSC_SW_COLLAPSE));
}

static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val;
    int ret;

    before = readl_relaxed(gdsc->gdscr);
    val = before;
    val &= ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE |
             A52_GDSC_SW_COLLAPSE);
    writel_relaxed(val, gdsc->gdscr);
    mb();
    udelay(1);

    ret = a52_legacy_gdsc_poll(gdsc, true, &val);
    a52_ackfr_record(
        "A52GDSC enable profile=%s name=%s rc=%d before=0x%x after=0x%x",
        a52_legacy_gdsc_profile_name(gdsc), gdsc->desc.name,
        ret, before, val);
    a52_persistent_diag_mark(
        "A52GDSC ENABLE dev=%s name=%s ret=%d reg=0x%08x\n",
        dev_name(gdsc->dev), gdsc->desc.name, ret, val);

    if (ret)
        dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\n", val);
    return ret;
}

static int a52_legacy_gdsc_disable_ufs(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 val = readl_relaxed(gdsc->gdscr);

    a52_ackfr_record(
        "A52GDSC disable-keep-on profile=ufs name=%s reg=0x%x",
        gdsc->desc.name, val);
    a52_persistent_diag_mark(
        "A52GDSC DISABLE_KEEP_ON dev=%s name=%s reg=0x%08x\n",
        dev_name(gdsc->dev), gdsc->desc.name, val);
    return 0;
}

static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val;
    int ret;

    before = readl_relaxed(gdsc->gdscr);
    val = before;
    if (val & A52_GDSC_HW_CONTROL) {
        val &= ~A52_GDSC_HW_CONTROL;
        writel_relaxed(val, gdsc->gdscr);
        mb();
        udelay(1);
    }

    val |= A52_GDSC_SW_COLLAPSE;
    writel_relaxed(val, gdsc->gdscr);
    mb();
    udelay(1);

    ret = a52_legacy_gdsc_poll(gdsc, false, &val);
    a52_ackfr_record(
        "A52GDSC disable profile=mdss name=%s rc=%d before=0x%x after=0x%x",
        gdsc->desc.name, ret, before, val);
    if (ret)
        dev_err(gdsc->dev, "disable timed out, GDSCR=0x%08x\n", val);
    return ret;
}

static unsigned int a52_legacy_gdsc_get_mode(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 val = readl_relaxed(gdsc->gdscr);

    return val & A52_GDSC_HW_CONTROL ?
           REGULATOR_MODE_FAST : REGULATOR_MODE_NORMAL;
}

static int a52_legacy_gdsc_set_mode(struct regulator_dev *rdev,
                                    unsigned int mode)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val;
    int ret = 0;

    if (!gdsc->support_hw_trigger)
        return -EINVAL;

    before = readl_relaxed(gdsc->gdscr);
    val = before;
    switch (mode) {
    case REGULATOR_MODE_FAST:
        val |= A52_GDSC_HW_CONTROL;
        writel_relaxed(val, gdsc->gdscr);
        mb();
        udelay(1);
        break;
    case REGULATOR_MODE_NORMAL:
        val &= ~A52_GDSC_HW_CONTROL;
        writel_relaxed(val, gdsc->gdscr);
        mb();
        udelay(1);
        if ((val & A52_GDSC_PWR_ON) &&
            !(val & A52_GDSC_SW_COLLAPSE))
            ret = a52_legacy_gdsc_poll(gdsc, true, &val);
        break;
    default:
        return -EINVAL;
    }

    a52_ackfr_record(
        "A52GDSC mode profile=%s name=%s mode=%u rc=%d before=0x%x after=0x%x",
        a52_legacy_gdsc_profile_name(gdsc), gdsc->desc.name,
        mode, ret, before, val);
    return ret;
}

static const struct regulator_ops a52_legacy_gdsc_ufs_ops = {
    .enable = a52_legacy_gdsc_enable,
    .disable = a52_legacy_gdsc_disable_ufs,
    .is_enabled = a52_legacy_gdsc_is_enabled,
};

static const struct regulator_ops a52_legacy_gdsc_mdss_ops = {
    .enable = a52_legacy_gdsc_enable,
    .disable = a52_legacy_gdsc_disable_mdss,
    .is_enabled = a52_legacy_gdsc_is_enabled,
    .set_mode = a52_legacy_gdsc_set_mode,
    .get_mode = a52_legacy_gdsc_get_mode,
};

static int a52_legacy_gdsc_probe(struct platform_device *pdev)
{
    struct regulator_config config = { };
    struct regulator_init_data *init_data = NULL;
    struct a52_legacy_gdsc *gdsc;
    struct regulator_dev *rdev;
    struct resource *res;
    const char *name;
    u32 before, val;

    if (of_property_read_string(pdev->dev.of_node, "regulator-name", &name))
        return -EINVAL;

    if (!strcmp(name, "gcc_ufs_phy_gdsc")) {
    } else if (!strcmp(name, "mdss_core_gdsc")) {
    } else {
        return -ENODEV;
    }

    gdsc = devm_kzalloc(&pdev->dev, sizeof(*gdsc), GFP_KERNEL);
    if (!gdsc)
        return -ENOMEM;

    gdsc->dev = &pdev->dev;
    gdsc->profile = !strcmp(name, "mdss_core_gdsc") ?
                    A52_GDSC_PROFILE_MDSS : A52_GDSC_PROFILE_UFS;
    gdsc->support_hw_trigger =
        of_property_read_bool(pdev->dev.of_node,
                              "qcom,support-hw-trigger");

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
    gdsc->desc.ops = gdsc->profile == A52_GDSC_PROFILE_MDSS ?
                     &a52_legacy_gdsc_mdss_ops :
                     &a52_legacy_gdsc_ufs_ops;

    if (gdsc->profile == A52_GDSC_PROFILE_MDSS) {
        init_data = of_get_regulator_init_data(&pdev->dev,
                                               pdev->dev.of_node,
                                               &gdsc->desc);
        if (init_data && gdsc->support_hw_trigger) {
            init_data->constraints.valid_ops_mask |= REGULATOR_CHANGE_MODE;
            init_data->constraints.valid_modes_mask |=
                REGULATOR_MODE_NORMAL | REGULATOR_MODE_FAST;
        }

        before = readl_relaxed(gdsc->gdscr);
        val = before & ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE);
        writel_relaxed(val, gdsc->gdscr);
        mb();
        a52_ackfr_record(
            "A52GDSC mdss-init name=%s before=0x%x after=0x%x hw=%u",
            name, before, val, gdsc->support_hw_trigger);
    }

    config.dev = &pdev->dev;
    config.of_node = pdev->dev.of_node;
    config.init_data = init_data;
    config.driver_data = gdsc;

    a52_ackfr_record(
        "A52GDSC register enter dev=%s name=%s profile=%s hw=%u",
        dev_name(&pdev->dev), name,
        a52_legacy_gdsc_profile_name(gdsc), gdsc->support_hw_trigger);
    rdev = devm_regulator_register(&pdev->dev, &gdsc->desc, &config);
    if (IS_ERR(rdev)) {
        int ret = PTR_ERR(rdev);

        a52_ackfr_record(
            "A52GDSC register exit dev=%s name=%s rc=%d",
            dev_name(&pdev->dev), name, ret);
        if (ret != -EPROBE_DEFER)
            dev_err(&pdev->dev, "failed to register %s: %d\n", name, ret);
        return ret;
    }

    platform_set_drvdata(pdev, gdsc);
    val = readl_relaxed(gdsc->gdscr);
    a52_ackfr_record(
        "A52GDSC register exit dev=%s name=%s rc=0 reg=0x%x profile=%s",
        dev_name(&pdev->dev), name, val,
        a52_legacy_gdsc_profile_name(gdsc));
    a52_persistent_diag_mark(
        "A52GDSC PROBE dev=%s name=%s reg=0x%08x pwr=%u collapse=%u\n",
        dev_name(&pdev->dev), name, val,
        !!(val & A52_GDSC_PWR_ON), !!(val & A52_GDSC_SW_COLLAPSE));
    dev_info(&pdev->dev, "registered A52 legacy GDSC regulator %s (%s)\n",
             name, a52_legacy_gdsc_profile_name(gdsc));
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
    int ret;

    a52_ackfr_record("A52GDSC driver-register enter");
    ret = platform_driver_register(&a52_legacy_gdsc_driver);
    a52_ackfr_record("A52GDSC driver-register exit rc=%d", ret);
    return ret;
}
subsys_initcall(a52_legacy_gdsc_init);

static void __exit a52_legacy_gdsc_exit(void)
{
    platform_driver_unregister(&a52_legacy_gdsc_driver);
}
module_exit(a52_legacy_gdsc_exit);

MODULE_DESCRIPTION("Samsung A52 legacy Qualcomm UFS and MDSS GDSC regulator bridge");
MODULE_LICENSE("GPL");
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    path = args.root / "drivers/regulator/a52-legacy-gdsc-regulator.c"
    if not path.is_file():
        raise SystemExit(f"legacy GDSC bridge missing: {path}")

    old = path.read_text(encoding="utf-8")
    for marker in (
        'strcmp(name, "gcc_ufs_phy_gdsc")',
        "A52GDSC DISABLE_KEEP_ON",
        "a52-legacy-gdsc-regulator",
    ):
        if marker not in old:
            raise SystemExit(f"unexpected inherited GDSC bridge, missing: {marker}")
    if '"mdss_core_gdsc"' in old:
        raise SystemExit("mdss_core_gdsc support already present")

    for marker in (
        '"gcc_ufs_phy_gdsc"',
        '"mdss_core_gdsc"',
        "A52GDSC disable-keep-on profile=ufs",
        "A52GDSC disable profile=mdss",
        "A52GDSC mdss-init",
        "REGULATOR_CHANGE_MODE",
        "A52_GDSC_HW_CONTROL",
        "A52_GDSC_SW_COLLAPSE",
    ):
        if marker not in DRIVER_SOURCE:
            raise SystemExit(f"new GDSC source missing: {marker}")

    path.write_text(DRIVER_SOURCE, encoding="utf-8")
    print("phase194 MDSS core GDSC provider applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
