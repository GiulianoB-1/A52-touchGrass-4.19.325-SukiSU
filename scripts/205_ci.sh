#!/usr/bin/env bash
set -Eeuo pipefail
# Comparison-only PR gate. No kernel behavior or boot artifact is modified.

BASE="$PWD/artifacts/a52xq-apps-smmu-scm-handoff"
OUT="$PWD/artifacts/a52xq-post-smmu-touchgrass-audit"
ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
BUILD="$PWD/workspace/gki-phase199-out"

mkdir -p "$OUT/logs"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/204_ci.sh

rm -rf "$OUT"
mkdir -p "$OUT"/{logs,report,source}

python3 scripts/205_compare_post_smmu_touchgrass.py \
  --gki "$ROOT" \
  --touchgrass "$TG" \
  --boot "$BASE/package/boot.img" \
  --config "$BUILD/.config" \
  --out "$OUT/report" \
  | tee "$OUT/logs/phase205-comparison.log"

cp scripts/205_compare_post_smmu_touchgrass.py "$OUT/source/"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" "$OUT/source/gki-arm-smmu.c"
cp "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" "$OUT/source/gki-arm-smmu-qcom.c"
cp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$OUT/source/gki-msm-smmu.c"
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" "$OUT/source/gki-msm-drv.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$OUT/source/gki-sde-kms.c"
cp "$TG/drivers/iommu/arm-smmu.c" "$OUT/source/touchgrass-arm-smmu.c"
cp "$TG/techpack/display/msm/msm_smmu.c" "$OUT/source/touchgrass-msm-smmu.c"
cp "$TG/techpack/display/msm/msm_drv.c" "$OUT/source/touchgrass-msm-drv.c"
cp "$TG/techpack/display/msm/sde/sde_kms.c" "$OUT/source/touchgrass-sde-kms.c"

python3 - <<'PY'
import json
from pathlib import Path

root = Path('artifacts/a52xq-post-smmu-touchgrass-audit')
report = json.loads((root / 'report/post-smmu-touchgrass-report.json').read_text())
assert report['status'] == 'post-smmu-touchgrass-audit-complete'
assert report['kernel_behavior_changed'] is False
assert report['hardware_validated'] is False
assert report['hardware_test_recommended'] is False
assert 'missing-early-map-domain-attribute' in report['blocking_findings']
assert report['active_dt_nodes']['display_unsecure_smmu']
assert report['active_dt_nodes']['display_secure_smmu']
assert report['active_dt_nodes']['apps_smmu']
assert report['attribute_matrix']['early_map']['gki_display_calls']
assert not report['attribute_matrix']['early_map']['gki_arm_case']
assert report['attribute_matrix']['early_map']['touchgrass_arm_case']

final = {
    'status': 'phase205-post-smmu-touchgrass-audit-complete',
    'phase': 205,
    'base_phase': 204,
    'comparison_only': True,
    'flashable_candidate': False,
    'kernel_behavior_changed': False,
    'hardware_validated': False,
    'hardware_test_recommended': report['hardware_test_recommended'],
    'boot_sha256_compared': report['boot_sha256'],
    'dtb_sha256_compared': report['dtb_sha256'],
    'blocking_findings': report['blocking_findings'],
    'finding_count': len(report['findings']),
    'touchgrass_commit': '6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
    'next_required_fix': 'port-narrow-display-smmu-domain-contracts-before-another-recorder',
}
(root / 'final-audit.json').write_text(json.dumps(final, indent=2, sort_keys=True) + '\n')
PY

cat > "$OUT/README-FIRST.txt" <<'README_EOF'
A52 Phase 205 post-SMMU TouchGrass audit

THIS ARTIFACT IS NON-FLASHABLE.

It compares the exact Phase 204 GKI source and preserved boot DTB against
TouchGrass commit 6bf351bdf18bdb228db79e66f14a7a9c0178e5d7 before another recorder
or functional display candidate is created.

Read first:
  report/post-smmu-touchgrass-report.md
  report/post-smmu-touchgrass-report.json
  final-audit.json

The audit is expected to block hardware testing when a deterministic source/DT
mismatch exists. It does not modify kernel behavior or publish a boot image.
README_EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
