#!/usr/bin/env python3
from pathlib import Path
import argparse, shutil, tempfile

CORE = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
QCOM = Path('drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c')


def ro(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1, got {n}')
    return text.replace(old, new, 1)


def patch_core(t):
    if 'SMMU parent-probe enter dev=%s' in t:
        return t
    t = ro(t,
        '#include <linux/slab.h>\n',
        '#include <linux/slab.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'core include')
    t = ro(t,
        'static bool using_legacy_binding, using_generic_binding;\n',
        '''static bool using_legacy_binding, using_generic_binding;\n\nstatic bool arm_smmu_qsmmuv500_node(const struct arm_smmu_device *smmu)\n{\n\treturn smmu && smmu->dev && smmu->dev->of_node &&\n\t\tof_device_is_compatible(smmu->dev->of_node,\n\t\t\t\t\t"qcom,qsmmu-v500");\n}\n\nstatic bool arm_smmu_qsmmuv500_skip_init(const struct arm_smmu_device *smmu)\n{\n\treturn arm_smmu_qsmmuv500_node(smmu) &&\n\t\tof_property_read_bool(smmu->dev->of_node, "qcom,skip-init");\n}\n''',
        'core helpers')
    t = ro(t,
        '''\t\tif (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH64) {\n\t\t\tfmt = ARM_64_LPAE_S1;\n\t\t} else if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_L) {\n''',
        '''\t\tif (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH64) {\n\t\t\tfmt = ARM_64_LPAE_S1;\n\t\t\tif (arm_smmu_qsmmuv500_node(smmu) &&\n\t\t\t    of_property_read_bool(smmu->dev->of_node,\n\t\t\t\t\t\t  "qcom,use-3-lvl-tables")) {\n\t\t\t\tias = min(ias, 39UL);\n\t\t\t\ta52_ackfr_record("SMMU parent-domain 3lvl ias=%lu", ias);\n\t\t\t}\n\t\t} else if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_L) {\n''',
        '3lvl clamp')
    t = ro(t,
        '''static void arm_smmu_device_reset(struct arm_smmu_device *smmu)\n{\n\tint i;\n\tu32 reg;\n\n\t/* clear global FSR */\n''',
        '''static void arm_smmu_device_reset(struct arm_smmu_device *smmu)\n{\n\tbool skip_init = arm_smmu_qsmmuv500_skip_init(smmu);\n\tint i;\n\tu32 reg;\n\n\tif (arm_smmu_qsmmuv500_node(smmu))\n\t\ta52_ackfr_record("SMMU parent-reset enter skip=%d groups=%u cbs=%u",\n\t\t\tskip_init, smmu->num_mapping_groups,\n\t\t\tsmmu->num_context_banks);\n\n\t/* clear global FSR */\n''',
        'reset enter')
    t = ro(t,
        '''\t/*\n\t * Reset stream mapping groups: Initial values mark all SMRn as\n\t * invalid and all S2CRn as bypass unless overridden.\n\t */\n\tfor (i = 0; i < smmu->num_mapping_groups; ++i)\n\t\tarm_smmu_write_sme(smmu, i);\n\n\t/* Make sure all context banks are disabled and clear CB_FSR  */\n\tfor (i = 0; i < smmu->num_context_banks; ++i) {\n\t\tarm_smmu_write_context_bank(smmu, i);\n\t\tarm_smmu_cb_write(smmu, i, ARM_SMMU_CB_FSR, ARM_SMMU_FSR_FAULT);\n\t}\n''',
        '''\t/*\n\t * TouchGrass marks the Lagoon Apps SMMU qcom,skip-init because the\n\t * boot firmware owns live stream/context mappings. Preserve them.\n\t */\n\tif (!skip_init) {\n\t\t/*\n\t\t * Reset stream mapping groups: Initial values mark all SMRn as\n\t\t * invalid and all S2CRn as bypass unless overridden.\n\t\t */\n\t\tfor (i = 0; i < smmu->num_mapping_groups; ++i)\n\t\t\tarm_smmu_write_sme(smmu, i);\n\n\t\t/* Make sure all context banks are disabled and clear CB_FSR  */\n\t\tfor (i = 0; i < smmu->num_context_banks; ++i) {\n\t\t\tarm_smmu_write_context_bank(smmu, i);\n\t\t\tarm_smmu_cb_write(smmu, i, ARM_SMMU_CB_FSR,\n\t\t\t\t\t  ARM_SMMU_FSR_FAULT);\n\t\t}\n\t}\n''',
        'reset preserve mappings')
    t = ro(t,
        '''\t/* Push the button */\n\tarm_smmu_tlb_sync_global(smmu);\n\tarm_smmu_gr0_write(smmu, ARM_SMMU_GR0_sCR0, reg);\n}\n''',
        '''\t/* Push the button */\n\tarm_smmu_tlb_sync_global(smmu);\n\tarm_smmu_gr0_write(smmu, ARM_SMMU_GR0_sCR0, reg);\n\tif (arm_smmu_qsmmuv500_node(smmu))\n\t\ta52_ackfr_record("SMMU parent-reset exit skip=%d scr0=%x",\n\t\t\tskip_init, reg);\n}\n''',
        'reset exit')
    t = ro(t,
        '''ARM_SMMU_MATCH_DATA(qcom_smmuv2, ARM_SMMU_V2, QCOM_SMMUV2);\n''',
        '''ARM_SMMU_MATCH_DATA(qcom_smmuv2, ARM_SMMU_V2, QCOM_SMMUV2);\nARM_SMMU_MATCH_DATA(qcom_qsmmuv500, ARM_SMMU_V2, QCOM_SMMUV2);\n''',
        'match data')
    t = ro(t,
        '''\t{ .compatible = "qcom,smmu-v2", .data = &qcom_smmuv2 },\n\t{ },\n''',
        '''\t{ .compatible = "qcom,smmu-v2", .data = &qcom_smmuv2 },\n\t{ .compatible = "qcom,qsmmu-v500", .data = &qcom_qsmmuv500 },\n\t{ },\n''',
        'of match')
    t = ro(t,
        '''\tdata = of_device_get_match_data(dev);\n\tsmmu->version = data->version;\n\tsmmu->model = data->model;\n''',
        '''\tdata = of_device_get_match_data(dev);\n\tif (!data)\n\t\treturn -ENODEV;\n\tsmmu->version = data->version;\n\tsmmu->model = data->model;\n\tif (of_device_is_compatible(dev->of_node, "qcom,qsmmu-v500"))\n\t\ta52_ackfr_record("SMMU parent-dt version=%d model=%d girq=%u skip=%d lvl3=%d",\n\t\t\tsmmu->version, smmu->model, smmu->num_global_irqs,\n\t\t\tof_property_read_bool(dev->of_node, "qcom,skip-init"),\n\t\t\tof_property_read_bool(dev->of_node,\n\t\t\t\t\t      "qcom,use-3-lvl-tables"));\n''',
        'dt trace')
    t = ro(t,
        '''\tirqreturn_t (*global_fault)(int irq, void *dev);\n\n\tsmmu = devm_kzalloc(dev, sizeof(*smmu), GFP_KERNEL);\n''',
        '''\tirqreturn_t (*global_fault)(int irq, void *dev);\n\tbool trace = dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,qsmmu-v500");\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe enter dev=%s driver=%s",\n\t\t\tdev_name(dev), dev->driver ? dev->driver->name : "-");\n\tsmmu = devm_kzalloc(dev, sizeof(*smmu), GFP_KERNEL);\n''',
        'probe enter')
    t = ro(t,
        '''\tif (err)\n\t\treturn err;\n\n\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n\tif (IS_ERR(smmu->base))\n\t\treturn PTR_ERR(smmu->base);\n''',
        '''\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-dt-probe rc=%d", err);\n\tif (err)\n\t\treturn err;\n\n\tsmmu->base = devm_platform_get_and_ioremap_resource(pdev, 0, &res);\n\tif (IS_ERR(smmu->base)) {\n\t\terr = PTR_ERR(smmu->base);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("SMMU parent-map rc=%d", err);\n\t\treturn err;\n\t}\n''',
        'probe dt/map')
    t = ro(t,
        '''\tsmmu->numpage = resource_size(res);\n\n\tsmmu = arm_smmu_impl_init(smmu);\n\tif (IS_ERR(smmu))\n\t\treturn PTR_ERR(smmu);\n''',
        '''\tsmmu->numpage = resource_size(res);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-map ok base=%pa bytes=%pa",\n\t\t\t&ioaddr, &smmu->numpage);\n\n\tsmmu = arm_smmu_impl_init(smmu);\n\tif (IS_ERR(smmu)) {\n\t\terr = PTR_ERR(smmu);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("SMMU parent-impl rc=%d", err);\n\t\treturn err;\n\t}\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-impl rc=0 impl=%d", !!smmu->impl);\n''',
        'probe impl')
    t = ro(t,
        '''\tsmmu->num_clks = err;\n\n\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n\tif (err)\n\t\treturn err;\n\n\terr = arm_smmu_device_cfg_probe(smmu);\n\tif (err)\n\t\treturn err;\n''',
        '''\tsmmu->num_clks = err;\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-res irqs=%d girq=%u cirq=%u clocks=%d",\n\t\t\tnum_irqs, smmu->num_global_irqs, smmu->num_context_irqs,\n\t\t\tsmmu->num_clks);\n\n\terr = clk_bulk_prepare_enable(smmu->num_clks, smmu->clks);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-clocks rc=%d", err);\n\tif (err)\n\t\treturn err;\n\n\terr = arm_smmu_device_cfg_probe(smmu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-cfg rc=%d feat=%lx groups=%u cbs=%u",\n\t\t\terr, smmu->features, smmu->num_mapping_groups,\n\t\t\tsmmu->num_context_banks);\n\tif (err)\n\t\treturn err;\n''',
        'probe resources/cfg')
    t = ro(t,
        '''\terr = iommu_device_register(&smmu->iommu);\n\tif (err) {\n\t\tdev_err(dev, "Failed to register iommu\\n");\n\t\treturn err;\n\t}\n\n\tplatform_set_drvdata(pdev, smmu);\n\tarm_smmu_device_reset(smmu);\n''',
        '''\terr = iommu_device_register(&smmu->iommu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-register rc=%d", err);\n\tif (err) {\n\t\tdev_err(dev, "Failed to register iommu\\n");\n\t\treturn err;\n\t}\n\n\tplatform_set_drvdata(pdev, smmu);\n\tarm_smmu_device_reset(smmu);\n''',
        'probe register')
    t = ro(t,
        '''\tif (!using_legacy_binding)\n\t\treturn arm_smmu_bus_init(&arm_smmu_ops);\n\n\treturn 0;\n}\n''',
        '''\tif (!using_legacy_binding) {\n\t\terr = arm_smmu_bus_init(&arm_smmu_ops);\n\t\tif (trace)\n\t\t\ta52_ackfr_record("SMMU parent-probe exit rc=%d legacy=0", err);\n\t\treturn err;\n\t}\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-probe exit rc=0 legacy=1");\n\treturn 0;\n}\n''',
        'probe exit')
    return t


def patch_qcom(t):
    if 'SMMU parent-qcom-create scm=%d' in t:
        return t
    t = ro(t,
        '#include <linux/qcom_scm.h>\n',
        '#include <linux/qcom_scm.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'qcom include')
    t = ro(t,
        '''static int qcom_sdm845_smmu500_cfg_probe(struct arm_smmu_device *smmu)\n{\n\tu32 s2cr;\n''',
        '''static int qcom_sdm845_smmu500_cfg_probe(struct arm_smmu_device *smmu)\n{\n\tbool trace = smmu->dev->of_node &&\n\t\tof_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500");\n\tu32 s2cr;\n''',
        'qcom cfg enter var')
    t = ro(t,
        '''\tu32 s2cr;\n\tu32 smr;\n\tint i;\n\n\tfor (i = 0; i < smmu->num_mapping_groups; i++) {\n''',
        '''\tu32 s2cr;\n\tu32 smr;\n\tint i;\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom-cfg enter groups=%u",\n\t\t\tsmmu->num_mapping_groups);\n\tfor (i = 0; i < smmu->num_mapping_groups; i++) {\n''',
        'qcom cfg trace enter')
    t = ro(t,
        '''\treturn 0;\n}\n\n#define QCOM_ADRENO_SMMU_GPU_SID 0\n''',
        '''\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom-cfg exit rc=0");\n\treturn 0;\n}\n\n#define QCOM_ADRENO_SMMU_GPU_SID 0\n''',
        'qcom cfg exit')
    t = ro(t,
        '''\tstruct qcom_smmu *qsmmu;\n\n\t/* Check to make sure qcom_scm has finished probing */\n\tif (!qcom_scm_is_available())\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n''',
        '''\tstruct qcom_smmu *qsmmu;\n\tbool trace = smmu->dev->of_node &&\n\t\tof_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500");\n\tbool scm = qcom_scm_is_available();\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom-create scm=%d", scm);\n\t/* Check to make sure qcom_scm has finished probing */\n\tif (!scm)\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n''',
        'qcom create scm')
    t = ro(t,
        '''\tqsmmu->smmu.impl = impl;\n\tdevm_kfree(smmu->dev, smmu);\n\n\treturn &qsmmu->smmu;\n}\n''',
        '''\tqsmmu->smmu.impl = impl;\n\tdevm_kfree(smmu->dev, smmu);\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom-create rc=0");\n\treturn &qsmmu->smmu;\n}\n''',
        'qcom create exit')
    return t


def apply(root):
    for rel, fn in ((CORE, patch_core), (QCOM, patch_qcom)):
        path = root / rel
        old = path.read_text()
        new = fn(old)
        path.write_text(new)


def self_test(root):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for rel in (CORE, QCOM):
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, dst)
        apply(tmp)
        first = {rel: (tmp / rel).read_text() for rel in (CORE, QCOM)}
        apply(tmp)
        second = {rel: (tmp / rel).read_text() for rel in (CORE, QCOM)}
        assert first == second
        core = first[CORE]
        qcom = first[QCOM]
        for marker in (
            'qcom,qsmmu-v500',
            'SMMU parent-probe enter dev=%s',
            'SMMU parent-reset enter skip=%d',
            'SMMU parent-domain 3lvl ias=%lu',
            'SMMU parent-probe exit rc=%d legacy=0',
        ):
            assert marker in core, marker
        for marker in (
            'SMMU parent-qcom-create scm=%d',
            'SMMU parent-qcom-cfg enter groups=%u',
            'SMMU parent-qcom-create rc=0',
        ):
            assert marker in qcom, marker
        assert core.count('.compatible = "qcom,qsmmu-v500"') == 1
        assert 'if (!skip_init)' in core
        assert 'ias = min(ias, 39UL);' in core
    print('phase203 qsmmuv500 compatibility patcher self-test: PASS')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('phase203 qsmmuv500 compatibility and parent trace applied')

if __name__ == '__main__':
    main()
