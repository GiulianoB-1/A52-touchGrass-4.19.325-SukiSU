#!/usr/bin/env python3
from pathlib import Path
import argparse

DD=Path('drivers/base/dd.c')
CORE=Path('drivers/base/core.c')
PLATFORM=Path('drivers/base/platform.c')
OFDEV=Path('drivers/of/device.c')
OFIOMMU=Path('drivers/iommu/of_iommu.c')
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')


def ro(text, old, new, label):
    n=text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1, got {n}')
    return text.replace(old,new,1)

HELPER='''\nstatic bool a52_smmu_unsec_trace_dev(const struct device *dev)\n{\n\treturn dev && dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec");\n}\n\n'''

def patch_dd(t):
    if 'DCORE attach enter dev=%s' in t:
        return t
    t=ro(t,'#include <linux/slab.h>\n','#include <linux/slab.h>\n#include <linux/of.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','dd includes')
    t=ro(t,'static bool defer_all_probes;\n','static bool defer_all_probes;\n'+HELPER,'dd helper')
    t=ro(t,'\t\tdev_dbg(dev, "Retrying from deferred list\\n");\n\t\tbus_probe_device(dev);\n','\t\tdev_dbg(dev, "Retrying from deferred list\\n");\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DCORE defer retry dev=%s", dev_name(dev));\n\t\tbus_probe_device(dev);\n','dd retry')
    t=ro(t,'void driver_deferred_probe_add(struct device *dev)\n{\n\tmutex_lock(&deferred_probe_mutex);\n','void driver_deferred_probe_add(struct device *dev)\n{\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE defer add dev=%s", dev_name(dev));\n\tmutex_lock(&deferred_probe_mutex);\n','dd defer add')
    t=ro(t,'static int really_probe(struct device *dev, struct device_driver *drv)\n{\n\tint ret = -EPROBE_DEFER;\n','static int really_probe(struct device *dev, struct device_driver *drv)\n{\n\tint ret = -EPROBE_DEFER;\n','dd really signature')
    t=ro(t,'\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n\t\t\t   !drv->suppress_bind_attrs;\n\n\tif (defer_all_probes) {\n','\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n\t\t\t   !drv->suppress_bind_attrs;\n\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE really enter dev=%s drv=%s all=%d",\n\t\t\tdev_name(dev), drv->name, defer_all_probes);\n\n\tif (defer_all_probes) {\n','dd really enter')
    t=ro(t,'\tret = device_links_check_suppliers(dev);\n\tif (ret == -EPROBE_DEFER)\n','\tret = device_links_check_suppliers(dev);\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE suppliers rc=%d status=%d",\n\t\t\tret, dev->links.status);\n\tif (ret == -EPROBE_DEFER)\n','dd suppliers')
    t=ro(t,'\tif (dev->bus->dma_configure) {\n\t\tret = dev->bus->dma_configure(dev);\n\t\tif (ret)\n\t\t\tgoto probe_failed;\n\t}\n','\tif (dev->bus->dma_configure) {\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DCORE dma enter dev=%s", dev_name(dev));\n\t\tret = dev->bus->dma_configure(dev);\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DCORE dma exit rc=%d mapped=%d", ret,\n\t\t\t\tdevice_iommu_mapped(dev));\n\t\tif (ret)\n\t\t\tgoto probe_failed;\n\t}\n','dd dma')
    t=ro(t,'\tif (dev->bus->probe) {\n\t\tret = dev->bus->probe(dev);\n','\tif (dev->bus->probe) {\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DCORE bus-probe enter drv=%s", drv->name);\n\t\tret = dev->bus->probe(dev);\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DCORE bus-probe exit rc=%d", ret);\n','dd bus probe')
    t=ro(t,'done:\n\tatomic_dec(&probe_count);\n','done:\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE really exit ret=%d bound=%d", ret,\n\t\t\t!!dev->driver);\n\tatomic_dec(&probe_count);\n','dd really exit')
    t=ro(t,'static int __device_attach_driver(struct device_driver *drv, void *_data)\n{\n\tstruct device_attach_data *data = _data;\n\tstruct device *dev = data->dev;\n\tbool async_allowed;\n\tint ret;\n\n\tret = driver_match_device(drv, dev);\n','static int __device_attach_driver(struct device_driver *drv, void *_data)\n{\n\tstruct device_attach_data *data = _data;\n\tstruct device *dev = data->dev;\n\tbool async_allowed;\n\tint ret;\n\n\tret = driver_match_device(drv, dev);\n\tif (a52_smmu_unsec_trace_dev(dev) &&\n\t    !strcmp(drv->name, "msmdrm_smmu"))\n\t\ta52_ackfr_record("DCORE match drv=%s rc=%d", drv->name, ret);\n','dd match')
    t=ro(t,'\treturn driver_probe_device(drv, dev);\n}\n\nstatic void __device_attach_async_helper','\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE match probe drv=%s async=%d",\n\t\t\tdrv->name, async_allowed);\n\treturn driver_probe_device(drv, dev);\n}\n\nstatic void __device_attach_async_helper','dd probe call')
    t=ro(t,'static int __device_attach(struct device *dev, bool allow_async)\n{\n\tint ret = 0;\n\tbool async = false;\n\n\tdevice_lock(dev);\n','static int __device_attach(struct device *dev, bool allow_async)\n{\n\tint ret = 0;\n\tbool async = false;\n\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE attach enter dev=%s async=%d driver=%d",\n\t\t\tdev_name(dev), allow_async, !!dev->driver);\n\tdevice_lock(dev);\n','dd attach enter')
    t=ro(t,'\tif (async)\n\t\tasync_schedule_dev(__device_attach_async_helper, dev);\n\treturn ret;\n}\n','\tif (async)\n\t\tasync_schedule_dev(__device_attach_async_helper, dev);\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE attach exit ret=%d driver=%d async=%d",\n\t\t\tret, !!dev->driver, async);\n\treturn ret;\n}\n','dd attach exit')
    t=ro(t,'int driver_probe_device(struct device_driver *drv, struct device *dev)\n{\n\tint ret = 0;\n\n\tif (!device_is_registered(dev))\n','int driver_probe_device(struct device_driver *drv, struct device *dev)\n{\n\tint ret = 0;\n\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE probe-device enter drv=%s reg=%d",\n\t\t\tdrv->name, device_is_registered(dev));\n\tif (!device_is_registered(dev))\n','dd probe-device enter')
    t=ro(t,'\tpm_runtime_put_suppliers(dev);\n\treturn ret;\n}\n\nstatic inline bool cmdline_requested_async_probing','\tpm_runtime_put_suppliers(dev);\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DCORE probe-device exit drv=%s ret=%d",\n\t\t\tdrv->name, ret);\n\treturn ret;\n}\n\nstatic inline bool cmdline_requested_async_probing','dd probe-device exit')
    return t

def patch_core(t):
    if 'DLINK check enter dev=%s' in t:
        return t
    t=ro(t,'#include <linux/sysfs.h>\n','#include <linux/sysfs.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','core include')
    t=ro(t,'static bool fw_devlink_is_permissive(void);\n','static bool fw_devlink_is_permissive(void);\n'+HELPER,'core helper')
    t=ro(t,'int device_links_check_suppliers(struct device *dev)\n{\n\tstruct device_link *link;\n\tint ret = 0;\n','int device_links_check_suppliers(struct device *dev)\n{\n\tstruct device_link *link;\n\tint ret = 0;\n\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DLINK check enter dev=%s status=%d permissive=%d",\n\t\t\tdev_name(dev), dev->links.status, fw_devlink_is_permissive());\n','core enter')
    t=ro(t,'\t\tdev_dbg(dev, "probe deferral - wait for supplier %pfwP\\n",\n\t\t\tlist_first_entry(&dev->fwnode->suppliers,\n\t\t\tstruct fwnode_link,\n\t\t\tc_hook)->supplier);\n','\t\tdev_dbg(dev, "probe deferral - wait for supplier %pfwP\\n",\n\t\t\tlist_first_entry(&dev->fwnode->suppliers,\n\t\t\tstruct fwnode_link,\n\t\t\tc_hook)->supplier);\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DLINK fwnode wait supplier=%pfwP",\n\t\t\t\tlist_first_entry(&dev->fwnode->suppliers,\n\t\t\t\tstruct fwnode_link, c_hook)->supplier);\n','core fwnode')
    t=ro(t,'\tlist_for_each_entry(link, &dev->links.suppliers, c_node) {\n\t\tif (!(link->flags & DL_FLAG_MANAGED))\n','\tlist_for_each_entry(link, &dev->links.suppliers, c_node) {\n\t\tif (a52_smmu_unsec_trace_dev(dev))\n\t\t\ta52_ackfr_record("DLINK supplier=%s st=%d flags=%x supst=%d",\n\t\t\t\tdev_name(link->supplier), link->status, link->flags,\n\t\t\t\tlink->supplier->links.status);\n\t\tif (!(link->flags & DL_FLAG_MANAGED))\n','core links')
    t=ro(t,'\tdevice_links_write_unlock();\n\treturn ret;\n}\n\n/**\n * __device_links_queue_sync_state','\tdevice_links_write_unlock();\n\tif (a52_smmu_unsec_trace_dev(dev))\n\t\ta52_ackfr_record("DLINK check exit rc=%d status=%d", ret,\n\t\t\tdev->links.status);\n\treturn ret;\n}\n\n/**\n * __device_links_queue_sync_state','core exit')
    return t

def patch_platform(t):
    if 'DCORE platform-match drv=%s rc=%d' in t:
        return t
    t=ro(t,'#include <linux/types.h>\n','#include <linux/types.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','platform include')
    old='''static int platform_match(struct device *dev, struct device_driver *drv)\n{\n\tstruct platform_device *pdev = to_platform_device(dev);\n\tstruct platform_driver *pdrv = to_platform_driver(drv);\n\n\t/* When driver_override is set, only bind to the matching driver */\n\tif (pdev->driver_override)\n\t\treturn !strcmp(pdev->driver_override, drv->name);\n\n\t/* Attempt an OF style match first */\n\tif (of_driver_match_device(dev, drv))\n\t\treturn 1;\n\n\t/* Then try ACPI style match */\n\tif (acpi_driver_match_device(dev, drv))\n\t\treturn 1;\n\n\t/* Then try to match against the id table */\n\tif (pdrv->id_table)\n\t\treturn platform_match_id(pdrv->id_table, pdev) != NULL;\n\n\t/* fall-back to driver name match */\n\treturn (strcmp(pdev->name, drv->name) == 0);\n}\n'''
    new='''static int platform_match(struct device *dev, struct device_driver *drv)\n{\n\tstruct platform_device *pdev = to_platform_device(dev);\n\tstruct platform_driver *pdrv = to_platform_driver(drv);\n\tint ret;\n\n\t/* When driver_override is set, only bind to the matching driver */\n\tif (pdev->driver_override)\n\t\tret = !strcmp(pdev->driver_override, drv->name);\n\telse if (of_driver_match_device(dev, drv))\n\t\tret = 1;\n\telse if (acpi_driver_match_device(dev, drv))\n\t\tret = 1;\n\telse if (pdrv->id_table)\n\t\tret = platform_match_id(pdrv->id_table, pdev) != NULL;\n\telse\n\t\tret = strcmp(pdev->name, drv->name) == 0;\n\n\tif (dev->of_node &&\n\t    of_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec") &&\n\t    !strcmp(drv->name, "msmdrm_smmu"))\n\t\ta52_ackfr_record("DCORE platform-match drv=%s rc=%d",\n\t\t\tdrv->name, ret);\n\treturn ret;\n}\n'''
    t=ro(t,old,new,'platform match')
    t=ro(t,'int platform_dma_configure(struct device *dev)\n{\n\tenum dev_dma_attr attr;\n\tint ret = 0;\n','int platform_dma_configure(struct device *dev)\n{\n\tenum dev_dma_attr attr;\n\tint ret = 0;\n\tbool trace = dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec");\n\n\tif (trace)\n\t\ta52_ackfr_record("DMA platform enter dev=%s", dev_name(dev));\n','platform dma enter')
    t=ro(t,'\treturn ret;\n}\n\nstatic const struct dev_pm_ops platform_dev_pm_ops','\tif (trace)\n\t\ta52_ackfr_record("DMA platform exit rc=%d mapped=%d", ret,\n\t\t\tdevice_iommu_mapped(dev));\n\treturn ret;\n}\n\nstatic const struct dev_pm_ops platform_dev_pm_ops','platform dma exit')
    return t

def patch_ofdev(t):
    if 'DMA of enter dev=%s force=%d' in t:
        return t
    t=ro(t,'#include <linux/platform_device.h>\n','#include <linux/platform_device.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','ofdev include')
    t=ro(t,'\tbool coherent;\n\tint ret;\n\n\tret = of_dma_get_range(np, &map);\n','\tbool coherent;\n\tbool trace = dev && np &&\n\t\tof_device_is_compatible(np, "qcom,smmu_sde_unsec");\n\tint ret;\n\n\tif (trace)\n\t\ta52_ackfr_record("DMA of enter dev=%s force=%d",\n\t\t\tdev_name(dev), force_dma);\n\tret = of_dma_get_range(np, &map);\n\tif (trace)\n\t\ta52_ackfr_record("DMA range rc=%d", ret);\n','ofdev enter')
    t=ro(t,'\tiommu = of_iommu_configure(dev, np, id);\n\tif (PTR_ERR(iommu) == -EPROBE_DEFER) {\n','\tiommu = of_iommu_configure(dev, np, id);\n\tif (trace)\n\t\ta52_ackfr_record("DMA iommu result err=%ld ptr=%d mapped=%d",\n\t\t\tIS_ERR(iommu) ? PTR_ERR(iommu) : 0L, !!iommu,\n\t\t\tdevice_iommu_mapped(dev));\n\tif (PTR_ERR(iommu) == -EPROBE_DEFER) {\n','ofdev iommu')
    t=ro(t,'\tarch_setup_dma_ops(dev, dma_start, size, iommu, coherent);\n\n\treturn 0;\n}\n','\tarch_setup_dma_ops(dev, dma_start, size, iommu, coherent);\n\tif (trace)\n\t\ta52_ackfr_record("DMA of exit mapped=%d fwspec=%d",\n\t\t\tdevice_iommu_mapped(dev), !!dev_iommu_fwspec_get(dev));\n\n\treturn 0;\n}\n','ofdev exit')
    return t

def patch_ofiommu(t):
    if 'IOMMU of enter dev=%s fwspec=%d' in t:
        return t
    t=ro(t,'#include <linux/fsl/mc.h>\n','#include <linux/fsl/mc.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','ofiommu include')
    t=ro(t,'\tstruct iommu_fwspec *fwspec = dev_iommu_fwspec_get(dev);\n\tint err = NO_IOMMU;\n\n\tif (!master_np)\n','\tstruct iommu_fwspec *fwspec = dev_iommu_fwspec_get(dev);\n\tbool trace = dev && master_np &&\n\t\tof_device_is_compatible(master_np, "qcom,smmu_sde_unsec");\n\tint err = NO_IOMMU;\n\n\tif (trace)\n\t\ta52_ackfr_record("IOMMU of enter dev=%s fwspec=%d ops=%d",\n\t\t\tdev_name(dev), !!fwspec, fwspec && !!fwspec->ops);\n\tif (!master_np)\n','ofiommu enter')
    t=ro(t,'\t\terr = of_iommu_configure_device(master_np, dev, id);\n\n\t\tfwspec = dev_iommu_fwspec_get(dev);\n','\t\terr = of_iommu_configure_device(master_np, dev, id);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("IOMMU configure-device err=%d", err);\n\n\t\tfwspec = dev_iommu_fwspec_get(dev);\n','ofiommu configure')
    t=ro(t,'\tif (!err && dev->bus && !device_iommu_mapped(dev))\n\t\terr = iommu_probe_device(dev);\n','\tif (!err && dev->bus && !device_iommu_mapped(dev)) {\n\t\tif (trace)\n\t\t\ta52_ackfr_record("IOMMU probe-device enter");\n\t\terr = iommu_probe_device(dev);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("IOMMU probe-device exit err=%d mapped=%d",\n\t\t\t\terr, device_iommu_mapped(dev));\n\t}\n','ofiommu probe')
    t=ro(t,'\treturn ops;\n}\n','\tif (trace)\n\t\ta52_ackfr_record("IOMMU of exit err=%d ops=%d fwspec=%d", err,\n\t\t\t!IS_ERR_OR_NULL(ops), !!dev_iommu_fwspec_get(dev));\n\treturn ops;\n}\n','ofiommu exit')
    return t

def patch_rec(t):
    if '!strncmp(message, "DCORE ", 6)' in t:
        return t
    t=ro(t,'\t       !strncmp(message, "SMMU ", 5) ||\n\t       !strncmp(message, "A52GDSC ", 8);\n','\t       !strncmp(message, "SMMU ", 5) ||\n\t       !strncmp(message, "DCORE ", 6) ||\n\t       !strncmp(message, "DLINK ", 6) ||\n\t       !strncmp(message, "DMA ", 4) ||\n\t       !strncmp(message, "IOMMU ", 6) ||\n\t       !strncmp(message, "A52GDSC ", 8);\n','rec critical')
    return t

def run(root):
    funcs=[(DD,patch_dd),(CORE,patch_core),(PLATFORM,patch_platform),(OFDEV,patch_ofdev),(OFIOMMU,patch_ofiommu),(REC,patch_rec)]
    for p,f in funcs:
        q=root/p
        q.write_text(f(q.read_text()))

def self_test(root: Path):
    pairs=[(DD,patch_dd),(CORE,patch_core),(PLATFORM,patch_platform),(OFDEV,patch_ofdev),(OFIOMMU,patch_ofiommu),(REC,patch_rec)]
    for p,f in pairs:
        src=(root/p).read_text()
        out=f(src)
        assert out != src, p
        assert f(out) == out, p
    markers={
        DD:['DCORE attach enter dev=%s','DCORE suppliers rc=%d status=%d','DCORE defer retry dev=%s'],
        CORE:['DLINK fwnode wait supplier=%pfwP','DLINK supplier=%s st=%d flags=%x supst=%d'],
        PLATFORM:['DCORE platform-match drv=%s rc=%d','DMA platform exit rc=%d mapped=%d'],
        OFDEV:['DMA iommu result err=%ld ptr=%d mapped=%d'],
        OFIOMMU:['IOMMU configure-device err=%d','IOMMU probe-device exit err=%d mapped=%d'],
        REC:['!strncmp(message, "DCORE ", 6)','!strncmp(message, "IOMMU ", 6)'],
    }
    for p,wanted in markers.items():
        out=dict(pairs)[p]((root/p).read_text())
        for marker in wanted:
            assert marker in out, (p, marker)
    print('phase202 driver-core trace patcher self-test: PASS')

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--self-test',action='store_true')
    a=ap.parse_args()
    if a.self_test:
        self_test(a.root)
    else:
        run(a.root)
        print('phase202 filtered SMMU driver-core trace applied')
