// SPDX-License-Identifier: GPL-2.0-only
/*
 * Qualcomm legacy standalone GDSC regulator bridge for Samsung A52 Lagoon DT.
 *
 * The vendor DT exposes standalone "qcom,gdsc" regulators that Android
 * common 5.10 does not otherwise bind:
 *
 *   - gcc_ufs_phy_gdsc: boot-critical UFS profile, kept on after enable
 *   - mdss_core_gdsc: display profile, normal software collapse supported
 *   - gpu_gx_gdsc: exact Lagoon GX profile with reset and clamp sequencing
 *
 * Keep the profiles explicit. Do not claim unrelated qcom,gdsc nodes.
 *
 * A52_PHASE231_GPU_GX_GDSC_PROVIDER
 */

#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/bitops.h>
#include <linux/delay.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/iopoll.h>
#include <linux/module.h>
#include <linux/mfd/syscon.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/machine.h>
#include <linux/regulator/of_regulator.h>
#include <linux/regmap.h>
#include <linux/string.h>

#define A52_GDSC_PWR_ON          BIT(31)
#define A52_GDSC_SW_OVERRIDE     BIT(2)
#define A52_GDSC_HW_CONTROL      BIT(1)
#define A52_GDSC_SW_COLLAPSE     BIT(0)
#define A52_GDSC_TIMEOUT_US      100
#define A52_GDSC_GPU_GX_ADDR      0x03d9100c
#define A52_GDSC_GMEM_CLAMP_IO    BIT(0)
#define A52_GDSC_GMEM_RESET       BIT(4)
#define A52_GDSC_GPU_SW_RESET     BIT(0)

enum a52_legacy_gdsc_profile {
    A52_GDSC_PROFILE_UFS,
    A52_GDSC_PROFILE_MDSS,
    A52_GDSC_PROFILE_GPU_GX,
};

struct a52_legacy_gdsc {
    struct device *dev;
    void __iomem *gdscr;
    struct regulator_desc desc;
    enum a52_legacy_gdsc_profile profile;
    struct regmap *domain_addr;
    struct regmap *sw_reset;
    bool support_hw_trigger;
    bool reset_aon;
};

extern void a52_persistent_diag_mark(const char *fmt, ...);

static const char *a52_legacy_gdsc_profile_name(
        const struct a52_legacy_gdsc *gdsc)
{
    switch (gdsc->profile) {
    case A52_GDSC_PROFILE_MDSS:
        return "mdss";
    case A52_GDSC_PROFILE_GPU_GX:
        return "gpu-gx";
    case A52_GDSC_PROFILE_UFS:
    default:
        return "ufs";
    }
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

static int a52_legacy_gdsc_regmap_bit(struct regmap *map, u32 mask,
                                     bool set, u32 *last)
{
    u32 val;
    int ret;

    ret = regmap_read(map, 0, &val);
    if (ret)
        return ret;

    if (set)
        val |= mask;
    else
        val &= ~mask;

    ret = regmap_write(map, 0, val);
    if (ret)
        return ret;

    ret = regmap_read(map, 0, &val);
    if (!ret && last)
        *last = val;
    return ret;
}

static int a52_legacy_gdsc_enable_gpu_gx(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val, reset = 0, domain = 0;
    int ret;

    before = readl_relaxed(gdsc->gdscr);

    ret = a52_legacy_gdsc_regmap_bit(gdsc->sw_reset,
                                     A52_GDSC_GPU_SW_RESET, true, &reset);
    if (ret)
        goto out;
    udelay(1);
    ret = a52_legacy_gdsc_regmap_bit(gdsc->sw_reset,
                                     A52_GDSC_GPU_SW_RESET, false, &reset);
    if (ret)
        goto out;

    if (gdsc->reset_aon) {
        ret = a52_legacy_gdsc_regmap_bit(gdsc->domain_addr,
                                         A52_GDSC_GMEM_RESET, true,
                                         &domain);
        if (ret)
            goto out;
        udelay(1);
        ret = a52_legacy_gdsc_regmap_bit(gdsc->domain_addr,
                                         A52_GDSC_GMEM_RESET, false,
                                         &domain);
        if (ret)
            goto out;
    }

    ret = a52_legacy_gdsc_regmap_bit(gdsc->domain_addr,
                                     A52_GDSC_GMEM_CLAMP_IO, false,
                                     &domain);
    if (ret)
        goto out;

    val = readl_relaxed(gdsc->gdscr);
    val &= ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE |
             A52_GDSC_SW_COLLAPSE);
    writel_relaxed(val, gdsc->gdscr);
    mb();
    udelay(1);

    ret = a52_legacy_gdsc_poll(gdsc, true, &val);
out:
    if (ret)
        val = readl_relaxed(gdsc->gdscr);
    a52_ackfr_record(
        "A52GDSC gpu-enable rc=%d before=0x%x after=0x%x reset=0x%x domain=0x%x",
        ret, before, val, reset, domain);
    a52_persistent_diag_mark(
        "A52GDSC GPU_ENABLE dev=%s ret=%d reg=0x%08x reset=0x%08x domain=0x%08x\n",
        dev_name(gdsc->dev), ret, val, reset, domain);
    if (ret)
        dev_err(gdsc->dev, "GPU GX enable failed: %d, GDSCR=0x%08x\n",
                ret, val);
    return ret;
}

static int a52_legacy_gdsc_disable_gpu_gx(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val, domain = 0;
    int ret;

    before = readl_relaxed(gdsc->gdscr);
    val = before;
    if (val & A52_GDSC_HW_CONTROL)
        val &= ~A52_GDSC_HW_CONTROL;
    val |= A52_GDSC_SW_COLLAPSE;
    writel_relaxed(val, gdsc->gdscr);
    mb();
    udelay(1);

    ret = a52_legacy_gdsc_poll(gdsc, false, &val);
    if (!ret)
        ret = a52_legacy_gdsc_regmap_bit(gdsc->domain_addr,
                                         A52_GDSC_GMEM_CLAMP_IO, true,
                                         &domain);

    a52_ackfr_record(
        "A52GDSC gpu-disable rc=%d before=0x%x after=0x%x domain=0x%x",
        ret, before, val, domain);
    if (ret)
        dev_err(gdsc->dev, "GPU GX disable failed: %d, GDSCR=0x%08x\n",
                ret, val);
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

static const struct regulator_ops a52_legacy_gdsc_gpu_gx_ops = {
    .enable = a52_legacy_gdsc_enable_gpu_gx,
    .disable = a52_legacy_gdsc_disable_gpu_gx,
    .is_enabled = a52_legacy_gdsc_is_enabled,
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
    } else if (!strcmp(name, "gpu_gx_gdsc")) {
    } else {
        return -ENODEV;
    }

    gdsc = devm_kzalloc(&pdev->dev, sizeof(*gdsc), GFP_KERNEL);
    if (!gdsc)
        return -ENOMEM;

    gdsc->dev = &pdev->dev;
    if (!strcmp(name, "mdss_core_gdsc"))
        gdsc->profile = A52_GDSC_PROFILE_MDSS;
    else if (!strcmp(name, "gpu_gx_gdsc"))
        gdsc->profile = A52_GDSC_PROFILE_GPU_GX;
    else
        gdsc->profile = A52_GDSC_PROFILE_UFS;
    gdsc->support_hw_trigger =
        of_property_read_bool(pdev->dev.of_node,
                              "qcom,support-hw-trigger");

    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    if (!res)
        return -EINVAL;
    if (gdsc->profile == A52_GDSC_PROFILE_GPU_GX &&
        (res->start != A52_GDSC_GPU_GX_ADDR || resource_size(res) != 4)) {
        dev_err(&pdev->dev, "refusing unexpected GPU GX resource\n");
        return -EINVAL;
    }
    gdsc->gdscr = devm_ioremap(&pdev->dev, res->start, resource_size(res));
    if (!gdsc->gdscr)
        return -ENOMEM;

    gdsc->desc.name = name;
    gdsc->desc.of_match = name;
    gdsc->desc.type = REGULATOR_VOLTAGE;
    gdsc->desc.owner = THIS_MODULE;
    switch (gdsc->profile) {
    case A52_GDSC_PROFILE_MDSS:
        gdsc->desc.ops = &a52_legacy_gdsc_mdss_ops;
        break;
    case A52_GDSC_PROFILE_GPU_GX:
        gdsc->desc.ops = &a52_legacy_gdsc_gpu_gx_ops;
        break;
    case A52_GDSC_PROFILE_UFS:
    default:
        gdsc->desc.ops = &a52_legacy_gdsc_ufs_ops;
        break;
    }

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
    } else if (gdsc->profile == A52_GDSC_PROFILE_GPU_GX) {
        init_data = of_get_regulator_init_data(&pdev->dev,
                                               pdev->dev.of_node,
                                               &gdsc->desc);
        if (!init_data)
            return -ENOMEM;
        if (of_get_property(pdev->dev.of_node, "parent-supply", NULL))
            init_data->supply_regulator = "parent";

        gdsc->domain_addr = syscon_regmap_lookup_by_phandle(
                                pdev->dev.of_node, "domain-addr");
        if (IS_ERR(gdsc->domain_addr))
            return PTR_ERR(gdsc->domain_addr);
        gdsc->sw_reset = syscon_regmap_lookup_by_phandle(
                             pdev->dev.of_node, "sw-reset");
        if (IS_ERR(gdsc->sw_reset))
            return PTR_ERR(gdsc->sw_reset);
        gdsc->reset_aon = of_property_read_bool(pdev->dev.of_node,
                                                "qcom,reset-aon-logic");
        if (!gdsc->reset_aon)
            return -EINVAL;

        before = readl_relaxed(gdsc->gdscr);
        val = before & ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE);
        writel_relaxed(val, gdsc->gdscr);
        mb();
        a52_ackfr_record(
            "A52GDSC GPU_GX_PROFILE_V1 init name=%s before=0x%x after=0x%x",
            name, before, val);
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

MODULE_DESCRIPTION("Samsung A52 legacy Qualcomm UFS, MDSS and GPU GX GDSC regulator bridge");
MODULE_LICENSE("GPL");
