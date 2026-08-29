#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
DSI="$KERNEL/techpack/display/msm/dsi"
CTRL="$DSI/dsi_ctrl.c"
HWC="$DSI/dsi_ctrl_hw_cmn.c"
PHY="$DSI/dsi_phy.c"
PHYV3="$DSI/dsi_phy_hw_v3_0.c"
OUT="$ROOT/phase319-golden-out"
FAIL="$ROOT/phase319-golden-failure"
STAGE=startup
stage(){ STAGE="$1"; echo "== Phase319 Golden stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase319-golden-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p319gold-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/319_apply_dsi_sixpoint_temporal_observer.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase315G Golden observer"
bash scripts/315g_ci_build.sh 2>&1 | tee phase319-golden-phase315g.log
for f in phase315-golden-out/Image phase315-golden-out/config "$CTRL" "$HWC" "$PHY" "$PHYV3"; do test -s "$f"; done
grep -Fq 'A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1' "$CTRL"
grep -Fq 'TG315 DONE ret=%d irq=%d' "$CTRL"
! grep -Fq 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' "$CTRL"
! grep -Fq 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' "$HWC"
cp "$CTRL" /tmp/p319gold-ctrl-before.c
cp "$HWC" /tmp/p319gold-hwc-before.c
cp "$PHY" /tmp/p319gold-phy-before.c
cp "$PHYV3" /tmp/p319gold-phyv3-before.c

stage "apply six-selector q0/q1/q2 temporal observer"
python3 -m py_compile scripts/319_apply_dsi_sixpoint_temporal_observer.py
python3 scripts/319_apply_dsi_sixpoint_temporal_observer.py --root "$DSI" --flavor golden
python3 scripts/319_apply_dsi_sixpoint_temporal_observer.py --root "$DSI" --flavor golden --check-only
git -C "$KERNEL" diff --check -- techpack/display/msm/dsi/dsi_ctrl.c techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c
cp "$CTRL" /tmp/p319gold-ctrl-after.c
cp "$HWC" /tmp/p319gold-hwc-after.c
diff -u /tmp/p319gold-ctrl-before.c /tmp/p319gold-ctrl-after.c > /tmp/p319gold-ctrl.diff || true
diff -u /tmp/p319gold-hwc-before.c /tmp/p319gold-hwc-after.c > /tmp/p319gold-hwc.diff || true
cmp -s /tmp/p319gold-phy-before.c "$PHY"
cmp -s /tmp/p319gold-phyv3-before.c "$PHYV3"

stage "strict scope audit"
python3 - <<'PY'
from pathlib import Path
bctrl=Path('/tmp/p319gold-ctrl-before.c').read_text(); actrl=Path('/tmp/p319gold-ctrl-after.c').read_text()
bhw=Path('/tmp/p319gold-hwc-before.c').read_text(); ahw=Path('/tmp/p319gold-hwc-after.c').read_text()

if actrl.count('DSI_W32(') != bctrl.count('DSI_W32(') or actrl.count('wmb(') != bctrl.count('wmb('):
    raise SystemExit('Phase319 Golden dsi_ctrl.c added write/barrier')
if ahw.count('DSI_W32(') - bhw.count('DSI_W32(') != 2:
    raise SystemExit('Phase319 Golden expected exactly two new DSI_W32 call sites')
if ahw.count('wmb(') - bhw.count('wmb(') != 2:
    raise SystemExit('Phase319 Golden expected exactly two new wmb call sites')
for token in ('writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
              'readl_poll_timeout', 'wait_for_completion_timeout(', 'udelay(', 'ndelay(',
              'usleep_range(', 'msleep(', 'clk_set_rate(', 'clk_set_parent(',
              'clk_prepare_enable(', 'clk_disable_unprepare(', 'regulator_enable(',
              'regulator_disable(', 'reset_control_assert(', 'reset_control_deassert('):
    if ahw.count(token) != bhw.count(token) or actrl.count(token) != bctrl.count(token):
        raise SystemExit('Phase319 Golden forbidden functional delta: ' + token)
added=[x[1:] for x in Path('/tmp/p319gold-hwc.diff').read_text().splitlines()
       if x.startswith('+') and not x.startswith('+++')]
for line in added:
    if 'DSI_W32(' in line and 'DSI_DEBUG_BUS_CTL' not in line:
        raise SystemExit('Phase319 Golden added non-debugbus DSI_W32: ' + line)
combined=actrl+ahw
for token in (
    'A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1',
    '0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211',
    'TG319 B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x',
    'restored_ctl = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);',
    'a52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);',
):
    if token not in combined:
        raise SystemExit('Phase319 Golden marker missing: ' + token)
if ahw.count('a52_g319_debugbus_snapshot(ctrl, 0);') != 2:
    raise SystemExit('Phase319 Golden q0 hook count is not exactly two trigger paths')
if ahw.count('a52_g319_debugbus_snapshot(ctrl, 1);') != 2:
    raise SystemExit('Phase319 Golden q1 hook count is not exactly two trigger paths')
if 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' in combined:
    raise SystemExit('Phase319 Golden inherited forbidden Phase317 full sweep')
print('Phase319 Golden six-selector temporal scope audit: PASS')
PY

stage "rebuild Golden Image"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"' 2>&1 | tee phase319-golden-build.log
IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"
for marker in \
  'TG319 B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' \
  'TG315 DONE ret=%d irq=%d'; do
  grep -aFq "$marker" "$IMAGE"
done
! grep -aFq 'TG317 B%u T%u %x %x %x %x' "$IMAGE"

stage "assemble evidence"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package}
cp "$IMAGE" "$OUT/Image"; cp "$CONFIG" "$OUT/config"
cp phase319-golden-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/319_apply_dsi_sixpoint_temporal_observer.py "$OUT/audit/"
cp /tmp/p319gold-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$PHY" "$PHYV3" "$OUT/source/"
cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=PHASE319-GOLDEN-DSI-SIXPOINT-TEMPORAL-OBSERVER-V1
base=exact-Phase315G-known-working-TouchGrass-observer
kernel_version=4.19.200-touchGrassKernel+
target=controller0-exact-F05A5A
hooks=q0-immediately-before-trigger,q1-immediately-after-trigger,q2-after-completion-outcome
selectors=0x0171,0x0181,0x0191,0x01a1,0x01e1,0x0211
phase317_basis=all-six-are-members-of-measured-Golden-GKI-q2-raw-delta-set
writes=DSI_DEBUG_BUS_CTL-only-plus-exact-original-selector-restore-at-each-snapshot
restore_validation=DSI_DEBUG_BUS_CTL-readback-recorded-as-r-and-must-equal-saved-c
runtime_selector_writes_per_snapshot=7
phase317_full_sweep_inherited=no
functional_clock_phy_reset_regulator_delay_retry_changes=none
flashable=pending-known-good-96MiB-container-repack
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo 'Phase319 Golden six-selector q0/q1/q2 temporal observer build: PASS'
