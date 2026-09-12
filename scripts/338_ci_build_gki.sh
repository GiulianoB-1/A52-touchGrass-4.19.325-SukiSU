#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase338-gki-out"
FAIL="$PWD/phase338-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase338 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase338-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p338-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/338_apply_atomic_latch_pre_retention_snapshot.py \
     scripts/338_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase337 baseline"
bash scripts/337_ci_build_gki.sh 2>&1 | tee phase338-phase337.log

for f in \
  phase337-gki-out/package/boot.img \
  phase337-gki-out/compile/Image \
  phase337-gki-out/config/final.config \
  "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase337-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE337_ATOMIC_CHECK_STAGE_PROBE_V1' "$KMS"
grep -Fq 'P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u' "$KMS"
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$CTRL"
grep -Fq 'a52_ackfr_retain_timeout_snapshot();' "$CTRL"

cp phase337-gki-out/config/final.config /tmp/p338-phase337.config
cp "$KMS" /tmp/p338-kms-before
cp "$CTRL" /tmp/p338-ctrl-before
cp "$DRV" /tmp/p338-drv-before
cp "$ATOMIC" /tmp/p338-atomic-before
cp "$REC" /tmp/p338-rec-before

stage "apply atomic latch pre-retention snapshot"
python3 -m py_compile scripts/338_apply_atomic_latch_pre_retention_snapshot.py
python3 scripts/338_apply_atomic_latch_pre_retention_snapshot.py --root "$ROOT"
python3 scripts/338_apply_atomic_latch_pre_retention_snapshot.py \
  --root "$ROOT" --check-only

! cmp -s /tmp/p338-kms-before "$KMS"
! cmp -s /tmp/p338-ctrl-before "$CTRL"
cmp -s /tmp/p338-drv-before "$DRV"
cmp -s /tmp/p338-atomic-before "$ATOMIC"
cmp -s /tmp/p338-rec-before "$REC"

diff -u /tmp/p338-kms-before "$KMS" > /tmp/p338-kms.diff || true
diff -u /tmp/p338-ctrl-before "$CTRL" > /tmp/p338-ctrl.diff || true

git diff --no-index --check /tmp/p338-kms-before "$KMS" \
  > /tmp/p338-kms-whitespace-check 2>&1 || true
git diff --no-index --check /tmp/p338-ctrl-before "$CTRL" \
  > /tmp/p338-ctrl-whitespace-check 2>&1 || true
test ! -s /tmp/p338-kms-whitespace-check
test ! -s /tmp/p338-ctrl-whitespace-check

stage "strict diagnostic-only scope audit"
python3 - "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" <<'PY'
from pathlib import Path
import sys

kms = Path(sys.argv[1]).read_text()
ctrl = Path(sys.argv[2]).read_text()
drv = Path(sys.argv[3]).read_text()
atomic = Path(sys.argv[4]).read_text()
rec = Path(sys.argv[5]).read_text()

bk = Path("/tmp/p338-kms-before").read_text()
bc = Path("/tmp/p338-ctrl-before").read_text()
bd = Path("/tmp/p338-drv-before").read_text()
ba = Path("/tmp/p338-atomic-before").read_text()
br = Path("/tmp/p338-rec-before").read_text()

if drv != bd or atomic != ba or rec != br:
    raise SystemExit("Phase338 changed a non-target source file")

for token in (
    "A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1",
    "P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d",
    "P276 338F v=1 n=%u r=%d ms=%d zp=%d pl=%d sv=%u se=%d cs=%x",
    "a52_p338_emit_atomic_latch();",
):
    if token not in kms + ctrl:
        raise SystemExit("Phase338 marker missing: " + token)

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
    before = bk.count(token) + bc.count(token)
    after = kms.count(token) + ctrl.count(token)
    if before != after:
        raise SystemExit(
            f"Phase338 changed protected behavior {token}: {before} -> {after}"
        )

for token in (
    "drm_atomic_helper_check_modeset(dev, state)",
    "drm_atomic_normalize_zpos(dev, state)",
    "drm_atomic_helper_check_planes(dev, state)",
    "drm_atomic_helper_async_check(dev, state)",
    "drm_self_refresh_helper_alter_state(state)",
    "sde_kms_check_secure_transition(kms, state)",
    "P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u",
    "P276 337B n=%u sec=%d",
    "P276 337C n=%u cs=%x nd=%u",
):
    if kms.count(token) != bk.count(token):
        raise SystemExit("Phase338 changed Phase337 semantics/token count: " + token)

for token in (
    "a52_ackfr_retain_timeout_snapshot();",
    "P276 280Z q=2",
    "P276 332A q=2 g=1 d=%u st=%x m=%x",
    "P276 332B q=2 retained=1",
):
    if ctrl.count(token) != bc.count(token):
        raise SystemExit("Phase338 changed timeout/freeze token count: " + token)

if not (
    ctrl.index("P276 332A q=2") <
    ctrl.index("a52_p338_emit_atomic_latch();") <
    ctrl.index("P276 280Z q=2") <
    ctrl.index("a52_ackfr_retain_timeout_snapshot();") <
    ctrl.index("P276 332B q=2")
):
    raise SystemExit("Phase338 atomic snapshot is not immediately pre-freeze")

if kms.count("a52_ackfr_record(") != bk.count("a52_ackfr_record(") + 2:
    raise SystemExit("Phase338 expected exactly two new recorder calls")
if ctrl.count("a52_ackfr_record(") != bc.count("a52_ackfr_record("):
    raise SystemExit("Phase338 unexpectedly added DSI recorder calls")

print("Phase338 strict diagnostic-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p338-phase337.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase338-olddefconfig.log 2>&1
cmp -s /tmp/p338-phase337.config "$BUILD/.config"

stage "compile incremental Phase338 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase338-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d' \
  'P276 338F v=1 n=%u r=%d ms=%d zp=%d pl=%d sv=%u se=%d cs=%x' \
  'P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u' \
  'P276 337B n=%u sec=%d' \
  'P276 337C n=%u cs=%x nd=%u' \
  'P276 332A q=2 g=1 d=%u st=%x m=%x' \
  'P276 332B q=2 retained=1'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase338-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/338_apply_atomic_latch_pre_retention_snapshot.py \
   scripts/338_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p338-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" "$OUT/source/"

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

r = Path("phase338-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "338",
    "flavor": "gki",
    "name": "ATOMIC-LATCH-PRE-RETENTION-SNAPSHOT-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "337",
    "phase337_hardware_finding": (
        "The phone was left in the failed boot for about five minutes, but the "
        "protected recorder retained only the early window and no 337A/B/C. "
        "The inherited Phase280 freeze admits only P276 331*/332* afterward."
    ),
    "change": (
        "Diagnostic-only: latch latest Phase337 atomic result and latest failing "
        "atomic result in RAM, then emit compact 338A/338F records at the exact-F0 "
        "q2 timeout immediately before the existing Phase280 retention freeze."
    ),
    "recorder_policy_changed": False,
    "retention_freeze_changed": False,
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
  find . -type f ! -name SHA256SUMS -print0 | sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage complete
echo 'Phase338 atomic latch pre-retention snapshot build: PASS'
trap - EXIT
