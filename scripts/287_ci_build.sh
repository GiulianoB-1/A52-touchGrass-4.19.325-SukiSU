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
  cp scripts/287_apply_dsi_dma_fetch_provenance.py phase287-failure/audit/ 2>/dev/null || true
  cp /tmp/p287-*.config /tmp/p287-*.diff phase287-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Build the complete Phase286 causal recorder first, then add only read-only
# command-buffer provenance and post-trigger register readbacks.
bash scripts/286_ci_build.sh
test -s phase286-out/package/boot.img
for f in "$DSI" "$HW" "$REC"; do test -s "$f"; done
cp "$OUT/.config" /tmp/p287-base.config
cp "$DSI" /tmp/p287-dsi-before.c
cp "$HW" /tmp/p287-hw-before.c
cp "$REC" /tmp/p287-rec-before.c

python3 -m py_compile scripts/287_apply_dsi_dma_fetch_provenance.py
python3 scripts/287_apply_dsi_dma_fetch_provenance.py --root "$ROOT"
python3 scripts/287_apply_dsi_dma_fetch_provenance.py --root "$ROOT" --check-only
cmp -s /tmp/p287-rec-before.c "$REC"
! cmp -s /tmp/p287-dsi-before.c "$DSI"
! cmp -s /tmp/p287-hw-before.c "$HW"
cmp -s /tmp/p287-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p287-base.config "$OUT/.config"
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase287-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase287-out
mkdir -p phase287-out/{compile,config,package,audit,source}
cp "$IMAGE" phase287-out/compile/Image
cp "$OUT/.config" phase287-out/config/final.config
cp /tmp/p287-base.config phase287-out/audit/phase286-final.config
cp /tmp/p287-dsi-before.c phase287-out/audit/dsi-ctrl-before.c
cp /tmp/p287-hw-before.c phase287-out/audit/dsi-ctrl-hw-cmn-before.c
cp /tmp/p287-rec-before.c phase287-out/audit/recorder-before.c
cp phase287-compile.log phase287-out/audit/
cp scripts/287_apply_dsi_dma_fetch_provenance.py phase287-out/audit/
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

All Phase286 A/B/HK/D/DX/HT/E/G/W/T records remain present.
EOF

python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase287-out')
idn={
 'phase':'287','name':'DSI-DMA-FETCH-PROVENANCE','git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,'base':'Phase286 Golden-FDR causal-chain recorder',
 'behavior_change':False,'register_writes_added':False,'recovery_changed':False,
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
for m in ['P287 M0 c=%d i=%llx va=%llx pre=%u add=%u','P287 M1 c=%d i=%llx len=%u last=1','P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x','P287 R c=%d ro=%x rl=%x ax=%x vb=%x','P286 HT c=%d sw=1','P286 T c=%d st=%x done=%d irq=%d']:
 if m.encode() not in img: raise SystemExit('Phase287 marker missing from Image: '+m)
print('Phase287 compiled marker audit: PASS')
PY
python3 scripts/287_apply_dsi_dma_fetch_provenance.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase287 DSI DMA fetch provenance build/repack: PASS'
