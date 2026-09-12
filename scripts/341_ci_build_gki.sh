#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase341-gki-out"
FAIL="$PWD/phase341-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase341 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase341-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p341-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/341_apply_hardirq_timer_frontier.py \
     scripts/341_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase340 baseline"
bash scripts/340_ci_build_gki.sh 2>&1 | tee phase341-phase340.log

for f in \
  phase340-gki-out/package/boot.img \
  phase340-gki-out/compile/Image \
  phase340-gki-out/config/final.config \
  "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase340-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1' "$REC"
grep -Fq 'P276 340K t=%u q=%llu r=%u' "$REC"
grep -Fq 'A52_PHASE339_WARM_REBOOT_PRESERVATION_PROBE_V1' "$REC"
grep -Fq 'A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1' "$KMS"

cp phase340-gki-out/config/final.config /tmp/p341-phase340.config
cp "$KMS" /tmp/p341-kms-before
cp "$CTRL" /tmp/p341-ctrl-before
cp "$DRV" /tmp/p341-drv-before
cp "$ATOMIC" /tmp/p341-atomic-before
cp "$REC" /tmp/p341-rec-before

stage "apply hardirq timer frontier"
python3 -m py_compile scripts/341_apply_hardirq_timer_frontier.py
python3 scripts/341_apply_hardirq_timer_frontier.py --root "$ROOT"
python3 scripts/341_apply_hardirq_timer_frontier.py \
  --root "$ROOT" --check-only

cmp -s /tmp/p341-kms-before "$KMS"
cmp -s /tmp/p341-ctrl-before "$CTRL"
cmp -s /tmp/p341-drv-before "$DRV"
cmp -s /tmp/p341-atomic-before "$ATOMIC"
! cmp -s /tmp/p341-rec-before "$REC"

diff -u /tmp/p341-rec-before "$REC" > /tmp/p341-rec.diff || true
git diff --no-index --check /tmp/p341-rec-before "$REC" \
  > /tmp/p341-rec-whitespace-check 2>&1 || true
test ! -s /tmp/p341-rec-whitespace-check

stage "strict hardirq-only scope audit"
python3 - "$REC" "$KMS" "$CTRL" "$DRV" "$ATOMIC" <<'PY'
from pathlib import Path
import sys

rec = Path(sys.argv[1]).read_text()
kms = Path(sys.argv[2]).read_text()
ctrl = Path(sys.argv[3]).read_text()
drv = Path(sys.argv[4]).read_text()
atomic = Path(sys.argv[5]).read_text()

br = Path("/tmp/p341-rec-before").read_text()
bk = Path("/tmp/p341-kms-before").read_text()
bc = Path("/tmp/p341-ctrl-before").read_text()
bd = Path("/tmp/p341-drv-before").read_text()
ba = Path("/tmp/p341-atomic-before").read_text()

if kms != bk or ctrl != bc or drv != bd or atomic != ba:
    raise SystemExit("Phase341 changed a non-recorder source file")

for token in (
    "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1",
    "A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL",
    "A52_R341_INTERVAL_MS 500U",
    "A52_R341_LIMIT 30U",
    "P276 341A map=1 mask=%x int=%u lim=%u",
    "hrtimer_forward_now(timer, ms_to_ktime(A52_R341_INTERVAL_MS));",
    "smp_call_function_single(cpu, a52_r341_start_cpu, NULL, 1);",
    "__flush_dcache_area(dst0, sizeof(slot));",
    "__flush_dcache_area(dst1, sizeof(slot));",
):
    if token not in rec:
        raise SystemExit("Phase341 marker missing: " + token)

for token in (
    "A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1",
    "P276 340A map=%u pm=%d kt=%ld",
    "P276 340K t=%u q=%llu r=%u",
    "P276 340P e=%lu q=%llu r=%u",
    "P276 340R t=60 warm=1",
):
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase341 changed Phase340 token count: " + token)

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
        raise SystemExit("Phase341 changed protected hardware behavior: " + token)

if rec.count("hrtimer_start(") != br.count("hrtimer_start(") + 1:
    raise SystemExit("Phase341 expected one hrtimer start call site")
if rec.count("smp_call_function_single(") != br.count("smp_call_function_single(") + 1:
    raise SystemExit("Phase341 expected one SMP call site")
if rec.count("a52_ackfr_record(") != br.count("a52_ackfr_record(") + 2:
    raise SystemExit("Phase341 expected two arm recorder call sites")
if rec.count("__flush_dcache_area(") != br.count("__flush_dcache_area(") + 2:
    raise SystemExit("Phase341 expected two mirrored cache-clean sites")

print("Phase341 strict hardirq-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p341-phase340.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase341-olddefconfig.log 2>&1
cmp -s /tmp/p341-phase340.config "$BUILD/.config"

stage "compile incremental Phase341 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase341-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 341A map=1 mask=%x int=%u lim=%u' \
  'P276 340A map=%u pm=%d kt=%ld' \
  'P276 340K t=%u q=%llu r=%u' \
  'P276 339S t=%u q=%llu r=%u j=%lu' \
  'P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase341-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/341_apply_hardirq_timer_frontier.py \
     scripts/341_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p341-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase340-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase340-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE340-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase341-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "339",
    "flavor": "gki",
    "name": "HARDIRQ-TIMER-FRONTIER-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "340",
    "finding_before_phase": (
        "Phase340 proves both system delayed_work and a dedicated kthread run at "
        "11.740 s, then both stop before their 12 s checkpoint. A cache-cleaned "
        "raw sideband independently confirms the kthread t=10 event. No PM "
        "suspend/resume notifier fires."
    ),
    "experiment": (
        "Start pinned 500 ms hrtimers on CPU0, CPU5 and CPU7. Their callbacks "
        "write mirrored raw sideband slots only, with cache clean to PoC, for "
        "30 ticks. This bypasses scheduler threads, workqueues and R48."
    ),
    "display_logic_changed": False,
    "register_writes_added": 0,
    "clock_power_reset_regulator_changed": False,
    "phase280_freeze_setter_changed": False,
    "automatic_warm_reboot_seconds": null,
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
echo 'Phase341 hardirq timer frontier build: PASS'
trap - EXIT
