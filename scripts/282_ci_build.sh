#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMMON="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"

fail_report(){
  set +e
  rm -rf phase282-failure
  mkdir -p phase282-failure/{source,logs,audit}
  cp phase282-compile.log phase282-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase282-failure/source/ || true
  done
  cp scripts/282_apply_golden_fifo_ab.py phase282-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct exact Phase281 first. Phase282 changes only dsi_ctrl.c and uses
# the existing TouchGrass FIFO/TPG command transport for one diagnostic packet.
bash scripts/281_ci_build.sh
test -s phase281-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$SMMU" "$REC" "$COMMON" "$PANEL"; do test -s "$f"; done

cp "$OUT/.config" /tmp/p282-base.config
cp "$DSI" /tmp/p282-dsi-before.c
cp "$SMMU" /tmp/p282-smmu-before.c
cp "$REC" /tmp/p282-rec-before.c
cp "$COMMON" /tmp/p282-common-before.c
cp "$PANEL" /tmp/p282-panel-before.c

python3 -m py_compile scripts/282_apply_golden_fifo_ab.py
python3 scripts/282_apply_golden_fifo_ab.py --root "$ROOT"
python3 scripts/282_apply_golden_fifo_ab.py --root "$ROOT" --check-only

# This phase must not alter SMMU, recorder implementation, Samsung brightness
# mapping, panel source, kernel config, DT, or any unrelated subsystem.
cmp -s /tmp/p282-smmu-before.c "$SMMU"
cmp -s /tmp/p282-rec-before.c "$REC"
cmp -s /tmp/p282-common-before.c "$COMMON"
cmp -s /tmp/p282-panel-before.c "$PANEL"
! cmp -s /tmp/p282-dsi-before.c "$DSI"

grep -Fq 'A52_PHASE282_GOLDEN_FIFO_AB_V1' "$DSI"
grep -Fq 'msg->type == 0x29' "$DSI"
grep -Fq '*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;' "$DSI"
grep -Fq '*flags |= DSI_CTRL_CMD_FIFO_STORE;' "$DSI"
grep -Fq 'P276 282P t=%02x f=%x b=%02x%02x%02x' "$DSI"
grep -Fq 'P276 282E %02x%02x%02x%02x%02x%02x%02x%02x' "$DSI"
grep -Fq 'P276 282F a=0 t=1' "$DSI"
grep -Fq 'P276 282F a=%d t=0' "$DSI"
grep -Fq 'P276 282Z q=2' "$DSI"
grep -Fq 'P276 280Z q=2' "$DSI"
grep -Fq 'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);' "$DSI"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p282-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase282-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase282-out
mkdir -p phase282-out/{compile,config,package,audit,source}
cp "$IMAGE" phase282-out/compile/Image
cp "$OUT/.config" phase282-out/config/final.config
cp /tmp/p282-base.config phase282-out/audit/phase281-final.config
cp /tmp/p282-dsi-before.c phase282-out/audit/dsi-ctrl-before.c
cp /tmp/p282-smmu-before.c phase282-out/audit/arm-smmu-before.c
cp /tmp/p282-rec-before.c phase282-out/audit/recorder-before.c
cp /tmp/p282-common-before.c phase282-out/audit/ss-dsi-panel-common-before.c
cp /tmp/p282-panel-before.c phase282-out/audit/ss-dsi-panel-a52-before.c
cp phase282-compile.log phase282-out/audit/
cp scripts/282_apply_golden_fifo_ab.py phase282-out/audit/
cp "$DSI" phase282-out/source/dsi_ctrl.c
cp "$SMMU" phase282-out/source/arm-smmu.c
cp "$REC" phase282-out/source/a52_ack_secure_flight_recorder.c
cp "$COMMON" phase282-out/source/ss_dsi_panel_common.c
cp "$PANEL" phase282-out/source/ss_dsi_panel_S6E3FC3_AMS646YD01.c

gzip -n -c "$IMAGE" > phase282-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase281-out/package/boot.img \
  --kernel phase282-out/package/Image.gz \
  --output phase282-out/package/boot.img \
  --report phase282-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase282-out')
idn={
 'phase':'282',
 'name':'GOLDEN-FIFO-VS-MEMORY-DMA-AB',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact Phase281 DSI DMA consumption + brightness trace',
 'golden_runtime_evidence':'TouchGrass completes Samsung brightness writes, including logical 128 -> gm2_wrdisbv 422 (0x01a6)',
 'golden_source_evidence':'TouchGrass supports FIFO/TPG command transport and selects FIFO_STORE instead of FETCH_MEMORY in secure mode',
 'phase281_hardware_evidence':'first retained stalled message: type decimal 41 = 0x29 Generic Long Write, tx_len=3; memory DMA enters persistent non-completing state',
 'functional_change':'one-shot diagnostic: first deep-window 0x29 tx_len=3 memory command is routed through existing FIFO_STORE transport; all later commands retain normal policy',
 'smmu_state_changed':False,
 'mapping_or_tlb_changed':False,
 'recorder_implementation_changed':False,
 'brightness_mapping_changed_from_phase281':False,
 'timeout_recovery_changed':False,
 'phase281_q2_snapshot_preserved':True,
 'record_schema':{
   '282P':'3-byte message identity: type, pre-policy flags, raw payload bytes',
   '282A':'one-shot FIFO A/B selection and resulting flags',
   '282E':'exact 8-byte encoded packet after MIPI packet construction/padding',
   '282F':'FIFO outcome: a=DMA IRQ/completion atomic, t=timeout boolean',
   '282Z':'Phase282 timeout boundary immediately before inherited Phase280 freeze'
 },
 'decision':{
   'fifo_success_then_memory_timeout':'DSI core/trigger/completion/link can progress without external command-memory fetch; isolate external DMA fetch/interconnect/coherency path',
   'fifo_timeout':'failure is downstream/shared with FIFO path; prioritize DSI core clocks/power/state machine/PHY rather than external memory fetch',
   'fifo_success_and_boot_progress':'the bypassed first generic command identifies the memory-DMA transport as causal; inspect subsequent behavior and retained payloads'
 }
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[
 'compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json',
 'audit/phase281-final.config','audit/dsi-ctrl-before.c','audit/arm-smmu-before.c','audit/recorder-before.c',
 'audit/ss-dsi-panel-common-before.c','audit/ss-dsi-panel-a52-before.c','audit/phase282-compile.log',
 'audit/282_apply_golden_fifo_ab.py','source/dsi_ctrl.c','source/arm-smmu.c',
 'source/a52_ack_secure_flight_recorder.c','source/ss_dsi_panel_common.c',
 'source/ss_dsi_panel_S6E3FC3_AMS646YD01.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase282-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase282-out')
d=(r/'source/dsi_ctrl.c').read_text(); img=(r/'compile/Image').read_bytes()
for m in [
 'A52_PHASE282_GOLDEN_FIFO_AB_V1','msg->type == 0x29',
 '*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;','*flags |= DSI_CTRL_CMD_FIFO_STORE;',
 'a52_p281_dsi_dma_snapshot(dsi_ctrl, 0);','a52_p281_dsi_dma_snapshot(dsi_ctrl, 1);',
 'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);','P276 280Z q=2']:
 if m not in d: raise SystemExit('Phase282 source marker missing: '+m)
for m in [
 'P276 282P t=%02x f=%x b=%02x%02x%02x','P276 282A m=fifo f=%x',
 'P276 282E %02x%02x%02x%02x%02x%02x%02x%02x',
 'P276 282F a=0 t=1','P276 282F a=%d t=0','P276 282Z q=2','P276 280Z q=2']:
 if m.encode() not in img: raise SystemExit('Phase282 runtime marker missing from Image: '+m)
print('Phase282 compiled Golden FIFO A/B marker audit: PASS')
PY

python3 scripts/282_apply_golden_fifo_ab.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase282 Golden FIFO-vs-memory DMA A/B build/repack: PASS'
