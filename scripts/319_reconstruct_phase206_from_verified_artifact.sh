#!/usr/bin/env bash
set -Eeuo pipefail

# Phase319 CI provenance repair.
#
# The historical Phase206 reconstruction bridge delegated to the source-only
# prefix of 199_ci.sh, which downloads two now-expired Phase198/199 artifacts.
# The already-verified Phase206 artifact itself contains the cumulative
# pristine-GKI -> Phase199 git patch plus exact Phase200-206 source snapshots.
# Reconstruct from that live artifact instead, then replay the original
# Phase200-206 patchers and compare every exported source snapshot byte-for-byte.
# This changes CI materialization only, never kernel behavior.

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
BASE="$PWD/artifacts/a52xq-smmu-display-contracts"
STAGE="$BASE/stage"
P199="$STAGE/phase199-post-kms-crc32c.patch"

: "${GKI_COMMON_SHA:?}"

test -d "$ROOT/.git"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
test -d "$STAGE"
test -s "$BASE/SHA256SUMS"

# 209_prepare_phase206.sh has already verified both the artifact ZIP digest and
# its internal SHA256SUMS before this script is invoked. Recheck the extracted
# bundle so this bridge never consumes an unverified staging directory.
(
  cd "$BASE"
  sha256sum -c SHA256SUMS
)

for required in \
  "$P199" \
  "$BASE/config/before-phase199.config" \
  "$BASE/config/before-phase200.config" \
  "$BASE/config/before-phase201.config" \
  "$BASE/config/before-phase202.config" \
  "$BASE/config/before-phase203.config" \
  "$BASE/config/before-phase204.config" \
  "$BASE/config/before-phase206.config" \
  "$BASE/config/final.config" \
  "$STAGE/recorder-after-phase199.c" \
  "$STAGE/msm-smmu-after-phase200.c" \
  "$STAGE/sde-kms-after-phase200.c" \
  "$STAGE/recorder-after-phase200.c" \
  "$STAGE/msm-drv-after-phase201.c" \
  "$STAGE/msm-smmu-after-phase201.c" \
  "$STAGE/recorder-after-phase201.c" \
  "$STAGE/drivers-base-dd-after-phase202.c" \
  "$STAGE/drivers-base-core-after-phase202.c" \
  "$STAGE/drivers-base-platform-after-phase202.c" \
  "$STAGE/drivers-of-device-after-phase202.c" \
  "$STAGE/drivers-iommu-of_iommu-after-phase202.c" \
  "$STAGE/drivers-a52_secure-a52_ack_secure_flight_recorder-after-phase202.c" \
  "$STAGE/arm-smmu-after-phase203.c" \
  "$STAGE/arm-smmu-qcom-after-phase203.c" \
  "$STAGE/recorder-after-phase203.c" \
  "$STAGE/arm-smmu-after-phase204.c" \
  "$STAGE/arm-smmu-qcom-after-phase204.c" \
  "$STAGE/recorder-after-phase204.c" \
  "$STAGE/drivers-a52_display-msm-msm_smmu.c-after-phase206" \
  "$STAGE/drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase206" \
  "$STAGE/drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase206" \
  "$STAGE/drivers-iommu-dma-iommu.c-after-phase206" \
  "$STAGE/include-linux-iommu.h-after-phase206" \
  "$STAGE/recorder-after-phase206.c"; do
  test -s "$required"
done

# All Phase199-206 configs in the successful artifact are byte-identical.
for cfg in \
  before-phase200.config before-phase201.config before-phase202.config \
  before-phase203.config before-phase204.config before-phase206.config final.config; do
  cmp -s "$BASE/config/before-phase199.config" "$BASE/config/$cfg"
done

# The cumulative Phase199 patch is an authoritative diff from the exact pinned
# GKI common tree because 199_ci.sh reset/cleaned to GKI_COMMON_SHA before
# applying all inherited Phase177-198 changes and Phase199 itself, then emitted
# one git diff. Lock its digest as an additional guard beyond artifact SHA256SUMS.
printf '%s  %s\n' \
  f9d08b3ce41d6a5a71ddea5699046983e0a5deddb9b6504bc1b5b30894c0a049 \
  "$P199" | sha256sum -c -

git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" clean -fd

git -C "$ROOT" apply --check "$P199"
git -C "$ROOT" apply "$P199"
git -C "$ROOT" diff --check

mkdir -p "$BUILD"
cp "$BASE/config/before-phase199.config" "$BUILD/.config"

compare_file() {
  local source="$1" reference="$2"
  cmp "$ROOT/$source" "$STAGE/$reference"
}

# Exact Phase199 anchor supplied by the live Phase206 artifact.
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase199.c
sha256sum -c "$STAGE/phase198-invariants-before-phase199.sha256"
printf '%s\n' 'Phase319 repair: cumulative Phase199 source anchor PASS'

# Replay exactly the same Phase200-204 patchers used by the successful Phase206
# workflow. Apply twice where the historical reconstruction explicitly proved
# idempotence, then compare the artifact's source snapshots byte-for-byte.
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
compare_file drivers/a52_display/msm/msm_smmu.c msm-smmu-after-phase200.c
compare_file drivers/a52_display/msm/sde/sde_kms.c sde-kms-after-phase200.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase200.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase200.config"

python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
compare_file drivers/a52_display/msm/msm_drv.c msm-drv-after-phase201.c
compare_file drivers/a52_display/msm/msm_smmu.c msm-smmu-after-phase201.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase201.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase201.config"

python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
compare_file drivers/base/dd.c drivers-base-dd-after-phase202.c
compare_file drivers/base/core.c drivers-base-core-after-phase202.c
compare_file drivers/base/platform.c drivers-base-platform-after-phase202.c
compare_file drivers/of/device.c drivers-of-device-after-phase202.c
compare_file drivers/iommu/of_iommu.c drivers-iommu-of_iommu-after-phase202.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c drivers-a52_secure-a52_ack_secure_flight_recorder-after-phase202.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase202.config"

python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
compare_file drivers/iommu/arm/arm-smmu/arm-smmu.c arm-smmu-after-phase203.c
compare_file drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c arm-smmu-qcom-after-phase203.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase203.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase203.config"

python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
compare_file drivers/iommu/arm/arm-smmu/arm-smmu.c arm-smmu-after-phase204.c
compare_file drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c arm-smmu-qcom-after-phase204.c
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase204.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase204.config"

git -C "$ROOT" diff --check
printf '%s\n' 'Phase319 repair: Phase199-204 exact artifact comparisons PASS'

# Phase206. Preserve the historical idempotence proof and compare every final
# source snapshot exported by the successful artifact.
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
compare_file drivers/a52_display/msm/msm_smmu.c drivers-a52_display-msm-msm_smmu.c-after-phase206
compare_file drivers/iommu/arm/arm-smmu/arm-smmu.c drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase206
compare_file drivers/iommu/arm/arm-smmu/arm-smmu.h drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase206
compare_file drivers/iommu/dma-iommu.c drivers-iommu-dma-iommu.c-after-phase206
compare_file include/linux/iommu.h include-linux-iommu.h-after-phase206
compare_file drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase206.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase206.config"
cp "$BASE/config/final.config" "$BUILD/.config"
git -C "$ROOT" diff --check
printf '%s\n' 'Phase319 repair: Phase206 source-only reconstruction exact live-artifact replay PASS'

# Preserve the exact generated Phase213 patcher repair performed by the original
# 208_reconstruct_phase206_source.sh after Phase206 reconstruction.
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
