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
  rm -rf phase286-failure
  mkdir -p phase286-failure/{source,logs,audit,config}
  cp phase286-compile.log phase286-failure/logs/ 2>/dev/null || true
  for f in "$DSI" "$PHY" "$DISPLAY" "$CLK" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
    [ -f "$f" ] && cp "$f" phase286-failure/source/ || true
  done
  cp scripts/286_apply_hs_clock_handoff_fix.py phase286-failure/audit/ 2>/dev/null || true
  cp /tmp/p286-*.config /tmp/p286-*.diff phase286-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase285 hardware-tested diagnostic lineage first.
# Phase286 then changes only dsi_display.c at the proven zero-Hz handoff point.
bash scripts/285_ci_build.sh
test -s phase285-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DSI" "$PHY" "$DISPLAY" "$CLK" "$SMMU" "$REC" "$COMMON" "$PANEL"; do
  test -s "$f"
done

cp "$OUT/.config" /tmp/p286-base.config
cp "$DSI" /tmp/p286-dsi-before.c
cp "$PHY" /tmp/p286-phy-before.c
cp "$DISPLAY" /tmp/p286-display-before.c
cp "$CLK" /tmp/p286-clk-before.c
cp "$SMMU" /tmp/p286-smmu-before.c
cp "$REC" /tmp/p286-rec-before.c
cp "$COMMON" /tmp/p286-common-before.c
cp "$PANEL" /tmp/p286-panel-before.c

python3 -m py_compile scripts/286_apply_hs_clock_handoff_fix.py
python3 scripts/286_apply_hs_clock_handoff_fix.py --root "$ROOT"
python3 scripts/286_apply_hs_clock_handoff_fix.py --root "$ROOT" --check-only

# This is the first behavioral clock-handoff fix after Phase285. Only the
# display orchestration source may change; provider/manager/PHY/recorder stay
# byte-identical so the validation retains the exact Phase284/285 evidence.
cmp -s /tmp/p286-dsi-before.c "$DSI"
cmp -s /tmp/p286-phy-before.c "$PHY"
! cmp -s /tmp/p286-display-before.c "$DISPLAY"
cmp -s /tmp/p286-clk-before.c "$CLK"
cmp -s /tmp/p286-smmu-before.c "$SMMU"
cmp -s /tmp/p286-rec-before.c "$REC"
cmp -s /tmp/p286-common-before.c "$COMMON"
cmp -s /tmp/p286-panel-before.c "$PANEL"

grep -Fq 'A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1' "$DISPLAY"
grep -Fq 'P276 286F c=%u z=%x' "$DISPLAY"
grep -Fq 'P276 286B c=%u rc=%d' "$DISPLAY"
grep -Fq 'P276 286P c=%u rc=%d' "$DISPLAY"
grep -Fq 'P276 286A c=%u b=%lx p=%lx i=%lx' "$DISPLAY"
grep -Fq 'dsi_clk_set_byte_clk_rate(display->dsi_clk_handle,' "$DISPLAY"
grep -Fq 'dsi_clk_set_pixel_clk_rate(display->dsi_clk_handle,' "$DISPLAY"
grep -Fq 'ctrl->ctrl->clk_freq.byte_clk_rate' "$DISPLAY"
grep -Fq 'ctrl->ctrl->clk_freq.pix_clk_rate' "$DISPLAY"
grep -Fq 'zero_mask |= BIT(0);' "$DISPLAY"
grep -Fq 'zero_mask |= BIT(1);' "$DISPLAY"
grep -Fq 'zero_mask |= BIT(2);' "$DISPLAY"

# Preserve the decisive Phase284 + Phase285 instrumentation. M1/M2 are the
# exact setters Phase286 deliberately reuses and must remain instrumented.
for token in \
  'P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx' \
  'P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx' \
  'P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx' \
  'P276 284M3 cb=%lx bp=%lx tb=%lx cp=%lx pp=%lx tp=%lx' \
  'P276 284M4 rc=%d cb=%lx bp=%lx cp=%lx pp=%lx' \
  'P276 284C1 q=%u %x %x %x %x' \
  'P276 284C2 q=%u %x %x %x %x %x %x'; do
  grep -Fq "$token" "$DISPLAY" "$CLK" "$DSI" || {
    echo "::error::missing Phase284 validation marker: $token"; exit 1;
  }
done
grep -Fq 'A52_PHASE285_LATCHED_CLOCK_CHAIN_VALUES_V1' "$REC"
grep -Fq 'a52_p285_capture_fmt(fmt, args);' "$REC"
grep -Fq 'strncmp(message, "P285 ", 5)' "$REC"

# Strict config equality: Phase286 is source-only and must not change Kconfig.
cp "$OUT/.config" /tmp/p286-pre-olddefconfig.config
if ! cmp -s /tmp/p286-base.config /tmp/p286-pre-olddefconfig.config; then
  diff -u /tmp/p286-base.config /tmp/p286-pre-olddefconfig.config > /tmp/p286-pre-olddefconfig.diff || true
  echo '::error::Phase286 changed .config before olddefconfig'
  cat /tmp/p286-pre-olddefconfig.diff
  exit 1
fi
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cp "$OUT/.config" /tmp/p286-post-olddefconfig.config
if ! cmp -s /tmp/p286-base.config /tmp/p286-post-olddefconfig.config; then
  diff -u /tmp/p286-base.config /tmp/p286-post-olddefconfig.config > /tmp/p286-config.diff || true
  echo '::error::Phase286 .config changed after olddefconfig'
  cat /tmp/p286-config.diff
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
  2>&1 | tee phase286-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

python3 - <<'PY'
from pathlib import Path
img = Path('workspace/gki-phase199-out/arch/arm64/boot/Image').read_bytes()
for token in [
    'A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1',
    'P276 286F c=%u z=%x',
    'P276 286B c=%u rc=%d',
    'P276 286P c=%u rc=%d',
    'P276 286A c=%u b=%lx p=%lx i=%lx',
    'P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx',
    'P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx',
    'P276 284C1 q=%u %x %x %x %x',
    'P276 284C2 q=%u %x %x %x %x %x %x',
    'P285 %c%sa n=%u q=%llx 0=%llx 1=%llx',
]:
    if token.encode() not in img:
        raise SystemExit('Phase286 compiled marker missing: ' + token)
print('Phase286 compiled fix + validation marker audit: PASS')
PY

rm -rf phase286-out
mkdir -p phase286-out/{compile,config,package,audit,source}
cp "$IMAGE" phase286-out/compile/Image
cp "$OUT/.config" phase286-out/config/final.config
cp /tmp/p286-base.config phase286-out/audit/phase285-final.config
cp /tmp/p286-display-before.c phase286-out/audit/dsi-display-phase285.c
cp phase286-compile.log phase286-out/audit/
cp scripts/286_apply_hs_clock_handoff_fix.py phase286-out/audit/
cp "$DSI" phase286-out/source/dsi_ctrl.c
cp "$PHY" phase286-out/source/dsi_phy.c
cp "$DISPLAY" phase286-out/source/dsi_display.c
cp "$CLK" phase286-out/source/dsi_clk_manager.c
cp "$REC" phase286-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase286-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase285-out/package/boot.img \
  --kernel phase286-out/package/Image.gz \
  --output phase286-out/package/boot.img \
  --report phase286-out/package/repack-report.json

cat > phase286-out/PHASE286-VALIDATION.txt <<'EOF'
Phase286 HS clock handoff repair validation
===========================================
Hardware fact from Phase285:
  target byte      = 138500000 Hz
  target pixel     = 184666666 Hz
  target byte_intf =  69250000 Hz
  target escape    =  19200000 Hz (already healthy)
  byte/pixel/intf live HS chain = 0 Hz after successful parent switch
  earlier continuous-splash HS rate application was skipped while targets=0
  no subsequent M6/M7/M8 or M1/M2 rate programming occurred before command timeout

Phase286 repair:
  After dsi_display_set_clk_src() selects parents, only when a configured HS
  target is non-zero and a corresponding live HS leaf remains 0, invoke the
  existing dsi_clk_set_byte_clk_rate()/dsi_clk_set_pixel_clk_rate() APIs once.

Expected hardware proof:
  286F: zero-rate repair condition fired
  284M2: byte/intf non-zero requests, rc=0, actual non-zero
  286B: byte/intf setter returned 0
  284M1: pixel non-zero request, rc=0, actual non-zero
  286P: pixel setter returned 0
  286A: final byte/pixel/intf readbacks non-zero
  284C1/C2 at command time: RCG/source/parent/leaf values non-zero
  DSI command completes instead of the existing FIFO/DMA timeout
EOF

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase286-out')
idn = {
    'phase': '286',
    'name': 'HS-CLOCK-HANDOFF-ZERO-RATE-REPAIR',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'hardware-tested Phase285 exact clock-chain retention',
    'phase285_root_cause': (
        'valid non-zero HS targets are cached, parent switch returns success, but live '
        'byte/pixel/intf clock hierarchy stays at 0 Hz because the only earlier HS '
        'rate-application call was skipped during continuous splash before targets existed'
    ),
    'behavior_change_from_phase285': True,
    'clock_programming_changed': True,
    'clock_provider_changed': False,
    'clock_parenting_algorithm_changed': False,
    'phy_changed': False,
    'recorder_changed': False,
    'brightness_mapping_changed': False,
    'config_changed': False,
    'repair_condition': 'configured HS target non-zero AND live HS leaf rate zero after source-parent selection',
    'repair_action': 'reuse existing Qualcomm byte/intf and pixel rate setters exactly once for the proven zero-rate state',
    'validation': 'Phase284 M1/M2 + C1/C2 and Phase285 latch retained; Phase286 adds 286F/B/P/A markers',
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True)+'\n')
files = [
    'compile/Image','config/final.config','package/Image.gz','package/boot.img',
    'package/repack-report.json','audit/phase285-final.config',
    'audit/dsi-display-phase285.c','audit/phase286-compile.log',
    'audit/286_apply_hs_clock_handoff_fix.py','source/dsi_ctrl.c','source/dsi_phy.c',
    'source/dsi_display.c','source/dsi_clk_manager.c',
    'source/a52_ack_secure_flight_recorder.c','PHASE286-VALIDATION.txt','BUILD-IDENTITY.json'
]
with (r/'SHA256SUMS').open('w') as f:
    for n in files:
        f.write(hashlib.sha256((r/n).read_bytes()).hexdigest()+'  ./'+n+'\n')
PY
(cd phase286-out && sha256sum -c SHA256SUMS)

python3 scripts/286_apply_hs_clock_handoff_fix.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase286 HS clock handoff zero-rate repair build/repack: PASS'
