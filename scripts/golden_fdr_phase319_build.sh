#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
BASE_OUT="$ROOT/golden-dma-done-ref-out"
OUT="$ROOT/golden-fdr-phase319-out"
FAIL="$ROOT/golden-fdr-phase319-failure"
CTRL="$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"
HWC="$KERNEL/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c"
LABEL="touchgrass-4.19.200-golden-fdr-phase319-v1"

KNOWN_GOLDEN_CONFIG_SHA256="d2c21f394ec477a975ce96f59959fa265acde60a4a28ef4d200c9912dfb624d1"
KNOWN_GOLDEN_BOOT_SHA256="7c61a4fa58679c955391eedad666b3cb38e0eb5e49bc69ac87f1edd862a79f12"
KNOWN_GOLDEN_CONTAINER_SHA256="50683df47b3f74fd3dd4ca7ff96bc114a6b7339955670b4c555fecaeda07e8c0"
TOUCHGRASS_BASE="6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"

rm -rf "$OUT" "$FAIL"
mkdir -p "$OUT"/{audit,compile,config,package,source} "$FAIL"

on_err() {
  rc=$?
  set +e
  {
    echo "rc=$rc"
    date -u
    echo 'Phase319 Golden FDR build failed'
    [ -d "$KERNEL/.git" ] && git -C "$KERNEL" status --short || true
    [ -d "$KERNEL/.git" ] && git -C "$KERNEL" diff --stat || true
    [ -d "$KERNEL/.git" ] && git -C "$KERNEL" diff --check || true
  } > "$FAIL/diagnostics.txt" 2>&1
  [ -f "$CTRL" ] && cp "$CTRL" "$FAIL/dsi_ctrl.c" || true
  [ -f "$HWC" ] && cp "$HWC" "$FAIL/dsi_ctrl_hw_cmn.c" || true
  cp scripts/golden_fdr_phase319_observer.py "$FAIL/" 2>/dev/null || true
  exit "$rc"
}
trap on_err ERR

chmod +x scripts/*.sh scripts/*.py

# Reconstruct and audit the exact hardware-proven Golden FDR DMA_DONE lineage.
bash scripts/golden_touchgrass_dma_done_reference_build.sh

test -s "$BASE_OUT/compile/Image"
test -s "$BASE_OUT/config/final.config"
test -s "$CTRL" -a -s "$HWC"
test "$(git -C "$KERNEL" rev-parse HEAD)" = "$TOUCHGRASS_BASE"
printf '%s  %s\n' "$KNOWN_GOLDEN_CONFIG_SHA256" "$BASE_OUT/config/final.config" | sha256sum -c -
grep -Fq 'A52_GOLDEN_DMA_DONE_REFERENCE_V1' "$CTRL"
grep -Fq 'GDM DONE' "$BASE_OUT/audit/Image.strings.txt"

cp "$CTRL" "$OUT/audit/dsi_ctrl-before-phase319.c"
cp "$HWC" "$OUT/audit/dsi_ctrl_hw_cmn-before-phase319.c"
cp "$BASE_OUT/config/final.config" "$OUT/audit/hardware-proven-golden-reference.config"
cp "$BASE_OUT/audit/hardware-proven-golden-boot.img.sha256" "$OUT/audit/" 2>/dev/null || true
cp "$BASE_OUT/audit/hardware-proven-golden-container.sha256" "$OUT/audit/" 2>/dev/null || true

python3 -m py_compile scripts/golden_fdr_phase319_observer.py
python3 scripts/golden_fdr_phase319_observer.py --root "$KERNEL"
python3 scripts/golden_fdr_phase319_observer.py --root "$KERNEL" --check-only
git -C "$KERNEL" diff --check -- techpack/display/msm/dsi/dsi_ctrl.c techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c

cp "$CTRL" "$OUT/source/dsi_ctrl.c"
cp "$HWC" "$OUT/source/dsi_ctrl_hw_cmn.c"
diff -u "$OUT/audit/dsi_ctrl-before-phase319.c" "$CTRL" > "$OUT/audit/dsi_ctrl-phase319.diff" || true
diff -u "$OUT/audit/dsi_ctrl_hw_cmn-before-phase319.c" "$HWC" > "$OUT/audit/dsi_ctrl_hw_cmn-phase319.diff" || true

python3 - "$OUT/audit/dsi_ctrl-before-phase319.c" "$CTRL" \
           "$OUT/audit/dsi_ctrl_hw_cmn-before-phase319.c" "$HWC" \
           > "$OUT/audit/phase319-scope-audit.txt" <<'PY'
from pathlib import Path
import sys
bctrl=Path(sys.argv[1]).read_text(); actrl=Path(sys.argv[2]).read_text()
bhw=Path(sys.argv[3]).read_text(); ahw=Path(sys.argv[4]).read_text()
for token in ('DSI_W32(', 'writel(', 'writel_relaxed(', 'regmap_write(',
              'regmap_update_bits(', 'wait_for_completion_timeout(',
              'clk_set_rate(', 'clk_set_parent(', 'regulator_enable(',
              'regulator_disable(', 'reset_control_assert(', 'reset_control_deassert(',
              'msleep(', 'usleep_range(', 'udelay('):
    if actrl.count(token) != bctrl.count(token):
        raise SystemExit(f'Phase319 FDR controller functional primitive changed: {token}')
if ahw.count('DSI_W32(') - bhw.count('DSI_W32(') != 2:
    raise SystemExit('Phase319 FDR expected exactly two new DSI_W32 helper call sites')
if ahw.count('wmb(') - bhw.count('wmb(') != 2:
    raise SystemExit('Phase319 FDR expected exactly two new wmb helper call sites')
for token in ('writel(', 'writel_relaxed(', 'regmap_write(', 'regmap_update_bits(',
              'wait_for_completion_timeout(', 'clk_set_rate(', 'clk_set_parent(',
              'regulator_enable(', 'regulator_disable(', 'reset_control_assert(',
              'reset_control_deassert(', 'msleep(', 'usleep_range(', 'udelay('):
    if ahw.count(token) != bhw.count(token):
        raise SystemExit(f'Phase319 FDR hardware functional primitive changed: {token}')
added=[x[1:] for x in Path('golden-fdr-phase319-out/audit/dsi_ctrl_hw_cmn-phase319.diff').read_text().splitlines()
       if x.startswith('+') and not x.startswith('+++')]
for line in added:
    if 'DSI_W32(' in line and 'DSI_DEBUG_BUS_CTL' not in line:
        raise SystemExit('Phase319 FDR added non-debugbus DSI_W32: '+line)
print('Phase319 Golden FDR scope audit: PASS')
PY

source scripts/common.sh
build_kernel "$LABEL"
IMAGE="$ROOT/artifacts/Image-$LABEL"
CONFIG="$ROOT/artifacts/config-$LABEL"
test -s "$IMAGE" -a -s "$CONFIG"
cmp -s "$BASE_OUT/config/final.config" "$CONFIG"

cp "$IMAGE" "$OUT/compile/Image"
cp "$CONFIG" "$OUT/config/final.config"
gzip -n -9 -c "$IMAGE" > "$OUT/package/Image.gz"
test -s "$OUT/package/Image.gz"
strings "$IMAGE" > "$OUT/audit/Image.strings.txt"
for marker in \
  'A52_GOLDEN_DMA_DONE_REFERENCE_V1' \
  'GDM S00' 'GDM DONE' \
  'TG319F ARM c=0' \
  'TG319F B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x'; do
  grep -Fq "$marker" "$OUT/audit/Image.strings.txt"
done

cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=PHASE319-GOLDEN-FDR-SIXPOINT-TEMPORAL-OBSERVER-V1
base=hardware-proven-Golden-FDR-DMA-DONE-reference
base_branch=agent/a52-golden-fdr-dma-done-reference-v1
base_commit=61d3185792f9d4acec49a4719c5bb2b9c08637de
touchgrass_base=$TOUCHGRASS_BASE
kernel_version=4.19.200-touchGrassKernel+
hardware_proven_golden_boot_sha256=$KNOWN_GOLDEN_BOOT_SHA256
hardware_proven_golden_container_sha256=$KNOWN_GOLDEN_CONTAINER_SHA256
hardware_proven_golden_config_sha256=$KNOWN_GOLDEN_CONFIG_SHA256
existing_dma_done_recorder=preserved-GDM-S00-through-S09-and-DONE
target=controller0-exact-F05A5A
hooks=q0-immediately-before-SW_TRIGGER,q1-immediately-after-SW_TRIGGER,q2-after-completion-outcome
selectors=0x0171,0x0181,0x0191,0x01a1,0x01e1,0x0211
writes_added=DSI_DEBUG_BUS_CTL-only-with-exact-saved-selector-restore
runtime_selector_writes_per_snapshot=7
clock_phy_reset_regulator_delay_retry_timeout_changes=none
phase315g_reconstruction_used=no
phase317_full_sweep_used=no
EOF

sha256sum "$OUT/compile/Image" "$OUT/config/final.config" "$OUT/package/Image.gz" > "$OUT/audit/pre-repack-sha256.txt"
echo 'Phase319 Golden FDR six-selector observer kernel build: PASS'
trap - ERR
