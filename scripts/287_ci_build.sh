#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase287-failure
  mkdir -p phase287-failure/{source,logs,audit,config}
  cp phase287-compile.log phase287-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$HW" "$REC"; do [ -f "$f" ] && cp "$f" phase287-failure/source/ || true; done
  cp scripts/287*.py phase287-failure/audit/ 2>/dev/null || true
  cp /tmp/p287-*.config /tmp/p287-*.diff phase287-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Build the complete Phase286C causal recorder first. Phase287 then adds only
# read-only command-buffer provenance and post-trigger register readbacks, and
# Phase287B folds those exact values into Phase286C's typed timeout tail.
bash scripts/286_ci_build.sh
test -s phase286-out/package/boot.img
for f in "$DSI" "$HW" "$REC"; do test -s "$f"; done
grep -Fq 'A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1' "$REC"
grep -Fq 'A52_PHASE286C_PACKED_REPLAY_WIDTH_V1' "$REC"
grep -Fq 'P286 R0 %llx %x %x %llx %llx' "$REC"
grep -Fq 'P286 R1 %llx %llx %llx' "$REC"

cp "$OUT/.config" /tmp/p287-base.config
cp "$DSI" /tmp/p287-dsi-before.c
cp "$HW" /tmp/p287-hw-before.c
cp "$REC" /tmp/p287-rec-before.c

python3 -m py_compile \
  scripts/287_apply_dsi_dma_fetch_provenance.py \
  scripts/287b_apply_retained_fetch_provenance.py
python3 scripts/287_apply_dsi_dma_fetch_provenance.py --root "$ROOT"
python3 scripts/287b_apply_retained_fetch_provenance.py --root "$ROOT"
python3 scripts/287_apply_dsi_dma_fetch_provenance.py --root "$ROOT" --check-only
python3 scripts/287b_apply_retained_fetch_provenance.py --root "$ROOT" --check-only

! cmp -s /tmp/p287-rec-before.c "$REC"
! cmp -s /tmp/p287-dsi-before.c "$DSI"
! cmp -s /tmp/p287-hw-before.c "$HW"
cmp -s /tmp/p287-base.config "$OUT/.config"

for token in \
  'P287 M0 c=%d i=%llx va=%llx pre=%u add=%u' \
  'P287 M1 c=%d i=%llx len=%u last=1' \
  'P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x'; do grep -Fq "$token" "$DSI"; done
test "$(grep -Fc 'P287 R c=%d ro=%x rl=%x ax=%x vb=%x' "$HW")" -eq 2
for token in \
  'A52_PHASE287B_RETAINED_FETCH_PROVENANCE_V1' \
  'a52_p287_capture_fmt(fmt, args);' \
  'return !strncmp(message, "P287 ", 5)' \
  'strncmp(fmt, "P287", 4)' \
  'type = 14; n = 5;' \
  'type = 15; n = 3;' \
  'type = 16; n = 2;' \
  'type = 17; n = 5;' \
  'P286 R0 %llx %x %x %llx %llx' \
  'P286 R1 %llx %llx %llx'; do grep -Fq "$token" "$REC"; done

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
if ! cmp -s /tmp/p287-base.config "$OUT/.config"; then
  diff -u /tmp/p287-base.config "$OUT/.config" > /tmp/p287-config.diff || true
  echo '::error::Phase287 changed kernel config'
  cat /tmp/p287-config.diff
  exit 1
fi
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase287-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase287-out
mkdir -p phase287-out/{compile,config,package,audit,source}
cp "$IMAGE" phase287-out/compile/Image
cp "$OUT/.config" phase287-out/config/final.config
cp /tmp/p287-base.config phase287-out/audit/phase286c-final.config
cp /tmp/p287-dsi-before.c phase287-out/audit/dsi-ctrl-before.c
cp /tmp/p287-hw-before.c phase287-out/audit/dsi-ctrl-hw-cmn-before.c
cp /tmp/p287-rec-before.c phase287-out/audit/recorder-before.c
cp phase287-compile.log phase287-out/audit/
cp scripts/287_apply_dsi_dma_fetch_provenance.py phase287-out/audit/
cp scripts/287b_apply_retained_fetch_provenance.py phase287-out/audit/
cp "$DSI" phase287-out/source/dsi_ctrl.c
cp "$HW" phase287-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase287-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase287-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase286-out/package/boot.img \
  --kernel phase287-out/package/Image.gz \
  --output phase287-out/package/boot.img \
  --report phase287-out/package/repack-report.json
test -s phase287-out/package/boot.img
file phase287-out/package/boot.img | tee phase287-out/package/boot-file.txt

cat > phase287-out/PHASE287-RECORD-SCHEMA.txt <<'EOF'
Phase287 DMA fetch provenance additions
=======================================
M0 command-memory mapping at normal post-msm_gem_sync point:
   controller, command IOVA, CPU vaddr, existing batch length, bytes being appended
M1 final LASTCOMMAND DMA descriptor:
   controller, IOVA/offset, final DMA length
M2 first 8 bytes read back from the actual mapped command buffer after CPU copy
R  hardware DMA offset/length plus AXI2AHB and VBIF register readback strictly
   after the normal SW trigger write, for both immediate and deferred trigger paths

Phase287B retention:
P287 is explicitly admitted by the focused and post-capacity Golden FDR filters.
Its exact varargs are captured before text packing into the same final-32-event
circular causal tail used by Phase286C, then replayed immediately before the
Phase280 timeout freeze. Replay uses the existing compact P286 R0/R1 grammar.
Additional replay type map:
 14=M0: [controller, IOVA, CPU vaddr, pre_len, add_len]
 15=M1: [controller, IOVA, final_len]
 16=M2: [controller, packed8]
       packed8 byte order: b0 in bits 7:0, b1 in 15:8 ... b7 in 63:56
 17=R:  [controller, DMA_OFFSET, DMA_LENGTH, AXI2AHB, VBIF]

All Phase286 A/B/HK/D/DX/HT/E/G/W/T records and types 1..13 remain present.
EOF

python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase287-out')
idn={
 'phase':'287B','name':'DSI-DMA-FETCH-PROVENANCE-RETAINED','git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,'base':'Phase286C Golden-FDR causal-chain typed-retention recorder',
 'behavior_change':False,'register_writes_added':False,'recovery_changed':False,
 'recorder_change':'admit P287 and fold exact M0/M1/M2/R varargs into the Phase286C final-32 typed timeout tail',
 'previous_failure_addressed':'P287 evidence is not trusted merely because a52_ackfr_record was called; admission, overwrite retention, and compact replay are all audited',
 'question':'If SW trigger really executes but DMA_DONE never asserts, is the command-memory IOVA/length/content coherent and does hardware retain the same DMA descriptor at trigger time?',
 'golden_repack_source':'phase286-out/package/boot.img','repacker':'scripts/38_repack_a52_p1_boot.py'}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[p.relative_to(r) for p in sorted(r.rglob('*')) if p.is_file() and p.name!='SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
 for n in files:f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+str(n)+'\n')
PY
(cd phase287-out && sha256sum -c SHA256SUMS)
python3 - <<'PY'
from pathlib import Path
img=Path('phase287-out/compile/Image').read_bytes()
for m in [
 'P287 M0 c=%d i=%llx va=%llx pre=%u add=%u',
 'P287 M1 c=%d i=%llx len=%u last=1',
 'P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x',
 'P287 R c=%d ro=%x rl=%x ax=%x vb=%x',
 'P286 HT c=%d sw=1','P286 T c=%d st=%x done=%d irq=%d',
 'P286 R0 %llx %x %x %llx %llx','P286 R1 %llx %llx %llx']:
 if m.encode() not in img: raise SystemExit('Phase287 marker missing from Image: '+m)
print('Phase287B compiled producer + retained replay marker audit: PASS')
PY
python3 scripts/287_apply_dsi_dma_fetch_provenance.py --root "$ROOT" --check-only
python3 scripts/287b_apply_retained_fetch_provenance.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase287B DSI DMA fetch provenance build/repack: PASS'
