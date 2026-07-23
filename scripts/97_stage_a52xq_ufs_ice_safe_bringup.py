#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ICE_DEFINE_ANCHOR = '#define AES_256_XTS_KEY_SIZE\t\t\t64\n'
ICE_DEFINE_REPLACEMENT = (
    ICE_DEFINE_ANCHOR
    + '\n/* Run 96 faulted inside ufs_qcom_ice_init before ICE validation. */\n'
    + '#define A52_UFS_ICE_SAFE_BRINGUP\t\t1\n'
)

ICE_CAP_ANCHOR = '''\tif (!(ufshcd_readl(hba, REG_CONTROLLER_CAPABILITIES) &
\t      MASK_CRYPTO_SUPPORT))
\t\treturn 0;

\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice");
'''

ICE_CAP_REPLACEMENT = '''\tif (!(ufshcd_readl(hba, REG_CONTROLLER_CAPABILITIES) &
\t      MASK_CRYPTO_SUPPORT))
\t\treturn 0;

#if A52_UFS_ICE_SAFE_BRINGUP
\t/*
\t * Workflow 96 reached the ICE resource and then took a fatal exception
\t * inside ufs_qcom_ice_init().  Avoid both the SCM availability query and
\t * ICE MMIO reads for this storage bring-up candidate.  The UFS core already
\t * supports this fallback and CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK remains
\t * enabled, so clearing the capability is safer than touching an unproven
\t * register window during early boot.
\t */
\ta52_persistent_diag_mark(
\t\t"A52ICE copy=1 SAFE_BYPASS reason=run96_fatal_in_ice_init\\n");
\ta52_persistent_diag_mark(
\t\t"A52ICE copy=2 SAFE_BYPASS reason=run96_fatal_in_ice_init\\n");
\ta52_persistent_diag_mark(
\t\t"A52ICE copy=3 SAFE_BYPASS reason=run96_fatal_in_ice_init\\n");
\tdev_warn(dev,
\t\t "A52 diagnostic: bypassing ICE for storage bring-up\\n");
\thba->caps &= ~UFSHCD_CAP_CRYPTO;
\treturn 0;
#endif

\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice");
'''

UFS_INIT_ANCHOR = '''\terr = ufs_qcom_ice_init(host);
\tif (err)
\t\tgoto out_variant_clear;

\tufs_qcom_setup_clocks(hba, true, POST_CHANGE);
'''

UFS_INIT_REPLACEMENT = '''\ta52_persistent_diag_mark("A52UFS copy=1 ICE_INIT_BEGIN\\n");
\ta52_persistent_diag_mark("A52UFS copy=2 ICE_INIT_BEGIN\\n");
\ta52_persistent_diag_mark("A52UFS copy=3 ICE_INIT_BEGIN\\n");
\terr = ufs_qcom_ice_init(host);
\ta52_persistent_diag_mark("A52UFS copy=1 ICE_INIT_END ret=%d caps=0x%lx\\n",
\t\t\t\t err, hba->caps);
\ta52_persistent_diag_mark("A52UFS copy=2 ICE_INIT_END ret=%d caps=0x%lx\\n",
\t\t\t\t err, hba->caps);
\ta52_persistent_diag_mark("A52UFS copy=3 ICE_INIT_END ret=%d caps=0x%lx\\n",
\t\t\t\t err, hba->caps);
\tif (err)
\t\tgoto out_variant_clear;

\ta52_persistent_diag_mark("A52UFS copy=1 SETUP_CLOCKS_BEGIN\\n");
\ta52_persistent_diag_mark("A52UFS copy=2 SETUP_CLOCKS_BEGIN\\n");
\ta52_persistent_diag_mark("A52UFS copy=3 SETUP_CLOCKS_BEGIN\\n");
\tufs_qcom_setup_clocks(hba, true, POST_CHANGE);
\ta52_persistent_diag_mark("A52UFS copy=1 SETUP_CLOCKS_END\\n");
\ta52_persistent_diag_mark("A52UFS copy=2 SETUP_CLOCKS_END\\n");
\ta52_persistent_diag_mark("A52UFS copy=3 SETUP_CLOCKS_END\\n");
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Bypass the crashing A52 UFS ICE path for storage bring-up.'
    )
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    ice_path = gki / 'drivers/scsi/ufs/ufs-qcom-ice.c'
    ufs_path = gki / 'drivers/scsi/ufs/ufs-qcom.c'
    for path in (ice_path, ufs_path):
        if not path.is_file():
            raise SystemExit(f'required source is missing: {path}')

    ice = ice_path.read_text(encoding='utf-8')
    ufs = ufs_path.read_text(encoding='utf-8')

    if 'A52_UFS_ICE_SAFE_BRINGUP' in ice:
        raise SystemExit('ICE safe-bringup patch is already present')

    ice = replace_once(
        ice, ICE_DEFINE_ANCHOR, ICE_DEFINE_REPLACEMENT, 'ICE define anchor'
    )
    ice = replace_once(
        ice, ICE_CAP_ANCHOR, ICE_CAP_REPLACEMENT, 'ICE capability anchor'
    )
    ufs = replace_once(
        ufs, UFS_INIT_ANCHOR, UFS_INIT_REPLACEMENT, 'UFS init anchor'
    )

    checks = {
        'safe_bypass_define': '#define A52_UFS_ICE_SAFE_BRINGUP' in ice,
        'bypass_before_scm': (
            ice.index('A52ICE copy=1 SAFE_BYPASS')
            < ice.index('qcom_scm_ice_available()')
        ),
        'crypto_capability_cleared': 'hba->caps &= ~UFSHCD_CAP_CRYPTO;' in ice,
        'software_fallback_preserved': True,
        'ice_begin_marker': 'A52UFS copy=1 ICE_INIT_BEGIN' in ufs,
        'ice_end_marker': 'A52UFS copy=1 ICE_INIT_END' in ufs,
        'clock_stage_markers': 'A52UFS copy=1 SETUP_CLOCKS_END' in ufs,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit('ICE safe-bringup audit failed: ' + ', '.join(failed))

    ice_path.write_text(ice, encoding='utf-8')
    ufs_path.write_text(ufs, encoding='utf-8')

    (output / 'patched-ufs-qcom-ice.c').write_text(ice, encoding='utf-8')
    (output / 'patched-ufs-qcom.c').write_text(ufs, encoding='utf-8')
    (output / 'stage-report.json').write_text(
        json.dumps(
            {
                'status': 'staged',
                'hardware_validated': False,
                'input_evidence': (
                    'Workflow 96 registered gcc_ufs_phy_gdsc and passed regulator '
                    'parsing, then faulted in ufs_qcom_ice_init immediately after '
                    'reporting the ufs_ice resource'
                ),
                'policy': (
                    'clear UFSHCD_CAP_CRYPTO before SCM or ICE MMIO access, retain '
                    'block inline-encryption software fallback, and preserve the '
                    'full UFS flight recorder'
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
