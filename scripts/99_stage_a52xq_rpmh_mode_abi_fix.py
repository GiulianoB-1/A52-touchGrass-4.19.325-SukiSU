#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


OLD_INCLUDE = '#include <dt-bindings/regulator/qcom,rpmh-regulator.h>\n'
NEW_INCLUDE = '''#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>

/*
 * The Samsung downstream DT uses the downstream five-value mode ABI:
 * PASS=0, RET=1, LPM=2, AUTO=3, HPM=4.  Android common 5.10's upstream
 * qcom,rpmh-regulator.h instead uses RET=0, LPM=1, AUTO=2, HPM=3.
 * Mixing those headers makes valid PMIC5 LDO mode 2 look like AUTO and causes
 * every ldoe* aggregate provider to fail with -EINVAL.
 */
#if RPMH_REGULATOR_MODE_PASS != 0 || \
    RPMH_REGULATOR_MODE_RET != 1 || \
    RPMH_REGULATOR_MODE_LPM != 2 || \
    RPMH_REGULATOR_MODE_AUTO != 3 || \
    RPMH_REGULATOR_MODE_HPM != 4
#error "A52 downstream RPMh regulator mode ABI mismatch"
#endif
'''

OLD_INIT = '''static int rpmh_regulator_init(void)
{
	return platform_driver_register(&rpmh_regulator_driver);
}'''

NEW_INIT = '''static int rpmh_regulator_init(void)
{
	a52_persistent_diag_mark(
		"A52RPMHABI copy=1 pass=%d ret=%d lpm=%d auto=%d hpm=%d count=%d\\n",
		RPMH_REGULATOR_MODE_PASS, RPMH_REGULATOR_MODE_RET,
		RPMH_REGULATOR_MODE_LPM, RPMH_REGULATOR_MODE_AUTO,
		RPMH_REGULATOR_MODE_HPM, RPMH_REGULATOR_MODE_COUNT);
	a52_persistent_diag_mark(
		"A52RPMHABI copy=2 pass=%d ret=%d lpm=%d auto=%d hpm=%d count=%d\\n",
		RPMH_REGULATOR_MODE_PASS, RPMH_REGULATOR_MODE_RET,
		RPMH_REGULATOR_MODE_LPM, RPMH_REGULATOR_MODE_AUTO,
		RPMH_REGULATOR_MODE_HPM, RPMH_REGULATOR_MODE_COUNT);
	a52_persistent_diag_mark(
		"A52RPMHABI copy=3 pass=%d ret=%d lpm=%d auto=%d hpm=%d count=%d\\n",
		RPMH_REGULATOR_MODE_PASS, RPMH_REGULATOR_MODE_RET,
		RPMH_REGULATOR_MODE_LPM, RPMH_REGULATOR_MODE_AUTO,
		RPMH_REGULATOR_MODE_HPM, RPMH_REGULATOR_MODE_COUNT);
	return platform_driver_register(&rpmh_regulator_driver);
}'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    return text.replace(old, new, 1)


def macro_has_value(text: str, name: str, value: int) -> bool:
    return re.search(
        rf'^\s*#\s*define\s+{re.escape(name)}\s+{value}\b',
        text,
        flags=re.MULTILINE,
    ) is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Fix the Samsung downstream RPMh regulator mode-number ABI.'
    )
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    source = gki / 'drivers/regulator/a52-rpmh-regulator-downstream.c'
    levels = gki / 'include/dt-bindings/regulator/qcom,rpmh-regulator-levels.h'
    upstream = gki / 'include/dt-bindings/regulator/qcom,rpmh-regulator.h'
    if not source.is_file():
        raise SystemExit(f'missing source: {source}')
    if not levels.is_file():
        raise SystemExit(f'missing downstream mode header: {levels}')
    if not upstream.is_file():
        raise SystemExit(f'missing upstream mode header: {upstream}')

    text = source.read_text(encoding='utf-8')
    if 'A52RPMHABI copy=1' in text:
        raise SystemExit('Workflow 99 mode ABI fix is already present')

    upstream_text = upstream.read_text(encoding='utf-8')
    levels_text = levels.read_text(encoding='utf-8')

    expected_upstream = {
        'RPMH_REGULATOR_MODE_RET': 0,
        'RPMH_REGULATOR_MODE_LPM': 1,
        'RPMH_REGULATOR_MODE_AUTO': 2,
        'RPMH_REGULATOR_MODE_HPM': 3,
    }
    expected_downstream = {
        'RPMH_REGULATOR_MODE_PASS': 0,
        'RPMH_REGULATOR_MODE_RET': 1,
        'RPMH_REGULATOR_MODE_LPM': 2,
        'RPMH_REGULATOR_MODE_AUTO': 3,
        'RPMH_REGULATOR_MODE_HPM': 4,
    }

    missing_upstream = [
        f'{name}={value}'
        for name, value in expected_upstream.items()
        if not macro_has_value(upstream_text, name, value)
    ]
    missing_downstream = [
        f'{name}={value}'
        for name, value in expected_downstream.items()
        if not macro_has_value(levels_text, name, value)
    ]
    if missing_upstream:
        raise SystemExit(
            'unexpected upstream mode ABI: ' + ', '.join(missing_upstream)
        )
    if missing_downstream:
        raise SystemExit(
            'unexpected downstream mode ABI: ' + ', '.join(missing_downstream)
        )

    text = replace_once(text, OLD_INCLUDE, NEW_INCLUDE, 'RPMh mode header include')
    text = replace_once(text, OLD_INIT, NEW_INIT, 'RPMh regulator init marker')

    checks = {
        'uses_downstream_mode_header': (
            '#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>' in text
        ),
        'does_not_use_upstream_mode_header': OLD_INCLUDE.strip() not in text,
        'compile_time_abi_guard': (
            '#error "A52 downstream RPMh regulator mode ABI mismatch"' in text
        ),
        'runtime_abi_marker': 'A52RPMHABI copy=1' in text,
        'probe_marker_preserved': 'A52RPMHREG PROBE' in text,
        'ready_marker_preserved': 'A52RPMHREG READY' in text,
        'workflow98_ufs_audit_preserved': 'A52UFSDEP copy=1 HBA_STAGE' in (
            gki / 'drivers/scsi/ufs/ufshcd.c'
        ).read_text(encoding='utf-8'),
        'ice_safe_preserved': 'A52_UFS_ICE_SAFE_BRINGUP' in (
            gki / 'drivers/scsi/ufs/ufs-qcom-ice.c'
        ).read_text(encoding='utf-8'),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit('Workflow 99 staging failed: ' + ', '.join(failed))

    source.write_text(text, encoding='utf-8')
    (output / 'patched-a52-rpmh-regulator-downstream.c').write_text(
        text, encoding='utf-8'
    )
    (output / 'mode-abi-report.json').write_text(
        json.dumps(
            {
                'status': 'staged',
                'hardware_validated': False,
                'capture': 'A52_RAW_RAMOOPS_20260724_095943.zip',
                'observed_failure': {
                    'ufs_stage': 'init_device_vreg',
                    'rail': 'vcc',
                    'supplier': 'pm6150a_l7',
                    'provider_device': '18200000.rsc:rpmh-regulator-ldoe7',
                    'provider_probe_result': -22,
                    'ufs_result': -517,
                },
                'root_cause': (
                    'downstream DT mode values 0..4 were parsed using the '
                    'upstream 0..3 qcom,rpmh-regulator.h ABI'
                ),
                'fix': (
                    'compile the copied downstream RPMh driver against '
                    'qcom,rpmh-regulator-levels.h and enforce the expected '
                    'PASS=0 RET=1 LPM=2 AUTO=3 HPM=4 ABI'
                ),
                'checks': checks,
            },
            indent=2,
            sort_keys=True,
        )
        + '\n',
        encoding='utf-8',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
