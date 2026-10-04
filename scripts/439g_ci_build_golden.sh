#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
DSI="$KERNEL/techpack/display/msm/dsi"
CTRL="$DSI/dsi_ctrl.c"
HWC="$DSI/dsi_ctrl_hw_cmn.c"
OUT="$ROOT/phase439-golden-out"
FAIL="$ROOT/phase439-golden-failure"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase439G Golden stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase439-golden-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p439g-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/439g_apply_golden_dsi_autopsy.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase424G known-good Golden baseline"
bash scripts/424g_ci_build_golden.sh 2>&1 | tee phase439-golden-phase424g.log
test -s phase424-golden-out/Image
test -s "$CTRL" -a -s "$HWC"
grep -Fq 'A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1' "$HWC"
grep -Fq 'A52_PHASE424G_PAIRED_HW_SNAPSHOT_V1' "$HWC"
cp "$CTRL" /tmp/p439g-ctrl-before.c
cp "$HWC" /tmp/p439g-hwc-before.c

stage "apply Phase439G matched timed oracle"
python3 -m py_compile scripts/439g_apply_golden_dsi_autopsy.py
python3 scripts/439g_apply_golden_dsi_autopsy.py --root "$DSI"
python3 scripts/439g_apply_golden_dsi_autopsy.py --root "$DSI" --check-only
git -C "$KERNEL" diff --check -- techpack/display/msm/dsi/dsi_ctrl.c techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c
cp "$CTRL" /tmp/p439g-ctrl-after.c
cp "$HWC" /tmp/p439g-hwc-after.c
diff -u /tmp/p439g-ctrl-before.c /tmp/p439g-ctrl-after.c > /tmp/p439g-ctrl.diff || true
diff -u /tmp/p439g-hwc-before.c /tmp/p439g-hwc-after.c > /tmp/p439g-hwc.diff || true

stage "observer scope audit"
python3 - <<'PY'
from pathlib import Path
b=Path('/tmp/p439g-hwc-before.c').read_text()
a=Path('/tmp/p439g-hwc-after.c').read_text()
bc=Path('/tmp/p439g-ctrl-before.c').read_text()
ac=Path('/tmp/p439g-ctrl-after.c').read_text()

# The only newly injected DSI write is debug-bus selector programming/restore.
sw='DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);'
if a.count(sw) != b.count(sw):
    raise SystemExit('Phase439G changed SW_TRIGGER count')
for token in (
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
    'wait_for_completion_timeout(',
):
    if a.count(token) != b.count(token) or ac.count(token) != bc.count(token):
        raise SystemExit('Phase439G forbidden functional delta: '+token)

added=[x[1:] for x in Path('/tmp/p439g-hwc.diff').read_text().splitlines()
       if x.startswith('+') and not x.startswith('+++')]
for line in added:
    if 'DSI_W32(' in line and 'DSI_DEBUG_BUS_CTL' not in line:
        raise SystemExit('Phase439G added non-debugbus DSI write: '+line)

for token in (
    'A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2',
    'A52_G439_PHY_PHYS 0x0ae94000ULL',
    'a52_g439_wait_until(start,15000ULL)',
    'a52_g439_wait_until(start,35000ULL)',
    'a52_g439_wait_until(start,50000ULL)',
    'TG439 S i=%u p=%u ns=%llu',
    'TG439 D i=%u dc=%x',
    'TG439 Q i=%u s1=%x s2=%x dm=%x',
    'TG439 P i=%u ps=%x',
):
    if token not in a+ac:
        raise SystemExit('Phase439G marker missing: '+token)
print('Phase439G observer scope audit: PASS')
PY

stage "rebuild TouchGrass Golden Image"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"' 2>&1 | tee phase439-golden-build.log
IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"

stage "compiled marker audit"
for marker in \
  'A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2' \
  'TG439 S i=%u p=%u ns=%llu' \
  'TG439 D i=%u dc=%x' \
  'TG439 Q i=%u s1=%x s2=%x dm=%x' \
  'TG439 P i=%u ps=%x' \
  'A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1' \
  'A52_PHASE424G_PAIRED_HW_SNAPSHOT_V1'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "assemble Phase439G Golden evidence"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package}
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp phase439-golden-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/439g_apply_golden_dsi_autopsy.py \
   scripts/344g_apply_golden_dma_transition_recorder.py \
   scripts/424g_apply_golden_paired_hw_snapshot.py "$OUT/audit/"
cp /tmp/p439g-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$OUT/source/"
cat > "$OUT/BUILD-IDENTITY.txt" <<'EOF'
experiment=PHASE439G-GOLDEN-DSI-AUTOPSY-V2
base=exact-Phase424G-known-working-TouchGrass-Golden
target=controller0-exact-F05A5A
timed_points=PRE,+15us,+35us,+50us
timed_debug_selector=0x0171
timed_fields=DSI_STATUS,FIFO,CLK_CTRL,CLK_STATUS,INT_CTRL,LANE_STATUS,LANE_CTRL,DMA_CTRL,DMA_OFFSET,DMA_LENGTH,SW_TRIGGER,TRIG_CTRL,DMA_SCHEDULE_CTRL_0x100,DMA_SCHEDULE_CTRL2_0x104,DISP_CC_MISC_CMD,ACK_ERR,TIMEOUT,PHY_ERR,AXI2AHB,DBG171,PLL_STATUS_ONE,PHY_PLL_CTRL,PHY_CTRL0,PHY_RBUF,PHY_CLK_CFG1,PHY_LANE0,PHY_LANE1
sample_transport=normal-RAM-hot-path,/proc/a52_phase439g,post-success-printk;debug_bus=0x0171-timed-plus-256-selector-post-success
inherits=Phase319-six-selector-debugbus,Phase344G-DMA-transition,Phase424G-paired-hardware
functional_dsi_write_added=debug-selector-only
clock_phy_reset_regulator_policy_changes=none
flashable=pending-known-good-96MiB-FDR-container-repack
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo "Phase439G Golden DSI autopsy build: PASS"
