#!/usr/bin/env python3
from pathlib import Path
import argparse, shutil, tempfile
C=Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
Q=Path('drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c')

def one(s,a,b,n):
    if s.count(a)!=1: raise SystemExit(f'{n}: expected 1, got {s.count(a)}')
    return s.replace(a,b,1)

def pc(s):
    if 'SMMU parent-probe enter dev=%s driver=%s' in s: return s
    s=one(s,'#include <linux/slab.h>\n','#include <linux/slab.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','include')
    s=one(s,'static bool using_legacy_binding, using_generic_binding;\n','''static bool using_legacy_binding, using_generic_binding;\n\nstatic bool a52_apps_smmu(const struct device *dev)\n{\n\treturn dev && dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,qsmmu-v500");\n}\n\n''','helper')
    s=one(s,'''static int arm_smmu_device_probe(struct platform_device *pdev)\n{\n\tstruct resource *res;\n\tresource_size_t ioaddr;\n\tstruct arm_smmu_device *smmu;\n\tstruct device *dev = &pdev->dev;\n\tint num_irqs, i, err;\n\tirqreturn_t (*global_fault)(int irq, void *dev);\n\n''','''static int arm_smmu_device_probe(struct platform_device *pdev)\n{\n\tstruct resource *res;\n\tresource_size_t ioaddr;\n\tstruct arm_smmu_device *smmu;\n\tstruct device *dev = &pdev->dev;\n\tbool trace = a52_apps_smmu(dev);\n\tint num_irqs, i, err;\n\tirqreturn_t (*global_fault)(int irq, void *dev);\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe enter dev=%s driver=%s",\n\t\t\tdev_name(dev), dev->driver ? dev->driver->name : "-");\n\n''','probe enter')
    s=one(s,'''\tif (err)\n\t\treturn err;\n\n\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n''','''\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d",\n\t\t\terr, err ? -1 : smmu->model, smmu->skip_init,\n\t\t\tsmmu->use_3lvl_tables);\n\tif (err)\n\t\treturn err;\n\n\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n''','dt')
    s=one(s,'''\tsmmu = arm_smmu_impl_init(smmu);\n\tif (IS_ERR(smmu))\n\t\treturn PTR_ERR(smmu);\n''','''\tsmmu = arm_smmu_impl_init(smmu);\n\tif (IS_ERR(smmu)) {\n\t\terr = PTR_ERR(smmu);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("SMMU parent-impl rc=%d", err);\n\t\treturn err;\n\t}\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-impl rc=0 impl=%d", !!smmu->impl);\n''','impl')
    s=one(s,'''\terr = arm_smmu_device_cfg_probe(smmu);\n\tif (err)\n\t\treturn err;\n''','''\terr = arm_smmu_device_cfg_probe(smmu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-cfg rc=%d groups=%u cbs=%u", err,\n\t\t\tsmmu->num_mapping_groups, smmu->num_context_banks);\n\tif (err)\n\t\treturn err;\n''','cfg')
    s=one(s,'''\terr = iommu_device_register(&smmu->iommu);\n\tif (err) {\n''','''\terr = iommu_device_register(&smmu->iommu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-register rc=%d", err);\n\tif (err) {\n''','register')
    s=one(s,'''\tif (!using_legacy_binding)\n\t\treturn arm_smmu_bus_init(&arm_smmu_ops);\n\n\treturn 0;\n}\n''','''\tif (!using_legacy_binding) {\n\t\terr = arm_smmu_bus_init(&arm_smmu_ops);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("SMMU parent-probe exit rc=%d", err);\n\t\treturn err;\n\t}\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe exit rc=0");\n\treturn 0;\n}\n''','exit')
    return s

def pq(s):
    if 'SMMU parent-qcom scm=%d' in s: return s
    s=one(s,'#include <linux/qcom_scm.h>\n','#include <linux/qcom_scm.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n','q include')
    s=one(s,'''\tstruct qcom_smmu *qsmmu;\n\n\t/* Check to make sure qcom_scm has finished probing */\n\tif (!qcom_scm_is_available())\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n''','''\tstruct qcom_smmu *qsmmu;\n\tbool trace = smmu->dev->of_node &&\n\t\tof_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500");\n\tbool scm = qcom_scm_is_available();\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom scm=%d", scm);\n\t/* Check to make sure qcom_scm has finished probing */\n\tif (!scm)\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n''','q scm')
    return s

def apply(r):
    for p,f in ((C,pc),(Q,pq)):
        x=r/p; x.write_text(f(x.read_text()))

def test(r):
    with tempfile.TemporaryDirectory() as d:
        t=Path(d)
        for p in (C,Q):
            (t/p).parent.mkdir(parents=True,exist_ok=True); shutil.copy2(r/p,t/p)
        apply(t); a=[(t/p).read_text() for p in (C,Q)]; apply(t)
        assert a==[(t/p).read_text() for p in (C,Q)]
        c,q=a
        for m in ('SMMU parent-probe enter dev=%s driver=%s','SMMU parent-dt rc=%d model=%d skip=%d lvl3=%d','SMMU parent-impl rc=%d','SMMU parent-cfg rc=%d groups=%u cbs=%u','SMMU parent-register rc=%d','SMMU parent-probe exit rc=%d'): assert m in c
        assert 'SMMU parent-qcom scm=%d' in q
        assert '{ .compatible = "qcom,qsmmu-v500", .data = &arm_mmu500 },' in c
        assert 'if (!smmu->skip_init)' in c and 'if (smmu->use_3lvl_tables)' in c
    print('phase203 Apps SMMU parent trace self-test: PASS')

ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--self-test',action='store_true'); a=ap.parse_args()
if a.self_test: test(a.root)
else: apply(a.root); print('phase203 Apps SMMU parent trace applied')
