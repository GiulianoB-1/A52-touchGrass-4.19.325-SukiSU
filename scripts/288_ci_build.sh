#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report(){
  set +e
  rm -rf phase288-failure
  mkdir -p phase288-failure/{source,logs,audit,config}
  cp phase288-compile.log phase288-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$HW" "$REC"; do [ -f "$f" ] && cp "$f" phase288-failure/source/ || true; done
  cp scripts/288*.py phase288-failure/audit/ 2>/dev/null || true
  cp /tmp/p288-*.config /tmp/p288-*.diff phase288-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

bash scripts/287_ci_build.sh
test -s phase287-out/package/boot.img
for f in "$DSI" "$HW" "$REC"; do test -s "$f"; done
grep -Fq 'A52_PHASE287_DSI_DMA_FETCH_PROVENANCE_V1' "$HW"
grep -Fq 'A52_PHASE287B_RETAINED_FETCH_PROVENANCE_V1' "$REC"
grep -Fq 'A52_PHASE286C_PACKED_REPLAY_WIDTH_V1' "$REC"
grep -Fq 'P276 282A m=fifo f=%x' "$DSI"

cp "$OUT/.config" /tmp/p288-base.config
cp "$DSI" /tmp/p288-dsi-before.c
cp "$HW" /tmp/p288-hw-before.c
cp "$REC" /tmp/p288-rec-before.c

python3 -m py_compile scripts/288_apply_retained_fifo_causal_chain.py scripts/288b_apply_fifo_typed_retention.py
python3 scripts/288_apply_retained_fifo_causal_chain.py --root "$ROOT"
python3 scripts/288b_apply_fifo_typed_retention.py --root "$ROOT"
python3 scripts/288_apply_retained_fifo_causal_chain.py --root "$ROOT" --check-only
python3 scripts/288b_apply_fifo_typed_retention.py --root "$ROOT" --check-only

cmp -s /tmp/p288-dsi-before.c "$DSI"
! cmp -s /tmp/p288-hw-before.c "$HW"
! cmp -s /tmp/p288-rec-before.c "$REC"
cmp -s /tmp/p288-base.config "$OUT/.config"

for token in \
  'A52_PHASE288_FIFO_CAUSAL_CHAIN_V1' \
  'P288 F0 c=%d s=%u f=%x cfg=%x' \
  'P288 F1 c=%d tg=%x w0=%x w1=%x' \
  'P288 F2 c=%d st=%x fs=%x tg=%x' \
  'P288 F3 c=%d dc=%x dl=%x fs=%x in=%x' \
  'P288 F4 c=%d sw=%u st=%x fs=%x in=%x'; do grep -Fq "$token" "$HW"; done
test "$(grep -Fc 'P288 F4 c=%d sw=%u st=%x fs=%x in=%x' "$HW")" -eq 2
for token in \
  'A52_PHASE288B_RETAINED_FIFO_CHAIN_V1' \
  'a52_p288_capture_fmt(fmt, args);' \
  'return !strncmp(message, "P288 ", 5)' \
  'strncmp(fmt, "P288", 4)' \
  'type = 18; n = 4;' 'type = 19; n = 4;' 'type = 20; n = 4;' \
  'type = 21; n = 5;' 'type = 22; n = 5;' \
  'P286 R0 %llx %x %x %llx %llx' 'P286 R1 %llx %llx %llx'; do grep -Fq "$token" "$REC"; done

python3 - "$DSI" "$HW" "$REC" <<'PY'
from pathlib import Path
import sys
dsi, hw, rec = map(lambda p: Path(p).read_text(), sys.argv[1:])
fn0=hw.index('void dsi_ctrl_hw_cmn_kickoff_fifo_command(')
fn1=hw.index('\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo(',fn0)
fn=hw[fn0:fn1]
marks=['P288 F0 c=%d s=%u f=%x cfg=%x','P288 F1 c=%d tg=%x w0=%x w1=%x','P288 F2 c=%d st=%x fs=%x tg=%x','P288 F3 c=%d dc=%x dl=%x fs=%x in=%x']
pos=[fn.index(m) for m in marks]
if pos != sorted(pos): raise SystemExit('Phase288 FIFO producer order audit failed')
trig=fn.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);')
f4=fn.index('P288 F4 c=%d sw=%u st=%x fs=%x in=%x',trig)
if f4 < trig: raise SystemExit('Phase288 F4 precedes production trigger')
cap=rec.index('a52_p288_capture_fmt(fmt, args);')
pack=rec.find('vscnprintf',cap)
if pack < 0 or cap > pack: raise SystemExit('Phase288 typed capture occurs after text packing')
pt=dsi.index('P286 T c=%d st=%x done=%d irq=%d')
flush=dsi.index('a52_p286_flush_timeout_chain();',pt)
z=dsi.index('P276 280Z q=2',flush)
freeze=dsi.index('a52_ackfr_retain_timeout_snapshot();',z)
if not (pt < flush < z < freeze): raise SystemExit('retained replay is not immediately pre-freeze')
u=(1<<64)-1
lines=[f'P286 RH {u:x} {u:x}',f'P286 R0 {u:x} {22:x} {5:x} {u:x} {u:x}',f'P286 R1 {u:x} {u:x} {u:x}']
for line in lines:
    if len(line)>72: raise SystemExit(f'packed replay width {len(line)}>72: {line}')
print('Phase288 ordering, typed retention, and packed-width audits: PASS')
PY

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
if ! cmp -s /tmp/p288-base.config "$OUT/.config"; then
  diff -u /tmp/p288-base.config "$OUT/.config" > /tmp/p288-config.diff || true
  echo '::error::Phase288 changed kernel config'; cat /tmp/p288-config.diff; exit 1
fi
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase288-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase288-out
mkdir -p phase288-out/{compile,config,package,audit,source}
cp "$IMAGE" phase288-out/compile/Image
cp "$OUT/.config" phase288-out/config/final.config
cp /tmp/p288-base.config phase288-out/audit/phase287b-final.config
cp /tmp/p288-dsi-before.c phase288-out/audit/dsi-ctrl-before.c
cp /tmp/p288-hw-before.c phase288-out/audit/dsi-ctrl-hw-cmn-before.c
cp /tmp/p288-rec-before.c phase288-out/audit/recorder-before.c
cp phase288-compile.log phase288-out/audit/
cp scripts/288_apply_retained_fifo_causal_chain.py phase288-out/audit/
cp scripts/288b_apply_fifo_typed_retention.py phase288-out/audit/
cp "$DSI" phase288-out/source/dsi_ctrl.c
cp "$HW" phase288-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase288-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase288-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py --source phase287-out/package/boot.img --kernel phase288-out/package/Image.gz --output phase288-out/package/boot.img --report phase288-out/package/repack-report.json
test -s phase288-out/package/boot.img
file phase288-out/package/boot.img | tee phase288-out/package/boot-file.txt

cat > phase288-out/PHASE288-RECORD-SCHEMA.txt <<'EOF'
Phase288 retained FIFO causal-chain recorder
============================================
F0: FIFO function entry: controller, encoded size, hw flags, cfg bits (broadcast/master/LPM).
F1: after TPG/FIFO mode control write: TPG control readback + first two encoded DWORDs.
F2: after command DWORDs and pad are written to TPG DMA FIFO: DSI_STATUS/FIFO_STATUS/TPG control.
F3: after DMA control + DMA length programming and wmb: DMA control/length/FIFO status/INT control.
F4: immediate path strictly after existing SW trigger (sw=1), or deferred path without adding a trigger (sw=0), plus DSI/FIFO/INT state.
Retained types: 18=F0, 19=F1, 20=F2, 21=F3, 22=F4.
P288 is captured as exact typed varargs before text packing into the same final-32 tail used by Phase286C/287B. It is replayed using compact P286 R0/R1 immediately before the Phase280 ramoops retention freeze. Existing retained P286 W/T/G/HT provide wait, timeout, DMA_DONE ISR and deferred-trigger evidence.
EOF

python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase288-out')
idn={'phase':'288B','name':'RETAINED-DSI-FIFO-CAUSAL-CHAIN','git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,'base':'green Phase287B retained DMA provenance lineage','phase282_fifo_ab_preserved':True,'behavior_change':False,'register_writes_added':False,'trigger_policy_changed':False,'recovery_changed':False,'brightness_changed_from_base':False,'source_scope':'dsi_ctrl_hw_cmn.c passive readbacks/records + Golden-FDR typed capture/admission only','recorder_change':'admit P288 and fold exact F0..F4 varargs into existing final-32 typed timeout tail','previous_recorder_failures_addressed':['P288 explicitly admitted by both filters','typed capture occurs before fixed-width text packing','tail replay occurs immediately before retention freeze','compact R0/R1 replay remains <=72 bytes'],'question':'For the Phase282 FIFO-routed failing command, does FIFO/TPG programming execute, does the normal SW trigger execute or defer, and how do DSI/FIFO/interrupt states change at each causal boundary before the inherited 200 ms timeout?','golden_repack_source':'phase287-out/package/boot.img','repacker':'scripts/38_repack_a52_p1_boot.py'}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[p.relative_to(r) for p in sorted(r.rglob('*')) if p.is_file() and p.name!='SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
 for n in files:f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+str(n)+'\n')
PY
(cd phase288-out && sha256sum -c SHA256SUMS)
python3 - <<'PY'
from pathlib import Path
img=Path('phase288-out/compile/Image').read_bytes()
for m in ['P288 F0 c=%d s=%u f=%x cfg=%x','P288 F1 c=%d tg=%x w0=%x w1=%x','P288 F2 c=%d st=%x fs=%x tg=%x','P288 F3 c=%d dc=%x dl=%x fs=%x in=%x','P288 F4 c=%d sw=%u st=%x fs=%x in=%x','P286 W c=%d r=%d irq=%d','P286 T c=%d st=%x done=%d irq=%d','P286 G c=%d st=%x irq0=%d','P286 HT c=%d sw=1','P286 R0 %llx %x %x %llx %llx','P286 R1 %llx %llx %llx']:
 if m.encode() not in img: raise SystemExit('Phase288 marker missing from Image: '+m)
print('Phase288 compiled FIFO producer + retained replay marker audit: PASS')
PY
python3 scripts/288_apply_retained_fifo_causal_chain.py --root "$ROOT" --check-only
python3 scripts/288b_apply_fifo_typed_retention.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase288B retained FIFO causal-chain build/repack: PASS'
