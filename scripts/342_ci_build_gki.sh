#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase342-gki-out"
FAIL="$PWD/phase342-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase342 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase342-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p342-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/342_apply_busyloop_counter_frontier.py \
     scripts/342_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase341 baseline"
bash scripts/341_ci_build_gki.sh 2>&1 | tee phase342-phase341.log

for f in \
  phase341-gki-out/package/boot.img \
  phase341-gki-out/compile/Image \
  phase341-gki-out/config/final.config \
  "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase341-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1' "$REC"
grep -Fq 'P276 341A map=1 mask=%x int=%u lim=%u' "$REC"
grep -Fq 'A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1' "$REC"
grep -Fq 'A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1' "$KMS"

cp phase341-gki-out/config/final.config /tmp/p342-phase341.config
cp "$KMS" /tmp/p342-kms-before
cp "$CTRL" /tmp/p342-ctrl-before
cp "$DRV" /tmp/p342-drv-before
cp "$ATOMIC" /tmp/p342-atomic-before
cp "$REC" /tmp/p342-rec-before

stage "apply busyloop counter frontier"
python3 -m py_compile scripts/342_apply_busyloop_counter_frontier.py
python3 scripts/342_apply_busyloop_counter_frontier.py --root "$ROOT"
python3 scripts/342_apply_busyloop_counter_frontier.py \
  --root "$ROOT" --check-only

cmp -s /tmp/p342-kms-before "$KMS"
cmp -s /tmp/p342-ctrl-before "$CTRL"
cmp -s /tmp/p342-drv-before "$DRV"
cmp -s /tmp/p342-atomic-before "$ATOMIC"
! cmp -s /tmp/p342-rec-before "$REC"

diff -u /tmp/p342-rec-before "$REC" > /tmp/p342-rec.diff || true
git diff --no-index --check /tmp/p342-rec-before "$REC" \
  > /tmp/p342-rec-whitespace-check 2>&1 || true
test ! -s /tmp/p342-rec-whitespace-check

stage "strict busyloop-only scope audit"
python3 - "$REC" "$KMS" "$CTRL" "$DRV" "$ATOMIC" <<'PY'
from pathlib import Path
import sys

rec = Path(sys.argv[1]).read_text()
kms = Path(sys.argv[2]).read_text()
ctrl = Path(sys.argv[3]).read_text()
drv = Path(sys.argv[4]).read_text()
atomic = Path(sys.argv[5]).read_text()

br = Path("/tmp/p342-rec-before").read_text()
bk = Path("/tmp/p342-kms-before").read_text()
bc = Path("/tmp/p342-ctrl-before").read_text()
bd = Path("/tmp/p342-drv-before").read_text()
ba = Path("/tmp/p342-atomic-before").read_text()

if kms != bk or ctrl != bc or drv != bd or atomic != ba:
    raise SystemExit("Phase342 changed a non-recorder source file")

for token in (
    "A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1",
    "A52_R342_SIDEBAND_PHYS 0xB1BFA000ULL",
    "A52_R342_INTERVAL_NS 250000000ULL",
    "A52_R342_LIMIT 60U",
    "A52_R342_CPU 5U",
    "P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u",
    "sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);",
    "kthread_bind(a52_r342_task, A52_R342_CPU);",
    "__flush_dcache_area(dst0, sizeof(slot));",
    "__flush_dcache_area(dst1, sizeof(slot));",
):
    if token not in rec:
        raise SystemExit("Phase342 marker missing: " + token)

for token in (
    "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1",
    "P276 341A map=1 mask=%x int=%u lim=%u",
    "A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL",
):
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase342 changed Phase341 token count: " + token)

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
        raise SystemExit("Phase342 changed protected hardware behavior: " + token)

if rec.count("kthread_create(") != br.count("kthread_create(") + 1:
    raise SystemExit("Phase342 expected one kthread_create call")
if rec.count("kthread_bind(") != br.count("kthread_bind(") + 1:
    raise SystemExit("Phase342 expected one kthread_bind call")
if rec.count("a52_ackfr_record(") != br.count("a52_ackfr_record(") + 1:
    raise SystemExit("Phase342 expected one new R48 arm call")
if rec.count("__flush_dcache_area(") != br.count("__flush_dcache_area(") + 2:
    raise SystemExit("Phase342 expected two mirrored cache-clean sites")

print("Phase342 strict busyloop-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p342-phase341.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase342-olddefconfig.log 2>&1
cmp -s /tmp/p342-phase341.config "$BUILD/.config"

stage "compile incremental Phase342 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase342-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

for marker in \
  'P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u' \
  'P276 341A map=1 mask=%x int=%u lim=%u' \
  'P276 340A map=%u pm=%d kt=%ld' \
  'P276 339S t=%u q=%llu r=%u j=%lu' \
  'P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase342-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/342_apply_busyloop_counter_frontier.py \
     scripts/342_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p342-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase341-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase341-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE341-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase342-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "342",
    "flavor": "gki",
    "name": "BUSYLOOP-COUNTER-FRONTIER-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "341",
    "finding_before_phase": (
        "Phase341 shows pinned hard-IRQ hrtimers on CPU0, CPU5 and CPU7 all "
        "reach tick 20 at about 11.72 s, but none reaches tick 21 at about "
        "12.22 s. Scheduler, workqueue and R48 explanations are therefore closed."
    ),
    "experiment": (
        "Keep CPU5 continuously runnable in an RT FIFO busy loop, polling "
        "ktime_get_ns directly and writing mirrored cache-cleaned raw sideband "
        "checkpoints every 250 ms for 60 ticks. This does not sleep or require "
        "timer IRQ delivery for each checkpoint."
    ),
    "display_logic_changed": False,
    "register_writes_added": 0,
    "clock_power_reset_regulator_changed": False,
    "phase280_freeze_setter_changed": False,
    "automatic_warm_reboot_seconds": None,
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
echo 'Phase342 busyloop counter frontier build: PASS'
trap - EXIT
