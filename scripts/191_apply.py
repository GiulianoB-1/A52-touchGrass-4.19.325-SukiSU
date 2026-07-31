#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_msm_drv(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''\tint ret;\n\n\tif (of_device_is_compatible(dev->of_node, "qcom,sde-kms")) {\n\t\tstruct device_node *np = dev->of_node;\n\t\tunsigned int i;\n\n\t\tfor (i = 0; ; i++) {\n\t\t\tnode = of_parse_phandle(np, "connectors", i);\n\t\t\tif (!node)\n\t\t\t\tbreak;\n''',
        '''\tint ret;\n\n\ta52_ackfr_record("DRMCOMP collect enter dev=%s node=%s sde=%u mdss=%u",\n\t\t\t dev_name(dev), dev->of_node ? dev->of_node->full_name : "-",\n\t\t\t dev->of_node && of_device_is_compatible(dev->of_node,\n\t\t\t\t\t\t\t  "qcom,sde-kms"),\n\t\t\t dev->of_node && of_device_is_compatible(dev->of_node,\n\t\t\t\t\t\t\t  "qcom,mdss"));\n\n\tif (of_device_is_compatible(dev->of_node, "qcom,sde-kms")) {\n\t\tstruct device_node *np = dev->of_node;\n\t\tconst struct property *connectors_prop;\n\t\tint connectors_len = 0;\n\t\tunsigned int i;\n\n\t\tconnectors_prop = of_find_property(np, "connectors",\n\t\t\t\t\t\t  &connectors_len);\n\t\ta52_ackfr_record("DRMCOMP connectors prop=%u len=%d",\n\t\t\t\t   !!connectors_prop, connectors_len);\n\n\t\tfor (i = 0; ; i++) {\n\t\t\tconst char *compat;\n\n\t\t\tnode = of_parse_phandle(np, "connectors", i);\n\t\t\tif (!node) {\n\t\t\t\ta52_ackfr_record("DRMCOMP connectors end i=%u match=%u",\n\t\t\t\t\t\t   i, !!*matchptr);\n\t\t\t\tbreak;\n\t\t\t}\n\t\t\tcompat = of_get_property(node, "compatible", NULL);\n\t\t\ta52_ackfr_record("DRMCOMP connector i=%u node=%s compat=%s avail=%u",\n\t\t\t\t\t   i, node->full_name,\n\t\t\t\t\t   compat ? compat : "-",\n\t\t\t\t\t   of_device_is_available(node));\n''',
        "trace SDE connector collection",
    )

    text = replace_once(
        text,
        '''\t\t\tcomponent_match_add(dev, matchptr, compare_of, node);\n\t\t}\n\n\t\treturn 0;\n\t}\n''',
        '''\t\t\tcomponent_match_add(dev, matchptr, compare_of, node);\n\t\t\ta52_ackfr_record("DRMCOMP match-add i=%u node=%s match=%u",\n\t\t\t\t\t   i, node->full_name, !!*matchptr);\n\t\t}\n\n\t\ta52_ackfr_record("DRMCOMP collect exit rc=0 match=%u",\n\t\t\t\t   !!*matchptr);\n\t\treturn 0;\n\t}\n''',
        "trace SDE component match additions",
    )

    text = replace_once(
        text,
        '''\tret = add_display_components(&pdev->dev, &match);\n\tif (ret)\n\t\treturn ret;\n\tif (!match)\n\t\treturn -ENODEV;\n\n\tpdev->dev.coherent_dma_mask = DMA_BIT_MASK(32);\n\treturn component_master_add_with_match(&pdev->dev, &msm_drm_ops, match);\n''',
        '''\tret = add_display_components(&pdev->dev, &match);\n\ta52_ackfr_record("DRMCOMP probe collect rc=%d match=%u", ret, !!match);\n\tif (ret)\n\t\treturn ret;\n\tif (!match) {\n\t\ta52_ackfr_record("DRMCOMP probe no-match rc=%d", -ENODEV);\n\t\treturn -ENODEV;\n\t}\n\n\tpdev->dev.coherent_dma_mask = DMA_BIT_MASK(32);\n\ta52_ackfr_record("DRMCOMP master-add enter dev=%s", dev_name(&pdev->dev));\n\tret = component_master_add_with_match(&pdev->dev, &msm_drm_ops, match);\n\ta52_ackfr_record("DRMCOMP master-add exit rc=%d", ret);\n\treturn ret;\n''',
        "trace DRM master registration",
    )
    path.write_text(text, encoding="utf-8")


def patch_dsi_display(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''\trc = component_add(&pdev->dev, &dsi_display_comp_ops);\n\tif (rc)\n\t\tDSI_ERR("component add failed, rc=%d\\n", rc);\n\n\tDSI_ERR("component add success: %s\\n", display->name);\n''',
        '''\ta52_ackfr_record("DRMCOMP component-add enter dev=%s display=%s",\n\t\t\t dev_name(&pdev->dev), display->name ? display->name : "-");\n\trc = component_add(&pdev->dev, &dsi_display_comp_ops);\n\ta52_ackfr_record("DRMCOMP component-add exit dev=%s rc=%d",\n\t\t\t dev_name(&pdev->dev), rc);\n\tif (rc)\n\t\tDSI_ERR("component add failed, rc=%d\\n", rc);\n\n\tDSI_ERR("component add success: %s\\n", display->name);\n''',
        "trace DSI component registration",
    )
    path.write_text(text, encoding="utf-8")


def patch_component_core(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '#include <linux/debugfs.h>\n',
        '#include <linux/debugfs.h>\n#include <linux/of.h>\n#include <linux/string.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        "add component trace includes",
    )

    anchor = '''static DEFINE_MUTEX(component_mutex);\nstatic LIST_HEAD(component_list);\nstatic LIST_HEAD(masters);\n'''
    helper = '''static DEFINE_MUTEX(component_mutex);\nstatic LIST_HEAD(component_list);\nstatic LIST_HEAD(masters);\n\nstatic bool a52_component_trace_dev(const struct device *dev)\n{\n\tconst char *name;\n\n\tif (!dev)\n\t\treturn false;\n\tif (dev->of_node &&\n\t    (of_device_is_compatible(dev->of_node, "qcom,sde-kms") ||\n\t     of_device_is_compatible(dev->of_node, "qcom,dsi-display") ||\n\t     of_device_is_compatible(dev->of_node, "qcom,dsi-ctrl-hw-v2.4")))\n\t\treturn true;\n\tname = dev_name(dev);\n\treturn name && (strstr(name, "dsi") || strstr(name, "mdp") ||\n\t\t\tstrstr(name, "sde"));\n}\n\nstatic void a52_component_trace_match(struct master *master,\n\t\t\t\t      const char *stage)\n{\n\tsize_t i;\n\n\tif (!master || !master->match || !a52_component_trace_dev(master->dev))\n\t\treturn;\n\ta52_ackfr_record("COMP master stage=%s dev=%s num=%zu bound=%u",\n\t\t\t stage, dev_name(master->dev), master->match->num,\n\t\t\t master->bound);\n\tfor (i = 0; i < master->match->num; i++) {\n\t\tstruct component *c = master->match->compare[i].component;\n\n\t\ta52_ackfr_record("COMP slot i=%zu found=%u dev=%s bound=%u dup=%u",\n\t\t\t\t   i, !!c, c ? dev_name(c->dev) : "-",\n\t\t\t\t   c ? c->bound : 0,\n\t\t\t\t   master->match->compare[i].duplicate);\n\t}\n}\n'''
    text = replace_once(text, anchor, helper, "add component match trace helper")

    text = replace_once(
        text,
        '''int component_master_add_with_match(struct device *dev,\n\tconst struct component_master_ops *ops,\n\tstruct component_match *match)\n{\n\tstruct master *master;\n\tint ret;\n\n\t/* Reallocate the match array for its true size */\n''',
        '''int component_master_add_with_match(struct device *dev,\n\tconst struct component_master_ops *ops,\n\tstruct component_match *match)\n{\n\tstruct master *master;\n\tint ret;\n\n\tif (a52_component_trace_dev(dev))\n\t\ta52_ackfr_record("COMP master-add enter dev=%s num=%zu",\n\t\t\t\t   dev_name(dev), match ? match->num : 0);\n\n\t/* Reallocate the match array for its true size */\n''',
        "trace component master add entry",
    )

    text = replace_once(
        text,
        '''\tret = try_to_bring_up_master(master, NULL);\n\n\tif (ret < 0)\n\t\tfree_master(master);\n''',
        '''\tret = try_to_bring_up_master(master, NULL);\n\ta52_component_trace_match(master, "master-add-result");\n\tif (a52_component_trace_dev(dev))\n\t\ta52_ackfr_record("COMP master-add result dev=%s rc=%d bound=%u",\n\t\t\t\t   dev_name(dev), ret, master->bound);\n\n\tif (ret < 0)\n\t\tfree_master(master);\n''',
        "trace component master add result",
    )

    text = replace_once(
        text,
        '''static int __component_add(struct device *dev, const struct component_ops *ops,\n\tint subcomponent)\n{\n\tstruct component *component;\n\tint ret;\n\n\tcomponent = kzalloc(sizeof(*component), GFP_KERNEL);\n''',
        '''static int __component_add(struct device *dev, const struct component_ops *ops,\n\tint subcomponent)\n{\n\tstruct component *component;\n\tint ret;\n\n\tif (a52_component_trace_dev(dev))\n\t\ta52_ackfr_record("COMP component-add enter dev=%s sub=%d",\n\t\t\t\t   dev_name(dev), subcomponent);\n\n\tcomponent = kzalloc(sizeof(*component), GFP_KERNEL);\n''',
        "trace component add entry",
    )

    text = replace_once(
        text,
        '''\tret = try_to_bring_up_masters(component);\n\tif (ret < 0) {\n''',
        '''\tret = try_to_bring_up_masters(component);\n\tif (a52_component_trace_dev(dev))\n\t\ta52_ackfr_record("COMP component-add result dev=%s rc=%d master=%u",\n\t\t\t\t   dev_name(dev), ret, !!component->master);\n\tif (component->master)\n\t\ta52_component_trace_match(component->master,\n\t\t\t\t\t  "component-add-result");\n\tif (ret < 0) {\n''',
        "trace component add result",
    )

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
