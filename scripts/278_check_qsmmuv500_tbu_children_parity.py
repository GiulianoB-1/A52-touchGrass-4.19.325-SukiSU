#!/usr/bin/env python3
from pathlib import Path
import argparse
import re

SMMU_C = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
MARKER = 'A52_PHASE278_QSMMUV500_TBU_CHILDREN_PARITY_V1'
END_ANCHOR = 'static inline int arm_smmu_rpm_get(struct arm_smmu_device *smmu)'


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit('Phase278 checker: ' + msg)


def function_body(text: str, signature: str) -> str:
    start = text.find(signature)
    require(start >= 0, f'missing function signature: {signature}')
    brace = text.find('{', start)
    require(brace >= 0, f'missing opening brace: {signature}')
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    raise SystemExit('Phase278 checker: unterminated function: ' + signature)


def check(root: Path) -> None:
    path = root / SMMU_C
    require(path.is_file(), f'missing reconstructed source: {path}')
    text = path.read_text()

    require(MARKER in text, 'Phase278 marker missing')
    require('A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1' in text,
            'Phase277 ACTLR marker was lost')
    require('#include <linux/of_platform.h>' in text,
            'of_platform API include missing')

    probe = function_body(text,
        'static int a52_qsmmuv500_tbu_probe(struct platform_device *pdev)')
    require('platform_get_resource_byname(pdev, IORESOURCE_MEM, "base")' in probe,
            'TBU base resource is not consumed')
    require('platform_get_resource_byname(pdev, IORESOURCE_MEM, "status-reg")' in probe,
            'TBU status-reg resource is not consumed')
    require('of_property_read_u32_array(dev->of_node,' in probe and
            '"qcom,stream-id-range", range, 2' in probe,
            'qcom,stream-id-range is not parsed as two cells')
    require('platform_set_drvdata(pdev, tbu);' in probe,
            'TBU probe does not publish drvdata')

    match = re.search(
        r'static const struct of_device_id a52_qsmmuv500_tbu_of_match\[\]\s*=\s*\{(?P<body>.*?)\n\};',
        text, re.S)
    require(bool(match), 'TBU OF match table missing')
    require('.compatible = "qcom,qsmmuv500-tbu"' in match.group('body'),
            'exact Golden TBU compatible missing')

    driver = re.search(
        r'static struct platform_driver a52_qsmmuv500_tbu_driver\s*=\s*\{(?P<body>.*?)\n\};',
        text, re.S)
    require(bool(driver), 'TBU platform_driver missing')
    dbody = driver.group('body')
    require('.of_match_table = a52_qsmmuv500_tbu_of_match' in dbody,
            'TBU OF table not wired to platform_driver')
    require('.probe = a52_qsmmuv500_tbu_probe' in dbody,
            'TBU probe not wired to platform_driver')
    require('.name = "qsmmuv500-tbu"' in dbody,
            'TBU driver name differs from Golden')

    bind = function_body(text,
        'static int a52_qsmmuv500_bind_tbu_child(struct device *dev, void *cookie)')
    require('of_device_is_compatible(dev->of_node, "qcom,qsmmuv500-tbu")' in bind,
            'child validator is not limited to exact TBU compatible')
    require('if (!dev->driver)' in bind,
            'child validator does not reject unbound TBU children')
    require('tbu = dev_get_drvdata(dev);' in bind,
            'child validator does not consume bound drvdata')
    require('tbu->smmu = ctx->smmu;' in bind,
            'child is not associated with parent SMMU')

    populate = function_body(text,
        'static int a52_qsmmuv500_populate_tbus(struct arm_smmu_device *smmu)')
    guard = populate.find('if (!a52_apps_smmu(smmu->dev))')
    populate_call = populate.find(
        'of_platform_populate(smmu->dev->of_node, NULL, NULL, smmu->dev)')
    child_walk = populate.find('device_for_each_child(smmu->dev, &ctx,')
    require(0 <= guard < populate_call < child_walk,
            'A52 guard/populate/child-validation ordering is wrong')
    require('return -EPROBE_DEFER;' in populate,
            'parent does not defer when a required TBU child is unbound')
    require('ctx.bound' in populate,
            'no-child/bound-child result is not explicitly observable')

    parent = function_body(text,
        'static int arm_smmu_device_probe(struct platform_device *pdev)')
    require('err = a52_qsmmuv500_populate_tbus(smmu);' in parent,
            'A52 parent probe does not invoke TBU lifecycle')
    cfg = parent.find('err = arm_smmu_device_cfg_probe(smmu);')
    irq = parent.find('err = devm_request_irq(smmu->dev, smmu->irqs[i],')
    tbu = parent.find('err = a52_qsmmuv500_populate_tbus(smmu);')
    sysfs = parent.find('err = iommu_device_sysfs_add(&smmu->iommu')
    register = parent.find('iommu_device_register(&smmu->iommu)')
    require(0 <= cfg < irq < tbu < sysfs < register,
            'parent cfg/IRQ/TBU/sysfs/IOMMU registration ordering is wrong')
    tbu_error = parent[parent.find('err = a52_qsmmuv500_populate_tbus(smmu);'):sysfs]
    require('clk_bulk_disable(smmu->num_clks, smmu->clks);' in tbu_error and
            'a52_arm_smmu_disable_gdscs(smmu);' in tbu_error and
            'clk_bulk_unprepare(smmu->num_clks, smmu->clks);' in tbu_error,
            'TBU parent-defer path does not release manual clock/GDSC votes')
    require(tbu_error.find('clk_bulk_disable(smmu->num_clks, smmu->clks);') <
            tbu_error.find('a52_arm_smmu_disable_gdscs(smmu);') <
            tbu_error.find('clk_bulk_unprepare(smmu->num_clks, smmu->clks);') <
            tbu_error.find('return err;'),
            'TBU parent-defer cleanup ordering is wrong')

    init = function_body(text, 'static int __init a52_arm_smmu_driver_init(void)')
    tbu_reg = init.find('platform_driver_register(&a52_qsmmuv500_tbu_driver);')
    parent_reg = init.find('platform_driver_register(&arm_smmu_driver);')
    require(0 <= tbu_reg < parent_reg,
            'TBU driver is not registered before parent driver')
    require('platform_driver_unregister(&a52_qsmmuv500_tbu_driver);' in init,
            'parent-register failure does not roll back TBU driver')
    require('module_platform_driver(arm_smmu_driver);' not in text,
            'old one-driver registration macro remains active')

    exit_body = function_body(text,
        'static void __exit a52_arm_smmu_driver_exit(void)')
    require(exit_body.find('platform_driver_unregister(&arm_smmu_driver);') <
            exit_body.find('platform_driver_unregister(&a52_qsmmuv500_tbu_driver);'),
            'driver unregister order is not parent then child')

    # Phase278 deliberately does not alter the prior conservative PM policy.
    require('runtime PM disabled until qsmmuv500 TBU support is ported' in text,
            'Phase278 unexpectedly changed the inherited runtime-PM guard')
    require('system suspend blocked until qsmmuv500 TBU support is ported' in text,
            'Phase278 unexpectedly changed the inherited system-PM guard')

    start = text.index(MARKER)
    end = text.index(END_ANCHOR, start)
    added_block = text[start:end]
    forbidden = (
        'debugfs_create_', 'qsmmuv500_ecats', 'capture_bus_match',
        'devm_request_threaded_irq', 'arm_smmu_debug_',
    )
    for token in forbidden:
        require(token not in added_block,
                f'forbidden debug/ECATS functionality imported: {token}')

    attach = function_body(text,
        'static int arm_smmu_attach_dev(struct iommu_domain *domain, struct device *dev)')
    require('ret = arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);' in attach,
            'Phase277 master attach anchor missing')
    require('a52_arm_smmu_apply_display_actlr(smmu_domain, fwspec, dev);' in attach,
            'Phase277 ACTLR call missing after Phase278')

    print('Phase278 QSMMUv500 TBU child lifecycle checker: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    args = ap.parse_args()
    check(args.root)


if __name__ == '__main__':
    main()
