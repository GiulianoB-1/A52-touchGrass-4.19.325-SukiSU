#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase339-gki-out"
FAIL="$PWD/phase339-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase339 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase339-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p339-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/339_apply_warm_reboot_preservation_probe.py \
     scripts/339_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase338 baseline"
bash scripts/338_ci_build_gki.sh 2>&1 | tee phase339-phase338.log

for f in \
  phase338-gki-out/package/boot.img \
  phase338-gki-out/compile/Image \
  phase338-gki-out/config/final.config \
  "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase338-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1' "$KMS"
grep -Fq 'P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d' "$KMS"
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$REC"
grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$REC"

cp phase338-gki-out/config/final.config /tmp/p339-phase338.config
cp "$KMS" /tmp/p339-kms-before
cp "$CTRL" /tmp/p339-ctrl-before
cp "$DRV" /tmp/p339-drv-before
cp "$ATOMIC" /tmp/p339-atomic-before
cp "$REC" /tmp/p339-rec-before

stage "apply warm-reboot preservation probe"
python3 -m py_compile scripts/339_apply_warm_reboot_preservation_probe.py
python3 scripts/339_apply_warm_reboot_preservation_probe.py --root "$ROOT"
python3 scripts/339_apply_warm_reboot_preservation_probe.py \
  --root "$ROOT" --check-only

cmp -s /tmp/p339-kms-before "$KMS"
cmp -s /tmp/p339-ctrl-before "$CTRL"
cmp -s /tmp/p339-drv-before "$DRV"
cmp -s /tmp/p339-atomic-before "$ATOMIC"
! cmp -s /tmp/p339-rec-before "$REC"

diff -u /tmp/p339-rec-before "$REC" > /tmp/p339-rec.diff || true
git diff --no-index --check /tmp/p339-rec-before "$REC" \
  > /tmp/p339-rec-whitespace-check 2>&1 || true
test ! -s /tmp/p339-rec-whitespace-check

stage "strict preservation-only scope audit"
python3 - "$REC" "$KMS" "$CTRL" "$DRV" "$ATOMIC" <<'PY'
from pathlib import Path
import sys

rec = Path(sys.argv[1]).read_text()
kms = Path(sys.argv[2]).read_text()
ctrl = Path(sys.argv[3]).read_text()
drv = Path(sys.argv[4]).read_text()
atomic = Path(sys.argv[5]).read_text()

br = Path("/tmp/p339-rec-before").read_text()
bk = Path("/tmp/p339-kms-before").read_text()
bc = Path("/tmp/p339-ctrl-before").read_text()
bd = Path("/tmp/p339-drv-before").read_text()
ba = Path("/tmp/p339-atomic-before").read_text()

if kms != bk or ctrl != bc or drv != bd or atomic != ba:
    raise SystemExit("Phase339 changed a non-recorder source file")

for token in (
    "A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1",
    "P276 339A first=10 final=330 warm=1",
    "P276 339S t=%u q=%llu r=%u j=%lu",
    "P276 339R t=%u q=%llu r=%u warm=1",
    "P276 339E reboot_returned=1",
    'kernel_restart("recovery");',
    "10U, 12U, 15U, 30U, 60U, 120U, 180U, 240U, 300U, 330U",
    "fmt[7] == '9'",
):
    if token not in rec:
        raise SystemExit("Phase339 marker missing: " + token)

for token in (
    "a52_ackfr_retain_timeout_snapshot(void)",
    "atomic_set(&a52_r280_retained, 1);",
):
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase339 changed retention latch semantics: " + token)

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
        raise SystemExit("Phase339 changed protected hardware behavior: " + token)

if rec.count("kernel_restart(") != br.count("kernel_restart(") + 1:
    raise SystemExit("Phase339 expected one warm reboot call")
if rec.count("msleep(") != br.count("msleep(") + 1:
    raise SystemExit("Phase339 expected one pre-reboot sleep")
if rec.count("wmb(") != br.count("wmb(") + 2:
    raise SystemExit("Phase339 expected two explicit persistence-order barriers")
if rec.count("schedule_delayed_work(") != br.count("schedule_delayed_work(") + 2:
    raise SystemExit("Phase339 expected two preservation-worker schedule sites")
if rec.count("a52_ackfr_record(") != br.count("a52_ackfr_record(") + 4:
    raise SystemExit("Phase339 expected four new recorder calls")

print("Phase339 strict preservation-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p339-phase338.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase339-olddefconfig.log 2>&1
cmp -s /tmp/p339-phase338.config "$BUILD/.config"

stage "compile incremental Phase339 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase339-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 339A first=10 final=330 warm=1' \
  'P276 339S t=%u q=%llu r=%u j=%lu' \
  'P276 339R t=%u q=%llu r=%u warm=1' \
  'P276 339E reboot_returned=1' \
  'P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d' \
  'P276 338F v=1 n=%u r=%d ms=%d zp=%d pl=%d sv=%u se=%d cs=%x'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase339-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/339_apply_warm_reboot_preservation_probe.py \
   scripts/339_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p339-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase338-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase338-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE338-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase339-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "339",
    "flavor": "gki",
    "name": "WARM-REBOOT-PRESERVATION-PROBE-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "338",
    "finding_before_phase": (
        "Repeated five-minute tests recover the same valid 5.10 GKI R48 prefix "
        "ending near 11.932 s. The installed 4.19.206 Workflow-294 recovery "
        "kernel has PSTORE_RAM/CONSOLE/PMSG disabled and freezes the reserved "
        "1 MiB region at pure_initcall, so recovery is not generating the R48 data."
    ),
    "experiment": (
        "Emit P276 339 checkpoints at 10, 12, 15, 30, 60, 120, 180, 240, "
        "300 and 330 s. Admit only P276 339* through the inherited Phase280 "
        "post-retention gate. At 330 s, order writes, sleep 250 ms, and invoke "
        "kernel_restart(\\\"recovery\\\") to remove the manual hard-reset path."
    ),
    "display_logic_changed": False,
    "register_writes_added": 0,
    "clock_power_reset_regulator_changed": False,
    "phase280_freeze_setter_changed": False,
    "automatic_warm_reboot_seconds": 330,
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
echo 'Phase339 warm-reboot preservation probe build: PASS'
trap - EXIT
