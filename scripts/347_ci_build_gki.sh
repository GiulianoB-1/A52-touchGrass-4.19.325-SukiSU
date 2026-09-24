#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase347-gki-out"
FAIL="$PWD/phase347-gki-failure"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() {
  STAGE="$1"
  echo "== Phase347: $STAGE =="
}

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase347-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p347-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/346_apply_dma_raw.py scripts/347_apply_f0_fifo_ab.py \
     scripts/347_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$HWC" "$CTRL" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}

trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase345 baseline"

if [ "${PHASE347_PREWARMED_PHASE345:-0}" = "1" ]; then
  echo "Phase347: using validated cached Phase345 baseline"
else
  bash scripts/345_ci_build_gki.sh 2>&1 | tee phase347-phase345.log
fi

for f in \
  phase345-gki-out/package/boot.img \
  phase345-gki-out/compile/Image \
  phase345-gki-out/config/final.config \
  "$HWC" "$CTRL" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase345-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE345_DMA_US_FRONTIER_V1' "$HWC"
grep -Fq 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1' "$CTRL"
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"

cp phase345-gki-out/config/final.config /tmp/p347-base.config
cp "$HWC" /tmp/p347-hwc-phase345
cp "$CTRL" /tmp/p347-ctrl-phase345
cp "$REC" /tmp/p347-rec-phase345

stage "apply Phase346 raw sideband"

python3 -m py_compile scripts/346_apply_dma_raw.py
python3 scripts/346_apply_dma_raw.py --root "$ROOT"
python3 scripts/346_apply_dma_raw.py --root "$ROOT" --check-only

grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' "$HWC"
grep -Fq 'a52_p346_sideband_init();' "$CTRL"
cmp -s /tmp/p347-rec-phase345 "$REC"

cp "$HWC" /tmp/p347-hwc-phase346
cp "$CTRL" /tmp/p347-ctrl-phase346
cp "$REC" /tmp/p347-rec-phase346

stage "apply exact-F0 FIFO A/B"

python3 -m py_compile scripts/347_apply_f0_fifo_ab.py
python3 scripts/347_apply_f0_fifo_ab.py --root "$ROOT"
python3 scripts/347_apply_f0_fifo_ab.py --root "$ROOT" --check-only

! cmp -s /tmp/p347-ctrl-phase346 "$CTRL"
cmp -s /tmp/p347-hwc-phase346 "$HWC"
cmp -s /tmp/p347-rec-phase346 "$REC"

git diff --no-index --check /tmp/p347-ctrl-phase346 "$CTRL" > /tmp/p347-ctrl-check 2>&1 || true
test ! -s /tmp/p347-ctrl-check

stage "scope audit"

python3 - "$HWC" "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys

h = Path(sys.argv[1]).read_text()
c = Path(sys.argv[2]).read_text()
r = Path(sys.argv[3]).read_text()
bh = Path('/tmp/p347-hwc-phase346').read_text()
bc = Path('/tmp/p347-ctrl-phase346').read_text()
br = Path('/tmp/p347-rec-phase346').read_text()

for token in (
    'A52_PHASE346_DMA_RAW_SIDEBAND_V1',
    'A52_PHASE347_F0_FIFO_AB_V1',
    'u32 a52_p347_fifo_ab_enabled = 1;',
    'p[0] != 0xf0 || p[1] != 0x5a || p[2] != 0x5a',
    '*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;',
    '*flags |= DSI_CTRL_CMD_FIFO_STORE;',
    'a52_p347_maybe_switch_f0_fifo(dsi_ctrl, msg, flags);',
):
    if token not in h + c + r:
        raise SystemExit('Phase347 missing ' + token)

if h != bh:
    raise SystemExit('Phase347 unexpectedly changed DSI HWC source')
if r != br:
    raise SystemExit('Phase347 unexpectedly changed recorder source')

arm = c.index('a52_p293_gdm_try_arm(dsi_ctrl, msg, flags);')
switch = c.index('a52_p347_maybe_switch_f0_fifo(dsi_ctrl, msg, flags);')
if arm >= switch:
    raise SystemExit('Phase347 switch must occur after Phase293 exact-target arm')

for token in (
    'DSI_W32(', 'DSI_R32(', 'wait_for_completion_timeout(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
    'udelay(', 'usleep_range(', 'msleep(',
):
    if c.count(token) != bc.count(token):
        raise SystemExit('Phase347 changed protected primitive ' + token)

if c.count('DSI_CTRL_CMD_FIFO_STORE') != bc.count('DSI_CTRL_CMD_FIFO_STORE') + 1:
    raise SystemExit('Phase347 FIFO delta is not exactly one')
if c.count('DSI_CTRL_CMD_FETCH_MEMORY') != bc.count('DSI_CTRL_CMD_FETCH_MEMORY') + 2:
    raise SystemExit('Phase347 FETCH_MEMORY delta is not exactly predicate+clear')

print('Phase347 scope audit: PASS')
PY

stage "config"

cp /tmp/p347-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" \
  ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- \
  LLVM=1 LLVM_IAS=1 \
  olddefconfig > phase347-olddefconfig.log 2>&1

cmp -s /tmp/p347-base.config "$BUILD/.config"

stage "compile"

set +e
make -C "$ROOT" O="$BUILD" \
  ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- \
  LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase347-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"
test -s "$SYSTEM_MAP"

grep -Eq '[[:space:]]a52_p347_fifo_ab_enabled$' "$SYSTEM_MAP"
grep -aFq 'GDM S01 sel=%x hw=%x pm=%u pwr=%u' "$IMAGE"
grep -aFq 'P276 345R %x %x %llx %x %x %x %x' "$IMAGE"

stage "package"

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}

cp "$IMAGE" "$OUT/compile/Image"
cp "$SYSTEM_MAP" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase347-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/347_apply_f0_fifo_ab.py \
   scripts/347_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p347-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"

python3 scripts/38_repack_a52_p1_boot.py \
  --source phase345-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE347-TEST.txt" <<'EOF'
Phase347 exact-F0 FIFO A/B
==========================
Baseline: exact Phase345 source + Phase346 raw-sideband persistence.

Single functional change:
  For the first exact DSI0 transaction matching
    flags before arm = DSI_CTRL_CMD_FETCH_MEMORY (0x20)
    msg flags        = 0x8 (LASTCOMMAND)
    data type        = 0x29
    tx_len           = 3
    payload          = F0 5A 5A
  Phase293 is allowed to arm first, then the source mode is changed to
  DSI_CTRL_CMD_FIFO_STORE (0x10).

Everything downstream remains the normal Qualcomm path:
  packet construction -> DMA_CTRL -> DMA_CMD_LENGTH -> SW_TRIGGER ->
  DMA_DONE interrupt/wait -> DSI/PHY/panel state.

Runtime confirmation:
  GDM S00 should still report in=20 for the pre-mutation target arm.
  GDM S01 should report sel=10 for the FIFO transaction.

Interpretation:
  If DMA_DONE appears and the command completes, investigate GEM/IOVA/AXI
  command-memory fetch semantics.
  If the same STATUS/debugbus/lane stall remains, the failure is downstream
  of external command-memory fetch, narrowing focus to DSI command FSM,
  clock-lane/PHY launch, or their core clock/power prerequisites.
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import os

root = Path('phase347-gki-out')

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()

identity = {
    'phase': '347',
    'name': 'F0-FIFO-AB-V1',
    'base_phase': '346-over-345',
    'hardware_validated': False,
    'target': 'DSI0 type=0x29 flags=0x8 len=3 payload=F05A5A',
    'pre_arm_source': 'FETCH_MEMORY',
    'test_source': 'FIFO_STORE',
    'phase346_raw_sideband_preserved': True,
    'direct_dsi_mmio_changes': False,
    'clock_power_reset_changes': False,
    'boot_img_size': (root / 'package/boot.img').stat().st_size,
    'boot_img_sha256': sha256(root / 'package/boot.img'),
    'image_sha256': sha256(root / 'compile/Image'),
    'git_sha': os.getenv('GITHUB_SHA'),
}
(root / 'BUILD-IDENTITY.json').write_text(
    json.dumps(identity, indent=2, sort_keys=True) + '\n'
)
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage "complete"
echo "Phase347 exact-F0 FIFO A/B build: PASS"
trap - EXIT
