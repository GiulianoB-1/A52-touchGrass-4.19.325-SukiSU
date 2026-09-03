#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
BASE="$PWD/artifacts/a52xq-smmu-display-contracts"
STAGE="$BASE/stage"
P199_REF="$STAGE/phase199-post-kms-crc32c.patch"
TG="$PWD/workspace/touchgrass-a52xq"

: "${GKI_COMMON_SHA:?}"
: "${TOUCHGRASS_COMMIT:?}"
test -d "$ROOT/.git" && test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
test -d "$TG/.git" && test "$(git -C "$TG" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT"
test -s "$BASE/SHA256SUMS"
(cd "$BASE" && sha256sum -c SHA256SUMS)

for f in \
  "$P199_REF" \
  "$BASE/config/before-phase199.config" "$BASE/config/before-phase200.config" \
  "$BASE/config/before-phase201.config" "$BASE/config/before-phase202.config" \
  "$BASE/config/before-phase203.config" "$BASE/config/before-phase204.config" \
  "$BASE/config/before-phase206.config" "$BASE/config/final.config" \
  "$STAGE/recorder-after-phase199.c" \
  "$STAGE/msm-smmu-after-phase200.c" "$STAGE/sde-kms-after-phase200.c" "$STAGE/recorder-after-phase200.c" \
  "$STAGE/msm-drv-after-phase201.c" "$STAGE/msm-smmu-after-phase201.c" "$STAGE/recorder-after-phase201.c" \
  "$STAGE/drivers-base-dd-after-phase202.c" "$STAGE/drivers-base-core-after-phase202.c" \
  "$STAGE/drivers-base-platform-after-phase202.c" "$STAGE/drivers-of-device-after-phase202.c" \
  "$STAGE/drivers-iommu-of_iommu-after-phase202.c" \
  "$STAGE/drivers-a52_secure-a52_ack_secure_flight_recorder-after-phase202.c" \
  "$STAGE/arm-smmu-after-phase203.c" "$STAGE/arm-smmu-qcom-after-phase203.c" "$STAGE/recorder-after-phase203.c" \
  "$STAGE/arm-smmu-after-phase204.c" "$STAGE/arm-smmu-qcom-after-phase204.c" "$STAGE/recorder-after-phase204.c" \
  "$STAGE/drivers-a52_display-msm-msm_smmu.c-after-phase206" \
  "$STAGE/drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase206" \
  "$STAGE/drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase206" \
  "$STAGE/drivers-iommu-dma-iommu.c-after-phase206" "$STAGE/include-linux-iommu.h-after-phase206" \
  "$STAGE/recorder-after-phase206.c"; do
  test -s "$f"
done

for cfg in before-phase200.config before-phase201.config before-phase202.config before-phase203.config before-phase204.config before-phase206.config final.config; do
  cmp -s "$BASE/config/before-phase199.config" "$BASE/config/$cfg"
done

# The retained Phase206 verification artifact contains the exact cumulative
# Phase199 source patch. Seed directly from that pinned artifact instead of
# rebuilding Phase175 through an expired Actions artifact chain.
printf '%s  %s\n' f9d08b3ce41d6a5a71ddea5699046983e0a5deddb9b6504bc1b5b30894c0a049 "$P199_REF" | sha256sum -c -
git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" clean -fd
git -C "$ROOT" apply --check "$P199_REF"
git -C "$ROOT" apply "$P199_REF"
git -C "$ROOT" diff --check
cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$STAGE/recorder-after-phase199.c"
mkdir -p "$BUILD"
cp "$BASE/config/before-phase199.config" "$BUILD/.config"
echo 'Phase319 repair: exact verified Phase199 artifact seed PASS'

cmp_stage() { cmp "$ROOT/$1" "$STAGE/$2"; }

python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
python3 scripts/200_apply_smmu_defer_trace.py --root "$ROOT"
cmp_stage drivers/a52_display/msm/msm_smmu.c msm-smmu-after-phase200.c
cmp_stage drivers/a52_display/msm/sde/sde_kms.c sde-kms-after-phase200.c
cmp_stage drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase200.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase200.config"

python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
python3 scripts/201_apply_smmu_component_dependency.py --root "$ROOT"
cmp_stage drivers/a52_display/msm/msm_drv.c msm-drv-after-phase201.c
cmp_stage drivers/a52_display/msm/msm_smmu.c msm-smmu-after-phase201.c
cmp_stage drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase201.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase201.config"

python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
python3 scripts/202_apply_driver_core_trace.py --root "$ROOT"
cmp_stage drivers/base/dd.c drivers-base-dd-after-phase202.c
cmp_stage drivers/base/core.c drivers-base-core-after-phase202.c
cmp_stage drivers/base/platform.c drivers-base-platform-after-phase202.c
cmp_stage drivers/of/device.c drivers-of-device-after-phase202.c
cmp_stage drivers/iommu/of_iommu.c drivers-iommu-of_iommu-after-phase202.c
cmp_stage drivers/a52_secure/a52_ack_secure_flight_recorder.c drivers-a52_secure-a52_ack_secure_flight_recorder-after-phase202.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase202.config"

python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
python3 scripts/203_apply_apps_smmu_qsmmuv500_compat.py --root "$ROOT"
cmp_stage drivers/iommu/arm/arm-smmu/arm-smmu.c arm-smmu-after-phase203.c
cmp_stage drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c arm-smmu-qcom-after-phase203.c
cmp_stage drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase203.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase203.config"

python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
python3 scripts/204_apply_apps_smmu_scm_handoff.py --root "$ROOT"
cmp_stage drivers/iommu/arm/arm-smmu/arm-smmu.c arm-smmu-after-phase204.c
cmp_stage drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c arm-smmu-qcom-after-phase204.c
cmp_stage drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase204.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase204.config"
git -C "$ROOT" diff --check
echo 'Phase319 repair: Phase200-204 exact artifact comparisons PASS'

python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
python3 scripts/206_apply_smmu_display_contracts.py --root "$ROOT"
cmp_stage drivers/a52_display/msm/msm_smmu.c drivers-a52_display-msm-msm_smmu.c-after-phase206
cmp_stage drivers/iommu/arm/arm-smmu/arm-smmu.c drivers-iommu-arm-arm-smmu-arm-smmu.c-after-phase206
cmp_stage drivers/iommu/arm/arm-smmu/arm-smmu.h drivers-iommu-arm-arm-smmu-arm-smmu.h-after-phase206
cmp_stage drivers/iommu/dma-iommu.c drivers-iommu-dma-iommu.c-after-phase206
cmp_stage include/linux/iommu.h include-linux-iommu.h-after-phase206
cmp_stage drivers/a52_secure/a52_ack_secure_flight_recorder.c recorder-after-phase206.c
cmp -s "$BUILD/.config" "$BASE/config/before-phase206.config"
cp "$BASE/config/final.config" "$BUILD/.config"
git -C "$ROOT" diff --check
echo 'Phase319 repair: Phase206 exact live-artifact replay PASS'

# Preserve the Phase213 source-generator escape compatibility required by the
# later reconstruction path.
python3 - <<'PY'
from pathlib import Path
p = Path('scripts/213_apply_ion_transaction_trace.py')
t = p.read_text()
old = r'pr_warn_once("%s: ioctl validate failed\n", __func__);'
new = r'pr_warn_once("%s: ioctl validate failed\\n", __func__);'
if t.count(old) == 1 and t.count(new) == 0:
    p.write_text(t.replace(old, new, 1))
elif not (t.count(old) == 0 and t.count(new) == 1):
    raise SystemExit('Phase213 escape repair unexpected state')
t = p.read_text()
if t.count(new) != 1 or old in t:
    raise SystemExit('Phase213 escape repair verification failed')
print('Phase 213 C-string newline escape parity repaired')
PY
