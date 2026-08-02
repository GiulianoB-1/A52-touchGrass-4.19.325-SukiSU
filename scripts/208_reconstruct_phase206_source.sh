#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
LOG="$PWD/artifacts/a52xq-secure-vmid/logs/source-reconstruction.log"
mkdir -p "$(dirname "$LOG")" "$PWD/workspace"

# Reuse the fully audited Phase 198 reconstruction and Phase 199 patching, but
# stop before the first kernel compile. Later phases are source transformations
# with their own idempotent self-tests, so one final Phase 208 compile is enough.
awk '/^CLANG="\$\(readlink -f/{exit} {print}' scripts/199_ci.sh \
  > "$PWD/workspace/199_source_only.sh"
bash -n "$PWD/workspace/199_source_only.sh"
bash "$PWD/workspace/199_source_only.sh" | tee "$LOG"

python3 scripts/200_apply_smmu_defer_trace.py --self-test
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
python3 scripts/201_apply_smmu_component_dependency.py --self-test
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT" --self-test
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT" --self-test
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT" --self-test
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"

git -C "$ROOT" diff --check
test -s "$BUILD/.config"
python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
checks = {
    'drivers/a52_secure/a52_ack_secure_flight_recorder.c': (
        'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c',
        '!strncmp(message, "SMMU ", 5)',
    ),
    'drivers/a52_display/msm/msm_smmu.c': (
        'SMMU create state domain=%d existing=%d driver=%d client=%d',
        'component_add(&pdev->dev, &msm_smmu_component_ops)',
    ),
    'drivers/base/dd.c': ('DCORE suppliers rc=%d status=%d',),
    'drivers/iommu/arm/arm-smmu/arm-smmu.c': (
        'SMMU parent-probe enter dev=%s driver=%s',
        'qcom,qsmmu-v500',
    ),
    'drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c': (
        'if (!scm && !downstream_handoff)',
        'SMMU parent-qcom scm=%d handoff=%d',
    ),
}
for rel, markers in checks.items():
    text = (root / rel).read_text()
    for marker in markers:
        assert marker in text, f'{rel}: {marker}'
print('Phase 199-204 source-only reconstruction: PASS')
PY

python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT" --self-test
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
git -C "$ROOT" diff --check
printf '%s\n' 'Phase 206 source-only reconstruction: PASS'
