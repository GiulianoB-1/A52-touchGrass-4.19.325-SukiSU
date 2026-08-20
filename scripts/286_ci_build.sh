#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase286-failure
  mkdir -p phase286-failure/{source,logs,audit,config}
  cp phase286-compile.log phase286-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$HW" "$REC"; do [ -f "$f" ] && cp "$f" phase286-failure/source/ || true; done
  cp scripts/286_apply_golden_fdr_dma_chain.py phase286-failure/audit/ 2>/dev/null || true
  cp /tmp/p286-*.config /tmp/p286-*.diff phase286-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase285 lineage first, including the already-proven
# Golden FDR retention transport. Phase286 then changes only DSI tracing code.
bash scripts/285_ci_build.sh
test -s phase285-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$HW" "$REC"; do test -s "$f"; done

cp "$OUT/.config" /tmp/p286-base.config
cp "$DSI" /tmp/p286-dsi-before.c
cp "$HW" /tmp/p286-hw-before.c
cp "$REC" /tmp/p286-rec-before.c

python3 -m py_compile scripts/286_apply_golden_fdr_dma_chain.py
python3 scripts/286_apply_golden_fdr_dma_chain.py --root "$ROOT"
python3 scripts/286_apply_golden_fdr_dma_chain.py --root "$ROOT" --check-only

# Recorder implementation and config are immutable in this phase. We reuse its
# proven retention path and only add passive producers in DSI controller code.
cmp -s /tmp/p286-rec-before.c "$REC"
! cmp -s /tmp/p286-dsi-before.c "$DSI"
! cmp -s /tmp/p286-hw-before.c "$HW"
cmp -s /tmp/p286-base.config "$OUT/.config"

for token in \
  'P286 A c=%d mf=%x f=%x t=%u l=%u' \
  'P286 B c=%d f=%x h=%x pm=%d ve=%d' \
  'P286 D c=%d f=%x last=%d bm=%d b=%d' \
  'P286 DX c=%d reason=nolast' \
  'P286 E c=%d k=slave' \
  'P286 E c=%d k=sched cl=%u sl=%u lb=%u' \
  'P286 E c=%d k=master' \
  'P286 W c=%d r=%d irq=%d' \
  'P286 T c=%d st=%x done=%d irq=%d' \
  'P286 G c=%d st=%x irq0=%d'; do grep -Fq "$token" "$DSI"; done
for token in \
  'P286 HK c=%d o=%x l=%x f=%x sw=1' \
  'P286 HK c=%d o=%x l=%x f=%x sw=0' \
  'P286 HT c=%d sw=1'; do grep -Fq "$token" "$HW"; done

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cp "$OUT/.config" /tmp/p286-post-olddefconfig.config
if ! cmp -s /tmp/p286-base.config /tmp/p286-post-olddefconfig.config; then
  diff -u /tmp/p286-base.config /tmp/p286-post-olddefconfig.config > /tmp/p286-config.diff || true
  echo '::error::Phase286 changed kernel config'
  cat /tmp/p286-config.diff
  exit 1
fi

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase286-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase286-out
mkdir -p phase286-out/{compile,config,package,audit,source}
cp "$IMAGE" phase286-out/compile/Image
cp "$OUT/.config" phase286-out/config/final.config
cp /tmp/p286-base.config phase286-out/audit/phase285-final.config
cp /tmp/p286-dsi-before.c phase286-out/audit/dsi-ctrl-before.c
cp /tmp/p286-hw-before.c phase286-out/audit/dsi-ctrl-hw-cmn-before.c
cp /tmp/p286-rec-before.c phase286-out/audit/recorder-before.c
cp phase286-compile.log phase286-out/audit/
cp scripts/286_apply_golden_fdr_dma_chain.py phase286-out/audit/
cp "$DSI" phase286-out/source/dsi_ctrl.c
cp "$HW" phase286-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase286-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase286-out/package/Image.gz
# Golden FDR repack: keep the exact validated Phase285 boot container and only
# replace its kernel payload using the established Phase38 repacker.
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase285-out/package/boot.img \
  --kernel phase286-out/package/Image.gz \
  --output phase286-out/package/boot.img \
  --report phase286-out/package/repack-report.json

test -s phase286-out/package/boot.img
file phase286-out/package/boot.img | tee phase286-out/package/boot-file.txt

cat > phase286-out/PHASE286-RECORD-SCHEMA.txt <<'EOF'
Phase286 Golden-FDR DSI DMA causal chain
========================================
A  message entry: controller, mipi msg flags, controller flags, type, tx_len
B  kickoff policy: controller, controller flags, low-level hw_flags, panel mode, video-engine state
HK low-level memory kickoff after DMA offset/length+wmb: offset, length, hw_flags, immediate SW-trigger yes/no
D  deferred tx-trigger entry: flags, LAST_COMMAND, broadcast-master, broadcast
DX silent no-LAST early return
HT low-level deferred SW-trigger register write actually executed
E  high-level trigger call returned: slave / scheduled master / direct master
G  ISR observed DMA_DONE before setting dma_irq_trig/completing waiter
W  completion wait result and dma_irq_trig
T  timeout raw interrupt status and DMA_DONE bit

Inherited Phase281 q0/q1/q2 records provide DSI_STATUS, FIFO_STATUS,
COMMAND_MODE_DMA_CTRL, DMA_FIFO_CTRL, DMA_SW_TRIGGER, INT_CTRL, ACK_ERR,
TIMEOUT_STATUS, LANE_STATUS, PHY error, AXI2AHB, VBIF, DMA offset/length,
CLK_CTRL and CLK_STATUS at the same failing boundary.
EOF

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase286-out')
idn={
 'phase':'286',
 'name':'GOLDEN-FDR-DSI-DMA-CAUSAL-CHAIN',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase285 lineage and Golden FDR retention transport',
 'behavior_change':False,
 'register_writes_added':False,
 'trigger_behavior_changed':False,
 'timeout_recovery_changed':False,
 'brightness_behavior_changed':False,
 'recorder_implementation_changed':False,
 'question':'Where exactly does the real TouchGrass command-DMA path stop before DMA_DONE: deferred gate, SW trigger, hardware completion, IRQ delivery, or waiter completion?',
 'golden_repack_source':'phase285-out/package/boot.img',
 'repacker':'scripts/38_repack_a52_p1_boot.py'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[]
for p in sorted(r.rglob('*')):
 if p.is_file() and p.name != 'SHA256SUMS': files.append(p.relative_to(r))
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+str(n)+'\n')
PY
(cd phase286-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase286-out')
img=(r/'compile/Image').read_bytes()
for m in [
 'P286 A c=%d mf=%x f=%x t=%u l=%u','P286 B c=%d f=%x h=%x pm=%d ve=%d',
 'P286 D c=%d f=%x last=%d bm=%d b=%d','P286 DX c=%d reason=nolast',
 'P286 HT c=%d sw=1','P286 E c=%d k=master','P286 W c=%d r=%d irq=%d',
 'P286 T c=%d st=%x done=%d irq=%d','P286 G c=%d st=%x irq0=%d']:
 if m.encode() not in img: raise SystemExit('Phase286 runtime marker missing: '+m)
print('Phase286 compiled marker audit: PASS')
PY

python3 scripts/286_apply_golden_fdr_dma_chain.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase286 Golden-FDR DSI DMA causal-chain build/repack: PASS'
