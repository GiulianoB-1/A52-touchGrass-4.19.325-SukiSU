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
stage(){ STAGE="$1"; echo "== Phase347: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase347-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p347-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/347_apply_f0_arm_trace.py scripts/347_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$HWC" "$CTRL" "$REC"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE347_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase347: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase347-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$HWC" "$CTRL" "$REC"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' "$HWC"
grep -Fq 'A52_PHASE345_DMA_US_FRONTIER_V1' "$HWC"
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"

cp phase346-gki-out/config/final.config /tmp/p347-base.config
cp "$HWC" /tmp/p347-hwc-before
cp "$CTRL" /tmp/p347-ctrl-before
cp "$REC" /tmp/p347-rec-before

stage "apply F0 arm trace"
python3 -m py_compile scripts/347_apply_f0_arm_trace.py
python3 scripts/347_apply_f0_arm_trace.py --root "$ROOT"
python3 scripts/347_apply_f0_arm_trace.py --root "$ROOT" --check-only
cmp -s /tmp/p347-rec-before "$REC"
! cmp -s /tmp/p347-hwc-before "$HWC"
! cmp -s /tmp/p347-ctrl-before "$CTRL"

git diff --no-index --check /tmp/p347-hwc-before "$HWC" > /tmp/p347-hwc-check 2>&1 || true
git diff --no-index --check /tmp/p347-ctrl-before "$CTRL" > /tmp/p347-ctrl-check 2>&1 || true
test ! -s /tmp/p347-hwc-check
test ! -s /tmp/p347-ctrl-check

stage "scope audit"
python3 - "$HWC" "$CTRL" <<'PY'
from pathlib import Path
import sys
h=Path(sys.argv[1]).read_text(); c=Path(sys.argv[2]).read_text()
bh=Path('/tmp/p347-hwc-before').read_text(); bc=Path('/tmp/p347-ctrl-before').read_text()
for x in (
 'A52_PHASE347_F0_ARM_TRACE_V1',
 'A52_P347_TRACE_OFF          0x0800U',
 'A52_P347_FIXED_OFF          0x0c00U',
 'A52_P347_MAGIC              0x3734334d52414630ULL',
 'A52_P347_COMMIT             0x347c0de5U',
 'a52_p347_note_arm(2U',
 'a52_p347_note_arm(3U',
 'A52_P347_EVT_P345_BEGIN',
):
 if x not in h+c: raise SystemExit('Phase347 missing '+x)
for x in ('DSI_W32(','DSI_R32(','wait_for_completion_timeout(','clk_set_rate(','clk_set_parent(','regulator_enable(','reset_control_','udelay(','usleep_range(','msleep('):
 if h.count(x)!=bh.count(x) or c.count(x)!=bc.count(x):
  raise SystemExit('Phase347 changed protected primitive '+x)
print('Phase347 scope audit: PASS')
PY

stage "config"
cp /tmp/p347-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase347-olddefconfig.log 2>&1
cmp -s /tmp/p347-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase347-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p347_note_arm$' "$SYSTEM_MAP"
grep -aFq 'P276 345R %x %x %llx %x %x %x %x' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase347-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/347_apply_f0_arm_trace.py scripts/347_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p347-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE347-SCHEMA.txt" <<'EOF'
Phase347 F0 arm trace
=====================
Base: Phase346 DMA raw.

Phase346 sample region remains unchanged:
  0xB1BFA000 copy A / 0xB1BFB000 copy B
  p0..p6 remain in slots 1..7 when Phase345 arms.

Phase347 telemetry occupies the upper half of each 4 KiB mirror:
  rolling ring: +0x0800, 16 x 64-byte slots
  fixed events: +0x0c00, 4 x 64-byte slots
  copy B is +0x1000 from copy A.

64-byte slot:
 u64 magic
 u64 ns
 u32 seq
 u32 event
 u32 cell
 u32 flags
 u32 msg_flags
 u32 type_len       (len << 16 | type)
 u32 payload        (p0 | p1<<8 | p2<<16)
 u32 reason
 u32 state
 u32 aux            (CPU)
 u32 commit
 u32 version

magic  = 0x3734334d52414630
commit = 0x347c0de5
version= 1

event:
 1 TRY        controller0 near-candidate (type 0x29 or exact payload)
 2 F0_SEEN    payload F0 5A 5A seen regardless of other predicates
 3 ARMED      Phase293 atomic arm succeeded
 4 P345_BEGIN Phase345 entered its microsecond recorder
 5 P345_END   Phase345 completed p0..p6 and persistence

reason bits:
 bit0  dsi_ctrl NULL
 bit1  msg NULL
 bit2  flags pointer NULL
 bit3  cell_index != 0
 bit4  flags != DSI_CTRL_CMD_FETCH_MEMORY
 bit5  msg->flags != 0x8
 bit6  msg->type != 0x29
 bit7  msg->tx_len != 3
 bit8  msg->tx_buf NULL
 bit9  payload != F0 5A 5A
 bit10 Phase293 state already nonzero
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase347-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'347','name':'F0-ARM-TRACE-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BFA000',
 'trace_offset':'0x800','fixed_offset':'0xC00','slot_bytes':64,
 'measured_dsi_behavior_changed':False,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase347 F0 arm trace build: PASS"
trap - EXIT
