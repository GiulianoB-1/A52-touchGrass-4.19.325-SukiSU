#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
DISPLAY="$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
CLK="$ROOT/drivers/a52_display/msm/dsi/dsi_clk_manager.c"
SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMMON="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c"

fail_report(){
  set +e
  rm -rf phase284-failure
  mkdir -p phase284-failure/{source,logs,audit}
  cp phase284-compile.log phase284-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$PHY" "$DISPLAY" "$CLK" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase284-failure/source/ || true
  done
  cp scripts/284_apply_v3_phy_clock_trace.py phase284-failure/audit/ 2>/dev/null || true
  cp scripts/284b_apply_clock_causality_trace.py phase284-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact successful Phase283C lineage. Phase284 then layers two
# strictly read-only probes: the v3 PHY/result snapshot and the upstream rate
# producer/cache/parent/set-rate causality chain.
bash scripts/283c_ci_build.sh
test -s phase283b-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$PHY" "$DISPLAY" "$CLK" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
  test -s "$f"
done

cp "$OUT/.config" /tmp/p284-base.config
cp "$DSI" /tmp/p284-dsi-before.c
cp "$PHY" /tmp/p284-phy-before.c
cp "$DISPLAY" /tmp/p284-display-before.c
cp "$CLK" /tmp/p284-clk-before.c
cp "$SMMU" /tmp/p284-smmu-before.c
cp "$REC" /tmp/p284-rec-before.c
cp "$COMMON" /tmp/p284-common-before.c
cp "$PANEL" /tmp/p284-panel-before.c

python3 -m py_compile \
  scripts/284_apply_v3_phy_clock_trace.py \
  scripts/284b_apply_clock_causality_trace.py
python3 scripts/284_apply_v3_phy_clock_trace.py --root "$ROOT"
python3 scripts/284b_apply_clock_causality_trace.py --root "$ROOT"
python3 scripts/284_apply_v3_phy_clock_trace.py --root "$ROOT" --check-only
python3 scripts/284b_apply_clock_causality_trace.py --root "$ROOT" --check-only

# Phase284 remains observational. Only the four DSI diagnostic sources may
# differ from the reconstructed Phase283C tree.
cmp -s /tmp/p284-smmu-before.c "$SMMU"
cmp -s /tmp/p284-rec-before.c "$REC"
cmp -s /tmp/p284-common-before.c "$COMMON"
cmp -s /tmp/p284-panel-before.c "$PANEL"
! cmp -s /tmp/p284-dsi-before.c "$DSI"
! cmp -s /tmp/p284-phy-before.c "$PHY"
! cmp -s /tmp/p284-display-before.c "$DISPLAY"
! cmp -s /tmp/p284-clk-before.c "$CLK"

grep -Fq 'A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1' "$DSI"
grep -Fq 'P276 284C0 q=%u %x %x %x %x' "$DSI"
grep -Fq 'P276 284C3 q=%u e=%x' "$DSI"
grep -Fq 'A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1' "$PHY"
grep -Fq 'A52_P284_V3_PLL_CTRL        0x038' "$PHY"
grep -Fq 'A52_P284_V3_STATUS          0x0ec' "$PHY"
grep -Fq 'P276 284P0 q=%u %u %x %x %x %x' "$PHY"
grep -Fq 'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1' "$DISPLAY"
grep -Fq 'P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u' "$DISPLAY"
grep -Fq 'P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx' "$DISPLAY"
grep -Fq 'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1' "$CLK"
for m in 284M0 284M1 284M2 284M3 284M4 284M5 284M6 284M7 284M8; do
  grep -Fq "P276 $m" "$CLK"
done
grep -Fq 'P276 282F a=0 t=1' "$DSI"
grep -Fq 'P276 280Z q=2' "$DSI"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p284-base.config "$OUT/.config"
grep -Fxq 'CONFIG_A52_VENDOR_BLOB_TRUSTED_SOURCE=n' "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase284-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase284-out
mkdir -p phase284-out/{compile,config,package,audit,source}
cp "$IMAGE" phase284-out/compile/Image
cp "$OUT/.config" phase284-out/config/final.config
cp /tmp/p284-base.config phase284-out/audit/phase283-final.config
cp /tmp/p284-dsi-before.c phase284-out/audit/dsi-ctrl-before.c
cp /tmp/p284-phy-before.c phase284-out/audit/dsi-phy-before.c
cp /tmp/p284-display-before.c phase284-out/audit/dsi-display-before.c
cp /tmp/p284-clk-before.c phase284-out/audit/dsi-clk-manager-before.c
cp phase284-compile.log phase284-out/audit/
cp scripts/284_apply_v3_phy_clock_trace.py phase284-out/audit/
cp scripts/284b_apply_clock_causality_trace.py phase284-out/audit/
cp "$DSI" phase284-out/source/dsi_ctrl.c
cp "$PHY" phase284-out/source/dsi_phy.c
cp "$DISPLAY" phase284-out/source/dsi_display.c
cp "$CLK" phase284-out/source/dsi_clk_manager.c

gzip -n -c "$IMAGE" > phase284-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase283b-out/package/boot.img \
  --kernel phase284-out/package/Image.gz \
  --output phase284-out/package/boot.img \
  --report phase284-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path

r = Path('phase284-out')
idn = {
    'phase': '284',
    'name': 'V3-PHY-LINK-CLOCK-CAUSALITY-TRACE',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'hardware-tested Phase283 shared DSI/PHY + Golden handoff trace',
    'phase283_result': (
        'FIFO still times out while controller/runtime-PM and controller+PHY '
        'regulator votes remain asserted; PHY reports v3.0'
    ),
    'behavior_change_from_phase283': False,
    'brightness_mapping_changed': False,
    'smmu_changed': False,
    'timeout_recovery_changed': False,
    'splash_handoff_changed': False,
    'causal_question': (
        'Does the zero-Hz result originate in rate derivation/cache, in '
        'continuous-splash rate skipping, or during parent/set-rate propagation?'
    ),
    'records': {
        '284O0': 'rate-producer inputs: controller, PHY type, input bit clock, lanes, bpp, byte-interface divisor',
        '284O1': 'rate-producer outputs: aggregate/lane bit rate and byte/interface/pixel rates',
        '284M0': 'clock-manager cached link frequencies',
        '284M1': 'explicit pixel-rate setter request/result/actual/parent',
        '284M2': 'explicit byte/interface setter request/result/actual/parent',
        '284M3': 'clock-parent state immediately before parent switching',
        '284M4': 'clock-parent state immediately after parent switching',
        '284M5': 'continuous-splash rate-application skip and cached rates',
        '284M6': 'byte clock set-rate request/result/actual/parent',
        '284M7': 'pixel clock set-rate request/result/actual/parent',
        '284M8': 'byte-interface clock set-rate request/result/actual/parent',
        '284C0': 'configured target byte/pixel/byte-interface/escape rates at command snapshot',
        '284C1': 'controller RCG byte/pixel + selected PLL-source byte/pixel rates',
        '284C2': 'byte leaf/parent/grandparent + pixel leaf/parent/grandparent rates',
        '284C3': 'packed enabled states for RCG, selected PLL source, and parent chains',
        '284P0': 'v3 PHY version, PLL control, PHY status, lane status 0/1',
        '284P1': 'v3 common clock/global/rbuf/vreg/control0 registers',
        '284P2': 'v3 control1/2, lane config0/1, lane control0/1',
        '284P3': 'v3 lane control2/3/4',
    },
}
(r / 'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image',
    'config/final.config',
    'package/Image.gz',
    'package/boot.img',
    'package/repack-report.json',
    'audit/phase283-final.config',
    'audit/dsi-ctrl-before.c',
    'audit/dsi-phy-before.c',
    'audit/dsi-display-before.c',
    'audit/dsi-clk-manager-before.c',
    'audit/phase284-compile.log',
    'audit/284_apply_v3_phy_clock_trace.py',
    'audit/284b_apply_clock_causality_trace.py',
    'source/dsi_ctrl.c',
    'source/dsi_phy.c',
    'source/dsi_display.c',
    'source/dsi_clk_manager.c',
    'BUILD-IDENTITY.json',
]
with (r / 'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r / n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY
(cd phase284-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path

r = Path('phase284-out')
d = (r / 'source/dsi_ctrl.c').read_text()
p = (r / 'source/dsi_phy.c').read_text()
o = (r / 'source/dsi_display.c').read_text()
m = (r / 'source/dsi_clk_manager.c').read_text()
img = (r / 'compile/Image').read_bytes()

for token in [
    'A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1',
    'P276 284C0 q=%u %x %x %x %x',
    'P276 284C1 q=%u %x %x %x %x',
    'P276 284C2 q=%u %x %x %x %x %x %x',
    'P276 284C3 q=%u e=%x',
    'P276 282F a=0 t=1',
    'P276 280Z q=2',
]:
    if token not in d:
        raise SystemExit('Phase284 DSI source marker missing: ' + token)

for token in [
    'A52_PHASE284_V3_PHY_CLOCK_CHAIN_TRACE_V1',
    'P276 284P0 q=%u %u %x %x %x %x',
    'P276 284P1 q=%u %x %x %x %x %x %x',
    'P276 284P2 q=%u %x %x %x %x %x %x',
    'P276 284P3 q=%u %x %x %x',
]:
    if token not in p:
        raise SystemExit('Phase284 PHY source marker missing: ' + token)

for token in [
    'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1',
    'P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u',
    'P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx',
]:
    if token not in o:
        raise SystemExit('Phase284 origin source marker missing: ' + token)

for token in [
    'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1',
    'P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx',
    'P276 284M3 cb=%lx cp=%lx tb=%lx pp=%lx tp=%lx',
    'P276 284M4 rc=%d cb=%lx bp=%lx cp=%lx pp=%lx',
    'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx',
    'P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 284M8 c=%d req=%llx rc=%d a=%lx p=%lx',
]:
    if token not in m:
        raise SystemExit('Phase284 manager source marker missing: ' + token)

for token in [
    'P276 284C0 q=%u %x %x %x %x',
    'P276 284C3 q=%u e=%x',
    'P276 284P0 q=%u %u %x %x %x %x',
    'P276 284P3 q=%u %x %x %x',
    'P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u',
    'P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx',
    'P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx',
    'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx',
    'P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 282F a=0 t=1',
    'P276 280Z q=2',
]:
    if token.encode() not in img:
        raise SystemExit('Phase284 runtime marker missing from Image: ' + token)

print('Phase284 v3 PHY + full clock-causality marker audit: PASS')
PY

python3 scripts/284_apply_v3_phy_clock_trace.py --root "$ROOT" --check-only
python3 scripts/284b_apply_clock_causality_trace.py --root "$ROOT" --check-only

trap - EXIT
echo 'Phase284 v3 PHY + full clock-causality build/repack: PASS'
