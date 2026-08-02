#!/usr/bin/env python3
from pathlib import Path
import argparse
import shutil
import tempfile

IOMMU_H = Path('include/linux/iommu.h')
SMMU_H = Path('drivers/iommu/arm/arm-smmu/arm-smmu.h')
SMMU_C = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
DMA_IOMMU_C = Path('drivers/iommu/dma-iommu.c')
MSM_SMMU_C = Path('drivers/a52_display/msm/msm_smmu.c')
FILES = (IOMMU_H, SMMU_H, SMMU_C, DMA_IOMMU_C, MSM_SMMU_C)


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_iommu_h(text: str) -> str:
    if 'DOMAIN_ATTR_NON_FATAL_FAULTS' in text:
        return text
    # Append the new compatibility attribute immediately before MAX so all
    # existing vendor attribute numbers remain unchanged.
    old = '''\tDOMAIN_ATTR_USE_UPSTREAM_HINT,\n\tDOMAIN_ATTR_MAX,\n'''
    new = '''\tDOMAIN_ATTR_USE_UPSTREAM_HINT,\n\tDOMAIN_ATTR_NON_FATAL_FAULTS,\n\tDOMAIN_ATTR_MAX,\n'''
    return replace_one(text, old, new, 'IOMMU non-fatal attribute enum')


def patch_smmu_h(text: str) -> str:
    if 'unsigned long\t\t\tattributes;' in text:
        return text
    old = '''\tenum arm_smmu_domain_stage\tstage;\n\tbool\t\t\t\tnon_strict;\n'''
    new = '''\tenum arm_smmu_domain_stage\tstage;\n\tunsigned long\t\t\tattributes;\n\tbool\t\t\t\tnon_strict;\n'''
    return replace_one(text, old, new, 'ARM SMMU domain attribute storage')


def patch_smmu_c(text: str) -> str:
    if 'a52_arm_smmu_apply_dt_domain_attrs' in text:
        return text

    # The downstream qcom,qsmmuv500-tbu backend is not yet ported. Keep the
    # parent and its bootloader-programmed TBUs out of power collapse and reject
    # system suspend instead of losing translation state silently.
    old = '''static bool a52_apps_smmu(const struct device *dev)\n{\n\treturn dev && dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,qsmmu-v500");\n}\n\n'''
    new = '''static bool a52_apps_smmu(const struct device *dev)\n{\n\treturn dev && dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,qsmmu-v500");\n}\n\nstatic bool a52_apps_smmu_has_unmanaged_tbus(const struct device *dev)\n{\n\tstruct device_node *child;\n\n\tif (!a52_apps_smmu(dev))\n\t\treturn false;\n\n\tfor_each_available_child_of_node(dev->of_node, child) {\n\t\tif (of_device_is_compatible(child, "qcom,qsmmuv500-tbu")) {\n\t\t\tof_node_put(child);\n\t\t\treturn true;\n\t\t}\n\t}\n\n\treturn false;\n}\n\n'''
    text = replace_one(text, old, new, 'Apps SMMU unmanaged TBU detector')

    old = '''static struct arm_smmu_domain *to_smmu_domain(struct iommu_domain *dom)\n{\n\treturn container_of(dom, struct arm_smmu_domain, domain);\n}\n\n'''
    new = '''static struct arm_smmu_domain *to_smmu_domain(struct iommu_domain *dom)\n{\n\treturn container_of(dom, struct arm_smmu_domain, domain);\n}\n\nstatic struct arm_smmu_domain *cfg_to_smmu_domain(struct arm_smmu_cfg *cfg)\n{\n\treturn container_of(cfg, struct arm_smmu_domain, cfg);\n}\n\n'''
    text = replace_one(text, old, new, 'ARM SMMU cfg-to-domain helper')

    old = '''\t/* SCTLR */\n\treg = ARM_SMMU_SCTLR_CFIE | ARM_SMMU_SCTLR_CFRE | ARM_SMMU_SCTLR_AFE |\n\t      ARM_SMMU_SCTLR_TRE | ARM_SMMU_SCTLR_M;\n\tif (stage1)\n\t\treg |= ARM_SMMU_SCTLR_S1_ASIDPNE;\n'''
    new = '''\t/* SCTLR */\n\treg = ARM_SMMU_SCTLR_CFIE | ARM_SMMU_SCTLR_CFRE | ARM_SMMU_SCTLR_AFE |\n\t      ARM_SMMU_SCTLR_TRE;\n\t/*\n\t * TouchGrass leaves stage-1 translation disabled while the DT requests\n\t * qcom,iommu-earlymap, preserving the live bootloader context until KMS\n\t * has installed the splash mappings and explicitly clears the attribute.\n\t */\n\tif (!stage1 || !(cfg_to_smmu_domain(cfg)->attributes &\n\t\t\tBIT(DOMAIN_ATTR_EARLY_MAP)))\n\t\treg |= ARM_SMMU_SCTLR_M;\n\tif (stage1)\n\t\treg |= ARM_SMMU_SCTLR_S1_ASIDPNE;\n'''
    text = replace_one(text, old, new, 'ARM SMMU early-map SCTLR gate')

    old = '''static int arm_smmu_attach_dev(struct iommu_domain *domain, struct device *dev)\n{\n'''
    helper = '''static void a52_arm_smmu_apply_dt_domain_attrs(\n\t\tstruct arm_smmu_domain *smmu_domain, struct device *dev)\n{\n\tstruct device_node *np;\n\n\tif (!dev->of_node)\n\t\treturn;\n\n\tnp = of_parse_phandle(dev->of_node, "qcom,iommu-group", 0);\n\tif (!np)\n\t\tnp = of_node_get(dev->of_node);\n\n\tif (of_property_read_bool(np, "qcom,iommu-earlymap"))\n\t\tsmmu_domain->attributes |= BIT(DOMAIN_ATTR_EARLY_MAP);\n\n\t/* Upstream faults are already non-fatal. Keep the downstream DT/API\n\t * contract explicit so clients can query it without changing policy.\n\t */\n\tif (of_property_match_string(np, "qcom,iommu-faults",\n\t\t\t\t     "non-fatal") >= 0)\n\t\tsmmu_domain->attributes |= BIT(DOMAIN_ATTR_NON_FATAL_FAULTS);\n\n\tof_node_put(np);\n}\n\nstatic int arm_smmu_attach_dev(struct iommu_domain *domain, struct device *dev)\n{\n'''
    text = replace_one(text, old, helper, 'ARM SMMU DT domain attribute parser')

    old = '''\tcfg = dev_iommu_priv_get(dev);\n\tif (!cfg)\n\t\treturn -ENODEV;\n\n\tsmmu = cfg->smmu;\n\n\tret = arm_smmu_rpm_get(smmu);\n'''
    new = '''\tcfg = dev_iommu_priv_get(dev);\n\tif (!cfg)\n\t\treturn -ENODEV;\n\n\tsmmu = cfg->smmu;\n\ta52_arm_smmu_apply_dt_domain_attrs(smmu_domain, dev);\n\n\tret = arm_smmu_rpm_get(smmu);\n'''
    text = replace_one(text, old, new, 'ARM SMMU DT attributes before attach')

    old = '''static int arm_smmu_domain_get_attr(struct iommu_domain *domain,\n\t\t\t\t    enum iommu_attr attr, void *data)\n{\n'''
    helper = '''static int arm_smmu_enable_s1_translations(\n\t\tstruct arm_smmu_domain *smmu_domain)\n{\n\tstruct arm_smmu_device *smmu = smmu_domain->smmu;\n\tu32 reg;\n\tint ret;\n\n\tif (!smmu)\n\t\treturn 0;\n\n\tret = arm_smmu_rpm_get(smmu);\n\tif (ret < 0)\n\t\treturn ret;\n\n\treg = arm_smmu_cb_read(smmu, smmu_domain->cfg.cbndx,\n\t\t\t       ARM_SMMU_CB_SCTLR);\n\treg |= ARM_SMMU_SCTLR_M;\n\tarm_smmu_cb_write(smmu, smmu_domain->cfg.cbndx,\n\t\t\t  ARM_SMMU_CB_SCTLR, reg);\n\tarm_smmu_rpm_put(smmu);\n\n\treturn 0;\n}\n\nstatic int arm_smmu_domain_get_attr(struct iommu_domain *domain,\n\t\t\t\t    enum iommu_attr attr, void *data)\n{\n'''
    text = replace_one(text, old, helper, 'ARM SMMU enable stage-1 helper')

    old = '''\tstruct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);\n\n\tswitch(domain->type) {\n'''
    new = '''\tstruct arm_smmu_domain *smmu_domain = to_smmu_domain(domain);\n\n\tswitch (attr) {\n\tcase DOMAIN_ATTR_EARLY_MAP:\n\tcase DOMAIN_ATTR_NON_FATAL_FAULTS:\n\t\t*(int *)data = !!(smmu_domain->attributes & BIT(attr));\n\t\treturn 0;\n\tdefault:\n\t\tbreak;\n\t}\n\n\tswitch(domain->type) {\n'''
    text = replace_one(text, old, new, 'ARM SMMU get downstream attributes')

    old = '''\tmutex_lock(&smmu_domain->init_mutex);\n\n\tswitch(domain->type) {\n'''
    new = '''\tmutex_lock(&smmu_domain->init_mutex);\n\n\tswitch (attr) {\n\tcase DOMAIN_ATTR_EARLY_MAP:\n\t\tif (*(int *)data) {\n\t\t\tsmmu_domain->attributes |= BIT(DOMAIN_ATTR_EARLY_MAP);\n\t\t} else {\n\t\t\tret = arm_smmu_enable_s1_translations(smmu_domain);\n\t\t\tif (!ret)\n\t\t\t\tsmmu_domain->attributes &=\n\t\t\t\t\t~BIT(DOMAIN_ATTR_EARLY_MAP);\n\t\t}\n\t\tgoto out_unlock;\n\tcase DOMAIN_ATTR_NON_FATAL_FAULTS:\n\t\tif (*(int *)data)\n\t\t\tsmmu_domain->attributes |=\n\t\t\t\tBIT(DOMAIN_ATTR_NON_FATAL_FAULTS);\n\t\telse\n\t\t\tsmmu_domain->attributes &=\n\t\t\t\t~BIT(DOMAIN_ATTR_NON_FATAL_FAULTS);\n\t\tgoto out_unlock;\n\tdefault:\n\t\tbreak;\n\t}\n\n\tswitch(domain->type) {\n'''
    text = replace_one(text, old, new, 'ARM SMMU set downstream attributes')

    old = '''\tif (dev->pm_domain) {\n\t\tpm_runtime_set_active(dev);\n\t\tpm_runtime_enable(dev);\n\t}\n'''
    new = '''\tif (dev->pm_domain && !a52_apps_smmu_has_unmanaged_tbus(dev)) {\n\t\tpm_runtime_set_active(dev);\n\t\tpm_runtime_enable(dev);\n\t} else if (a52_apps_smmu_has_unmanaged_tbus(dev)) {\n\t\tdev_warn(dev,\n\t\t\t "runtime PM disabled until qsmmuv500 TBU support is ported\\n");\n\t}\n'''
    text = replace_one(text, old, new, 'Apps SMMU runtime-PM fail-closed policy')

    old = '''static int __maybe_unused arm_smmu_runtime_suspend(struct device *dev)\n{\n\tstruct arm_smmu_device *smmu = dev_get_drvdata(dev);\n\n\tclk_bulk_disable(smmu->num_clks, smmu->clks);\n\n\treturn 0;\n}\n'''
    new = '''static int __maybe_unused arm_smmu_runtime_suspend(struct device *dev)\n{\n\tstruct arm_smmu_device *smmu = dev_get_drvdata(dev);\n\n\tif (a52_apps_smmu_has_unmanaged_tbus(dev))\n\t\treturn -EBUSY;\n\n\tclk_bulk_disable(smmu->num_clks, smmu->clks);\n\n\treturn 0;\n}\n'''
    text = replace_one(text, old, new, 'Apps SMMU runtime suspend guard')

    old = '''static int __maybe_unused arm_smmu_pm_suspend(struct device *dev)\n{\n\tif (pm_runtime_suspended(dev))\n\t\treturn 0;\n\n\treturn arm_smmu_runtime_suspend(dev);\n}\n'''
    new = '''static int __maybe_unused arm_smmu_pm_suspend(struct device *dev)\n{\n\tif (a52_apps_smmu_has_unmanaged_tbus(dev)) {\n\t\tdev_warn(dev,\n\t\t\t "system suspend blocked until qsmmuv500 TBU support is ported\\n");\n\t\treturn -EBUSY;\n\t}\n\n\tif (pm_runtime_suspended(dev))\n\t\treturn 0;\n\n\treturn arm_smmu_runtime_suspend(dev);\n}\n'''
    text = replace_one(text, old, new, 'Apps SMMU system suspend guard')

    return text


def patch_dma_iommu_c(text: str) -> str:
    if 'a52_iommu_get_dma_window' in text:
        return text

    if '#include <linux/of.h>\n' not in text:
        anchor = '#include <linux/mutex.h>\n'
        text = replace_one(text, anchor, anchor + '#include <linux/of.h>\n',
                           'DMA IOMMU OF include')

    old = '''void iommu_setup_dma_ops(struct device *dev, u64 dma_base, u64 size)\n{\n'''
    helper = '''/*\n * Preserve the downstream Qualcomm DMA aperture contract. The display\n * context-bank nodes use this to reserve IOVA 0 and keep all mappings below\n * 4 GiB: qcom,iommu-dma-addr-pool = <0x20000 0xfffe0000>.\n */\nstatic int a52_iommu_get_dma_window(struct device *dev, u64 *dma_base,\n\t\t\t\t    u64 *size)\n{\n\tstruct device_node *np;\n\tconst __be32 *ranges;\n\tint naddr, nsize, len;\n\n\tif (!dev->of_node)\n\t\treturn 0;\n\n\tnp = of_parse_phandle(dev->of_node, "qcom,iommu-group", 0);\n\tif (!np)\n\t\tnp = of_node_get(dev->of_node);\n\n\tranges = of_get_property(np, "qcom,iommu-dma-addr-pool", &len);\n\tif (!ranges) {\n\t\tof_node_put(np);\n\t\treturn 0;\n\t}\n\n\tlen /= sizeof(*ranges);\n\tnaddr = of_n_addr_cells(np);\n\tnsize = of_n_size_cells(np);\n\tif (!naddr || !nsize || len < naddr + nsize) {\n\t\tdev_err(dev, "invalid qcom,iommu-dma-addr-pool cells\\n");\n\t\tof_node_put(np);\n\t\treturn -EINVAL;\n\t}\n\n\t*dma_base = of_read_number(ranges, naddr);\n\t*size = of_read_number(ranges + naddr, nsize);\n\tof_node_put(np);\n\n\tif (!*size || *dma_base + *size < *dma_base) {\n\t\tdev_err(dev, "invalid qcom,iommu-dma-addr-pool range\\n");\n\t\treturn -EINVAL;\n\t}\n\n\treturn 0;\n}\n\nvoid iommu_setup_dma_ops(struct device *dev, u64 dma_base, u64 size)\n{\n'''
    text = replace_one(text, old, helper, 'DMA IOMMU aperture helper')

    old = '''\tif (domain->type == IOMMU_DOMAIN_DMA) {\n\t\tif (iommu_dma_init_domain(domain, dma_base, size, dev))\n'''
    new = '''\tif (domain->type == IOMMU_DOMAIN_DMA) {\n\t\tif (a52_iommu_get_dma_window(dev, &dma_base, &size))\n\t\t\tgoto out_err;\n\t\tif (iommu_dma_init_domain(domain, dma_base, size, dev))\n'''
    text = replace_one(text, old, new, 'DMA IOMMU aperture application')
    return text


def patch_msm_smmu_c(text: str) -> str:
    if 'secure display SMMU is fail-closed' in text:
        return text
    old = '''\tstruct msm_smmu *smmu;\n\tstruct device *client_dev;\n\n\tsmmu = kzalloc(sizeof(*smmu), GFP_KERNEL);\n'''
    new = '''\tstruct msm_smmu *smmu;\n\tstruct device *client_dev;\n\n\t/*\n\t * TouchGrass secure domains require VMID ownership transfer and secure\n\t * page-table assignment through SCM. The 5.10 adaptation does not yet\n\t * carry that backend, so secure display SMMU is fail-closed rather than\n\t * silently attached as an ordinary non-secure domain.\n\t */\n\tif (domain == MSM_SMMU_DOMAIN_SECURE ||\n\t    domain == MSM_SMMU_DOMAIN_NRT_SECURE) {\n\t\ta52_ackfr_record("SMMU secure-domain unavailable domain=%d", domain);\n\t\treturn ERR_PTR(-EOPNOTSUPP);\n\t}\n\n\tsmmu = kzalloc(sizeof(*smmu), GFP_KERNEL);\n'''
    return replace_one(text, old, new, 'Display secure-domain fail-closed gate')


def apply(root: Path) -> None:
    patchers = {
        IOMMU_H: patch_iommu_h,
        SMMU_H: patch_smmu_h,
        SMMU_C: patch_smmu_c,
        DMA_IOMMU_C: patch_dma_iommu_c,
        MSM_SMMU_C: patch_msm_smmu_c,
    }
    for rel, patcher in patchers.items():
        path = root / rel
        path.write_text(patcher(path.read_text()))


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for rel in FILES:
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, target)

        iommu_before = (tmp / IOMMU_H).read_text()
        enum_before = iommu_before.split('enum iommu_attr {', 1)[1].split('};', 1)[0]
        attrs_before = [line.strip().rstrip(',').split()[0]
                        for line in enum_before.splitlines()
                        if line.strip().startswith('DOMAIN_ATTR_')]

        apply(tmp)
        once = {rel: (tmp / rel).read_text() for rel in FILES}
        enum_after = once[IOMMU_H].split('enum iommu_attr {', 1)[1].split('};', 1)[0]
        attrs_after = [line.strip().rstrip(',').split()[0]
                       for line in enum_after.splitlines()
                       if line.strip().startswith('DOMAIN_ATTR_')]
        for name in attrs_before:
            if name == 'DOMAIN_ATTR_MAX':
                continue
            if attrs_before.index(name) != attrs_after.index(name):
                raise AssertionError(f'IOMMU attribute ABI moved: {name}')
        if attrs_after[-2:] != ['DOMAIN_ATTR_NON_FATAL_FAULTS',
                                'DOMAIN_ATTR_MAX']:
            raise AssertionError('non-fatal attribute is not appended before MAX')
        apply(tmp)
        twice = {rel: (tmp / rel).read_text() for rel in FILES}
        if once != twice:
            raise AssertionError('phase206 patcher is not idempotent')

        combined = '\n'.join(once.values())
        required = (
            'DOMAIN_ATTR_NON_FATAL_FAULTS',
            'unsigned long\t\t\tattributes;',
            'cfg_to_smmu_domain',
            'a52_apps_smmu_has_unmanaged_tbus',
            'system suspend blocked until qsmmuv500 TBU support is ported',
            'a52_arm_smmu_apply_dt_domain_attrs',
            'qcom,iommu-earlymap',
            'arm_smmu_enable_s1_translations',
            '~BIT(DOMAIN_ATTR_EARLY_MAP)',
            'a52_iommu_get_dma_window',
            'qcom,iommu-dma-addr-pool',
            'secure display SMMU is fail-closed',
            'return ERR_PTR(-EOPNOTSUPP);',
        )
        for marker in required:
            if marker not in combined:
                raise AssertionError(marker)
        smmu = once[SMMU_C]
        if 'ARM_SMMU_SCTLR_TRE | ARM_SMMU_SCTLR_M' in smmu:
            raise AssertionError('unconditional SCTLR.M remains')
        if smmu.count('a52_arm_smmu_apply_dt_domain_attrs(smmu_domain, dev);') != 1:
            raise AssertionError('DT domain attributes not applied exactly once')
    print('phase206 display SMMU contracts self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('phase206 display SMMU contracts applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
