#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
BASE="$PWD/artifacts/a52xq-smmu-display-contracts"
STAGE="$BASE/stage"
P175="$PWD/workspace/phase319-regenerated-phase175.patch"
P199_REF="$STAGE/phase199-post-kms-crc32c.patch"
P199_REPLAY="$PWD/workspace/phase319-replayed-phase199.patch"
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
  "$STAGE/recorder-after-phase199.c" "$STAGE/recorder-before-phase200.c" \
  "$STAGE/msm-smmu-before-phase200.c" "$STAGE/sde-kms-before-phase200.c" \
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

# Phase199 through Phase206 share the same build config. final.config is
# intentionally different and is installed only after the Phase206 replay.
for cfg in before-phase200.config before-phase201.config before-phase202.config before-phase203.config before-phase204.config before-phase206.config; do
  cmp -s "$BASE/config/before-phase199.config" "$BASE/config/$cfg"
done
printf '%s  %s\n' f9d08b3ce41d6a5a71ddea5699046983e0a5deddb9b6504bc1b5b30894c0a049 "$P199_REF" | sha256sum -c -

# The retained Phase199 patch contains only tracked Git files. The historical
# Phase175 replay is still needed to hydrate the imported, untracked A52 display
# and secure source trees. Run40 proved that the successful Phase175 producer
# deterministically regenerates those trees while its tracked cumulative patch
# remains byte-different from the expired artifact. Accept only that exact known
# mismatch here, then prove the later Phase199 boundary against retained source
# snapshots and the immutable Phase199 tracked-patch SHA256.
set +e
bash scripts/319_regenerate_phase175_base.sh "$P175"
P175_RC=$?
set -e
test "$P175_RC" -ne 0
test -s "$P175"
P175_ACTUAL="$(sha256sum "$P175" | awk '{print $1}')"
test "$P175_ACTUAL" = 404d7fad70d4a6bec460ff0f09c813c98122169c3f1007a2b2b59cd8e75afae3
printf '%s\n' "Phase319 hybrid: known Run40 Phase175 tracked mismatch reproduced sha256=${P175_ACTUAL}"
test -s "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
test -s "$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
test -s "$ROOT/drivers/a52_display/msm/msm_smmu.c"

# Advance the hydrated untracked tree through the historical Phase177-199
# source mutations. Tracked-file exactness is replaced below by the retained
# Phase199 patch, while untracked boundary files are checked byte-for-byte.
P177="$PWD/workspace/phase177.patch"
P177_TMP="$PWD/workspace/phase177-payload"
rm -rf "$P177_TMP"
mkdir -p "$P177_TMP"
for item in \
  '00.txt 2e2e8773465271521302902aeedd3c0779c3eba750e191881eb0649654402a39' \
  '01.txt 2cc8aa74d1080a38f3deabaa790ba6cec24accfe542d9747c3f4c6eda00699ba' \
  '02.txt 640c2ce3842ecb8120e14494bb23b5d2e4a1c68700c2a67bab2ba55a04e36731' \
  '03.txt 3799c05943d31375851b3e0680f61fcde892d96272edad98454b641589fd69e0' \
  '04.txt 9e18d44f8df3abe72ac2c67a42919f8acbf90590acdb424ba609d0086c7cfc47' \
  '05.txt 9f0a5026d1a78d096b73ff97f3c785a9fd807999a36decb7a89e7fa851477945' \
  '06.txt b62056e0d393a7969bfac6c60295eb3fb6e78228f6955196e9326cdb90ea1f82' \
  '07.txt da48f50560314b78ef184d7910d5b5a8026164cae8227997a3795e457c16180a'; do
  set -- $item
  tr -d '\r\n' < "scripts/177_patch_payload_chunks/$1" > "$P177_TMP/$1"
  printf '%s  %s\n' "$2" "$P177_TMP/$1" | sha256sum -c -
done
cat "$P177_TMP"/*.txt | base64 --decode | gzip -dc > "$P177"
printf '%s  %s\n' 9412b28da19c71e7bc97e767e83ce717e146313d32321c7429e6d4050a4f0d00 "$P177" | sha256sum -c -
git -C "$ROOT" apply --check "$P177"
git -C "$ROOT" apply "$P177"

P179="$PWD/workspace/phase179.patch"
tr -d '\r\n' < scripts/179_payloads/patch.gz.b64 | base64 --decode | gzip -dc > "$P179"
printf '%s  %s\n' 81bc17510b643274dba9652baa5edf52e9c2127af02a77eb1597637be0c3c59f "$P179" | sha256sum -c -
git -C "$ROOT" apply --check "$P179"
git -C "$ROOT" apply "$P179"

python3 scripts/180_apply.py --root "$ROOT" --audit-source scripts/180_a52_display_bind_audit.c
for p in 181 182 183 185; do python3 "scripts/${p}_apply.py" --root "$ROOT"; done
python3 scripts/186_apply.py --root "$ROOT" --touchgrass "$TG"
for p in 187 188 189 190 191 192 193 194 195 196; do python3 "scripts/${p}_apply.py" --root "$ROOT"; done
python3 scripts/197_apply_triple_rs.py --self-test
python3 scripts/197_apply_triple_rs.py --root "$ROOT"
python3 scripts/198_apply_catalog_trace.py --self-test
python3 scripts/198_apply_catalog_trace.py --root "$ROOT"
python3 scripts/199_apply_recorder_crc32c.py --self-test
python3 scripts/199_apply_recorder_crc32c.py --root "$ROOT"
git -C "$ROOT" diff --check

# Prove the untracked Phase199 boundary before replacing tracked state.
cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$STAGE/recorder-after-phase199.c"
cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$STAGE/recorder-before-phase200.c"
cmp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$STAGE/msm-smmu-before-phase200.c"
cmp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$STAGE/sde-kms-before-phase200.c"
echo 'Phase319 hybrid: exact retained untracked Phase199 boundary PASS'

# Reset tracked files only. Do not clean: the exact hydrated A52 sources above
# are intentionally untracked in the upstream GKI repository. Install the
# retained Phase199 tracked patch and prove its byte identity independently.
git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" apply --check "$P199_REF"
git -C "$ROOT" apply "$P199_REF"
git -C "$ROOT" diff --check
git -C "$ROOT" diff --binary --no-ext-diff > "$P199_REPLAY"
printf '%s  %s\n' f9d08b3ce41d6a5a71ddea5699046983e0a5deddb9b6504bc1b5b30894c0a049 "$P199_REPLAY" | sha256sum -c -
cmp "$P199_REPLAY" "$P199_REF"
cmp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$STAGE/recorder-after-phase199.c"
cmp "$ROOT/drivers/a52_display/msm/msm_smmu.c" "$STAGE/msm-smmu-before-phase200.c"
cmp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$STAGE/sde-kms-before-phase200.c"
mkdir -p "$BUILD"
cp "$BASE/config/before-phase199.config" "$BUILD/.config"
echo 'Phase319 repair: exact hybrid Phase199 tracked + untracked boundary PASS'

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
