#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMMON="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"

fail_report(){
  set +e
  rm -rf phase283-failure
  mkdir -p phase283-failure/{source,logs,audit}
  cp phase283-compile.log phase283-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$PHY" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase283-failure/source/ || true
  done
  cp scripts/283_apply_shared_engine_phy_trace.py phase283-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the hardware-tested Phase282 source chain first. Phase283 keeps
# the one-shot Golden FIFO probe and only adds read-only shared-path evidence.
bash scripts/282_ci_build.sh
test -s phase282-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$PHY" "$SMMU" "$REC" "$COMMON" "$PANEL"; do test -s "$f"; done

cp "$OUT/.config" /tmp/p283-base.config
cp "$DSI" /tmp/p283-dsi-before.c
cp "$PHY" /tmp/p283-phy-before.c
cp "$SMMU" /tmp/p283-smmu-before.c
cp "$REC" /tmp/p283-rec-before.c
cp "$COMMON" /tmp/p283-common-before.c
cp "$PANEL" /tmp/p283-panel-before.c

python3 -m py_compile scripts/283_apply_shared_engine_phy_trace.py
python3 scripts/283_apply_shared_engine_phy_trace.py --root "$ROOT"
python3 scripts/283_apply_shared_engine_phy_trace.py --root "$ROOT" --check-only

# No SMMU, recorder implementation, Samsung brightness mapping, panel source,
# config, DT or unrelated subsystem may change in this phase.
cmp -s /tmp/p283-smmu-before.c "$SMMU"
cmp -s /tmp/p283-rec-before.c "$REC"
cmp -s /tmp/p283-common-before.c "$COMMON"
cmp -s /tmp/p283-panel-before.c "$PANEL"
! cmp -s /tmp/p283-dsi-before.c "$DSI"
! cmp -s /tmp/p283-phy-before.c "$PHY"

grep -Fq 'A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1' "$DSI"
grep -Fq 'A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1' "$PHY"
grep -Fq 'P276 283R0 q=%u %x %x %x %x %x %x' "$DSI"
grep -Fq 'P276 283C0 q=%u e=%x' "$DSI"
grep -Fq 'P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x' "$PHY"
grep -Fq 'a52_p283_shared_snapshot(dsi_ctrl, 0);' "$DSI"
grep -Fq 'a52_p283_shared_snapshot(dsi_ctrl, 1);' "$DSI"
grep -Fq 'a52_p283_shared_snapshot(dsi_ctrl, 2);' "$DSI"
grep -Fq 'P276 282F a=0 t=1' "$DSI"
grep -Fq 'P276 280Z q=2' "$DSI"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p283-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase283-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase283-out
mkdir -p phase283-out/{compile,config,package,audit,source}
cp "$IMAGE" phase283-out/compile/Image
cp "$OUT/.config" phase283-out/config/final.config
cp /tmp/p283-base.config phase283-out/audit/phase282-final.config
cp /tmp/p283-dsi-before.c phase283-out/audit/dsi-ctrl-before.c
cp /tmp/p283-phy-before.c phase283-out/audit/dsi-phy-before.c
cp phase283-compile.log phase283-out/audit/
cp scripts/283_apply_shared_engine_phy_trace.py phase283-out/audit/
cp "$DSI" phase283-out/source/dsi_ctrl.c
cp "$PHY" phase283-out/source/dsi_phy.c

# Preserve the exact Phase282 boot container and replace only the kernel.
gzip -n -c "$IMAGE" > phase283-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase282-out/package/boot.img \
  --kernel phase283-out/package/Image.gz \
  --output phase283-out/package/boot.img \
  --report phase283-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase283-out')
idn={
 'phase':'283',
 'name':'DSI-SHARED-ENGINE-CLOCK-PHY-TRACE',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'hardware-tested Phase282 Golden FIFO-vs-memory A/B',
 'phase282_result':'FIFO/TPG command 0x29 payload f05a5a encoded 29030040f05a5aff timed out; external command-memory fetch is not the primary blocker',
 'behavior_change_from_phase282':False,
 'brightness_mapping_changed':False,
 'smmu_changed':False,
 'timeout_recovery_changed':False,
 'trace_points':'q0 immediately before Golden FIFO kickoff, q1 immediately after kickoff, q2 pristine timeout state before retention freeze',
 'records':{
   '283S':'controller software state/version/power/controller/cmd-engine/host-init/tpg',
   '283R0':'DSI_CTRL/TRIG_CTRL/TPG_CTRL/TPG_FIFO_STATUS/DMA_LENGTH/INT_CTRL',
   '283R1':'DSI_STATUS/FIFO_STATUS/LANE_STATUS/CLK_CTRL/CLK_STATUS/PHY_SW_RESET',
   '283C0':'actual clock handle enable-state ternary bitmap, 0=disabled 1=enabled 2=missing per two-bit slot',
   '283C1':'actual byte and pixel clock rates',
   '283C2':'actual byte-interface and escape clock rates',
   '283P0':'PHY v4.x version/software power+engine/PLL/PHY status/lane status',
   '283P1':'PHY CLK_CFG0/1/GLBL_CTRL/VREG_CTRL0/1/CTRL0',
   '283P2':'PHY CTRL1/2/3/4/LANE_CTRL0',
   '283P3':'PHY LANE_CTRL1/2/3/4'
 }
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=['compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json','audit/phase282-final.config','audit/dsi-ctrl-before.c','audit/dsi-phy-before.c','audit/phase283-compile.log','audit/283_apply_shared_engine_phy_trace.py','source/dsi_ctrl.c','source/dsi_phy.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase283-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase283-out')
d=(r/'source/dsi_ctrl.c').read_text(); p=(r/'source/dsi_phy.c').read_text(); img=(r/'compile/Image').read_bytes()
for m in ['A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1','a52_p283_shared_snapshot(dsi_ctrl, 0);','a52_p283_shared_snapshot(dsi_ctrl, 1);','a52_p283_shared_snapshot(dsi_ctrl, 2);','P276 282F a=0 t=1','P276 280Z q=2']:
 if m not in d: raise SystemExit('Phase283 DSI source marker missing: '+m)
for m in ['A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1','void a52_p283_phy_snapshot','P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x']:
 if m not in p: raise SystemExit('Phase283 PHY source marker missing: '+m)
for m in ['P276 283S q=%u v=%u p=%u c=%u m=%u h=%u t=%u','P276 283R0 q=%u %x %x %x %x %x %x','P276 283C0 q=%u e=%x','P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x','P276 283P3 q=%u %x %x %x %x','P276 282F a=0 t=1','P276 280Z q=2']:
 if m.encode() not in img: raise SystemExit('Phase283 runtime marker missing from Image: '+m)
print('Phase283 compiled shared-path marker audit: PASS')
PY

python3 scripts/283_apply_shared_engine_phy_trace.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase283 shared DSI engine/clock/PHY trace build/repack: PASS'
