#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase343-gki-out"
FAIL="$PWD/phase343-gki-failure"

ENC="$ROOT/drivers/a52_display/msm/sde/sde_encoder.c"
RM="$ROOT/drivers/a52_display/msm/sde/sde_rm.c"
CRTC="$ROOT/drivers/a52_display/msm/sde/sde_crtc.c"
KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
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
  cp scripts/343_apply_drm_rm_reservation_state_probe.py \
     scripts/343_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$ENC" "$RM" "$CRTC" "$KMS" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && \
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase337 baseline"
bash scripts/337_ci_build_gki.sh 2>&1 | tee phase343-phase337.log

for f in \
  phase337-gki-out/package/boot.img \
  phase337-gki-out/compile/Image \
  phase337-gki-out/config/final.config \
  "$ENC" "$RM" "$CRTC" "$KMS" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase337-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE337_ATOMIC_CHECK_STAGE_PROBE_V1' "$KMS"
grep -Fq 'P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u' "$KMS"
grep -Fq 'P276 296A x r=%d' "$ROOT/drivers/a52_display/msm/msm_drv.c"

cp phase337-gki-out/config/final.config /tmp/p343-phase337.config
cp "$ENC" /tmp/p343-encoder-before
cp "$RM" /tmp/p343-rm-before
cp "$CRTC" /tmp/p343-crtc-before

stage "apply Phase343 reservation-state discriminator"
python3 -m py_compile scripts/343_apply_drm_rm_reservation_state_probe.py
python3 scripts/343_apply_drm_rm_reservation_state_probe.py \
  --root "$ROOT" --report /tmp/p343-report.json
python3 scripts/343_apply_drm_rm_reservation_state_probe.py \
  --root "$ROOT" --check-only

! cmp -s /tmp/p343-encoder-before "$ENC"
! cmp -s /tmp/p343-rm-before "$RM"
! cmp -s /tmp/p343-crtc-before "$CRTC"

diff -u /tmp/p343-encoder-before "$ENC" > /tmp/p343-encoder.diff || true
diff -u /tmp/p343-rm-before "$RM" > /tmp/p343-rm.diff || true
diff -u /tmp/p343-crtc-before "$CRTC" > /tmp/p343-crtc.diff || true

for pair in \
  "/tmp/p343-encoder-before:$ENC" \
  "/tmp/p343-rm-before:$RM" \
  "/tmp/p343-crtc-before:$CRTC"; do
  before="${pair%%:*}"
  after="${pair#*:}"
  git diff --no-index --check "$before" "$after" \
    >> /tmp/p343-whitespace-check 2>&1 || true
done
test ! -s /tmp/p343-whitespace-check

stage "strict diagnostic-only scope audit"
python3 - "$ENC" "$RM" "$CRTC" <<'PY'
from pathlib import Path
import sys

pairs = (
    (Path("/tmp/p343-encoder-before"), Path(sys.argv[1])),
    (Path("/tmp/p343-rm-before"), Path(sys.argv[2])),
    (Path("/tmp/p343-crtc-before"), Path(sys.argv[3])),
)
protected = (
    "writel(", "writel_relaxed(", "writeq_relaxed(", "readl_relaxed(",
    "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
    "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
    "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
    "reset_control_assert(", "reset_control_deassert(",
    "udelay(", "usleep_range(", "msleep(", "wmb(", "mb(",
    "pm_runtime_get_sync(", "pm_runtime_put_sync(",
    "sde_power_resource_enable(",
)
for before_path, after_path in pairs:
    before = before_path.read_text()
    after = after_path.read_text()
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f"Phase343 changed protected behavior {after_path} {token}: "
                f"{before.count(token)} -> {after.count(token)}"
            )

required = {
    Path(sys.argv[1]): (
        "P276 343E n=%u e=%u c=%u ea=%u/%u em=%x cm=%x ch=%u/%u/%u cn=%u be=%u",
        "P276 343Q n=%u e=%u rm=%d em=%x cm=%x",
    ),
    Path(sys.argv[2]): (
        "P276 343R n=%u t=%u e=%u cur=%u nxt=%u sp=%u em=%x cm=%x",
        "P276 343T n=%u e=%u cur=%u nxt=%u em=%x cm=%x",
        "P276 343N n=%u e=%u nxt=%u lock=0",
        "P276 343X n=%u e=%u t=%u ret=%d",
    ),
    Path(sys.argv[3]): (
        "P276 343D n=%u c=%u ea=%u/%u em=%x cm=%x",
        "P276 343L n=%u c=%u e=%u",
    ),
}
for path, markers in required.items():
    text = path.read_text()
    for marker in markers:
        if text.count(marker) != 1:
            raise SystemExit(f"Phase343 marker count mismatch {path}: {marker}")

print("Phase343 strict diagnostic-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p343-phase337.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase343-olddefconfig.log 2>&1
cmp -s /tmp/p343-phase337.config "$BUILD/.config"

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

for marker in \
  'P276 343E n=%u e=%u c=%u ea=%u/%u em=%x cm=%x ch=%u/%u/%u cn=%u be=%u' \
  'P276 343Q n=%u e=%u rm=%d em=%x cm=%x' \
  'P276 343R n=%u t=%u e=%u cur=%u nxt=%u sp=%u em=%x cm=%x' \
  'P276 343T n=%u e=%u cur=%u nxt=%u em=%x cm=%x' \
  'P276 343N n=%u e=%u nxt=%u lock=0' \
  'P276 343X n=%u e=%u t=%u ret=%d' \
  'P276 343D n=%u c=%u ea=%u/%u em=%x cm=%x' \
  'P276 343L n=%u c=%u e=%u' \
  'P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase343-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/343_apply_drm_rm_reservation_state_probe.py \
   scripts/343_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p343-* "$OUT/audit/" 2>/dev/null || true
cp "$ENC" "$RM" "$CRTC" "$KMS" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase337-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase337-gki-out/BUILD-IDENTITY.json \
   "$OUT/audit/PHASE337-BASE-BUILD-IDENTITY.json"

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
    "name": "DRM-RM-RESERVATION-STATE-DISCRIMINATOR-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "337",
    "reason": (
        "TouchGrass TEST_ONLY RM reservations are released from "
        "sde_crtc_destroy_state only for encoders present in the destroyed "
        "CRTC state's encoder_mask. Persistent composer -EINVAL may therefore "
        "be caused by a stale rsvp_nxt lifecycle or by an earlier modeset "
        "failure. Phase343 records both sides without changing behavior."
    ),
    "change": (
        "Diagnostic-only SDE encoder/RM/CRTC reservation lifecycle tracing, "
        "retaining Phase337 atomic-helper stage decomposition."
    ),
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
echo 'Phase343 DRM RM reservation state discriminator build: PASS'
trap - EXIT
