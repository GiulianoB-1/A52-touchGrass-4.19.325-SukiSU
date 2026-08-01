#!/usr/bin/env python3
from pathlib import Path
import argparse
import shutil
import tempfile

QCOM = Path('drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c')


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_qcom(text: str) -> str:
    if 'SMMU parent-qcom scm=%d handoff=%d' in text:
        return text

    old = '''\tbool trace = smmu->dev->of_node &&\n\t\tof_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500");\n\tbool scm = qcom_scm_is_available();\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom scm=%d", scm);\n\t/* Check to make sure qcom_scm has finished probing */\n\tif (!scm)\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n'''
    new = '''\tbool trace = smmu->dev->of_node &&\n\t\tof_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500");\n\tbool downstream_handoff = trace &&\n\t\tof_property_read_bool(smmu->dev->of_node, "qcom,skip-init");\n\tbool scm = qcom_scm_is_available();\n\n\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-qcom scm=%d handoff=%d",\n\t\t\tscm, downstream_handoff);\n\t/*\n\t * The downstream Lagoon DT has no qcom,scm platform device.  Its\n\t * qcom,qsmmu-v500 node instead requests qcom,skip-init so the live\n\t * bootloader mappings are preserved.  In that exact handoff mode the\n\t * Qualcomm implementation neither resets the SMMU nor issues an SCM\n\t * operation, so waiting for the upstream SCM platform device creates\n\t * a permanent dependency which does not exist in the downstream\n\t * TouchGrass driver.  Keep the normal SCM gate for every other QCOM\n\t * SMMU configuration.\n\t */\n\tif (!scm && !downstream_handoff)\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n'''
    return replace_one(text, old, new, 'Qualcomm SCM availability gate')


def apply(root: Path) -> None:
    path = root / QCOM
    path.write_text(patch_qcom(path.read_text()))


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        target = tmp / QCOM
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / QCOM, target)
        apply(tmp)
        once = target.read_text()
        apply(tmp)
        twice = target.read_text()
        if once != twice:
            raise AssertionError('patcher is not idempotent')
        required = (
            'bool downstream_handoff = trace &&',
            'of_property_read_bool(smmu->dev->of_node, "qcom,skip-init")',
            'SMMU parent-qcom scm=%d handoff=%d',
            'if (!scm && !downstream_handoff)',
            'return ERR_PTR(-EPROBE_DEFER);',
            'The downstream Lagoon DT has no qcom,scm platform device.',
        )
        for marker in required:
            if marker not in twice:
                raise AssertionError(marker)
        if twice.count('if (!scm && !downstream_handoff)') != 1:
            raise AssertionError('unexpected conditional SCM gate count')
        if '\tif (!scm)\n\t\treturn ERR_PTR(-EPROBE_DEFER);' in twice:
            raise AssertionError('unconditional SCM gate remains')
    print('phase204 Lagoon SCM-less SMMU handoff self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('phase204 Lagoon SCM-less SMMU handoff applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
