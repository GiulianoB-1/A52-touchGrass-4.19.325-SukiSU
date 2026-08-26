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
OUT="$ROOT/phase317-golden-out"
FAIL="$ROOT/phase317-golden-failure"
STAGE=startup
stage(){ STAGE="$1"; echo "== Phase317 Golden stage: $STAGE =="; }
fail_report(){ set +e; rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}; printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"; cp phase317-golden-*.log "$FAIL/logs/" 2>/dev/null || true; cp /tmp/p317gold-* "$FAIL/audit/" 2>/dev/null || true; for f in "$CTRL" "$HWC" "$PHY" "$PHYV3"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done; }
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase315G Golden observer"
bash scripts/315g_ci_build.sh 2>&1 | tee phase317-golden-phase315g.log
for f in phase315-golden-out/Image phase315-golden-out/config "$CTRL" "$HWC" "$PHY" "$PHYV3"; do test -s "$f"; done
grep -Fq 'A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1' "$CTRL"
grep -Fq 'TG315 DONE ret=%d irq=%d' "$CTRL"
cp "$CTRL" /tmp/p317gold-ctrl-before.c; cp "$HWC" /tmp/p317gold-hwc-before.c
cp "$PHY" /tmp/p317gold-phy-before.c; cp "$PHYV3" /tmp/p317gold-phyv3-before.c

stage "apply q2-only matched debug-bus observer"
python3 -m py_compile scripts/317_apply_dsi_internal_debugbus_delta.py
python3 scripts/317_apply_dsi_internal_debugbus_delta.py --root "$DSI" --flavor golden
python3 scripts/317_apply_dsi_internal_debugbus_delta.py --root "$DSI" --flavor golden --check-only
git -C "$KERNEL" diff --check -- techpack/display/msm/dsi/dsi_ctrl.c techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c
cp "$CTRL" /tmp/p317gold-ctrl-after.c; cp "$HWC" /tmp/p317gold-hwc-after.c
diff -u /tmp/p317gold-ctrl-before.c /tmp/p317gold-ctrl-after.c > /tmp/p317gold-ctrl.diff || true
diff -u /tmp/p317gold-hwc-before.c /tmp/p317gold-hwc-after.c > /tmp/p317gold-hwc.diff || true
cmp -s /tmp/p317gold-phy-before.c "$PHY"; cmp -s /tmp/p317gold-phyv3-before.c "$PHYV3"

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
bctrl=Path('/tmp/p317gold-ctrl-before.c').read_text(); actrl=Path('/tmp/p317gold-ctrl-after.c').read_text()
bhw=Path('/tmp/p317gold-hwc-before.c').read_text(); ahw=Path('/tmp/p317gold-hwc-after.c').read_text()
if actrl.count('DSI_W32(')!=bctrl.count('DSI_W32(') or actrl.count('wmb(')!=bctrl.count('wmb('): raise SystemExit('Golden ctrl write/barrier delta')
if ahw.count('DSI_W32(')-bhw.count('DSI_W32(')!=2: raise SystemExit('Golden expected exactly two DSI_W32 call sites')
if ahw.count('wmb(')-bhw.count('wmb(')!=2: raise SystemExit('Golden expected exactly two wmb call sites')
for token in ('writel_relaxed(','writel(','regmap_write(','regmap_update_bits(','readl_poll_timeout','wait_for_completion_timeout(',
              'udelay(','ndelay(','usleep_range(','msleep(','clk_set_rate(','clk_set_parent(','clk_prepare_enable(',
              'clk_disable_unprepare(','regulator_enable(','regulator_disable(','reset_control_assert(','reset_control_deassert('):
 if ahw.count(token)!=bhw.count(token) or actrl.count(token)!=bctrl.count(token): raise SystemExit('Golden forbidden functional delta: '+token)
added=[x[1:] for x in Path('/tmp/p317gold-hwc.diff').read_text().splitlines() if x.startswith('+') and not x.startswith('+++')]
for line in added:
 if 'DSI_W32(' in line and 'DSI_DEBUG_BUS_CTL' not in line: raise SystemExit('Golden added non-debugbus DSI_W32: '+line)
for token in ('A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1','TG317 H ctl=%x','TG317 B%u T%u %x %x %x %x','TG317 Z ctl=%x st=%x'):
 if token not in actrl+ahw: raise SystemExit('Golden marker missing: '+token)
print('Phase317 Golden scope audit: PASS')
PY

stage "rebuild Golden Image"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"' 2>&1 | tee phase317-golden-build.log
IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"
for marker in 'TG317 H ctl=%x' 'TG317 B%u T%u %x %x %x %x' 'TG317 Z ctl=%x st=%x' \
              'TG315 DONE ret=%d irq=%d'; do grep -aFq "$marker" "$IMAGE"; done

stage "assemble evidence"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package}
cp "$IMAGE" "$OUT/Image"; cp "$CONFIG" "$OUT/config"
cp phase317-golden-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/317_apply_dsi_internal_debugbus_delta.py "$OUT/audit/"
cp /tmp/p317gold-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$PHY" "$PHYV3" "$OUT/source/"
cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=PHASE317-GOLDEN-DSI-INTERNAL-DEBUGBUS-DELTA-V1
base=exact-Phase315G-known-working-TouchGrass-observer
kernel_version=4.19.200-touchGrassKernel+
target=controller0-exact-F05A5A
hook=q2-after-completion-wait-outcome
selector=((block&3)<<12)|((test&0x3f)<<4)|BIT(0)
space=4-blocks-x-64-test-points
writes=DSI_DEBUG_BUS_CTL-only-and-original-selector-restore-post-outcome
functional_clock_phy_reset_regulator_changes=none
flashable=pending-known-good-96MiB-container-repack
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo 'Phase317 Golden matched q2-only internal DSI debug-bus observer build: PASS'
