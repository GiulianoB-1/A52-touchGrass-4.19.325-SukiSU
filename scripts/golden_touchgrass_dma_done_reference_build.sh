#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/golden-dma-done-ref-out"
FAIL="$ROOT/golden-dma-done-ref-failure"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
DSI="$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"
BASE_OUT="$ROOT/golden-clock-ref-out"
LABEL="touchgrass-4.19.200-golden-dma-done-ref-v1"

# Hardware-proven Golden reference identity supplied by the A52 bring-up.
KNOWN_GOLDEN_CONFIG_SHA256="d2c21f394ec477a975ce96f59959fa265acde60a4a28ef4d200c9912dfb624d1"
KNOWN_GOLDEN_IMAGE_SHA256="01453e38d74af1660177283852c04c80d21af26fb66aff226e49f4bc35c52b75"
KNOWN_GOLDEN_IMAGE_GZ_SHA256="a372d19a69483c59836c673db96d5a475eb1bcbb050daad22f5c8aee7fd312fe"
KNOWN_GOLDEN_BOOT_SHA256="7c61a4fa58679c955391eedad666b3cb38e0eb5e49bc69ac87f1edd862a79f12"
KNOWN_GOLDEN_CONTAINER_SHA256="50683df47b3f74fd3dd4ca7ff96bc114a6b7339955670b4c555fecaeda07e8c0"
KNOWN_GOLDEN_RAMDISK_SHA256="54f7891881a755536e908acd66fa2cf48bb24ed5f9f8d2fc4b0bf259f395a4c1"
KNOWN_GOLDEN_DTB_SHA256="93612e8e1c49cff5660b733e5ff75e53dfca6511c79c00eee4d0355b5bb92d94"
TOUCHGRASS_BASE="6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"

rm -rf "$OUT" "$FAIL"
mkdir -p "$OUT"/{audit,compile,config,package,source} "$FAIL"

on_err() {
  rc=$?
  set +e
  {
    echo "rc=$rc"
    date -u
    echo "Golden DMA_DONE reference build failed"
    if [ -d "$KERNEL/.git" ]; then
      git -C "$KERNEL" status --short || true
      git -C "$KERNEL" diff --stat || true
      git -C "$KERNEL" diff --check || true
    fi
  } > "$FAIL/diagnostics.txt" 2>&1
  test -f "$DSI" && cp "$DSI" "$FAIL/dsi_ctrl.c" || true
  test -f "$OUT/audit/dsi_ctrl-before-dma.c" && cp "$OUT/audit/dsi_ctrl-before-dma.c" "$FAIL/" || true
  cp scripts/golden_touchgrass_dma_done_reference.py "$FAIL/" 2>/dev/null || true
  exit "$rc"
}
trap on_err ERR

chmod +x scripts/*.sh scripts/*.py

# STEP 1: Reconstruct the already hardware-proven Golden TouchGrass clock
# reference exactly as its established workflow does. This is intentionally
# NOT a Phase28x/Phase29x/GKI lineage.
bash scripts/golden_touchgrass_clock_reference_build.sh

test -s "$BASE_OUT/Image"
test -s "$BASE_OUT/config"
test -s "$DSI"
grep -Fq 'Linux version 4.19.200-touchGrassKernel+' "$BASE_OUT/Image.strings.txt"
grep -Fq 'A52_GOLDEN_TOUCHGRASS_CLOCK_CHAIN_REFERENCE_V2' \
  "$KERNEL/techpack/display/msm/dsi/dsi_clk_manager.c"
grep -Fq 'A52_GOLDEN_TOUCHGRASS_CLOCK_CHAIN_ORIGIN_V2' \
  "$KERNEL/techpack/display/msm/dsi/dsi_display.c"
grep -Fq 'TGREF CMD PRE' "$DSI"
grep -Fq 'TGREF CMD POST' "$DSI"

test "$(git -C "$KERNEL" rev-parse HEAD)" = "$TOUCHGRASS_BASE"

# .config is deterministic and is our hard reproducibility gate. The raw Image
# contains build timestamp/runner identity, so record its hash but do not falsely
# require a cross-run byte-identical binary.
printf '%s  %s\n' "$KNOWN_GOLDEN_CONFIG_SHA256" "$BASE_OUT/config" | sha256sum -c -
sha256sum "$BASE_OUT/Image" > "$OUT/audit/rebuilt-golden-Image.sha256"
printf '%s\n' "$KNOWN_GOLDEN_IMAGE_SHA256" > "$OUT/audit/hardware-proven-golden-Image.sha256"
printf '%s\n' "$KNOWN_GOLDEN_IMAGE_GZ_SHA256" > "$OUT/audit/hardware-proven-golden-Image.gz.sha256"
printf '%s\n' "$KNOWN_GOLDEN_BOOT_SHA256" > "$OUT/audit/hardware-proven-golden-boot.img.sha256"
printf '%s\n' "$KNOWN_GOLDEN_CONTAINER_SHA256" > "$OUT/audit/hardware-proven-golden-container.sha256"

cp "$DSI" "$OUT/audit/dsi_ctrl-before-dma.c"
cp "$BASE_OUT/config" "$OUT/audit/golden-reference.config"
cp scripts/golden_touchgrass_clock_reference.py "$OUT/audit/"
cp scripts/golden_touchgrass_clock_origin.py "$OUT/audit/"
cp scripts/golden_touchgrass_dma_done_reference.py "$OUT/audit/"

python3 - "$OUT/audit/dsi_ctrl-before-dma.c" > "$OUT/audit/production-primitives-before.json" <<'PY'
import json, sys
from pathlib import Path
text=Path(sys.argv[1]).read_text()
tokens=[
 'DSI_W32(', 'writel(', 'writel_relaxed(', 'wait_for_completion_timeout(',
 'dsi_hw_ops.kickoff_command(', 'dsi_hw_ops.kickoff_command_non_embedded_mode(',
 'dsi_hw_ops.kickoff_fifo_command(', 'dsi_hw_ops.trigger_command_dma(',
 'dsi_hw_ops.reset_cmd_fifo(', 'dsi_hw_ops.soft_reset(', 'clk_set_rate(',
 'msleep(', 'usleep_range(', 'udelay('
]
print(json.dumps({t:text.count(t) for t in tokens}, indent=2, sort_keys=True))
PY

# STEP 2: Add only the single-shot read-only DMA_DONE reference recorder.
python3 scripts/golden_touchgrass_dma_done_reference.py --root "$KERNEL"
python3 scripts/golden_touchgrass_dma_done_reference.py --root "$KERNEL" --check-only
git -C "$KERNEL" diff --check

test -s "$DSI"
! cmp -s "$OUT/audit/dsi_ctrl-before-dma.c" "$DSI"
cp "$DSI" "$OUT/source/dsi_ctrl.c"

python3 - "$OUT/audit/dsi_ctrl-before-dma.c" "$DSI" \
  > "$OUT/audit/production-primitives-audit.txt" <<'PY'
import sys
from pathlib import Path
before=Path(sys.argv[1]).read_text()
after=Path(sys.argv[2]).read_text()
tokens=[
 'DSI_W32(', 'writel(', 'writel_relaxed(', 'wait_for_completion_timeout(',
 'dsi_hw_ops.kickoff_command(', 'dsi_hw_ops.kickoff_command_non_embedded_mode(',
 'dsi_hw_ops.kickoff_fifo_command(', 'dsi_hw_ops.trigger_command_dma(',
 'dsi_hw_ops.reset_cmd_fifo(', 'dsi_hw_ops.soft_reset(', 'clk_set_rate(',
 'msleep(', 'usleep_range(', 'udelay('
]
failed=[]
for token in tokens:
    a=before.count(token); b=after.count(token)
    print(f'{token}: before={a} after={b}')
    if a != b:
        failed.append((token,a,b))
if failed:
    raise SystemExit('production behavior primitive count changed: '+repr(failed))
if after.count('DSI_R32(') <= before.count('DSI_R32('):
    raise SystemExit('expected read-only DSI_R32 observations were not added')
if after.count('pr_info(') <= before.count('pr_info('):
    raise SystemExit('expected bounded post-completion GDM output was not added')
print('Golden DMA recorder behavior-preservation primitive audit: PASS')
PY

# Ensure the patch did not touch Golden clock/display source files.
cmp -s "$BASE_OUT/audit/dsi_clk_manager-after.c" \
  "$KERNEL/techpack/display/msm/dsi/dsi_clk_manager.c"
cmp -s "$BASE_OUT/audit/dsi_display-after.c" \
  "$KERNEL/techpack/display/msm/dsi/dsi_display.c"

# STEP 3: Rebuild the exact already-integrated Golden tree without rerunning any
# integration patcher. common.sh::build_kernel only configures/builds current tree.
source scripts/common.sh
build_kernel "$LABEL"

IMAGE="$ROOT/artifacts/Image-$LABEL"
CONFIG="$ROOT/artifacts/config-$LABEL"
test -s "$IMAGE"
test -s "$CONFIG"
cmp -s "$BASE_OUT/config" "$CONFIG"

cp "$IMAGE" "$OUT/compile/Image"
cp "$CONFIG" "$OUT/config/final.config"
cp "$ROOT/artifacts/logs/build-$LABEL.log" "$OUT/audit/build.log"

gzip -n -9 -c "$IMAGE" > "$OUT/package/Image.gz"
test -s "$OUT/package/Image.gz"

strings "$IMAGE" > "$OUT/audit/Image.strings.txt"
for marker in \
  'TGREF CMD PRE' 'TGREF CMD POST' \
  'GDM S00' 'GDM S01' 'GDM S02' 'GDM S03' 'GDM S04' \
  'GDM S05' 'GDM S06' 'GDM S07' 'GDM S08' 'GDM S09' 'GDM DONE'; do
  grep -Fq "$marker" "$OUT/audit/Image.strings.txt"
done
grep -Fq 'A52_GOLDEN_DMA_DONE_REFERENCE_V1' "$DSI"
python3 scripts/golden_touchgrass_dma_done_reference.py --root "$KERNEL" --check-only

cat > "$OUT/DMA-CHAIN-SCHEMA.txt" <<'EOF'
Golden-FDR successful DMA_DONE reference recorder
=================================================
Target arm signature, before tx-mode selection:
  controller=0
  incoming controller flags=0x20 (DSI_CTRL_CMD_FETCH_MEMORY)
  MIPI message flags=0x8
  DSI message type=0x29
  tx_len=3

GDM S00  exact target signature and first 3 payload bytes
GDM S01  Golden-selected tx mode, hw flags, panel/power/engine state
GDM S02  cached target clocks and actual Linux clock rates
GDM S03  raw IRQ/status/lane/clock state immediately before DMA_DONE arm
GDM S04  same state after normal IRQ arm + completion reinit
GDM S05  DMA control/offset/length/FIFO/trigger/clock state before normal kickoff
GDM S06  status/FIFO/lane/clock/TPG/interrupt state after normal kickoff returns
GDM S07  DMA_DONE ISR translated status, raw INT_CTRL, error bits, irq state
GDM S08  normal completion wait result + irq/raw status
GDM S09  final status/FIFO/lane/clock/ACK/timeout/PHY/controller/TPG state
GDM DONE success=1 means the normal Golden path observed DMA_DONE in the ISR.

Instrumentation policy:
  single shot
  read only before/during DMA
  no output from ISR
  bounded pr_info output only after normal completion path
  no DSI writes added
  no trigger added/removed
  no wait added/removed
  no reset/recovery added/removed
  no clock programming added/removed
  no panel packets added/removed
  no brightness behavior changed
EOF

cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=GOLDEN-FDR-DMA-DONE-REFERENCE-V1
purpose=record successful Golden DSI DMA chain through DMA_DONE for slot-by-slot comparison with broken GKI
base=hardware-proven Golden TouchGrass clock-reference lineage
base_project_branch=agent/a52-golden-touchgrass-clock-reference-v1
touchgrass_base=$TOUCHGRASS_BASE
kernel_version=4.19.200-touchGrassKernel+
behavior_change=none-read-only-single-shot-dma-observation
modified_runtime_source=techpack/display/msm/dsi/dsi_ctrl.c-only-after-proven-golden-reconstruction
register_writes_added=false
triggers_added_or_removed=false
waits_added_or_removed=false
resets_or_recovery_changed=false
clock_programming_changed=false
panel_packets_changed=false
brightness_changed=false
hardware_proven_golden_boot_sha256=$KNOWN_GOLDEN_BOOT_SHA256
hardware_proven_golden_raw_Image_sha256=$KNOWN_GOLDEN_IMAGE_SHA256
hardware_proven_golden_Image_gz_sha256=$KNOWN_GOLDEN_IMAGE_GZ_SHA256
hardware_proven_golden_config_sha256=$KNOWN_GOLDEN_CONFIG_SHA256
hardware_proven_golden_container_sha256=$KNOWN_GOLDEN_CONTAINER_SHA256
expected_ramdisk_sha256=$KNOWN_GOLDEN_RAMDISK_SHA256
expected_dtb_sha256=$KNOWN_GOLDEN_DTB_SHA256
repacker=scripts/38_repack_a52_p1_boot.py
EOF

sha256sum "$OUT/compile/Image" "$OUT/config/final.config" "$OUT/package/Image.gz" \
  > "$OUT/audit/pre-repack-sha256.txt"

echo 'Golden-FDR DMA_DONE reference kernel build: PASS'
trap - ERR
