#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase340-gki-out"
FAIL="$PWD/phase340-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase340 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase340-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p340-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/340_apply_post12_runtime_discriminator.py \
     scripts/340_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase339 baseline"
bash scripts/339_ci_build_gki.sh 2>&1 | tee phase340-phase339.log

for f in \
  phase339-gki-out/package/boot.img \
  phase339-gki-out/compile/Image \
  phase339-gki-out/config/final.config \
  "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase339-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1' "$REC"
grep -Fq 'P276 339S t=%u q=%llu r=%u j=%lu' "$REC"
grep -Fq 'A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1' "$KMS"
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$REC"

cp phase339-gki-out/config/final.config /tmp/p340-phase339.config
cp "$KMS" /tmp/p340-kms-before
cp "$CTRL" /tmp/p340-ctrl-before
cp "$DRV" /tmp/p340-drv-before
cp "$ATOMIC" /tmp/p340-atomic-before
cp "$REC" /tmp/p340-rec-before

stage "apply post-12s runtime discriminator"
python3 -m py_compile scripts/340_apply_post12_runtime_discriminator.py
python3 scripts/340_apply_post12_runtime_discriminator.py --root "$ROOT"
python3 scripts/340_apply_post12_runtime_discriminator.py \
  --root "$ROOT" --check-only

cmp -s /tmp/p340-kms-before "$KMS"
cmp -s /tmp/p340-ctrl-before "$CTRL"
cmp -s /tmp/p340-drv-before "$DRV"
cmp -s /tmp/p340-atomic-before "$ATOMIC"
! cmp -s /tmp/p340-rec-before "$REC"

diff -u /tmp/p340-rec-before "$REC" > /tmp/p340-rec.diff || true
git diff --no-index --check /tmp/p340-rec-before "$REC" \
  > /tmp/p340-rec-whitespace-check 2>&1 || true
test ! -s /tmp/p340-rec-whitespace-check

stage "strict post-12s discriminator scope audit"
python3 - "$REC" "$KMS" "$CTRL" "$DRV" "$ATOMIC" <<'PY'
from pathlib import Path
import sys

rec = Path(sys.argv[1]).read_text()
kms = Path(sys.argv[2]).read_text()
ctrl = Path(sys.argv[3]).read_text()
drv = Path(sys.argv[4]).read_text()
atomic = Path(sys.argv[5]).read_text()

br = Path("/tmp/p340-rec-before").read_text()
bk = Path("/tmp/p340-kms-before").read_text()
bc = Path("/tmp/p340-ctrl-before").read_text()
bd = Path("/tmp/p340-drv-before").read_text()
ba = Path("/tmp/p340-atomic-before").read_text()

if kms != bk or ctrl != bc or drv != bd or atomic != ba:
    raise SystemExit("Phase340 changed a non-recorder source file")

for token in (
    "A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1",
    "A52_R340_SIDEBAND_PHYS 0xB1BFF800ULL",
    "P276 340A map=%u pm=%d kt=%ld",
    "P276 340K t=%u q=%llu r=%u",
    "P276 340P e=%lu q=%llu r=%u",
    "P276 340R t=60 warm=1",
    "__flush_dcache_area(dst, sizeof(slot));",
    'kthread_run(a52_r340_thread_fn, NULL,',
):
    if token not in rec:
        raise SystemExit("Phase340 marker missing: " + token)

for token in (
    "A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1",
    "P276 339A first=10 final=330 warm=1",
    "P276 339S t=%u q=%llu r=%u j=%lu",
    "P276 339R t=%u q=%llu r=%u warm=1",
):
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase340 changed Phase339 token count: " + token)

protected = (
    "writel_relaxed(", "writel(", "writeq_relaxed(", "readl_relaxed(",
    "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
    "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
    "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
    "reset_control_assert(", "reset_control_deassert(",
    "udelay(", "usleep_range(",
    "pm_runtime_get_sync(", "pm_runtime_put_sync(",
    "sde_power_resource_enable(",
)
for token in protected:
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase340 changed protected hardware behavior: " + token)

if rec.count("kernel_restart(") != br.count("kernel_restart(") + 1:
    raise SystemExit("Phase340 expected one additional warm restart call")
if rec.count("a52_ackfr_record(") != br.count("a52_ackfr_record(") + 4:
    raise SystemExit("Phase340 expected four new R48 call sites")
if rec.count("__flush_dcache_area(") != br.count("__flush_dcache_area(") + 1:
    raise SystemExit("Phase340 expected one sideband cache-clean site")

print("Phase340 strict post-12s discriminator scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p340-phase339.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase340-olddefconfig.log 2>&1
cmp -s /tmp/p340-phase339.config "$BUILD/.config"

stage "compile incremental Phase340 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase340-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 340A map=%u pm=%d kt=%ld' \
  'P276 340K t=%u q=%llu r=%u' \
  'P276 340P e=%lu q=%llu r=%u' \
  'P276 340R t=60 warm=1' \
  'P276 339A first=10 final=330 warm=1' \
  'P276 339S t=%u q=%llu r=%u j=%lu' \
  'P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase340-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/340_apply_post12_runtime_discriminator.py \
   scripts/340_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p340-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase339-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase339-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE339-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase340-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "339",
    "flavor": "gki",
    "name": "POST12-RUNTIME-DISCRIMINATOR-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "339",
    "finding_before_phase": (
        "Phase339 proves the recorder reaches P276 339S t=10 at 11.740 s and "
        "TRIPOST t=10 at 11.932 s, but the scheduled 12 s checkpoint and every "
        "later delayed-work recorder vanish. Phase280 retention was still zero."
    ),
    "experiment": (
        "Add a dedicated kthread checkpoint stream independent of system_wq, "
        "PM suspend/resume notifications, and a raw cache-cleaned sideband in "
        "the final 2 KiB of the lightly-used pmsg zone. Warm reboot from the "
        "dedicated kthread at 60 s if scheduler execution survives."
    ),
    "display_logic_changed": False,
    "register_writes_added": 0,
    "clock_power_reset_regulator_changed": False,
    "phase280_freeze_setter_changed": False,
    "automatic_warm_reboot_seconds": 60,
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
echo 'Phase340 post-12s runtime discriminator build: PASS'
trap - EXIT
