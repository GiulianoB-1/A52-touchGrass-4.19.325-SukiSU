#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
DSI="$KERNEL/techpack/display/msm/dsi"
CTRL="$DSI/dsi_ctrl.c"
HWC="$DSI/dsi_ctrl_hw_cmn.c"
OUT="$ROOT/phase344-golden-out"
FAIL="$ROOT/phase344-golden-failure"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase344G Golden stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase344-golden-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p344g-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/344g_apply_golden_dma_transition_recorder.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase319 Golden known-good observer baseline"
bash scripts/319g_ci_build_golden.sh 2>&1 | tee phase344-golden-phase319.log
test -s phase319-golden-out/Image
test -s "$CTRL" -a -s "$HWC"
grep -Fq 'A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1' "$CTRL"
grep -Fq 'A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1' "$HWC"
cp "$CTRL" /tmp/p344g-ctrl-before.c
cp "$HWC" /tmp/p344g-hwc-before.c

stage "apply read-only Golden DMA transition recorder"
python3 -m py_compile scripts/344g_apply_golden_dma_transition_recorder.py
python3 scripts/344g_apply_golden_dma_transition_recorder.py --root "$DSI"
python3 scripts/344g_apply_golden_dma_transition_recorder.py --root "$DSI" --check-only
git -C "$KERNEL" diff --check -- techpack/display/msm/dsi/dsi_ctrl.c techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c
cp "$CTRL" /tmp/p344g-ctrl-after.c
cp "$HWC" /tmp/p344g-hwc-after.c
diff -u /tmp/p344g-ctrl-before.c /tmp/p344g-ctrl-after.c > /tmp/p344g-ctrl.diff || true
diff -u /tmp/p344g-hwc-before.c /tmp/p344g-hwc-after.c > /tmp/p344g-hwc.diff || true

stage "strict observation-only scope audit"
python3 - <<'PY'
from pathlib import Path
bctrl=Path('/tmp/p344g-ctrl-before.c').read_text()
actrl=Path('/tmp/p344g-ctrl-after.c').read_text()
bhw=Path('/tmp/p344g-hwc-before.c').read_text()
ahw=Path('/tmp/p344g-hwc-after.c').read_text()

# Absolutely no new MMIO/clock/power/reset/delay/wait behavior.
for token in (
    'DSI_W32(', 'DSI_DISP_CC_W32(', 'DSI_MMSS_MISC_W32(', 'DSI_MISC_W32(',
    'writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
    'readl_poll_timeout', 'wait_for_completion_timeout(',
    'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
):
    if actrl.count(token) != bctrl.count(token) or ahw.count(token) != bhw.count(token):
        raise SystemExit('Phase344G forbidden functional delta: ' + token)

# Trigger/completion contract must remain exactly one normal write on each
# inherited trigger path and one normal completion wait.
for text,label in ((bhw,'before-hw'),(ahw,'after-hw')):
    if text.count('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);') != 2:
        raise SystemExit(f'Phase344G {label} unexpected SW_TRIGGER write count')
for text,label in ((bctrl,'before-ctrl'),(actrl,'after-ctrl')):
    if text.count('wait_for_completion_timeout(') != 1:
        raise SystemExit(f'Phase344G {label} unexpected completion-wait count')

if ahw.count('DSI_R32(') <= bhw.count('DSI_R32('):
    raise SystemExit('Phase344G expected read-only MMIO coverage to increase')
for token in (
    'A52_PHASE344G_GOLDEN_DMA_TRANSITION_RECORDER_V1',
    'TG344 S i=%u p=%u t=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x',
    'TG344 D i=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x',
    'TG344 DONE n=%u ret=%d irq=%d',
):
    if token not in actrl + ahw:
        raise SystemExit('Phase344G recorder marker missing: ' + token)
print('Phase344G observation-only scope audit: PASS')
PY

stage "rebuild TouchGrass Golden Image"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"' 2>&1 | tee phase344-golden-build.log
IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"

stage "compiled marker audit"
for marker in   'TG344 S i=%u p=%u t=%llu st=%x fs=%x cc=%x ck=%x in=%x ln=%x'   'TG344 D i=%u dc=%x o=%x l=%x sw=%x tg=%x ae=%x to=%x pe=%x ax=%x'   'TG344 DONE n=%u ret=%d irq=%d'   'TG319 B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x'   'TG315 DONE ret=%d irq=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "assemble Golden evidence"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package}
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp phase344-golden-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/344g_apply_golden_dma_transition_recorder.py "$OUT/audit/"
cp /tmp/p344g-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$OUT/source/"
cat > "$OUT/BUILD-IDENTITY.txt" <<'EOF'
experiment=PHASE344G-GOLDEN-FDR-DMA-TRANSITION-RECORDER-V1
base=exact-Phase319-Golden-known-working-TouchGrass-observer
touchgrass_commit=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
target=controller0-exact-F05A5A
source_audit_result=SW_TRIGGER-write-register-map-irq-status-clear-enable-and-wait-contract-source-parity
q0=last-compact-read-immediately-before-SW_TRIGGER
q1_q6=six-back-to-back-read-only-samples-immediately-after-SW_TRIGGER
qIRQ=DMA_DONE-ISR-before-normal-state-mutation
qWAIT=after-normal-completion-wait
sample_transport=RAM-buffer-first-print-after-normal-completion
sample_registers=STATUS,FIFO,CLK_CTRL,CLK_STATUS,INT_CTRL,LANE_STATUS,DMA_CTRL,DMA_OFFSET,DMA_LENGTH,SW_TRIGGER,TRIG_CTRL,ACK_ERR,TIMEOUT,PHY_ERR,AXI2AHB
inherits=Phase315G-full-q0-prestate,Phase319-six-debugbus-q0-q1-q2
functional_register_writes_added=none
clock_phy_reset_regulator_delay_retry_changes=none
trigger_behavior_changed=no
completion_behavior_changed=no
flashable=pending-known-good-96MiB-FDR-container-repack
EOF
cat > "$OUT/PHASE344G-RECORD-SCHEMA.txt" <<'EOF'
Phase344G Golden DMA transition recorder
========================================

Exact target: controller0, FETCH_MEMORY, msg_flags=0x8, type=0x29,
payload F0 5A 5A.

Point codes:
  0 = last compact sample immediately before SW_TRIGGER
  1..6 = consecutive read-only samples immediately after SW_TRIGGER
  8 = DMA_DONE ISR before dma_irq_trig/completion state mutation
  9 = completion-wait outcome

Each stored sample prints as two records after normal completion:
  TG344 S: time + STATUS/FIFO/CLK_CTRL/CLK_STATUS/INT_CTRL/LANE_STATUS
  TG344 D: DMA_CTRL/OFFSET/LENGTH/SW_TRIGGER/TRIG_CTRL/ACK_ERR/TIMEOUT/PHY_ERR/AXI2AHB

The q0..q6 path does not printk. Samples are buffered in RAM and dumped only
after the transaction completed, minimizing observer timing disturbance.

Inherited TG315 gives the full q0 controller+PHY prestate.
Inherited TG319 gives matched six-selector debug-bus q0/q1/q2 snapshots.
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo 'Phase344G Golden-FDR DMA transition recorder build: PASS'
# Phase344G workflow dispatch bridge
