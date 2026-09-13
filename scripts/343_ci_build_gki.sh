#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase343-gki-out"
FAIL="$PWD/phase343-gki-failure"

KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
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
  cp scripts/343_apply_instruction_counter_frontier.py \
     scripts/343_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] &&
    cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase342 baseline"
bash scripts/342_ci_build_gki.sh 2>&1 | tee phase343-phase342.log

for f in \
  phase342-gki-out/package/boot.img \
  phase342-gki-out/compile/Image \
  phase342-gki-out/config/final.config \
  "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase342-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u' "$REC"
grep -Fq 'A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1' "$REC"
grep -Fq 'A52_PHASE340_POST12_RUNTIME_DISCRIMINATOR_V1' "$REC"
grep -Fq 'A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1' "$KMS"

cp phase342-gki-out/config/final.config /tmp/p343-phase342.config
cp "$KMS" /tmp/p343-kms-before
cp "$CTRL" /tmp/p343-ctrl-before
cp "$DRV" /tmp/p343-drv-before
cp "$ATOMIC" /tmp/p343-atomic-before
cp "$REC" /tmp/p343-rec-before

stage "apply clock-independent instruction-counter frontier"
python3 -m py_compile scripts/343_apply_instruction_counter_frontier.py
python3 scripts/343_apply_instruction_counter_frontier.py --root "$ROOT"
python3 scripts/343_apply_instruction_counter_frontier.py \
  --root "$ROOT" --check-only

cmp -s /tmp/p343-kms-before "$KMS"
cmp -s /tmp/p343-ctrl-before "$CTRL"
cmp -s /tmp/p343-drv-before "$DRV"
cmp -s /tmp/p343-atomic-before "$ATOMIC"
! cmp -s /tmp/p343-rec-before "$REC"

diff -u /tmp/p343-rec-before "$REC" > /tmp/p343-rec.diff || true
git diff --no-index --check /tmp/p343-rec-before "$REC" \
  > /tmp/p343-rec-whitespace-check 2>&1 || true
test ! -s /tmp/p343-rec-whitespace-check

stage "strict instruction-counter-only scope audit"
python3 - "$REC" "$KMS" "$CTRL" "$DRV" "$ATOMIC" <<'PY'
from pathlib import Path
import sys

rec = Path(sys.argv[1]).read_text()
kms = Path(sys.argv[2]).read_text()
ctrl = Path(sys.argv[3]).read_text()
drv = Path(sys.argv[4]).read_text()
atomic = Path(sys.argv[5]).read_text()

br = Path("/tmp/p343-rec-before").read_text()
bk = Path("/tmp/p343-kms-before").read_text()
bc = Path("/tmp/p343-ctrl-before").read_text()
bd = Path("/tmp/p343-drv-before").read_text()
ba = Path("/tmp/p343-atomic-before").read_text()

if kms != bk or ctrl != bc or drv != bd or atomic != ba:
    raise SystemExit("Phase343 changed a non-recorder source file")

for token in (
    "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1",
    "A52_R343_SIDEBAND_PHYS 0xB1BF8000ULL",
    "A52_R343_STRIDE_SHIFT 28U",
    "A52_R343_LIMIT 128U",
    "A52_R343_CPU 5U",
    "P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u",
    "iteration++",
    "iteration == next",
    "read_sysreg(cntvct_el0)",
    "sched_setscheduler_nocheck(current, SCHED_FIFO, &sp);",
    "kthread_bind(a52_r343_task, A52_R343_CPU);",
    "__flush_dcache_area(dst0, sizeof(slot));",
    "__flush_dcache_area(dst1, sizeof(slot));",
):
    if token not in rec:
        raise SystemExit("Phase343 marker missing: " + token)

for token in (
    "A52_PHASE342_BUSYLOOP_COUNTER_FRONTIER_V1",
    "P276 342A map=%u kt=%ld cpu=%u int=250 lim=%u",
    "A52_R342_SIDEBAND_PHYS 0xB1BFA000ULL",
    "A52_PHASE341_HARDIRQ_TIMER_FRONTIER_V1",
    "A52_R341_SIDEBAND_PHYS 0xB1BFC000ULL",
):
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase343 changed inherited token count: " + token)

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
    if rec.count(token) != br.count(token):
        raise SystemExit("Phase343 changed protected hardware behavior: " + token)

if rec.count("a52_r342_start();") != br.count("a52_r342_start();") - 1:
    raise SystemExit("Phase343 must disable exactly one Phase342 runtime start")
if rec.count("a52_r343_start();") != br.count("a52_r343_start();") + 1:
    raise SystemExit("Phase343 must add exactly one Phase343 runtime start")
if rec.count("kthread_create(") != br.count("kthread_create(") + 1:
    raise SystemExit("Phase343 expected one kthread_create call")
if rec.count("kthread_bind(") != br.count("kthread_bind(") + 1:
    raise SystemExit("Phase343 expected one kthread_bind call")
if rec.count("a52_ackfr_record(") != br.count("a52_ackfr_record(") + 1:
    raise SystemExit("Phase343 expected one new R48 arm call")
if rec.count("__flush_dcache_area(") != br.count("__flush_dcache_area(") + 2:
    raise SystemExit("Phase343 expected two mirrored cache-clean sites")

print("Phase343 strict instruction-counter-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p343-phase342.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase343-olddefconfig.log 2>&1
cmp -s /tmp/p343-phase342.config "$BUILD/.config"

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
  'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u' \
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
cp phase343-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/343_apply_instruction_counter_frontier.py \
     scripts/343_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p343-* "$OUT/audit/" 2>/dev/null || true
cp "$KMS" "$CTRL" "$DRV" "$ATOMIC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase342-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase342-gki-out/BUILD-IDENTITY.json \
  "$OUT/audit/PHASE342-BASE-BUILD-IDENTITY.json"

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
    "name": "INSTRUCTION-COUNTER-FRONTIER-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "342",
    "finding_before_phase": (
        "Phase342 CPU5 ktime-gated busy-loop checkpoints stop at tick 41 "
        "around 12.008 s while the next 250 ms checkpoint is absent. "
        "Because checkpoint progress itself used ktime_get_ns(), this cannot "
        "distinguish CPU execution halt from a frozen clocksource."
    ),
    "experiment": (
        "Replace the Phase342 runtime thread with one CPU5 SCHED_FIFO tight "
        "loop whose checkpoint trigger depends only on a software iteration counter. "
        "At each iteration checkpoint, sample ktime_get_ns, get_jiffies_64 "
        "and CNTVCT as payload only, then write mirrored cache-cleaned raw "
        "sideband records. No timer or clock participates in progress."
    ),
    "stride_iterations": 1 << 28,
    "checkpoint_limit": 128,
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
echo 'Phase343 instruction-counter frontier build: PASS'
trap - EXIT
