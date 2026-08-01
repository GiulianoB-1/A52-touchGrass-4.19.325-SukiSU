#!/usr/bin/env python3
from pathlib import Path
import argparse
import shutil
import tempfile

CORE = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_core(text: str) -> str:
    if 'SMMU audit %s present=%d bound=%s match=%d ofmatch=%d' in text:
        return text

    text = replace_one(
        text,
        '''static bool a52_apps_smmu(const struct device *dev)\n{\n\treturn dev && dev->of_node &&\n\t\tof_device_is_compatible(dev->of_node, "qcom,qsmmu-v500");\n}\n''',
        '''#define A52_APPS_SMMU_NAME "15000000.apps-smmu"\n\nstatic bool a52_apps_smmu(const struct device *dev)\n{\n\treturn dev && ((!strcmp(dev_name(dev), A52_APPS_SMMU_NAME)) ||\n\t\t(dev->of_node &&\n\t\t of_device_is_compatible(dev->of_node, "qcom,qsmmu-v500")));\n}\n''',
        'Apps SMMU trace selector',
    )

    text = replace_one(
        text,
        '''static struct platform_driver arm_smmu_driver = {\n\t.driver\t= {\n\t\t.name\t\t\t= "arm-smmu",\n\t\t.of_match_table\t\t= arm_smmu_of_match,\n\t\t.pm\t\t\t= &arm_smmu_pm_ops,\n\t\t.suppress_bind_attrs    = true,\n\t},\n\t.probe\t= arm_smmu_device_probe,\n\t.remove\t= arm_smmu_device_remove,\n\t.shutdown = arm_smmu_device_shutdown,\n};\nmodule_platform_driver(arm_smmu_driver);\n''',
        '''static struct platform_driver arm_smmu_driver = {\n\t.driver\t= {\n\t\t.name\t\t\t= "arm-smmu",\n\t\t.of_match_table\t\t= arm_smmu_of_match,\n\t\t.pm\t\t\t= &arm_smmu_pm_ops,\n\t\t.suppress_bind_attrs    = true,\n\t},\n\t.probe\t= arm_smmu_device_probe,\n\t.remove\t= arm_smmu_device_remove,\n\t.shutdown = arm_smmu_device_shutdown,\n};\n\nstatic void a52_arm_smmu_audit(const char *stage, bool test_match)\n{\n\tstruct device *dev;\n\tstruct property *prop;\n\tconst struct of_device_id *ofmatch = NULL;\n\tconst char *compat;\n\tint compat_index = 0;\n\tint match = -ENODEV;\n\n\tdev = bus_find_device_by_name(&platform_bus_type, NULL,\n\t\tA52_APPS_SMMU_NAME);\n\tif (!dev) {\n\t\ta52_ackfr_record("SMMU audit %s present=0", stage);\n\t\treturn;\n\t}\n\n\tif (dev->of_node)\n\t\tofmatch = of_match_device(arm_smmu_of_match, dev);\n\tif (test_match)\n\t\tmatch = driver_match_device(&arm_smmu_driver.driver, dev);\n\n\ta52_ackfr_record(\n\t\t"SMMU audit %s present=%d bound=%s match=%d ofmatch=%d",\n\t\tstage, 1, dev->driver ? dev->driver->name : "-", match,\n\t\t!!ofmatch);\n\tif (dev->of_node) {\n\t\tof_property_for_each_string(dev->of_node, "compatible", prop, compat) {\n\t\t\ta52_ackfr_record("SMMU audit %s compat%d=%s", stage,\n\t\t\t\tcompat_index, compat);\n\t\t\tif (++compat_index == 4)\n\t\t\t\tbreak;\n\t\t}\n\t}\n\tput_device(dev);\n}\n\nstatic int __init arm_smmu_init(void)\n{\n\tint ret;\n\n\ta52_ackfr_record("SMMU arm-init enter");\n\ta52_arm_smmu_audit("pre", false);\n\tret = platform_driver_register(&arm_smmu_driver);\n\ta52_ackfr_record("SMMU arm-init register rc=%d", ret);\n\tif (!ret)\n\t\ta52_arm_smmu_audit("post", true);\n\treturn ret;\n}\nmodule_init(arm_smmu_init);\n\n#ifndef MODULE\nstatic int __init a52_arm_smmu_late_audit(void)\n{\n\ta52_arm_smmu_audit("late", true);\n\treturn 0;\n}\nlate_initcall_sync(a52_arm_smmu_late_audit);\n#endif\n\nstatic void __exit arm_smmu_exit(void)\n{\n\tplatform_driver_unregister(&arm_smmu_driver);\n}\nmodule_exit(arm_smmu_exit);\n''',
        'driver registration wrapper',
    )
    return text


def apply(root: Path) -> None:
    path = root / CORE
    path.write_text(patch_core(path.read_text()))


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        target = temp / CORE
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / CORE, target)
        apply(temp)
        once = target.read_text()
        apply(temp)
        twice = target.read_text()
        if once != twice:
            raise AssertionError('patcher is not idempotent')
        required = (
            '#define A52_APPS_SMMU_NAME "15000000.apps-smmu"',
            'SMMU arm-init enter',
            'SMMU arm-init register rc=%d',
            'SMMU audit %s present=%d bound=%s match=%d ofmatch=%d',
            'SMMU audit %s compat%d=%s',
            'bus_find_device_by_name(&platform_bus_type',
            'driver_match_device(&arm_smmu_driver.driver, dev)',
            'of_match_device(arm_smmu_of_match, dev)',
            'late_initcall_sync(a52_arm_smmu_late_audit);',
            'module_init(arm_smmu_init);',
            'module_exit(arm_smmu_exit);',
        )
        for marker in required:
            if marker not in twice:
                raise AssertionError(marker)
        if 'module_platform_driver(arm_smmu_driver);' in twice:
            raise AssertionError('legacy registration macro remains')
        if twice.count('platform_driver_register(&arm_smmu_driver)') != 1:
            raise AssertionError('unexpected registration call count')
    print('phase204 Apps SMMU registration audit self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('phase204 Apps SMMU registration audit applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
