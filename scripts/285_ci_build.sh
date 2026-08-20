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
  rm -rf phase285-failure
  mkdir -p phase285-failure/{source,logs,audit,config}
  cp phase285-compile.log phase285-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$PHY" "$DISPLAY" "$CLK" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase285-failure/source/ || true
  done
  cp scripts/284_apply_v3_phy_clock_trace.py phase285-failure/audit/ 2>/dev/null || true
  cp scripts/284b_apply_clock_causality_trace.py phase285-failure/audit/ 2>/dev/null || true
  cp scripts/285_apply_latched_clock_evidence.py phase285-failure/audit/ 2>/dev/null || true
  cp /tmp/p285-*.config /tmp/p285-*.diff phase285-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase283C lineage. Apply the unchanged
# Phase284 clock-chain/PHY probes, then layer only the Phase285 retention fix.
# We intentionally do not compile an intermediate Phase284 Image: Phase284 is
# observational, and its boot container is identical to Phase283's container.
bash scripts/283c_ci_build.sh
test -s phase283b-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$PHY" "$DISPLAY" "$CLK" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
  test -s "$f"
done

cp "$OUT/.config" /tmp/p285-base.config
cp "$SMMU" /tmp/p285-smmu-before.c
cp "$COMMON" /tmp/p285-common-before.c
cp "$PANEL" /tmp/p285-panel-before.c

python3 -m py_compile \
  scripts/284_apply_v3_phy_clock_trace.py \
  scripts/284b_apply_clock_causality_trace.py \
  scripts/285_apply_latched_clock_evidence.py
python3 scripts/284_apply_v3_phy_clock_trace.py --root "$ROOT"
python3 scripts/284b_apply_clock_causality_trace.py --root "$ROOT"
python3 scripts/284_apply_v3_phy_clock_trace.py --root "$ROOT" --check-only
python3 scripts/284b_apply_clock_causality_trace.py --root "$ROOT" --check-only

# Snapshot the exact Phase284 generated sources before Phase285. This proves
# the new phase only changes recorder retention, not the measured DSI chain.
cp "$DSI" /tmp/p285-dsi-phase284.c
cp "$PHY" /tmp/p285-phy-phase284.c
cp "$DISPLAY" /tmp/p285-display-phase284.c
cp "$CLK" /tmp/p285-clk-phase284.c
cp "$REC" /tmp/p285-rec-phase284.c

python3 scripts/285_apply_latched_clock_evidence.py --root "$ROOT"
python3 scripts/285_apply_latched_clock_evidence.py --root "$ROOT" --check-only

cmp -s /tmp/p285-dsi-phase284.c "$DSI"
cmp -s /tmp/p285-phy-phase284.c "$PHY"
cmp -s /tmp/p285-display-phase284.c "$DISPLAY"
cmp -s /tmp/p285-clk-phase284.c "$CLK"
! cmp -s /tmp/p285-rec-phase284.c "$REC"
cmp -s /tmp/p285-smmu-before.c "$SMMU"
cmp -s /tmp/p285-common-before.c "$COMMON"
cmp -s /tmp/p285-panel-before.c "$PANEL"

# Phase285 must retain every original Phase284 causal observation format.
for token in \
  'P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u' \
  'P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx' \
  'P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx' \
  'P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx' \
  'P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx' \
  'P276 284M3 cb=%lx bp=%lx tb=%lx cp=%lx pp=%lx tp=%lx' \
  'P276 284M4 rc=%d cb=%lx bp=%lx cp=%lx pp=%lx' \
  'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx' \
  'P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx' \
  'P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx' \
  'P276 284M8 c=%d req=%llx rc=%d a=%lx p=%lx'; do
  grep -Fq "$token" "$REC"
done
for token in \
  'P276 284C0 q=%u %x %x %x %x' \
  'P276 284C1 q=%u %x %x %x %x' \
  'P276 284C2 q=%u %x %x %x %x %x %x' \
  'P276 284C3 q=%u e=%x' \
  'P276 284P0 q=%u %u %x %x %x %x' \
  'P276 284P1 q=%u %x %x %x %x %x %x' \
  'P276 284P2 q=%u %x %x %x %x %x %x' \
  'P276 284P3 q=%u %x %x %x'; do
  grep -Fq "$token" "$REC"
done

grep -Fq 'A52_PHASE285_LATCHED_CLOCK_CHAIN_VALUES_V1' "$REC"
grep -Fq 'a52_p285_capture_fmt(fmt, args);' "$REC"
grep -Fq 'P285 %c%sa n=%u q=%llx 0=%llx 1=%llx' "$REC"
grep -Fq 'P285 %c%sb 2=%llx 3=%llx 4=%llx' "$REC"
grep -Fq 'P285 %c%sc 5=%llx 6=%llx 7=%llx' "$REC"
grep -Fq 'boot_s >= 180U && boot_s <= 300U' "$REC"
grep -Fq 'strncmp(message, "P285 ", 5)' "$REC"
grep -Fq 'strncmp(fmt, "P285", 4)' "$REC"

# All replay records must fit the 73-byte packed-message field (72 content
# bytes + NUL), even with maximum-width 64-bit hexadecimal values.
python3 - <<'PY'
examples = [
    'P285 SXXa n=8 q=' + 'f'*16 + ' 0=' + 'f'*16 + ' 1=' + 'f'*16,
    'P285 SXXb 2=' + 'f'*16 + ' 3=' + 'f'*16 + ' 4=' + 'f'*16,
    'P285 SXXc 5=' + 'f'*16 + ' 6=' + 'f'*16 + ' 7=' + 'f'*16,
]
for line in examples:
    if len(line) > 72:
        raise SystemExit(f'Phase285 replay format can truncate: {len(line)} bytes: {line}')
print('Phase285 replay packed-width audit:', max(map(len, examples)), '<= 72')
PY

# Strict observational-config invariant, both before and after olddefconfig.
cp "$OUT/.config" /tmp/p285-pre-olddefconfig.config
if ! cmp -s /tmp/p285-base.config /tmp/p285-pre-olddefconfig.config; then
  diff -u /tmp/p285-base.config /tmp/p285-pre-olddefconfig.config > /tmp/p285-pre-olddefconfig.diff || true
  echo '::error::Phase285 tracing changed .config before olddefconfig'
  cat /tmp/p285-pre-olddefconfig.diff
  exit 1
fi
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cp "$OUT/.config" /tmp/p285-post-olddefconfig.config
if ! cmp -s /tmp/p285-base.config /tmp/p285-post-olddefconfig.config; then
  diff -u /tmp/p285-base.config /tmp/p285-post-olddefconfig.config > /tmp/p285-config.diff || true
  echo '::error::Phase285 .config changed after olddefconfig'
  cat /tmp/p285-config.diff
  exit 1
fi
trusted_line="$(grep -E '^CONFIG_A52_VENDOR_BLOB_TRUSTED_SOURCE=' "$OUT/.config" || true)"
if [ -n "$trusted_line" ] && [ "$trusted_line" != 'CONFIG_A52_VENDOR_BLOB_TRUSTED_SOURCE=n' ]; then
  echo '::error::CONFIG_A52_VENDOR_BLOB_TRUSTED_SOURCE must remain disabled'
  printf '%s\n' "$trusted_line"
  exit 1
fi

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase285-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase285-out
mkdir -p phase285-out/{compile,config,package,audit,source}
cp "$IMAGE" phase285-out/compile/Image
cp "$OUT/.config" phase285-out/config/final.config
cp /tmp/p285-base.config phase285-out/audit/phase283-final.config
cp /tmp/p285-dsi-phase284.c phase285-out/audit/dsi-ctrl-phase284.c
cp /tmp/p285-phy-phase284.c phase285-out/audit/dsi-phy-phase284.c
cp /tmp/p285-display-phase284.c phase285-out/audit/dsi-display-phase284.c
cp /tmp/p285-clk-phase284.c phase285-out/audit/dsi-clk-manager-phase284.c
cp /tmp/p285-rec-phase284.c phase285-out/audit/recorder-phase284.c
cp phase285-compile.log phase285-out/audit/
cp scripts/284_apply_v3_phy_clock_trace.py phase285-out/audit/
cp scripts/284b_apply_clock_causality_trace.py phase285-out/audit/
cp scripts/285_apply_latched_clock_evidence.py phase285-out/audit/
cp "$DSI" phase285-out/source/dsi_ctrl.c
cp "$PHY" phase285-out/source/dsi_phy.c
cp "$DISPLAY" phase285-out/source/dsi_display.c
cp "$CLK" phase285-out/source/dsi_clk_manager.c
cp "$REC" phase285-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase285-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase283b-out/package/boot.img \
  --kernel phase285-out/package/Image.gz \
  --output phase285-out/package/boot.img \
  --report phase285-out/package/repack-report.json

cat > phase285-out/PHASE285-REPLAY-SCHEMA.txt <<'EOF'
Phase285 P285 exact-value replay schema
=======================================
S = stage observed once; F/L = first/last observation. q is the causal sequence.
All positional values are raw hexadecimal. Signed int values (rc, signed indices)
are sign-extended to u64, so negative values appear in two's-complement form.
Sort samples by q to recover the original Phase284 causal ordering.

O0 n=6: [ctrl, phy_type, input_bit_clk, lanes, bpp, byte_intf_div]
O1 n=5: [bit_rate, bit_rate_per_lane, byte_rate, byte_intf_rate, pixel_rate]
M0 n=6: [ctrl_index, manager_index, cached_byte, cached_pixel, cached_intf, cached_esc]
M1 n=5: [ctrl_index, pixel_req, rc, pixel_actual, pixel_parent]
M2 n=7: [ctrl_index, byte_req, intf_req, rc, byte_actual, byte_parent, intf_actual]
M3 n=6: [child_byte, child_byte_parent, target_byte_parent, child_pixel, child_pixel_parent, target_pixel_parent]
M4 n=5: [rc, child_byte, byte_parent, child_pixel, pixel_parent]
M5 n=4: [ctrl_index, cached_byte, cached_pixel, cached_intf]  (continuous-splash skip)
M6 n=5: [ctrl_index, byte_req, rc, byte_actual, byte_parent]
M7 n=5: [ctrl_index, pixel_req, rc, pixel_actual, pixel_parent]
M8 n=5: [ctrl_index, intf_req, rc, intf_actual, intf_parent]
C0 n=5: [point, configured_byte, configured_pixel, configured_intf, configured_esc]
C1 n=5: [point, rcg_byte, rcg_pixel, pll_source_byte, pll_source_pixel]
C2 n=7: [point, byte_leaf, byte_parent, byte_grandparent, pixel_leaf, pixel_parent, pixel_grandparent]
C3 n=2: [point, packed_enable_state]
P0 n=6: [point, phy_version, pll_ctrl, phy_status, lane_status0, lane_status1]
P1 n=7: [point, clk_cfg0, clk_cfg1, global_ctrl, rbuf_ctrl, vreg_ctrl, ctrl0]
P2 n=7: [point, ctrl1, ctrl2, lane_cfg0, lane_cfg1, lane_ctrl0, lane_ctrl1]
P3 n=4: [point, lane_ctrl2, lane_ctrl3, lane_ctrl4]
EOF

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path

r = Path('phase285-out')
idn = {
    'phase': '285',
    'name': 'LATCHED-EXACT-CLOCK-CHAIN-VALUES',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'source_lineage': 'Phase283C + unchanged Phase284 O/M/C/P probes + Phase285 recorder retention',
    'behavior_change_from_phase284': False,
    'clock_programming_changed': False,
    'clock_parenting_changed': False,
    'clock_readback_changed': False,
    'phy_access_changed': False,
    'brightness_mapping_changed': False,
    'splash_handoff_changed': False,
    'config_changed': False,
    'retention_change': (
        'capture exact Phase284 O/M/C/P varargs before text truncation; retain first/last '
        'per stage with causal q; replay compact P285 records every frontier summary '
        'from 180 through 300 seconds'
    ),
    'causal_question': (
        'Where does the non-zero upstream display rate become zero: derivation, cache, '
        'splash skip, set-rate request, provider result/readback, parent chain, or PHY state?'
    ),
    'phase284_hardware_fact': 'DRM mode records carried clk=184672 kHz; Phase284 P276 clock-chain records were overwritten',
}
(r / 'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase283-final.config',
    'audit/dsi-ctrl-phase284.c', 'audit/dsi-phy-phase284.c',
    'audit/dsi-display-phase284.c', 'audit/dsi-clk-manager-phase284.c',
    'audit/recorder-phase284.c', 'audit/phase285-compile.log',
    'audit/284_apply_v3_phy_clock_trace.py', 'audit/284b_apply_clock_causality_trace.py',
    'audit/285_apply_latched_clock_evidence.py', 'source/dsi_ctrl.c', 'source/dsi_phy.c',
    'source/dsi_display.c', 'source/dsi_clk_manager.c',
    'source/a52_ack_secure_flight_recorder.c', 'PHASE285-REPLAY-SCHEMA.txt',
    'BUILD-IDENTITY.json',
]
with (r / 'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r / n).read_bytes()).hexdigest() + '  ./' + n + '\n')
PY
(cd phase285-out && sha256sum -c SHA256SUMS)

# Compiled-image audit: both the original Phase284 evidence and Phase285
# exact-value replay must be present in the final kernel.
python3 - <<'PY'
from pathlib import Path
img = Path('phase285-out/compile/Image').read_bytes()
for token in [
    'P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u',
    'P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx',
    'P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx',
    'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx',
    'P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 284M8 c=%d req=%llx rc=%d a=%lx p=%lx',
    'P276 284C0 q=%u %x %x %x %x',
    'P276 284C3 q=%u e=%x',
    'P276 284P0 q=%u %u %x %x %x %x',
    'P276 284P3 q=%u %x %x %x',
    'P285 %c%sa n=%u q=%llx 0=%llx 1=%llx',
    'P285 %c%sb 2=%llx 3=%llx 4=%llx',
    'P285 %c%sc 5=%llx 6=%llx 7=%llx',
    'P285 H t=%lu n=%llu',
]:
    if token.encode() not in img:
        raise SystemExit('Phase285 runtime marker missing from Image: ' + token)
print('Phase285 compiled marker audit passed')
PY
