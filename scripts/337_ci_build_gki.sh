#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase337-gki-out"
FAIL="$PWD/phase337-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
CORE_ATOMIC="$ROOT/drivers/gpu/drm/drm_atomic_helper.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase337 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase337-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p337-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/337_apply_atomic_check_stage_probe.py scripts/337_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$DRV" "$ATOMIC" "$REC" "$CORE_ATOMIC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase336 baseline"
bash scripts/336_ci_build_gki.sh 2>&1 | tee phase337-phase336.log

for f in \
  phase336-gki-out/package/boot.img \
  phase336-gki-out/compile/Image \
  phase336-gki-out/config/final.config \
  "$KMS" "$DRV" "$ATOMIC" "$REC" "$CORE_ATOMIC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase336-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE336_QSMMUV500_HF0_TBU_STATE_RECORDER_V1' \
  "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
grep -Fq 'P276 296A x r=%d' "$DRV"
grep -Fq 'P276 296K p' "$KMS"
grep -Fq 'cont_splash_enabled' "$KMS"

stage "verify pinned GKI atomic-helper contract"
test -s "$ROOT/include/drm/drm_atomic_helper.h"
test -s "$ROOT/include/drm/drm_blend.h"
test -s "$ROOT/include/drm/drm_self_refresh_helper.h"
python3 - "$CORE_ATOMIC" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text()
start = text.find("int drm_atomic_helper_check(struct drm_device *dev,")
if start < 0:
    raise SystemExit("Phase337: drm_atomic_helper_check definition not found")
end = text.find("EXPORT_SYMBOL(drm_atomic_helper_check);", start)
if end < 0:
    raise SystemExit("Phase337: drm_atomic_helper_check export not found")
fn = text[start:end]

ordered = (
    "drm_atomic_helper_check_modeset(dev, state)",
    "dev->mode_config.normalize_zpos",
    "drm_atomic_normalize_zpos(dev, state)",
    "drm_atomic_helper_check_planes(dev, state)",
    "state->legacy_cursor_update",
    "drm_atomic_helper_async_check(dev, state)",
    "drm_self_refresh_helper_alter_state(state)",
)
pos = -1
for token in ordered:
    nxt = fn.find(token, pos + 1)
    if nxt < 0:
        raise SystemExit("Phase337 pinned helper contract missing: " + token)
    if nxt <= pos:
        raise SystemExit("Phase337 pinned helper contract order mismatch: " + token)
    pos = nxt

for token, expected in (
    ("drm_atomic_helper_check_modeset(dev, state)", 1),
    ("drm_atomic_normalize_zpos(dev, state)", 1),
    ("drm_atomic_helper_check_planes(dev, state)", 1),
    ("drm_atomic_helper_async_check(dev, state)", 1),
    ("drm_self_refresh_helper_alter_state(state)", 1),
):
    if fn.count(token) != expected:
        raise SystemExit(
            f"Phase337 pinned helper contract count mismatch {token}: "
            f"{fn.count(token)} != {expected}"
        )
print("Phase337 pinned Android12-5.10 atomic-helper contract: PASS")
PY

cp phase336-gki-out/config/final.config /tmp/p337-phase336.config
cp "$KMS" /tmp/p337-kms-before
cp "$DRV" /tmp/p337-drv-before
cp "$ATOMIC" /tmp/p337-atomic-before
cp "$REC" /tmp/p337-rec-before

stage "apply DRM atomic-check stage probe"
python3 -m py_compile scripts/337_apply_atomic_check_stage_probe.py
python3 scripts/337_apply_atomic_check_stage_probe.py --root "$ROOT"
python3 scripts/337_apply_atomic_check_stage_probe.py \
  --root "$ROOT" --check-only --before-kms /tmp/p337-kms-before

! cmp -s /tmp/p337-kms-before "$KMS"
cmp -s /tmp/p337-drv-before "$DRV"
cmp -s /tmp/p337-atomic-before "$ATOMIC"
cmp -s /tmp/p337-rec-before "$REC"

diff -u /tmp/p337-kms-before "$KMS" > /tmp/p337-kms.diff || true
git -C "$ROOT" diff --check -- drivers/a52_display/msm/sde/sde_kms.c

stage "strict Phase337 diagnostic-only scope audit"
python3 - "$KMS" <<'PY'
from pathlib import Path
import sys

before = Path("/tmp/p337-kms-before").read_text()
after = Path(sys.argv[1]).read_text()

required = (
    "A52_PHASE337_ATOMIC_CHECK_STAGE_PROBE_V1",
    "P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u",
    "P276 337B n=%u sec=%d",
    "P276 337C n=%u cs=%x nd=%u",
    "drm_self_refresh_helper_alter_state(state)",
)
for token in required:
    if token not in after:
        raise SystemExit("Phase337 marker missing: " + token)

protected = (
    "writel_relaxed(", "writel(", "writeq_relaxed(", "readl_relaxed(",
    "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
    "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
    "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
    "reset_control_assert(", "reset_control_deassert(",
    "udelay(", "usleep_range(", "msleep(", "wmb(", "mb(",
    "pm_runtime_get_sync(", "pm_runtime_put_sync(",
    "sde_power_resource_enable(",
)
for token in protected:
    if before.count(token) != after.count(token):
        raise SystemExit(
            f"Phase337 changed protected behavior {token}: "
            f"{before.count(token)} -> {after.count(token)}"
        )

if after.count("drm_atomic_helper_check(dev, state)") != \
        before.count("drm_atomic_helper_check(dev, state)") - 1:
    raise SystemExit("Phase337 expected exactly one helper-wrapper replacement")
for token in (
    "drm_atomic_helper_check_modeset(dev, state)",
    "drm_atomic_normalize_zpos(dev, state)",
    "drm_atomic_helper_check_planes(dev, state)",
    "drm_atomic_helper_async_check(dev, state)",
    "drm_self_refresh_helper_alter_state(state)",
):
    if after.count(token) != before.count(token) + 1:
        raise SystemExit("Phase337 expected exactly one added stage call: " + token)

if after.count("sde_kms_check_secure_transition(kms, state)") != \
        before.count("sde_kms_check_secure_transition(kms, state)"):
    raise SystemExit("Phase337 changed secure-transition call count")
if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 3:
    raise SystemExit("Phase337 expected exactly three recorder calls")

print("Phase337 strict diagnostic-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p337-phase336.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase337-olddefconfig.log 2>&1
cmp -s /tmp/p337-phase336.config "$BUILD/.config"

stage "compile incremental Phase337 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase337-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

grep -aFq 'P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u' "$IMAGE"
grep -aFq 'P276 337B n=%u sec=%d' "$IMAGE"
grep -aFq 'P276 337C n=%u cs=%x nd=%u' "$IMAGE"
grep -aFq 'P276 296A x r=%d' "$IMAGE"
grep -aFq 'P276 336A q=%u v=%u sb=%x sa=%x sc=%x tv=%u ts=%x' "$IMAGE"
grep -aFq 'P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u' "$IMAGE"

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase337-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/337_apply_atomic_check_stage_probe.py scripts/337_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p337-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$DRV" "$ATOMIC" "$REC" "$CORE_ATOMIC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase336-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase336-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE336-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase337-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "337",
    "flavor": "gki",
    "name": "DRM-ATOMIC-CHECK-STAGE-PROBE-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "336",
    "phase336_hardware_finding": (
        "apps-SMMU sACR stayed 0x1c at q0/q1/q2 and HF0 TBU status stayed "
        "0x1, closing the QSMMUv500/sACR/HF0-TBU line. Exact F0 still stalls "
        "at st=3 with irq=0 and DMA_DONE=0."
    ),
    "atomic_finding": (
        "composer atomic checks repeatedly return -EINVAL while an unrelated "
        "caller can return 0; continuous splash remains enabled before exact F0."
    ),
    "change": (
        "Diagnostic-only decomposition of the pinned GKI drm_atomic_helper_check "
        "stages inside sde_kms_atomic_check, preserving modeset, zpos, plane, "
        "legacy cursor async, self-refresh alteration, secure-transition order, "
        "and return values while recording stage results and splash state."
    ),
    "sde_kms_only_patch": True,
    "register_writes_added": 0,
    "clock_power_reset_delay_changed": False,
    "drm_atomic_return_semantics_changed": False,
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
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage complete
echo 'Phase337 DRM atomic-check stage probe build: PASS'
trap - EXIT
