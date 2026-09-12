#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase343-gki-out"
FAIL="$PWD/phase343-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
CONN="$ROOT/drivers/a52_display/msm/sde/sde_connector.c"
CRTC="$ROOT/drivers/a52_display/msm/sde/sde_crtc.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase343 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase343-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p343-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/343_apply_gki510_cont_splash_drm_state_parity.py \
     scripts/343_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$DRV" "$ATOMIC" "$CONN" "$CRTC" "$CTRL" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase335 functional baseline"
bash scripts/335_ci_build_gki.sh 2>&1 | tee phase343-phase335.log

for f in \
  phase335-gki-out/package/boot.img \
  phase335-gki-out/compile/Image \
  phase335-gki-out/config/final.config \
  "$KMS" "$DRV" "$ATOMIC" "$CONN" "$CRTC" "$CTRL" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase335-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'crtc->state->encoder_mask = (1 << drm_encoder_index(encoder));' "$KMS"
! grep -Fq 'A52_PHASE343_GKI510_CONT_SPLASH_DRM_STATE_PARITY_V1' "$KMS"
grep -Fq 'A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1' \
  "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"

cp phase335-gki-out/config/final.config /tmp/p343-phase335.config
cp "$KMS" /tmp/p343-kms-before
cp "$DRV" /tmp/p343-drv-before
cp "$ATOMIC" /tmp/p343-atomic-before
cp "$CONN" /tmp/p343-conn-before
cp "$CRTC" /tmp/p343-crtc-before
cp "$CTRL" /tmp/p343-ctrl-before
cp "$REC" /tmp/p343-rec-before

stage "apply Qualcomm native-5.10 continuous-splash DRM state parity"
python3 -m py_compile scripts/343_apply_gki510_cont_splash_drm_state_parity.py
python3 scripts/343_apply_gki510_cont_splash_drm_state_parity.py \
  --root "$ROOT"
python3 scripts/343_apply_gki510_cont_splash_drm_state_parity.py \
  --root "$ROOT" --check-only --before /tmp/p343-kms-before

! cmp -s /tmp/p343-kms-before "$KMS"
cmp -s /tmp/p343-drv-before "$DRV"
cmp -s /tmp/p343-atomic-before "$ATOMIC"
cmp -s /tmp/p343-conn-before "$CONN"
cmp -s /tmp/p343-crtc-before "$CRTC"
cmp -s /tmp/p343-ctrl-before "$CTRL"
cmp -s /tmp/p343-rec-before "$REC"

diff -u /tmp/p343-kms-before "$KMS" > /tmp/p343-kms.diff || true
git diff --no-index --check /tmp/p343-kms-before "$KMS" \
  > /tmp/p343-kms-whitespace-check 2>&1 || true
test ! -s /tmp/p343-kms-whitespace-check

stage "strict two-assignment DRM-state scope audit"
python3 - "$KMS" <<'PY'
from pathlib import Path
import sys

before = Path("/tmp/p343-kms-before").read_text()
after = Path(sys.argv[1]).read_text()

mark = "A52_PHASE343_GKI510_CONT_SPLASH_DRM_STATE_PARITY_V1"
if after.count(mark) != 1:
    raise SystemExit("Phase343 marker count mismatch")

required = (
    "crtc->state->encoder_mask = (1 << drm_encoder_index(encoder));",
    "crtc->state->connector_mask = drm_connector_mask(connector);",
    "connector->state->crtc = crtc;",
)
for token in required:
    if token not in after:
        raise SystemExit("Phase343 required state token missing: " + token)

if after.count("drm_connector_mask(connector)") != before.count(
        "drm_connector_mask(connector)") + 1:
    raise SystemExit("Phase343 must add exactly one connector-mask assignment")

if after.count("connector->state->crtc = crtc;") != before.count(
        "connector->state->crtc = crtc;") + 1:
    raise SystemExit("Phase343 must add exactly one connector-to-CRTC assignment")

for token in (
    "drm_encoder_index(encoder)",
    "drm_atomic_set_mode_for_crtc(",
    "drm_mode_config_reset(",
    "drm_dev_register(",
    "sde_encoder_update_caps_for_cont_splash(",
    "sde_crtc_update_cont_splash_settings(",
    "_sde_kms_update_planes_for_cont_splash(",
):
    if before.count(token) != after.count(token):
        raise SystemExit("Phase343 changed inherited splash flow: " + token)

protected = (
    "writel_relaxed(", "writel(", "writeq_relaxed(", "readl_relaxed(",
    "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
    "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
    "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
    "reset_control_assert(", "reset_control_deassert(",
    "udelay(", "usleep_range(", "msleep(",
    "pm_runtime_get_sync(", "pm_runtime_put_sync(",
    "sde_power_resource_enable(",
)
for token in protected:
    if before.count(token) != after.count(token):
        raise SystemExit("Phase343 changed hardware behavior: " + token)

print("Phase343 strict DRM-state-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p343-phase335.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase343-olddefconfig.log 2>&1
cmp -s /tmp/p343-phase335.config "$BUILD/.config"

stage "compile incremental Phase343 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase343-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

# Preserve known inherited evidence markers. Phase343 itself intentionally adds
# no runtime logging and no new string to the binary.
grep -aFq 'A52 P335 QSMMUV500 sACR before=%08x after=%08x changed=%u lock=%u' "$IMAGE"
grep -aFq 'P276 332G q=2 dump=0' "$IMAGE"
grep -aFq 'P276 296A x r=%d' "$IMAGE"

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase343-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/343_apply_gki510_cont_splash_drm_state_parity.py \
   scripts/343_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p343-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$DRV" "$ATOMIC" "$CONN" "$CRTC" "$CTRL" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase335-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase335-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE335-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase343-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "343",
    "flavor": "gki",
    "name": "GKI510-CONT-SPLASH-DRM-STATE-PARITY-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "335",
    "source_diagnosis": (
        "TouchGrass 4.19 continuous-splash reconstruction sets CRTC active, "
        "mode and encoder_mask but does not seed crtc_state.connector_mask or "
        "connector_state.crtc. Android GKI 5.10 duplicates those fields into "
        "atomic transactions and drm_atomic_helper_check_modeset rejects an "
        "enabled CRTC with connector_mask==0 using -EINVAL. Qualcomm native "
        "5.10 SDE explicitly seeds both missing relationships during splash "
        "handoff."
    ),
    "change": (
        "During SDE continuous-splash reconstruction only, set "
        "crtc->state->connector_mask = drm_connector_mask(connector) and "
        "connector->state->crtc = crtc, matching Qualcomm native 5.10 DRM "
        "state bookkeeping."
    ),
    "sde_files_changed": ["sde_kms.c"],
    "assignments_added": 2,
    "mmio_changes": False,
    "clock_changes": False,
    "regulator_changes": False,
    "reset_changes": False,
    "dsi_command_changes": False,
    "scheduler_changes": False,
    "drm_validation_bypassed": False,
    "atomic_return_semantics_changed": False,
    "mode_config_reset_order_changed": False,
    "native_5_10_reference": (
        "Qualcomm DISPLAY.LA 5.10 SDE continuous-splash implementation seeds "
        "encoder_mask, connector_mask and connector->state->crtc before "
        "userspace takeover."
    ),
    "image_sha256": sha(r / "compile/Image"),
    "boot_img_sha256": sha(r / "package/boot.img"),
    "boot_img_size": (r / "package/boot.img").stat().st_size,
}
(r / "BUILD-IDENTITY.json").write_text(
    json.dumps(identity, indent=2, sort_keys=True) + "\n"
)
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage complete
echo 'Phase343 GKI 5.10 continuous-splash DRM state parity build: PASS'
trap - EXIT
