#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_sde_rsc(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = one(
        text,
        '#include <linux/msm-bus.h>\n',
        '#include <linux/msm-bus.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'add RSCC recorder include',
    )

    old = '''static struct sde_rsc_priv *rsc_prv_list[MAX_RSC_COUNT];
static struct device *rpmh_dev[MAX_RSC_COUNT];
'''
    new = '''static struct sde_rsc_priv *rsc_prv_list[MAX_RSC_COUNT];
static struct device *rpmh_dev[MAX_RSC_COUNT];

static void a52_rscc_record_registration_state(const char *stage,
\t\t\t\t\t       const char *compatible)
{
\tstruct device_node *np;
\tstruct platform_device *pdev;
\tconst char *bound = "-";

\tnp = of_find_compatible_node(NULL, NULL, compatible);
\tpdev = np ? of_find_device_by_node(np) : NULL;
\tif (pdev && pdev->dev.driver && pdev->dev.driver->name)
\t\tbound = pdev->dev.driver->name;
\ta52_ackfr_record("RSCC state=%s compat=%s node=%u pdev=%u bound=%s",
\t\t\t stage, compatible, !!np, !!pdev, bound);
\tif (pdev)
\t\tput_device(&pdev->dev);
\tof_node_put(np);
}
'''
    text = one(text, old, new, 'add RSCC registration-state helper')

    old = '''static int sde_rsc_bind(struct device *dev,
\t\tstruct device *master,
\t\tvoid *data)
{
\tstruct sde_rsc_priv *rsc;
\tstruct drm_device *drm;
\tstruct platform_device *pdev = to_platform_device(dev);

\tif (!dev || !pdev || !master) {
'''
    new = '''static int sde_rsc_bind(struct device *dev,
\t\tstruct device *master,
\t\tvoid *data)
{
\tstruct sde_rsc_priv *rsc;
\tstruct drm_device *drm;
\tstruct platform_device *pdev = to_platform_device(dev);

\ta52_ackfr_record("RSCC bind enter dev=%s master=%s",
\t\t\t dev ? dev_name(dev) : "-",
\t\t\t master ? dev_name(master) : "-");
\tif (!dev || !pdev || !master) {
\t\ta52_ackfr_record("RSCC bind exit rc=%d stage=args", -EINVAL);
'''
    text = one(text, old, new, 'trace RSCC bind entry')

    old = '''\tif (!drm || !rsc) {
\t\tpr_err("invalid param(s), drm %pK, rsc %pK\\n",
\t\t\t\tdrm, rsc);
\t\treturn -EINVAL;
\t}
'''
    new = '''\tif (!drm || !rsc) {
\t\tpr_err("invalid param(s), drm %pK, rsc %pK\\n",
\t\t\t\tdrm, rsc);
\t\ta52_ackfr_record("RSCC bind exit rc=%d stage=drvdata drm=%u rsc=%u",
\t\t\t\t   -EINVAL, !!drm, !!rsc);
\t\treturn -EINVAL;
\t}
'''
    text = one(text, old, new, 'trace RSCC bind drvdata failure')

    old = '''\tsde_dbg_reg_register_base(SDE_RSC_WRAPPER_DBG_NAME,
\t\t\t\trsc->wrapper_io.base, rsc->wrapper_io.len);
\treturn 0;
}
'''
    new = '''\tsde_dbg_reg_register_base(SDE_RSC_WRAPPER_DBG_NAME,
\t\t\t\trsc->wrapper_io.base, rsc->wrapper_io.len);
\ta52_ackfr_record("RSCC bind exit rc=0");
\treturn 0;
}
'''
    text = one(text, old, new, 'trace RSCC bind success')

    old = '''static int sde_rsc_probe(struct platform_device *pdev)
{
\tint ret;
\tstruct sde_rsc_priv *rsc;
\tstatic int counter;
\tchar  name[MAX_RSC_CLIENT_NAME_LEN];

\tif (counter >= MAX_RSC_COUNT) {
'''
    new = '''static int sde_rsc_probe(struct platform_device *pdev)
{
\tint ret;
\tint stage_rc = 0;
\tconst char *stage = "enter";
\tstruct sde_rsc_priv *rsc;
\tstatic int counter;
\tchar  name[MAX_RSC_CLIENT_NAME_LEN];

\ta52_ackfr_record("RSCC probe enter dev=%s node=%s counter=%d rpmh=%u",
\t\t\t dev_name(&pdev->dev),
\t\t\t pdev->dev.of_node ? pdev->dev.of_node->full_name : "-",
\t\t\t counter, counter < MAX_RSC_COUNT &&
\t\t\t !!rpmh_dev[SDE_RSC_INDEX + counter]);
\tif (counter >= MAX_RSC_COUNT) {
\t\ta52_ackfr_record("RSCC probe exit rc=%d stage=max-count", -EINVAL);
'''
    text = one(text, old, new, 'trace RSCC probe entry')

    old = '''\trsc = kzalloc(sizeof(*rsc), GFP_KERNEL);
\tif (!rsc) {
\t\tret = -ENOMEM;
\t\tgoto rsc_alloc_fail;
\t}
'''
    new = '''\tstage = "alloc";
\trsc = kzalloc(sizeof(*rsc), GFP_KERNEL);
\tif (!rsc) {
\t\tret = -ENOMEM;
\t\tstage_rc = ret;
\t\ta52_ackfr_record("RSCC probe stage=alloc rc=%d", ret);
\t\tgoto rsc_alloc_fail;
\t}
\ta52_ackfr_record("RSCC probe stage=alloc rc=0");
'''
    text = one(text, old, new, 'trace RSCC allocation')

    old = '''\tof_property_read_u32(pdev->dev.of_node, "qcom,sde-rsc-version",
\t\t\t\t\t\t\t\t&rsc->version);

\tif (rsc->version == SDE_RSC_REV_2)
'''
    new = '''\tof_property_read_u32(pdev->dev.of_node, "qcom,sde-rsc-version",
\t\t\t\t\t\t\t\t&rsc->version);
\ta52_ackfr_record("RSCC probe stage=version value=%u", rsc->version);

\tif (rsc->version == SDE_RSC_REV_2)
'''
    text = one(text, old, new, 'trace RSCC version')

    old = '''\tret = sde_power_resource_init(pdev, &rsc->phandle);
\tif (ret) {
\t\tpr_err("sde rsc:power resource init failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\trsc->rpmh_dev = rpmh_dev[SDE_RSC_INDEX + counter];
'''
    new = '''\tstage = "power-init";
\tret = sde_power_resource_init(pdev, &rsc->phandle);
\tstage_rc = ret;
\ta52_ackfr_record("RSCC probe stage=power-init rc=%d", ret);
\tif (ret) {
\t\tpr_err("sde rsc:power resource init failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tstage = "rpmh-link";
\trsc->rpmh_dev = rpmh_dev[SDE_RSC_INDEX + counter];
'''
    text = one(text, old, new, 'trace RSCC power init')

    old = '''\tif (IS_ERR_OR_NULL(rsc->rpmh_dev)) {
\t\tret = !rsc->rpmh_dev ? -EINVAL : PTR_ERR(rsc->rpmh_dev);
\t\trsc->rpmh_dev = NULL;
\t\tpr_err("rpmh device node is not available ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tret = msm_dss_ioremap_byname(pdev, &rsc->wrapper_io, "wrapper");
'''
    new = '''\tif (IS_ERR_OR_NULL(rsc->rpmh_dev)) {
\t\tret = !rsc->rpmh_dev ? -EINVAL : PTR_ERR(rsc->rpmh_dev);
\t\tstage_rc = ret;
\t\ta52_ackfr_record("RSCC probe stage=rpmh-link rc=%d present=%u",
\t\t\t\t   ret, !!rsc->rpmh_dev);
\t\trsc->rpmh_dev = NULL;
\t\tpr_err("rpmh device node is not available ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}
\ta52_ackfr_record("RSCC probe stage=rpmh-link rc=0 dev=%s",
\t\t\t dev_name(rsc->rpmh_dev));

\tstage = "map-wrapper";
\tret = msm_dss_ioremap_byname(pdev, &rsc->wrapper_io, "wrapper");
'''
    text = one(text, old, new, 'trace RSCC RPMh link')

    old = '''\tif (ret) {
\t\tpr_err("sde rsc: wrapper io data mapping failed ret=%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tret = msm_dss_ioremap_byname(pdev, &rsc->drv_io, "drv");
'''
    new = '''\tstage_rc = ret;
\ta52_ackfr_record("RSCC probe stage=map-wrapper rc=%d", ret);
\tif (ret) {
\t\tpr_err("sde rsc: wrapper io data mapping failed ret=%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tstage = "map-drv";
\tret = msm_dss_ioremap_byname(pdev, &rsc->drv_io, "drv");
'''
    text = one(text, old, new, 'trace RSCC wrapper map')

    old = '''\tif (ret) {
\t\tpr_err("sde rsc: drv io data mapping failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\trsc->fs = devm_regulator_get(&pdev->dev, "vdd");
\tif (IS_ERR_OR_NULL(rsc->fs)) {
\t\trsc->fs = NULL;
\t\tpr_err("unable to get regulator\\n");
\t\tgoto sde_rsc_fail;
\t}

\tif (rsc->version >= SDE_RSC_REV_3)
'''
    new = '''\tstage_rc = ret;
\ta52_ackfr_record("RSCC probe stage=map-drv rc=%d", ret);
\tif (ret) {
\t\tpr_err("sde rsc: drv io data mapping failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tstage = "get-vdd";
\trsc->fs = devm_regulator_get(&pdev->dev, "vdd");
\tif (IS_ERR_OR_NULL(rsc->fs)) {
\t\tstage_rc = rsc->fs ? PTR_ERR(rsc->fs) : -ENODEV;
\t\ta52_ackfr_record("RSCC probe stage=get-vdd rc=%d", stage_rc);
\t\trsc->fs = NULL;
\t\tpr_err("unable to get regulator\\n");
\t\tgoto sde_rsc_fail;
\t}
\ta52_ackfr_record("RSCC probe stage=get-vdd rc=0");

\tstage = "hw-register";
\tif (rsc->version >= SDE_RSC_REV_3)
'''
    text = one(text, old, new, 'trace RSCC driver map and regulator')

    old = '''\tif (ret) {
\t\tpr_err("sde rsc: hw register failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tret = regulator_enable(rsc->fs);
'''
    new = '''\tstage_rc = ret;
\ta52_ackfr_record("RSCC probe stage=hw-register rc=%d", ret);
\tif (ret) {
\t\tpr_err("sde rsc: hw register failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tstage = "vdd-enable";
\tret = regulator_enable(rsc->fs);
'''
    text = one(text, old, new, 'trace RSCC hardware registration')

    old = '''\tif (ret) {
\t\tpr_err("sde rsc: fs on failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\trsc->sw_fs_enabled = true;

\tret = sde_rsc_resource_enable(rsc);
'''
    new = '''\tstage_rc = ret;
\ta52_ackfr_record("RSCC probe stage=vdd-enable rc=%d", ret);
\tif (ret) {
\t\tpr_err("sde rsc: fs on failed ret:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\trsc->sw_fs_enabled = true;

\tstage = "resource-enable";
\tret = sde_rsc_resource_enable(rsc);
'''
    text = one(text, old, new, 'trace RSCC regulator enable')

    old = '''\tif (ret < 0) {
\t\tpr_err("failed to enable sde rsc power resources rc:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tif (sde_rsc_timer_calculate(rsc, NULL, SDE_RSC_IDLE_STATE))
\t\tgoto sde_rsc_fail;

\tsde_rsc_resource_disable(rsc);
'''
    new = '''\tstage_rc = ret;
\ta52_ackfr_record("RSCC probe stage=resource-enable rc=%d", ret);
\tif (ret < 0) {
\t\tpr_err("failed to enable sde rsc power resources rc:%d\\n", ret);
\t\tgoto sde_rsc_fail;
\t}

\tstage = "timer-calc";
\tstage_rc = sde_rsc_timer_calculate(rsc, NULL, SDE_RSC_IDLE_STATE);
\ta52_ackfr_record("RSCC probe stage=timer-calc rc=%d", stage_rc);
\tif (stage_rc)
\t\tgoto sde_rsc_fail;

\tsde_rsc_resource_disable(rsc);
\ta52_ackfr_record("RSCC probe stage=resource-disable done=1");
'''
    text = one(text, old, new, 'trace RSCC resource and timer stages')

    old = '''\tret = component_add(&pdev->dev, &sde_rsc_comp_ops);
\tif (ret)
\t\tpr_debug("component add failed, ret=%d\\n", ret);
\tret = 0;

\treturn ret;

sde_rsc_fail:
\tsde_rsc_deinit(pdev, rsc);
rsc_alloc_fail:
\treturn ret;
'''
    new = '''\tstage = "component-add";
\ta52_ackfr_record("RSCC component-add enter dev=%s",
\t\t\t dev_name(&pdev->dev));
\tret = component_add(&pdev->dev, &sde_rsc_comp_ops);
\tstage_rc = ret;
\ta52_ackfr_record("RSCC component-add exit rc=%d", ret);
\tif (ret)
\t\tpr_debug("component add failed, ret=%d\\n", ret);
\tret = 0;

\ta52_ackfr_record("RSCC probe exit rc=0 component_rc=%d counter=%d",
\t\t\t stage_rc, counter);
\treturn ret;

sde_rsc_fail:
\ta52_ackfr_record("RSCC probe fail stage=%s stage_rc=%d return_rc=%d",
\t\t\t stage, stage_rc, ret);
\ta52_ackfr_record("RSCC cleanup enter stage=%s", stage);
\tsde_rsc_deinit(pdev, rsc);
\ta52_ackfr_record("RSCC cleanup exit stage=%s", stage);
rsc_alloc_fail:
\ta52_ackfr_record("RSCC probe exit rc=%d stage=%s stage_rc=%d",
\t\t\t ret, stage, stage_rc);
\treturn ret;
'''
    text = one(text, old, new, 'trace RSCC component add and failure')

    old = '''static int sde_rsc_rpmh_probe(struct platform_device *pdev)
{
\tint ret = 0;
\tuint32_t index = 0;

\tret = of_property_read_u32(pdev->dev.of_node, "cell-index", &index);
'''
    new = '''static int sde_rsc_rpmh_probe(struct platform_device *pdev)
{
\tint ret = 0;
\tuint32_t index = 0;

\ta52_ackfr_record("RSCC rpmh-probe enter dev=%s node=%s",
\t\t\t dev_name(&pdev->dev),
\t\t\t pdev->dev.of_node ? pdev->dev.of_node->full_name : "-");
\tret = of_property_read_u32(pdev->dev.of_node, "cell-index", &index);
'''
    text = one(text, old, new, 'trace RSCC RPMh probe entry')

    old = '''\tif (ret) {
\t\tpr_err("unable to find sde rsc cell index\\n");
\t\treturn ret;
\t} else if (index >= MAX_RSC_COUNT) {
\t\tpr_err("invalid cell index for sde rsc:%d\\n", index);
\t\treturn -EINVAL;
\t}

\trpmh_dev[index] = &pdev->dev;
\treturn 0;
'''
    new = '''\tif (ret) {
\t\tpr_err("unable to find sde rsc cell index\\n");
\t\ta52_ackfr_record("RSCC rpmh-probe exit rc=%d stage=cell-index", ret);
\t\treturn ret;
\t} else if (index >= MAX_RSC_COUNT) {
\t\tpr_err("invalid cell index for sde rsc:%d\\n", index);
\t\ta52_ackfr_record("RSCC rpmh-probe exit rc=%d index=%u",
\t\t\t\t   -EINVAL, index);
\t\treturn -EINVAL;
\t}

\trpmh_dev[index] = &pdev->dev;
\ta52_ackfr_record("RSCC rpmh-probe exit rc=0 index=%u dev=%s",
\t\t\t index, dev_name(&pdev->dev));
\treturn 0;
'''
    text = one(text, old, new, 'trace RSCC RPMh probe result')

    old = '''static int __init sde_rsc_register(void)
{
\treturn platform_driver_register(&sde_rsc_platform_driver);
}
'''
    new = '''static int __init sde_rsc_register(void)
{
\tint ret;

\ta52_ackfr_record("RSCC main-register enter");
\ta52_rscc_record_registration_state("main-before", "qcom,sde-rsc");
\tret = platform_driver_register(&sde_rsc_platform_driver);
\ta52_ackfr_record("RSCC main-register exit rc=%d", ret);
\ta52_rscc_record_registration_state("main-after", "qcom,sde-rsc");
\treturn ret;
}
'''
    text = one(text, old, new, 'trace RSCC main driver registration')

    old = '''static int __init sde_rsc_rpmh_register(void)
{
\treturn platform_driver_register(&sde_rsc_rpmh_driver);
}
'''
    new = '''static int __init sde_rsc_rpmh_register(void)
{
\tint ret;

\ta52_ackfr_record("RSCC rpmh-register enter");
\ta52_rscc_record_registration_state("rpmh-before", "qcom,sde-rsc-rpmh");
\tret = platform_driver_register(&sde_rsc_rpmh_driver);
\ta52_ackfr_record("RSCC rpmh-register exit rc=%d", ret);
\ta52_rscc_record_registration_state("rpmh-after", "qcom,sde-rsc-rpmh");
\treturn ret;
}
'''
    text = one(text, old, new, 'trace RSCC RPMh driver registration')

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    patch_sde_rsc(args.root / 'drivers/a52_display/msm/sde_rsc.c')
    print('phase192 SDE RSCC registration and probe trace applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
