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
  cp scripts/286*.py phase286-failure/audit/ 2>/dev/null || true
  cp /tmp/p286-*.config /tmp/p286-*.diff phase286-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase285 lineage first. Phase286 adds passive DSI
# producers. Phase286B captures their exact typed varargs in a private circular
# tail and replays them just before Phase280 freezes the timeout snapshot.
# Phase286C enforces that every replay record fits the recorder's packed field.
bash scripts/285_ci_build.sh
test -s phase285-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$HW" "$REC"; do test -s "$f"; done

cp "$OUT/.config" /tmp/p286-base.config
cp "$DSI" /tmp/p286-dsi-before.c
cp "$HW" /tmp/p286-hw-before.c
cp "$REC" /tmp/p286-rec-before.c

python3 -m py_compile \
  scripts/286_apply_golden_fdr_dma_chain.py \
  scripts/286b_apply_dma_chain_retention.py \
  scripts/286c_fix_dma_replay_width.py
python3 scripts/286_apply_golden_fdr_dma_chain.py --root "$ROOT"
python3 scripts/286b_apply_dma_chain_retention.py --root "$ROOT"
python3 scripts/286_apply_golden_fdr_dma_chain.py --root "$ROOT" --check-only
python3 scripts/286b_apply_dma_chain_retention.py --root "$ROOT" --check-only
python3 scripts/286c_fix_dma_replay_width.py --root "$ROOT"
python3 scripts/286c_fix_dma_replay_width.py --root "$ROOT" --check-only

! cmp -s /tmp/p286-dsi-before.c "$DSI"
! cmp -s /tmp/p286-hw-before.c "$HW"
! cmp -s /tmp/p286-rec-before.c "$REC"
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
for token in \
  'A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1' \
  'A52_PHASE286C_PACKED_REPLAY_WIDTH_V1' \
  '#define A52_P286_TAIL 32U' \
  'a52_p286_capture_fmt(fmt, args);' \
  'return !strncmp(message, "P286 ", 5)' \
  'strncmp(fmt, "P286", 4)' \
  'P286 RH %llx %llx' \
  'P286 R0 %llx %x %x %llx %llx' \
  'P286 R1 %llx %llx %llx' \
  'EXPORT_SYMBOL_GPL(a52_p286_flush_timeout_chain);'; do grep -Fq "$token" "$REC"; done

grep -Fq 'a52_p286_flush_timeout_chain();' "$DSI"
python3 - <<'PY'
from pathlib import Path
d=Path('gki/common/drivers/a52_display/msm/dsi/dsi_ctrl.c').read_text()
assert d.index('P286 T c=%d st=%x done=%d irq=%d') < d.index('a52_p286_flush_timeout_chain();')
assert d.index('a52_p286_flush_timeout_chain();') < d.index('P276 280Z q=2')
assert d.index('P276 280Z q=2') < d.index('a52_ackfr_retain_timeout_snapshot();')
print('Phase286 replay-before-freeze ordering: PASS')
PY

python3 - <<'PY'
lines = [
    'P286 RH ' + 'f'*16 + ' ' + 'f'*16,
    'P286 R0 ' + 'f'*16 + ' ff ff ' + 'f'*16 + ' ' + 'f'*16,
    'P286 R1 ' + 'f'*16 + ' ' + 'f'*16 + ' ' + 'f'*16,
]
for line in lines:
    if len(line) > 72:
        raise SystemExit(f'Phase286 replay format can truncate: {len(line)} bytes: {line}')
print('Phase286 replay packed-width audit:', max(map(len, lines)), '<= 72')
PY

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
cp scripts/286b_apply_dma_chain_retention.py phase286-out/audit/
cp scripts/286c_fix_dma_replay_width.py phase286-out/audit/
cp "$DSI" phase286-out/source/dsi_ctrl.c
cp "$HW" phase286-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase286-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase286-out/package/Image.gz
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
Live producer records:
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

Phase286B/C guaranteed timeout-tail retention:
The exact varargs for the final 32 P286 producer events are captured in a
private circular typed buffer before normal FDR text packing. On DMA timeout,
that tail is replayed immediately before P276 280Z and the Phase280 recorder
freeze. Therefore ordinary ring overwrite cannot remove the causal tail.
Every replay line is CI-audited to fit the recorder's 72-byte packed field.

Replay:
  P286 RH <total> <first-retained-seq>
  P286 R0 <seq> <type> <argc> <v0> <v1>
  P286 R1 <v2> <v3> <v4> when argc > 2; associates with preceding R0
Type map:
  1=A
  2=B
  3=D
  4=DX(no-LAST)
  5=E(slave)
  6=E(scheduled master)
  7=E(direct master)
  8=W
  9=T
 10=G(DMA_DONE ISR)
 11=HK(immediate SW trigger)
 12=HK(deferred, trigger omitted here)
 13=HT(actual deferred SW-trigger write)

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
 'phase':'286C',
 'name':'GOLDEN-FDR-DSI-DMA-CAUSAL-CHAIN-TYPED-RETENTION',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase285 lineage and Golden FDR retention transport',
 'behavior_change':False,
 'register_writes_added':False,
 'trigger_behavior_changed':False,
 'timeout_recovery_changed':False,
 'brightness_behavior_changed':False,
 'recorder_implementation_changed':True,
 'recorder_change':'admit P286; capture exact varargs for last 32 causal events; replay immediately before Phase280 timeout freeze; enforce <=72 byte replay records',
 'previous_failure_addressed':'ordinary early trace records can be filtered, overwritten, or packed/truncated before ramoops harvest; Phase286C explicitly guards all three failure modes',
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
 'P286 T c=%d st=%x done=%d irq=%d','P286 G c=%d st=%x irq0=%d',
 'P286 RH %llx %llx','P286 R0 %llx %x %x %llx %llx','P286 R1 %llx %llx %llx']:
 if m.encode() not in img: raise SystemExit('Phase286 runtime marker missing: '+m)
print('Phase286 compiled producer + typed-retention marker audit: PASS')
PY

python3 scripts/286_apply_golden_fdr_dma_chain.py --root "$ROOT" --check-only
python3 scripts/286c_fix_dma_replay_width.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase286C Golden-FDR DSI DMA causal-chain build/repack: PASS'
