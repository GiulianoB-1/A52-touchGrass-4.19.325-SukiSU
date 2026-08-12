#!/usr/bin/env bash
set -Eeuo pipefail

# Phase257 fast rebuild trigger v3: exact Phase213 patcher parity.
ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
BASE="$PWD/artifacts/a52xq-smmu-display-contracts"
LOG="$PWD/artifacts/a52xq-secure-vmid/logs/source-reconstruction.log"
mkdir -p "$(dirname "$LOG")" "$PWD/workspace"
test -d "$BASE/stage"

# The historical Phase199 source-only helper consumes these pinned artifact
# identities. Legacy callers already export them; the Phase257 one-compile
# rebuild intentionally skips the old parent workflow, so provide the exact
# proven defaults only when a caller has not supplied them.
: "${SOURCE_ARTIFACT_ID:=8681171875}"
: "${SOURCE_ARTIFACT_SHA256:=c9f6b0041abd5c9305d05534b1afcdf5ebee6de607ad08784721e364a2bf4c11}"
: "${PHASE198_ARTIFACT_ID:=8819127383}"
: "${PHASE198_ARTIFACT_SHA256:=99ff045d09811d43106612ef2682216a7d29dfaa7825e2360bef403e31a41eb6}"
export SOURCE_ARTIFACT_ID SOURCE_ARTIFACT_SHA256
export PHASE198_ARTIFACT_ID PHASE198_ARTIFACT_SHA256

# Reuse the fully audited Phase 198 reconstruction and Phase 199 patching, but
# stop before the first kernel compile. Every later phase is checked against
# the exact source snapshot packaged by the successful Phase 206 workflow.
awk '/^CLANG="\$\(readlink -f/{exit} {print}' scripts/199_ci.sh \
  > "$PWD/workspace/199_source_only.sh"
bash -n "$PWD/workspace/199_source_only.sh"
bash "$PWD/workspace/199_source_only.sh" | tee "$LOG"

compare_file() {
  cmp "$ROOT/$1" "$BASE/stage/$2"
}

# Phase 200. Apply twice to prove idempotence on the exact reconstructed tree,
# then compare directly with the successful Phase 206 artifact.
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
compare_file drivers/a52_display/msm/msm_smmu.c msm-smmu-after-phase200.c
compare_file drivers/a52_display/msm/sde/sde_kms.c sde-kms-after-phase200.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase200.c

# Phase 201.
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
compare_file drivers/a52_display/msm/msm_drv.c msm-drv-after-phase201.c
compare_file drivers/a52_display/msm/msm_smmu.c msm-smmu-after-phase201.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase201.c

# Phase 202.
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
compare_file drivers/base/dd.c drivers-base-dd-after-phase202.c
compare_file drivers/base/core.c drivers-base-core-after-phase202.c
compare_file drivers/base/platform.c drivers-base-platform-after-phase202.c
compare_file drivers/of/device.c drivers-of-device-after-phase202.c
compare_file drivers/iommu/of_iommu.c drivers-iommu-of_iommu-after-phase202.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c drivers-a52_secure-a52_ack_secure_flight_recorder-after-phase202.c

# Phase 203.
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
compare_file drivers/iommu/arm/arm-smmu/arm-smmu.c arm-smmu-after-phase203.c
compare_file drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c arm-smmu-qcom-after-phase203.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase203.c

# Phase 204.
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
compare_file drivers/iommu/arm/arm-smmu/arm-smmu.c arm-smmu-after-phase204.c
compare_file drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c arm-smmu-qcom-after-phase204.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase204.c

git -C "$ROOT" diff --check
test -s "$BUILD/.config"
printf '%s\n' 'Phase 199-204 source-only reconstruction: exact artifact match PASS'

# Phase 206. The caller performs a final byte-for-byte comparison against all
# Phase 206 source snapshots after this second idempotent application.
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
git -C "$ROOT" diff --check
printf '%s\n' 'Phase 206 source-only reconstruction: staged for exact comparison'

# The proven Phase227 child repairs this generated Phase213 patcher before the
# 213 source mutation. Mirror that exact repair here so the one-compile replay
# reaches the same pre-Phase218 ion.c hash. Be idempotent for legacy callers
# that already performed the repair earlier in their workflow.
python3 - <<'PY'
from pathlib import Path
path = Path('scripts/213_apply_ion_transaction_trace.py')
text = path.read_text(encoding='utf-8')
old = r'pr_warn_once("%s: ioctl validate failed\n", __func__);'
new = r'pr_warn_once("%s: ioctl validate failed\\n", __func__);'
old_count = text.count(old)
new_count = text.count(new)
if old_count == 1 and new_count == 0:
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
elif old_count == 0 and new_count == 1:
    pass
else:
    raise SystemExit(
        f'Phase213 escape repair expected pristine or repaired state; '
        f'old={old_count} new={new_count}'
    )
repaired = path.read_text(encoding='utf-8')
if repaired.count(new) != 1 or old in repaired:
    raise SystemExit('Phase213 C-string escape repair verification failed')
print('Phase 213 C-string newline escape parity repaired')
PY