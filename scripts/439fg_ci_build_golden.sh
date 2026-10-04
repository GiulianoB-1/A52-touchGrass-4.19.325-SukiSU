#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
DSI="$KERNEL/techpack/display/msm/dsi"
CTRL="$DSI/dsi_ctrl.c"
HWC="$DSI/dsi_ctrl_hw_cmn.c"
OUT="$ROOT/phase439f-golden-out"
FAIL="$ROOT/phase439f-golden-failure"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase439FG stage: $STAGE =="; }

fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase439f-golden-*.log "$FAIL/logs/" 2>/dev/null || true
  cp scripts/439fg_apply_golden_dense.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/" || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct proven Phase439G Golden"
bash scripts/439g_ci_build_golden.sh 2>&1 | tee phase439f-golden-phase439g.log
test -s phase439-golden-out/Image
grep -Fq 'A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2' "$HWC"

cp "$CTRL" /tmp/p439fg-ctrl-before.c
cp "$HWC" /tmp/p439fg-hwc-before.c

stage "apply dense clock observer"
python3 -m py_compile scripts/439fg_apply_golden_dense.py
python3 scripts/439fg_apply_golden_dense.py --root "$DSI"
python3 scripts/439fg_apply_golden_dense.py --root "$DSI" --check-only

git -C "$KERNEL" diff --check -- \
  techpack/display/msm/dsi/dsi_ctrl.c \
  techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c

cp "$CTRL" /tmp/p439fg-ctrl-after.c
cp "$HWC" /tmp/p439fg-hwc-after.c
diff -u /tmp/p439fg-ctrl-before.c /tmp/p439fg-ctrl-after.c > /tmp/p439fg-ctrl.diff || true
diff -u /tmp/p439fg-hwc-before.c /tmp/p439fg-hwc-after.c > /tmp/p439fg-hwc.diff || true

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
b=Path('/tmp/p439fg-hwc-before.c').read_text()
a=Path('/tmp/p439fg-hwc-after.c').read_text()
bc=Path('/tmp/p439fg-ctrl-before.c').read_text()
ac=Path('/tmp/p439fg-ctrl-after.c').read_text()

for token in (
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
):
    if a.count(token)!=b.count(token) or ac.count(token)!=bc.count(token):
        raise SystemExit('Phase439FG forbidden functional delta: '+token)

if a.count('DSI_W32(ctrl, DSI_CLK_CTRL') != b.count('DSI_W32(ctrl, DSI_CLK_CTRL'):
    raise SystemExit('Phase439FG unexpectedly changes CLK_CTRL writes')

for token in (
    'A52_PHASE439FG_GOLDEN_DENSE_CLOCK_V1',
    'TG439F F i=%u',
    'a52_g439f_dense_run(ctrl);',
    'a52_g439f_terminal',
):
    if token not in a+ac:
        raise SystemExit('Phase439FG marker missing: '+token)

print('Phase439FG scope audit: PASS')
PY

stage "incremental rebuild"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"' \
  2>&1 | tee phase439f-golden-build.log

IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"

for marker in \
  'A52_PHASE439FG_GOLDEN_DENSE_CLOCK_V1' \
  'TG439F F i=%u ns=%llu delta_ns=%llu' \
  'A52_PHASE439G_GOLDEN_DSI_AUTOPSY_V2'; do
  grep -aFq "$marker" "$IMAGE"
done

stage "assemble"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package}
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp phase439f-golden-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/439fg_apply_golden_dense.py \
   scripts/439g_apply_golden_dsi_autopsy.py "$OUT/audit/"
cp /tmp/p439fg-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$OUT/source/"

cat > "$OUT/BUILD-IDENTITY.txt" <<'EOF'
experiment=PHASE439F-GOLDEN-DENSE-CLOCK-V1
base=proven-Phase439G-Golden
hot_loop=64-samples-about-1us-apart
hot_fields=DSI_STATUS,DSI_CLK_STATUS,DSI_INT_CTRL,DEBUG_BUS_STATUS-selector-0x171
provider_points=pre-trigger,post-64us,post-DMA-completion
provider_fields=PCLK0/BYTE0/BYTE0_INTF/ESC0-CBCR,PCLK0/BYTE0/ESC0-CMD+CFG,PLL_STATUS
functional_clock_change=none
proc=/proc/a52_phase439g
EOF

sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo "Phase439FG Golden build: PASS"
