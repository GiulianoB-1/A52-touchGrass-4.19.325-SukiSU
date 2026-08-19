#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
DISPLAY="$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMMON="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"

fail_report(){
  set +e
  rm -rf phase283b-failure
  mkdir -p phase283b-failure/{source,logs,audit}
  cp phase283b-compile.log phase283b-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$PHY" "$DISPLAY" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase283b-failure/source/ || true
  done
  cp scripts/283_apply_shared_engine_phy_trace.py phase283b-failure/audit/ 2>/dev/null || true
  cp scripts/283b_apply_golden_handoff_trace.py phase283b-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the hardware-tested Phase282 source chain, then install both
# read-only Phase283 layers: shared DSI/PHY state and Golden splash handoff state.
bash scripts/282_ci_build.sh
test -s phase282-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$PHY" "$DISPLAY" "$SMMU" "$REC" "$COMMON" "$PANEL"; do test -s "$f"; done

cp "$OUT/.config" /tmp/p283b-base.config
cp "$DSI" /tmp/p283b-dsi-before.c
cp "$PHY" /tmp/p283b-phy-before.c
cp "$DISPLAY" /tmp/p283b-display-before.c
cp "$SMMU" /tmp/p283b-smmu-before.c
cp "$REC" /tmp/p283b-rec-before.c
cp "$COMMON" /tmp/p283b-common-before.c
cp "$PANEL" /tmp/p283b-panel-before.c

python3 -m py_compile scripts/283_apply_shared_engine_phy_trace.py scripts/283b_apply_golden_handoff_trace.py
python3 scripts/283_apply_shared_engine_phy_trace.py --root "$ROOT"
python3 scripts/283b_apply_golden_handoff_trace.py --root "$ROOT"
python3 scripts/283_apply_shared_engine_phy_trace.py --root "$ROOT" --check-only
python3 scripts/283b_apply_golden_handoff_trace.py --root "$ROOT" --check-only

# Preserve all previously-cleared subsystems. Only DSI ctrl, DSI PHY and DSI
# display handoff source are diagnostic deltas over hardware-tested Phase282.
cmp -s /tmp/p283b-smmu-before.c "$SMMU"
cmp -s /tmp/p283b-rec-before.c "$REC"
cmp -s /tmp/p283b-common-before.c "$COMMON"
cmp -s /tmp/p283b-panel-before.c "$PANEL"
! cmp -s /tmp/p283b-dsi-before.c "$DSI"
! cmp -s /tmp/p283b-phy-before.c "$PHY"
! cmp -s /tmp/p283b-display-before.c "$DISPLAY"

grep -Fq 'A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1' "$DSI"
grep -Fq 'A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1' "$DSI"
grep -Fq 'A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1' "$DISPLAY"
grep -Fq 'P276 283D0 q=%u s=%u p=%u u=%u c=%u x=%u t=%u' "$DISPLAY"
grep -Fq 'P276 283D2 q=%u cg=%d/%u pg=%d/%u pm=%u' "$DISPLAY"
grep -Fq 'a52_p283_display_handoff_snapshot(dsi_ctrl, point);' "$DSI"
grep -Fq 'P276 282F a=0 t=1' "$DSI"
grep -Fq 'P276 280Z q=2' "$DSI"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p283b-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase283b-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase283b-out
mkdir -p phase283b-out/{compile,config,package,audit,source}
cp "$IMAGE" phase283b-out/compile/Image
cp "$OUT/.config" phase283b-out/config/final.config
cp /tmp/p283b-base.config phase283b-out/audit/phase282-final.config
cp /tmp/p283b-dsi-before.c phase283b-out/audit/dsi-ctrl-before.c
cp /tmp/p283b-phy-before.c phase283b-out/audit/dsi-phy-before.c
cp /tmp/p283b-display-before.c phase283b-out/audit/dsi-display-before.c
cp phase283b-compile.log phase283b-out/audit/
cp scripts/283_apply_shared_engine_phy_trace.py phase283b-out/audit/
cp scripts/283b_apply_golden_handoff_trace.py phase283b-out/audit/
cp "$DSI" phase283b-out/source/dsi_ctrl.c
cp "$PHY" phase283b-out/source/dsi_phy.c
cp "$DISPLAY" phase283b-out/source/dsi_display.c

gzip -n -c "$IMAGE" > phase283b-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase282-out/package/boot.img \
  --kernel phase283b-out/package/Image.gz \
  --output phase283b-out/package/boot.img \
  --report phase283b-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase283b-out')
idn={
 'phase':'283',
 'name':'DSI-SHARED-ENGINE-PHY-GOLDEN-HANDOFF-TRACE',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base':'hardware-tested Phase282 Golden FIFO-vs-memory A/B',
 'phase282_result':'FIFO and memory transports both timeout; shared DSI path is the frontier',
 'behavior_change_from_phase282':False,
 'brightness_mapping_changed':False,
 'smmu_changed':False,
 'timeout_recovery_changed':False,
 'golden_comparison':'working continuous splash enables DPHY, retains splash ownership, configures ISR, votes all DSI clocks/regulators, then completes initial panel read/unlock and ON-command traffic',
 'extra_handoff_records':{
   '283D0':'continuous-splash, per-display PHY-enabled, ULPS, clamp, PHY-idle-poweroff and display TPG flags',
   '283D1':'clock/cmd/video master indexes, ctrl count and command-engine refcount',
   '283D2':'controller and PHY digital/GDSC regulator enabled state + refcount and controller runtime-PM active state',
   '283D3':'Samsung splash ownership flag'
 }
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
files=['compile/Image','config/final.config','package/Image.gz','package/boot.img','package/repack-report.json','audit/phase282-final.config','audit/dsi-ctrl-before.c','audit/dsi-phy-before.c','audit/dsi-display-before.c','audit/phase283b-compile.log','audit/283_apply_shared_engine_phy_trace.py','audit/283b_apply_golden_handoff_trace.py','source/dsi_ctrl.c','source/dsi_phy.c','source/dsi_display.c','BUILD-IDENTITY.json']
with (r/'SHA256SUMS').open('w') as f:
 for n in files: f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase283b-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
r=Path('phase283b-out')
d=(r/'source/dsi_ctrl.c').read_text(); p=(r/'source/dsi_phy.c').read_text(); x=(r/'source/dsi_display.c').read_text(); img=(r/'compile/Image').read_bytes()
for m in ['A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1','A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1','a52_p283_display_handoff_snapshot(dsi_ctrl, point);','P276 282F a=0 t=1','P276 280Z q=2']:
 if m not in d: raise SystemExit('Phase283 DSI marker missing: '+m)
for m in ['A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1','P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x']:
 if m not in p: raise SystemExit('Phase283 PHY marker missing: '+m)
for m in ['A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1','P276 283D0 q=%u s=%u p=%u u=%u c=%u x=%u t=%u','P276 283D2 q=%u cg=%d/%u pg=%d/%u pm=%u','P276 283D3 q=%u ss=%u']:
 if m not in x: raise SystemExit('Phase283 display marker missing: '+m)
for m in ['P276 283S q=%u v=%u p=%u c=%u m=%u h=%u t=%u','P276 283P0 q=%u v=%u p=%u s=%u %x %x %x %x','P276 283D0 q=%u s=%u p=%u u=%u c=%u x=%u t=%u','P276 283D2 q=%u cg=%d/%u pg=%d/%u pm=%u','P276 282F a=0 t=1','P276 280Z q=2']:
 if m.encode() not in img: raise SystemExit('Phase283 runtime marker missing from Image: '+m)
print('Phase283 Golden handoff-complete marker audit: PASS')
PY

python3 scripts/283_apply_shared_engine_phy_trace.py --root "$ROOT" --check-only
python3 scripts/283b_apply_golden_handoff_trace.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase283 shared DSI/PHY + Golden handoff trace build/repack: PASS'
