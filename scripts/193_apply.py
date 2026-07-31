#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one match, found {n}")
    return text.replace(old, new, 1)


def patch_core(path: Path) -> None:
    text = path.read_text()
    anchor = '''\tif (dropped)\n\t\t*dropped = local_dropped;\n}\n\n\nstatic ssize_t waiting_for_supplier_show'''
    new = '''\tif (dropped)\n\t\t*dropped = local_dropped;\n}\n\nvoid a52_device_links_trace(struct device *dev, const char *tag);\nvoid a52_device_links_trace(struct device *dev, const char *tag)\n{\n\tstruct device_link *link;\n\tunsigned int link_index = 0;\n\tint idx;\n\n\tif (!dev)\n\t\treturn;\n\ta52_ackfr_record("RSCCCORE links begin tag=%s c=%s status=%u",\n\t\t\t tag ? tag : "-", dev_name(dev),\n\t\t\t (unsigned int)dev->links.status);\n\tidx = device_links_read_lock();\n\tlist_for_each_entry(link, &dev->links.suppliers, c_node) {\n\t\tconst char *sname = link->supplier ? dev_name(link->supplier) : "none";\n\t\tconst char *sdrv = link->supplier && link->supplier->driver &&\n\t\t\tlink->supplier->driver->name ? link->supplier->driver->name : "none";\n\t\tconst char *sof = link->supplier && link->supplier->of_node ?\n\t\t\tlink->supplier->of_node->full_name : "none";\n\n\t\ta52_ackfr_record("RSCCCORE link n=%u s=%s st=%u fl=0x%x",\n\t\t\t\t   link_index, sname,\n\t\t\t\t   (unsigned int)link->status, link->flags);\n\t\ta52_ackfr_record("RSCCCORE link n=%u of=%s drv=%s",\n\t\t\t\t   link_index, sof, sdrv);\n\t\tlink_index++;\n\t}\n\tdevice_links_read_unlock(idx);\n\ta52_ackfr_record("RSCCCORE links end tag=%s count=%u",\n\t\t\t tag ? tag : "-", link_index);\n}\n\n\nstatic ssize_t waiting_for_supplier_show'''
    text = one(text, anchor, new, "add read-only RSCC link trace")
    path.write_text(text)


def patch_dd(path: Path) -> None:
    text = path.read_text()
    text = one(text,
'''extern void a52_device_links_force_probe(struct device *dev,\n\t\t\t\t\t unsigned int *kept,\n\t\t\t\t\t unsigned int *dropped);\n''',
'''extern void a52_device_links_force_probe(struct device *dev,\n\t\t\t\t\t unsigned int *kept,\n\t\t\t\t\t unsigned int *dropped);\nextern void a52_device_links_trace(struct device *dev, const char *tag);\n''',
"declare link trace")

    anchor = '''static bool a52_display_probe_device(const struct device *dev)\n{\n\tif (!dev || !dev->of_node)\n\t\treturn false;\n\n\treturn of_device_is_compatible(dev->of_node, "qcom,sde-kms") ||\n\t       of_device_is_compatible(dev->of_node, "qcom,dsi-display") ||\n\t       of_device_is_compatible(dev->of_node, "qcom,dsi-ctrl-hw-v2.4");\n}\n'''
    helper = anchor + '''\nstatic bool a52_rscc_probe_device(const struct device *dev)\n{\n\treturn dev && dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,sde-rsc");\n}\n'''
    text = one(text, anchor, helper, "add RSCC device filter")

    old = '''void driver_deferred_probe_add(struct device *dev)\n{\n\tif (a52_storage_probe_device(dev)) {'''
    new = '''void driver_deferred_probe_add(struct device *dev)\n{\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE deferred-add dev=%s reason=%s",\n\t\t\tdev_name(dev), dev->p && dev->p->deferred_probe_reason ?\n\t\t\tdev->p->deferred_probe_reason : "-");\n\tif (a52_storage_probe_device(dev)) {'''
    text = one(text, old, new, "trace RSCC deferred add")

    old = '''\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n\t\t\t   !drv->suppress_bind_attrs;\n\n\tif (a52_display_probe_device(dev))'''
    new = '''\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n\t\t\t   !drv->suppress_bind_attrs;\n\n\tif (a52_rscc_probe_device(dev)) {\n\t\ta52_ackfr_record("RSCCCORE really-probe enter dev=%s drv=%s",\n\t\t\tdev_name(dev), drv && drv->name ? drv->name : "-");\n\t\ta52_device_links_trace(dev, "really-enter");\n\t}\n\n\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace really_probe entry")

    old = '''\tret = device_links_check_suppliers(dev);\n\tif (ret == -EPROBE_DEFER && a52_legacy_fw_devlink_consumer(dev)) {'''
    new = '''\tret = device_links_check_suppliers(dev);\n\tif (a52_rscc_probe_device(dev)) {\n\t\tconst char *reason = dev->p && dev->p->deferred_probe_reason ?\n\t\t\tdev->p->deferred_probe_reason : "-";\n\n\t\ta52_ackfr_record("RSCCCORE suppliers dev=%s rc=%d reason=%s",\n\t\t\tdev_name(dev), ret, reason);\n\t\ta52_device_links_trace(dev, "suppliers-checked");\n\t}\n\tif (ret == -EPROBE_DEFER && a52_legacy_fw_devlink_consumer(dev)) {'''
    text = one(text, old, new, "trace supplier gate")

    old = '''\tret = pinctrl_bind_pins(dev);\n\tif (a52_display_probe_device(dev))'''
    new = '''\tret = pinctrl_bind_pins(dev);\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE pinctrl dev=%s rc=%d", dev_name(dev), ret);\n\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace RSCC pinctrl")

    old = '''\t\tret = dev->bus->dma_configure(dev);\n\t\tif (a52_display_probe_device(dev))'''
    new = '''\t\tret = dev->bus->dma_configure(dev);\n\t\tif (a52_rscc_probe_device(dev))\n\t\t\ta52_ackfr_record("RSCCCORE dma dev=%s rc=%d", dev_name(dev), ret);\n\t\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace RSCC DMA")

    old = '''\tret = driver_sysfs_add(dev);\n\tif (a52_display_probe_device(dev))'''
    new = '''\tret = driver_sysfs_add(dev);\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE sysfs dev=%s rc=%d", dev_name(dev), ret);\n\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace RSCC sysfs")

    old = '''\tif (dev->pm_domain && dev->pm_domain->activate) {\n\t\tret = dev->pm_domain->activate(dev);\n\t\tif (a52_display_probe_device(dev))'''
    new = '''\tif (dev->pm_domain && dev->pm_domain->activate) {\n\t\tret = dev->pm_domain->activate(dev);\n\t\tif (a52_rscc_probe_device(dev))\n\t\t\ta52_ackfr_record("RSCCCORE pm dev=%s rc=%d", dev_name(dev), ret);\n\t\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace RSCC PM")

    old = '''\tif (dev->bus->probe) {\n\t\tif (a52_run40_preprobe_target(dev)) {'''
    new = '''\tif (dev->bus->probe) {\n\t\tif (a52_rscc_probe_device(dev))\n\t\t\ta52_ackfr_record("RSCCCORE busprobe enter dev=%s drv=%s",\n\t\t\t\tdev_name(dev), drv && drv->name ? drv->name : "-");\n\t\tif (a52_run40_preprobe_target(dev)) {'''
    text = one(text, old, new, "trace RSCC bus probe enter")

    old = '''\t\tret = dev->bus->probe(dev);\n\t\tif (a52_display_probe_device(dev))'''
    new = '''\t\tret = dev->bus->probe(dev);\n\t\tif (a52_rscc_probe_device(dev))\n\t\t\ta52_ackfr_record("RSCCCORE busprobe exit dev=%s rc=%d",\n\t\t\t\tdev_name(dev), ret);\n\t\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace RSCC bus probe exit")

    old = '''done:\n\tif (a52_display_probe_device(dev))'''
    new = '''done:\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE really-probe done dev=%s rc=%d bound=%s",\n\t\t\tdev_name(dev), ret, dev->driver && dev->driver->name ?\n\t\t\tdev->driver->name : "-");\n\tif (a52_display_probe_device(dev))'''
    text = one(text, old, new, "trace really_probe completion")

    old = '''int driver_probe_device(struct device_driver *drv, struct device *dev)\n{\n\tint ret = 0;\n\n\tif (!device_is_registered(dev))'''
    new = '''int driver_probe_device(struct device_driver *drv, struct device *dev)\n{\n\tint ret = 0;\n\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE driver-probe enter dev=%s drv=%s",\n\t\t\tdev_name(dev), drv && drv->name ? drv->name : "-");\n\n\tif (!device_is_registered(dev))'''
    text = one(text, old, new, "trace driver_probe_device entry")

    old = '''\tpm_runtime_put_suppliers(dev);\n\treturn ret;\n}\n\nstatic inline bool cmdline_requested_async_probing'''
    new = '''\tpm_runtime_put_suppliers(dev);\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE driver-probe exit dev=%s rc=%d bound=%s",\n\t\t\tdev_name(dev), ret, dev->driver && dev->driver->name ?\n\t\t\tdev->driver->name : "-");\n\treturn ret;\n}\n\nstatic inline bool cmdline_requested_async_probing'''
    text = one(text, old, new, "trace driver_probe_device exit")

    old = '''\tret = driver_match_device(drv, dev);\n\tif (ret == 0) {\n\t\t/* no match */\n\t\treturn 0;\n'''
    new = '''\tret = driver_match_device(drv, dev);\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE match path=device-attach dev=%s drv=%s rc=%d",\n\t\t\tdev_name(dev), drv && drv->name ? drv->name : "-", ret);\n\tif (ret == 0) {\n\t\t/* no match */\n\t\treturn 0;\n'''
    if text.count(old) != 2:
        raise SystemExit(f"trace device attach match: expected two matches, found {text.count(old)}")
    text = text.replace(old, new, 1)

    # second occurrence in __driver_attach
    old2 = '''\tret = driver_match_device(drv, dev);\n\tif (ret == 0) {\n\t\t/* no match */\n\t\treturn 0;\n'''
    new2 = '''\tret = driver_match_device(drv, dev);\n\tif (a52_rscc_probe_device(dev))\n\t\ta52_ackfr_record("RSCCCORE match path=driver-attach dev=%s drv=%s rc=%d",\n\t\t\tdev_name(dev), drv && drv->name ? drv->name : "-", ret);\n\tif (ret == 0) {\n\t\t/* no match */\n\t\treturn 0;\n'''
    text = one(text, old2, new2, "trace driver attach match")

    path.write_text(text)


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,required=True); a=p.parse_args()
    patch_core(a.root/'drivers/base/core.c')
    patch_dd(a.root/'drivers/base/dd.c')
    print('phase193 RSCC driver-core gate trace applied')
    return 0

if __name__=='__main__': raise SystemExit(main())
