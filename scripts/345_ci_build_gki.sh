#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase345-gki-out"
FAIL="$PWD/phase345-gki-failure"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase345: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase345-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p345-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/345_apply_dma_us_frontier.py scripts/345_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$HWC" "$CTRL" "$REC"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct Phase343"
bash scripts/343_ci_build_gki.sh 2>&1 | tee phase345-phase343.log

for f in phase343-gki-out/package/boot.img phase343-gki-out/compile/Image phase343-gki-out/config/final.config "$HWC" "$CTRL" "$REC"; do
  test -s "$f"
done
test "$(stat -c '%s' phase343-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1' "$HWC"
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$CTRL"

cp phase343-gki-out/config/final.config /tmp/p345-base.config
cp "$HWC" /tmp/p345-hwc-before
cp "$CTRL" /tmp/p345-ctrl-before
cp "$REC" /tmp/p345-rec-before

stage "apply DMA us frontier"
python3 -m py_compile scripts/345_apply_dma_us_frontier.py
python3 scripts/345_apply_dma_us_frontier.py --root "$ROOT"
python3 scripts/345_apply_dma_us_frontier.py --root "$ROOT" --check-only
cmp -s /tmp/p345-rec-before "$REC"
! cmp -s /tmp/p345-hwc-before "$HWC"
! cmp -s /tmp/p345-ctrl-before "$CTRL"
git diff --no-index --check /tmp/p345-hwc-before "$HWC" > /tmp/p345-hwc-check 2>&1 || true
git diff --no-index --check /tmp/p345-ctrl-before "$CTRL" > /tmp/p345-ctrl-check 2>&1 || true
test ! -s /tmp/p345-hwc-check
test ! -s /tmp/p345-ctrl-check

stage "scope audit"
python3 - "$HWC" "$CTRL" <<'PY'
from pathlib import Path
import sys
h=Path(sys.argv[1]).read_text(); c=Path(sys.argv[2]).read_text()
bh=Path('/tmp/p345-hwc-before').read_text(); bc=Path('/tmp/p345-ctrl-before').read_text()

for x in (
 'A52_PHASE345_DMA_US_FRONTIER_V1',
 'P276 345R %x %x %llx %x %x %x %x',
 'P276 345T %x %x %x %x %x %x',
 'DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, 0x0171);',
 'DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p345_saved_dbg_ctl);',
 'a52_p345_flush(&dsi_ctrl->hw);',
):
 if x not in h+c: raise SystemExit('Phase345 missing '+x)

# Exactly two added MMIO writes in source text: selector and restore. They are
# shared helpers used by either trigger path, not extra SW_TRIGGER writes.
if h.count('DSI_W32(') != bh.count('DSI_W32(') + 2:
 raise SystemExit('Phase345 unexpected DSI_W32 delta')
if h.count('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);') != bh.count('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);'):
 raise SystemExit('Phase345 changed SW_TRIGGER count')

for x in ('clk_set_rate(','clk_set_parent(','regulator_enable(','reset_control_','udelay(','usleep_range(','msleep(','wait_for_completion_timeout('):
 if h.count(x)!=bh.count(x) or c.count(x)!=bc.count(x):
  raise SystemExit('Phase345 changed protected primitive '+x)

if c.count('a52_ackfr_retain_timeout_snapshot();') != bc.count('a52_ackfr_retain_timeout_snapshot();'):
 raise SystemExit('Phase345 changed retention call count')
if not (c.index('a52_p345_flush(&dsi_ctrl->hw);') < c.index('P276 332A q=2') < c.index('a52_ackfr_retain_timeout_snapshot();')):
 raise SystemExit('Phase345 flush ordering invalid')

# Keep emitted R48 lines inside the 72-byte packed event payload.
worst=[
 'P276 345R f f ffffffffffffffff ffffffff ffffffff ffffffff ffffffff',
 'P276 345T f ffffffff ffffffff ffffffff ffffffff ffffffff',
]
if max(map(len,worst)) > 72:
 raise SystemExit('Phase345 packed record too long')
print('Phase345 scope audit: PASS')
PY

stage "config"
cp /tmp/p345-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase345-olddefconfig.log 2>&1
cmp -s /tmp/p345-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase345-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
for m in  'P276 345R %x %x %llx %x %x %x %x'  'P276 345T %x %x %x %x %x %x'  'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u'; do
 grep -aFq "$m" "$IMAGE"
done

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase345-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/345_apply_dma_us_frontier.py scripts/345_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p345-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase343-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE345-SCHEMA.txt" <<'EOF'
Phase345 DMA-us frontier
========================
P276 345R <index> <point> <delta_ns_hex> <STATUS> <INT_CTRL> <CLK_STATUS> <DEBUG171>
P276 345T <count> <STATUS> <INT_CTRL> <ACK_ERR> <TIMEOUT_STATUS> <PHY_ERR>

Golden Phase344G reference:
 p1 +18.802us STATUS=3 INT_CTRL=0x02000003
 p2 +35.625us STATUS=0 INT_CTRL=0x02000003
 ISR +46.042us
 direct DSI_CLK_STATUS remained 0x00002343.
 The historical 0x8027c3/0x8037c3 split is selector-0x0171 DEBUG_BUS_STATUS.

Phase345 selects only debug selector 0x0171 across p0..p6, restores the
original selector immediately after the burst, and flushes the RAM samples
immediately before the inherited Phase332/Phase280 timeout retention freeze.
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase345-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'345','name':'DMA-US-FRONTIER-V1','base_phase':'343',
 'hardware_validated':False,
 'golden_reference':'Phase344G',
 'golden_first_busy_us':18.802,
 'golden_idle_by_us':35.625,
 'golden_dma_done_isr_us':46.042,
 'golden_direct_clk_status':'0x2343',
 'debug_selector':'0x0171',
 'register_writes_added':2,
 'register_writes_added_scope':'DEBUG_BUS_CTL selector+restore only',
 'sw_trigger_changed':False,
 'clock_power_reset_regulator_changed':False,
 'timeout_recovery_changed':False,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage complete
echo 'Phase345 DMA-us frontier build: PASS'
trap - EXIT
