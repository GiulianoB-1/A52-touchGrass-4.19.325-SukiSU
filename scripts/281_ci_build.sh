#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMMON="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"

fail_report(){
  set +e
  rm -rf phase281-failure
  mkdir -p phase281-failure/{source,logs,audit}
  cp phase281-compile.log phase281-failure/logs/ 2>/dev/null || true
  for f in "$SMMU" "$DSI" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase281-failure/source/ || true
  done
  cp scripts/281_*.py phase281-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-validated Phase280 source first. Phase281
# keeps all Phase280 SMMU evidence and timeout retention, adds only read-only
# DSI controller snapshots, and separately changes the A52 panel's initial
# logical brightness from 255 to 128 while tracing the complete mapping/TX path.
bash scripts/280_ci_build.sh
test -s phase280-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$SMMU" "$DSI" "$REC" "$COMMON" "$PANEL"; do test -s "$f"; done

cp "$OUT/.config" /tmp/p281-base.config
cp "$SMMU" /tmp/p281-smmu-before.c
cp "$DSI" /tmp/p281-dsi-before.c
cp "$REC" /tmp/p281-rec-before.c
cp "$COMMON" /tmp/p281-common-before.c
cp "$PANEL" /tmp/p281-panel-before.c

python3 -m py_compile \
  scripts/281_apply_dsi_dma_trace.py \
  scripts/281_apply_brightness_trace.py \
  scripts/281_check_dsi_dma_brightness_trace.py

python3 scripts/281_apply_dsi_dma_trace.py --root "$ROOT"
python3 scripts/281_apply_dsi_dma_trace.py --root "$ROOT" --check-only
python3 scripts/281_apply_brightness_trace.py --root "$ROOT"
python3 scripts/281_apply_brightness_trace.py --root "$ROOT" --check-only
python3 scripts/281_check_dsi_dma_brightness_trace.py \
  --before-dsi /tmp/p281-dsi-before.c --after-dsi "$DSI" \
  --before-common /tmp/p281-common-before.c --after-common "$COMMON" \
  --before-panel /tmp/p281-panel-before.c --after-panel "$PANEL" \
  --before-smmu /tmp/p281-smmu-before.c --after-smmu "$SMMU" \
  --before-recorder /tmp/p281-rec-before.c --after-recorder "$REC"

# Preserve the Phase280 kernel configuration exactly.
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p281-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase281-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase281-out
mkdir -p phase281-out/{compile,config,package,audit,source}
cp "$IMAGE" phase281-out/compile/Image
cp "$OUT/.config" phase281-out/config/final.config
cp /tmp/p281-base.config phase281-out/audit/phase280-final.config
cp /tmp/p281-smmu-before.c phase281-out/audit/arm-smmu-before.c
cp /tmp/p281-dsi-before.c phase281-out/audit/dsi-ctrl-before.c
cp /tmp/p281-rec-before.c phase281-out/audit/recorder-before.c
cp /tmp/p281-common-before.c phase281-out/audit/ss-dsi-panel-common-before.c
cp /tmp/p281-panel-before.c phase281-out/audit/ss-dsi-panel-a52-before.c
cp phase281-compile.log phase281-out/audit/
cp scripts/281_apply_dsi_dma_trace.py phase281-out/audit/
cp scripts/281_apply_brightness_trace.py phase281-out/audit/
cp scripts/281_check_dsi_dma_brightness_trace.py phase281-out/audit/
cp "$SMMU" phase281-out/source/arm-smmu.c
cp "$DSI" phase281-out/source/dsi_ctrl.c
cp "$REC" phase281-out/source/a52_ack_secure_flight_recorder.c
cp "$COMMON" phase281-out/source/ss_dsi_panel_common.c
cp "$PANEL" phase281-out/source/ss_dsi_panel_S6E3FC3_AMS646YD01.c

gzip -n -c "$IMAGE" > phase281-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase280-out/package/boot.img \
  --kernel phase281-out/package/Image.gz \
  --output phase281-out/package/boot.img \
  --report phase281-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase281-out')
idn={
 'phase':'281',
 'name':'DSI-DMA-CONSUMPTION-BRIGHTNESS-TRACE',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'exact hardware-validated Phase280 retained SMMU timeout evidence',
 'smmu_state_changed':False,
 'mapping_or_tlb_changed':False,
 'timeout_recovery_changed':False,
 'dsi_register_writes_added':False,
 'display_behavior_change':'A52 panel initial logical brightness 255 -> 128 only',
 'brightness_note':'128 is 50% of the prior logical 255 default; panel luminance is table-mapped and need not be linear',
 'dsi_snapshot_points':{
   '0':'immediately before high-level kickoff_command call',
   '1':'immediately after kickoff_command returns, after its DMA programming/SW trigger path',
   '2':'DMA timeout, before interrupt/error handling and before Phase280 retention latch'
 },
 'record_schema':{
   '281R0':'q then DSI_STATUS,FIFO_STATUS,COMMAND_MODE_DMA_CTRL,DMA_FIFO_CTRL,DMA_SW_TRIGGER,INT_CTRL',
   '281R1':'q then ACK_ERR_STATUS,TIMEOUT_STATUS,LANE_STATUS,DLN0_PHY_ERR,AXI2AHB_CTRL,VBIF_CTRL',
   '281R2':'q=2 then DMA_CMD_OFFSET,DMA_CMD_LENGTH,CLK_CTRL,CLK_STATUS',
   '281BE':'brightness entry: requested level,current logical level,origin',
   '281BM':'mapped brightness: logical level,cd_idx,cd_level,gm2_wrdisbv',
   '281BV':'A52 panel WRDISBV command index,gm2_wrdisbv,and exact first 3 tx bytes',
   '281BT':'ss_send_cmd return code plus logical level and gm2_wrdisbv',
   '281BO':'backlight-device override from/to',
   '281BI':'A52 panel initialized logical brightness'
 },
 'hardware_question':'Does the DSI controller show evidence of command-DMA consumption/progress between trigger and timeout, and does the A52 0x51 brightness payload get built and transmitted at logical 128 or get overridden/fail in the same DMA path?',
 'interpretation':{
   'programming':'q1 core/FIFO state differs from q0 as expected => low-level kickoff programmed the controller',
   'consumption':'FIFO/status transition q1->q2 without DMA_DONE may indicate partial controller progress; no transition strengthens fetch/start stall',
   'controller_error':'ACK/TIMEOUT/LANE/PHY fields becoming nonzero identify controller/link-side failure',
   'interconnect':'stable clean link/error state with no consumption keeps AXI/VBIF/interconnect/coherency/controller-fetch path high priority',
   'brightness':'281BV shows exact command byte and WRDISBV bytes; 281BT gives Samsung send return; 281BO reveals later logical override'
 }
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=[
 'compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json',
 'audit/phase280-final.config','audit/arm-smmu-before.c','audit/dsi-ctrl-before.c','audit/recorder-before.c',
 'audit/ss-dsi-panel-common-before.c','audit/ss-dsi-panel-a52-before.c','audit/phase281-compile.log',
 'audit/281_apply_dsi_dma_trace.py','audit/281_apply_brightness_trace.py','audit/281_check_dsi_dma_brightness_trace.py',
 'source/arm-smmu.c','source/dsi_ctrl.c','source/a52_ack_secure_flight_recorder.c',
 'source/ss_dsi_panel_common.c','source/ss_dsi_panel_S6E3FC3_AMS646YD01.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase281-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase281-out')
d=(r/'source/dsi_ctrl.c').read_text()
c=(r/'source/ss_dsi_panel_common.c').read_text()
p=(r/'source/ss_dsi_panel_S6E3FC3_AMS646YD01.c').read_text()
img=(r/'compile/Image').read_bytes()
source_required=[
 'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1','A52_PHASE281_BRIGHTNESS_MAPPING_TRACE_V1',
 'A52_PHASE281_EARLY_50PCT_BRIGHTNESS_V1','vdd->br_info.common_br.bl_level = 128;',
 'a52_p281_dsi_dma_snapshot(dsi_ctrl, 0);','a52_p281_dsi_dma_snapshot(dsi_ctrl, 1);',
 'a52_p281_dsi_dma_snapshot(dsi_ctrl, 2);']
for m in source_required:
 if m not in d+c+p: raise SystemExit('Phase281 source marker missing: '+m)
runtime=[
 'P276 281R0 q=%u %x %x %x %x %x %x','P276 281R1 q=%u %x %x %x %x %x %x',
 'P276 281R2 q=2 %x %x %x %x','P276 281BE l=%d c=%d o=%d',
 'P276 281BM l=%d i=%d c=%d g=%x','P276 281BT r=%d l=%d g=%x',
 'P276 281BO f=%d t=%d','P276 281BI l=%d','P276 281BV i=%d g=%x %02x%02x%02x',
 'P276 280Z q=2']
for m in runtime:
 if m.encode() not in img: raise SystemExit('Phase281 runtime marker missing from Image: '+m)
print('Phase281 compiled DSI + brightness marker audit: PASS')
PY

python3 scripts/281_apply_dsi_dma_trace.py --root "$ROOT" --check-only
python3 scripts/281_apply_brightness_trace.py --root "$ROOT" --check-only
python3 scripts/281_check_dsi_dma_brightness_trace.py \
  --before-dsi /tmp/p281-dsi-before.c --after-dsi "$DSI" \
  --before-common /tmp/p281-common-before.c --after-common "$COMMON" \
  --before-panel /tmp/p281-panel-before.c --after-panel "$PANEL" \
  --before-smmu /tmp/p281-smmu-before.c --after-smmu "$SMMU" \
  --before-recorder /tmp/p281-rec-before.c --after-recorder "$REC"

trap - EXIT
echo 'Phase281 DSI DMA consumption + brightness trace build/repack: PASS'
