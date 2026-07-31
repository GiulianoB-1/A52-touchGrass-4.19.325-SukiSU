#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_msm_drv(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old = '''static int add_display_components(struct device *dev,
\t\t\t\t  struct component_match **matchptr)
{
\tstruct device *mdp_dev = NULL;
\tstruct device_node *node;
\tint ret;

\tif (of_device_is_compatible(dev->of_node, "qcom,sde-kms")) {
\t\tstruct device_node *np = dev->of_node;
\t\tunsigned int i;

\t\tfor (i = 0; ; i++) {
\t\t\tnode = of_parse_phandle(np, "connectors", i);
\t\t\tif (!node)
\t\t\t\tbreak;
'''
    new = '''static int add_display_components(struct device *dev,
\t\t\t\t  struct component_match **matchptr)
{
\tstruct device *mdp_dev = NULL;
\tstruct device_node *node;
\tint ret;

\ta52_ackfr_record("DRMCOMP collect enter dev=%s node=%s sde=%u mdss=%u",
\t\t\t dev_name(dev), dev->of_node ? dev->of_node->full_name : "-",
\t\t\t dev->of_node && of_device_is_compatible(dev->of_node,
\t\t\t\t\t\t\t  "qcom,sde-kms"),
\t\t\t dev->of_node && of_device_is_compatible(dev->of_node,
\t\t\t\t\t\t\t  "qcom,mdss"));

\tif (of_device_is_compatible(dev->of_node, "qcom,sde-kms")) {
\t\tstruct device_node *np = dev->of_node;
\t\tconst struct property *connectors_prop;
\t\tint connectors_len = 0;
\t\tunsigned int i;

\t\tconnectors_prop = of_find_property(np, "connectors",
\t\t\t\t\t\t  &connectors_len);
\t\ta52_ackfr_record("DRMCOMP connectors prop=%u len=%d",
\t\t\t\t   !!connectors_prop, connectors_len);

\t\tfor (i = 0; ; i++) {
\t\t\tconst char *compat;

\t\t\tnode = of_parse_phandle(np, "connectors", i);
\t\t\tif (!node) {
\t\t\t\ta52_ackfr_record("DRMCOMP connectors end i=%u match=%u",
\t\t\t\t\t\t   i, !!*matchptr);
\t\t\t\tbreak;
\t\t\t}
\t\t\tcompat = of_get_property(node, "compatible", NULL);
\t\t\ta52_ackfr_record("DRMCOMP connector i=%u node=%s compat=%s avail=%u",
\t\t\t\t\t   i, node->full_name,
\t\t\t\t\t   compat ? compat : "-",
\t\t\t\t\t   of_device_is_available(node));
'''
    text = one(text, old, new, "trace SDE connector collection")

    old = '''\t\t\tcomponent_match_add(dev, matchptr, compare_of, node);
\t\t}

\t\treturn 0;
\t}
'''
    new = '''\t\t\tcomponent_match_add(dev, matchptr, compare_of, node);
\t\t\ta52_ackfr_record("DRMCOMP match-add i=%u node=%s match=%u",
\t\t\t\t\t   i, node->full_name, !!*matchptr);
\t\t}

\t\ta52_ackfr_record("DRMCOMP collect exit rc=0 match=%u",
\t\t\t\t   !!*matchptr);
\t\treturn 0;
\t}
'''
    text = one(text, old, new, "trace SDE component match additions")

    old = '''\tret = add_display_components(&pdev->dev, &match);
\tif (ret)
\t\treturn ret;
\tif (!match)
\t\treturn -ENODEV;

\tpdev->dev.coherent_dma_mask = DMA_BIT_MASK(32);
\treturn component_master_add_with_match(&pdev->dev, &msm_drm_ops, match);
'''
    new = '''\tret = add_display_components(&pdev->dev, &match);
\ta52_ackfr_record("DRMCOMP probe collect rc=%d match=%u", ret, !!match);
\tif (ret)
\t\treturn ret;
\tif (!match) {
\t\ta52_ackfr_record("DRMCOMP probe no-match rc=%d", -ENODEV);
\t\treturn -ENODEV;
\t}

\tpdev->dev.coherent_dma_mask = DMA_BIT_MASK(32);
\ta52_ackfr_record("DRMCOMP master-add enter dev=%s", dev_name(&pdev->dev));
\tret = component_master_add_with_match(&pdev->dev, &msm_drm_ops, match);
\ta52_ackfr_record("DRMCOMP master-add exit rc=%d", ret);
\treturn ret;
'''
    text = one(text, old, new, "trace DRM master registration")
    path.write_text(text, encoding="utf-8")


def patch_dsi_display(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    old = '''\trc = component_add(&pdev->dev, &dsi_display_comp_ops);
\tif (rc)
\t\tDSI_ERR("component add failed, rc=%d\\n", rc);

\tDSI_ERR("component add success: %s\\n", display->name);
'''
    new = '''\ta52_ackfr_record("DRMCOMP component-add enter dev=%s display=%s",
\t\t\t dev_name(&pdev->dev), display->name ? display->name : "-");
\trc = component_add(&pdev->dev, &dsi_display_comp_ops);
\ta52_ackfr_record("DRMCOMP component-add exit dev=%s rc=%d",
\t\t\t dev_name(&pdev->dev), rc);
\tif (rc)
\t\tDSI_ERR("component add failed, rc=%d\\n", rc);

\tDSI_ERR("component add success: %s\\n", display->name);
'''
    text = one(text, old, new, "trace DSI component registration")
    path.write_text(text, encoding="utf-8")


def patch_component_core(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = one(
        text,
        '#include <linux/debugfs.h>\n',
        '#include <linux/debugfs.h>\n#include <linux/of.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        "add component trace includes",
    )

    anchor = '''static DEFINE_MUTEX(component_mutex);
static LIST_HEAD(component_list);
static LIST_HEAD(masters);
'''
    helper = '''static DEFINE_MUTEX(component_mutex);
static LIST_HEAD(component_list);
static LIST_HEAD(masters);

static bool a52_component_trace_dev(const struct device *dev)
{
\tconst char *name;

\tif (!dev)
\t\treturn false;
\tif (dev->of_node &&
\t    (of_device_is_compatible(dev->of_node, "qcom,sde-kms") ||
\t     of_device_is_compatible(dev->of_node, "qcom,dsi-display") ||
\t     of_device_is_compatible(dev->of_node, "qcom,dsi-ctrl-hw-v2.4")))
\t\treturn true;
\tname = dev_name(dev);
\treturn name && (strstr(name, "dsi") || strstr(name, "mdp") ||
\t\t\tstrstr(name, "sde"));
}

static void a52_component_trace_match(struct master *master,
\t\t\t\t      const char *stage)
{
\tsize_t i;

\tif (!master || !a52_component_trace_dev(master->dev))
\t\treturn;
\ta52_ackfr_record("COMP master stage=%s dev=%s num=%zu bound=%u",
\t\t\t stage, dev_name(master->dev), master->match->num,
\t\t\t master->bound);
\tfor (i = 0; i < master->match->num; i++) {
\t\tstruct component *c = master->match->compare[i].component;

\t\ta52_ackfr_record("COMP slot i=%zu found=%u dev=%s bound=%u dup=%u",
\t\t\t\t   i, !!c, c ? dev_name(c->dev) : "-",
\t\t\t\t   c ? c->bound : 0,
\t\t\t\t   master->match->compare[i].duplicate);
\t}
}
'''
    text = one(text, anchor, helper, "add component match trace helper")

    old = '''int component_master_add_with_match(struct device *dev,
\tconst struct component_master_ops *ops, struct component_match *match)
{
\tstruct master *master;
\tint ret;

\t/* Reallocate the match array for its true size */
'''
    new = '''int component_master_add_with_match(struct device *dev,
\tconst struct component_master_ops *ops, struct component_match *match)
{
\tstruct master *master;
\tint ret;

\tif (a52_component_trace_dev(dev))
\t\ta52_ackfr_record("COMP master-add enter dev=%s num=%zu",
\t\t\t\t   dev_name(dev), match ? match->num : 0);

\t/* Reallocate the match array for its true size */
'''
    text = one(text, old, new, "trace component master add entry")

    old = '''\tret = try_to_bring_up_master(master, NULL);

\tif (ret < 0)
\t\tfree_master(master);
'''
    new = '''\tret = try_to_bring_up_master(master, NULL);
\ta52_component_trace_match(master, "master-add-result");
\tif (a52_component_trace_dev(dev))
\t\ta52_ackfr_record("COMP master-add result dev=%s rc=%d bound=%u",
\t\t\t\t   dev_name(dev), ret, master->bound);

\tif (ret < 0)
\t\tfree_master(master);
'''
    text = one(text, old, new, "trace component master add result")

    old = '''static int __component_add(struct device *dev, const struct component_ops *ops,
\tint subcomponent)
{
\tstruct component *component;
\tint ret;

\tcomponent = kzalloc(sizeof(*component), GFP_KERNEL);
'''
    new = '''static int __component_add(struct device *dev, const struct component_ops *ops,
\tint subcomponent)
{
\tstruct component *component;
\tint ret;

\tif (a52_component_trace_dev(dev))
\t\ta52_ackfr_record("COMP component-add enter dev=%s sub=%d",
\t\t\t\t   dev_name(dev), subcomponent);

\tcomponent = kzalloc(sizeof(*component), GFP_KERNEL);
'''
    text = one(text, old, new, "trace component add entry")

    old = '''\tret = try_to_bring_up_masters(component);
\tif (ret < 0) {
'''
    new = '''\tret = try_to_bring_up_masters(component);
\tif (a52_component_trace_dev(dev))
\t\ta52_ackfr_record("COMP component-add result dev=%s rc=%d master=%u",
\t\t\t\t   dev_name(dev), ret, !!component->master);
\tif (component->master)
\t\ta52_component_trace_match(component->master,
\t\t\t\t\t  "component-add-result");
\tif (ret < 0) {
'''
    text = one(text, old, new, "trace component add result")

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    patch_msm_drv(root / "drivers/a52_display/msm/msm_drv.c")
    patch_dsi_display(root / "drivers/a52_display/msm/dsi/dsi_display.c")
    patch_component_core(root / "drivers/base/component.c")
    print("phase191 DRM component assembly trace applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
