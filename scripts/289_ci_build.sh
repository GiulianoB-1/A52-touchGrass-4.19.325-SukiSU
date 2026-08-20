#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase289-failure
  mkdir -p phase289-failure/{source,logs,audit,config}
  cp phase289-compile.log phase289-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$HW" "$REC"; do [ -f "$f" ] && cp "$f" phase289-failure/source/ || true; done
  cp scripts/289*.py phase289-failure/audit/ 2>/dev/null || true
  cp /tmp/p289-*.config /tmp/p289-*.diff phase289-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

bash scripts/288_ci_build.sh
test -s phase288-out/package/boot.img
for f in "$DSI" "$HW" "$REC"; do test -s "$f"; done
grep -Fq 'A52_PHASE282_GOLDEN_FIFO_AB_V1' "$DSI"
grep -Fq 'A52_PHASE288_FIFO_CAUSAL_CHAIN_V1' "$HW"
grep -Fq 'A52_PHASE288B_RETAINED_FIFO_CHAIN_V1' "$REC"
grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$REC"

cp "$OUT/.config" /tmp/p289-base.config
cp "$DSI" /tmp/p289-dsi-before.c
cp "$HW" /tmp/p289-hw-before.c
cp "$REC" /tmp/p289-rec-before.c

python3 -m py_compile scripts/289_apply_sticky_fifo_timeout_retention.py
python3 scripts/289_apply_sticky_fifo_timeout_retention.py --root "$ROOT"
python3 scripts/289_apply_sticky_fifo_timeout_retention.py --root "$ROOT" --check-only

! cmp -s /tmp/p289-dsi-before.c "$DSI"
! cmp -s /tmp/p289-hw-before.c "$HW"
! cmp -s /tmp/p289-rec-before.c "$REC"
cmp -s /tmp/p289-base.config "$OUT/.config"

for token in \
  'A52_PHASE289_TARGET_TIMEOUT_RETENTION_V1' \
  'bool a52_p289_fifo_trace_active(void)' \
  'a52_p289_snapshot_record(0, 4,' \
  'a52_p289_snapshot_record(6, 3,' \
  'a52_p289_snapshot_record(7, 3,' \
  'a52_p289_snapshot_record(8, 4,' \
  'a52_p289_flush_timeout_snapshot();'; do grep -Fq "$token" "$DSI"; done
for token in \
  'A52_PHASE289_FIFO_CAUSAL_SLOTS_V1' \
  'a52_p289_snapshot_record(1, 4,' \
  'a52_p289_snapshot_record(2, 4,' \
  'a52_p289_snapshot_record(3, 4,' \
  'a52_p289_snapshot_record(4, 5,'; do grep -Fq "$token" "$HW"; done
test "$(grep -Fc 'a52_p289_snapshot_record(5, 5,' "$HW")" -eq 2
for token in \
  'A52_PHASE289_STICKY_FIFO_SNAPSHOT_V1' \
  '#define A52_P289_SLOTS 9U' \
  'P289 RH v=%lx' \
  'P289 TARGET c=%x f=%x t=%x l=%x' \
  'P289 F0 c=%x s=%x f=%x cfg=%x' \
  'P289 F1 c=%x tg=%x w0=%x w1=%x' \
  'P289 F2 c=%x st=%x fs=%x tg=%x' \
  'P289 F3 c=%x dc=%x dl=%x fs=%x in=%x' \
  'P289 F4 c=%x sw=%x st=%x fs=%x in=%x' \
  'P289 W c=%x r=%x irq=%x' \
  'P289 G c=%x st=%x irq0=%x' \
  'P289 T c=%x st=%x done=%x irq=%x' \
  'return !strncmp(message, "P289 ", 5)' \
  'strncmp(fmt, "P289", 4)'; do grep -Fq "$token" "$REC"; done

python3 - "$DSI" "$HW" "$REC" /tmp/p289-dsi-before.c /tmp/p289-hw-before.c <<'PY'
from pathlib import Path
import sys
dsi, hw, rec, dsi0, hw0 = [Path(p).read_text() for p in sys.argv[1:]]
for label, before, after in [('dsi', dsi0, dsi), ('hw', hw0, hw)]:
    for needle in ['DSI_W32(', 'writel(', 'writel_relaxed(', 'trigger_command_dma(',
                   'wait_for_completion_timeout(', 'msleep(', 'usleep_range(', 'udelay(']:
        if before.count(needle) != after.count(needle):
            raise SystemExit(f'Phase289 changed {label} production primitive count for {needle}')
arm = dsi.index('atomic_set(&a52_p282_fifo_inflight, 1);')
target = dsi.index('a52_p289_snapshot_record(0, 4,', arm)
if target < arm:
    raise SystemExit('Phase289 TARGET precedes Phase282 inflight arm')
t = dsi.index('a52_p289_snapshot_record(8, 4,')
flush = dsi.index('a52_p289_flush_timeout_snapshot();', t)
freeze = dsi.index('a52_ackfr_retain_timeout_snapshot();', flush)
if not (t < flush < freeze):
    raise SystemExit('Phase289 timeout capture/replay/freeze ordering invalid')
between = dsi[flush:freeze]
if 'a52_ackfr_record(' in between:
    raise SystemExit('Phase289 replay is not the final recorder emission before freeze')
f0 = hw.index('void dsi_ctrl_hw_cmn_kickoff_fifo_command(')
f1 = hw.index('\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo(', f0)
ffn = hw[f0:f1]
w = ffn.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);')
s = ffn.index('a52_p289_snapshot_record(5, 5,', w)
if s < w:
    raise SystemExit('Phase289 immediate F4 precedes SW_TRIGGER')
tr = hw.index('void dsi_ctrl_hw_cmn_trigger_command_dma(')
tw = hw.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);', tr)
ts = hw.index('a52_p289_snapshot_record(5, 5,', tw)
if ts < tw:
    raise SystemExit('Phase289 deferred F4 precedes SW_TRIGGER')
if hw.count('a52_p289_snapshot_record(5, 5,') != 2:
    raise SystemExit('Phase289 F4 actual-trigger coverage is not exactly two paths')
for call in ['a52_p289_snapshot_record(1, 4,', 'a52_p289_snapshot_record(2, 4,',
             'a52_p289_snapshot_record(3, 4,', 'a52_p289_snapshot_record(4, 5,']:
    pos = hw.index(call)
    prefix = hw[max(0, pos-120):pos]
    if 'a52_p286_dma_trace_active()' in prefix and 'a52_p289_fifo_trace_active()' not in prefix:
        raise SystemExit('Phase289 sticky producer still depends on Phase286 gate: '+call)
print('Phase289 target, passive-write, actual-trigger, and final-freeze audits: PASS')
PY

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
if ! cmp -s /tmp/p289-base.config "$OUT/.config"; then
  diff -u /tmp/p289-base.config "$OUT/.config" > /tmp/p289-config.diff || true
  echo '::error::Phase289 changed kernel config'; cat /tmp/p289-config.diff; exit 1
fi
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase289-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase289-out
mkdir -p phase289-out/{compile,config,package,audit,source}
cp "$IMAGE" phase289-out/compile/Image
cp "$OUT/.config" phase289-out/config/final.config
cp /tmp/p289-base.config phase289-out/audit/phase288b-final.config
cp /tmp/p289-dsi-before.c phase289-out/audit/dsi-ctrl-before.c
cp /tmp/p289-hw-before.c phase289-out/audit/dsi-ctrl-hw-cmn-before.c
cp /tmp/p289-rec-before.c phase289-out/audit/recorder-before.c
cp phase289-compile.log phase289-out/audit/
cp scripts/289_apply_sticky_fifo_timeout_retention.py phase289-out/audit/
cp "$DSI" phase289-out/source/dsi_ctrl.c
cp "$HW" phase289-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase289-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase289-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase288-out/package/boot.img \
  --kernel phase289-out/package/Image.gz \
  --output phase289-out/package/boot.img \
  --report phase289-out/package/repack-report.json
test -s phase289-out/package/boot.img
test "$(stat -c '%s' phase289-out/package/boot.img)" -gt 1000000
file phase289-out/package/boot.img | tee phase289-out/package/boot-file.txt
sha256sum phase289-out/package/boot.img | tee phase289-out/package/boot-sha256.txt

cat > phase289-out/PHASE289-RECORD-SCHEMA.txt <<'EOF'
Phase289 sticky FIFO timeout snapshot
=====================================
TARGET: exact Phase282 one-shot 0x29/tx_len=3 command after FETCH_MEMORY is replaced by FIFO_STORE.
F0: FIFO function entry, encoded size, HW flags, broadcast/master/LPM bits.
F1: TPG control readback and first two encoded command DWORDs.
F2: post FIFO-fill DSI_STATUS, FIFO_STATUS, TPG control.
F3: post DMA-control/length programming and wmb, with FIFO/INT state.
F4: ACTUAL production SW_TRIGGER write happened. Phase289 does not store the inherited Phase288 deferred sw=0 pseudo-stage. For deferred commands F4 is stored only in dsi_ctrl_hw_cmn_trigger_command_dma(), after the real SW_TRIGGER write.
W: completion wait return and dma_irq_trig.
G: DMA_DONE IRQ observed, status and pre-set dma_irq_trig.
T: timeout raw interrupt status, DMA_DONE bit and dma_irq_trig.
RH: valid-slot bitmap. Bits 0..8 map TARGET,F0,F1,F2,F3,F4,W,G,T.

Retention model:
- Dedicated fixed one-write slots, not the Phase286 circular final-32 tail.
- Slots are gated by the Phase282 a52_p282_fifo_inflight latch, not a52_p286_dma_trace_active().
- At the target timeout, the fixed snapshot is replayed after inherited P286 replay and P276 280Z, as the final recorder output immediately before a52_ackfr_retain_timeout_snapshot() freezes the recorder.
- No DSI register writes, trigger policy, waits, reset/recovery behavior, or brightness behavior are changed.
EOF

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase289-out')
idn={
 'phase':'289',
 'name':'STICKY-FIFO-TIMEOUT-RETENTION',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'green Phase288B retained FIFO causal-chain lineage',
 'phase282_fifo_ab_preserved':True,
 'behavior_change':False,
 'register_writes_added':False,
 'trigger_policy_changed':False,
 'recovery_changed':False,
 'brightness_changed_from_base':False,
 'source_scope':'dedicated fixed diagnostic slots + exact Phase282 inflight gate + final timeout replay',
 'retention_change':'P289 fixed slots replayed as final recorder records immediately before inherited Phase280 freeze',
 'f4_semantics':'present only after an actual production SW_TRIGGER write, including deferred trigger helper',
 'question':'For the exact Phase282 FIFO-routed failing 0x29/len=3 command, what is the last causal boundary reached, and did an actual SW_TRIGGER occur before DMA_DONE remained absent?',
 'golden_repack_source':'phase288-out/package/boot.img',
 'repacker':'scripts/38_repack_a52_p1_boot.py'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[p.relative_to(r) for p in sorted(r.rglob('*')) if p.is_file() and p.name!='SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
 for n in files:
  f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+str(n)+'\n')
PY
(cd phase289-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
img=Path('phase289-out/compile/Image').read_bytes()
for m in [
 'P289 RH v=%lx', 'P289 TARGET c=%x f=%x t=%x l=%x',
 'P289 F0 c=%x s=%x f=%x cfg=%x', 'P289 F1 c=%x tg=%x w0=%x w1=%x',
 'P289 F2 c=%x st=%x fs=%x tg=%x', 'P289 F3 c=%x dc=%x dl=%x fs=%x in=%x',
 'P289 F4 c=%x sw=%x st=%x fs=%x in=%x', 'P289 W c=%x r=%x irq=%x',
 'P289 G c=%x st=%x irq0=%x', 'P289 T c=%x st=%x done=%x irq=%x',
 'P276 282A m=fifo f=%x', 'P276 280Z q=2'
]:
 if m.encode() not in img:
  raise SystemExit('Phase289 compiled marker missing from Image: '+m)
print('Phase289 compiled sticky FIFO + timeout replay marker audit: PASS')
PY
python3 scripts/289_apply_sticky_fifo_timeout_retention.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase289 sticky FIFO timeout retention build/Golden-FDR repack: PASS'
