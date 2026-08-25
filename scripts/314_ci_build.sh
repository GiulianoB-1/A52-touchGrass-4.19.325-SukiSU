#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="$PWD/phase314-gki-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
DISPLAY="$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
TGCTRLREG="$TG/techpack/display/msm/dsi/dsi_ctrl_reg.h"
TGCTRLHW="$TG/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c"
TGPHYV3="$TG/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
PHASE314_STAGE="startup"

set_stage() {
  PHASE314_STAGE="$1"
  echo "== Phase314 stage: $PHASE314_STAGE =="
}

fail_report() {
  set +e
  rm -rf phase314-gki-failure
  mkdir -p phase314-gki-failure/{logs,audit,source,nested,compile,package-meta}
  printf '%s\n' "$PHASE314_STAGE" > phase314-gki-failure/FAILED-STAGE.txt
  cp phase314-gki-compile.log phase314-gki-olddefconfig.log phase314-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p314-* phase314-gki-failure/audit/ 2>/dev/null || true
  cp scripts/314_apply_f0_full_prestate_recorder.py phase314-gki-failure/audit/ 2>/dev/null || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" phase314-gki-failure/compile/Image || true
  [ -f "$OUT/package/repack-report.json" ] && cp "$OUT/package/repack-report.json" phase314-gki-failure/package-meta/ || true
  [ -f "$OUT/BUILD-IDENTITY.json" ] && cp "$OUT/BUILD-IDENTITY.json" phase314-gki-failure/package-meta/ || true
  for f in "$CTRL" "$PHY" "$DISPLAY" "$HW" "$DISP"; do
    [ -f "$f" ] && cp "$f" phase314-gki-failure/source/ || true
  done
  for d in phase*-gki-failure; do
    [ -d "$d" ] || continue
    [ "$d" = "phase314-gki-failure" ] && continue
    cp -a "$d" phase314-gki-failure/nested/ || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

set_stage "reconstruct exact Phase313"
set +e
bash scripts/313_ci_build.sh 2>&1 | tee /tmp/p314-phase313.log
phase313_rc=${PIPESTATUS[0]}
set -e
if [ "$phase313_rc" -ne 0 ]; then
  echo "ERROR: Phase313 reconstruction failed rc=$phase313_rc" >&2
  exit "$phase313_rc"
fi

for f in \
  phase313-gki-out/package/boot.img \
  phase313-gki-out/compile/Image \
  phase313-gki-out/config/final.config \
  "$CTRL" "$PHY" "$DISPLAY" "$HW" "$DISP" "$TGCTRLREG" "$TGCTRLHW" "$TGPHYV3"; do
  test -s "$f"
done
test "$(stat -c '%s' phase313-gki-out/package/boot.img)" -eq 100663296

grep -Fq 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1' "$HW"
grep -Fq 'A52_PHASE312_GKI_F0_PHY_DEPENDENCY_RECORDER_V1' "$PHY"
grep -Fq 'A52_PHASE313_V3_TIMING9_HANDOFF_REPAIR_AB_V1' "$HW"
grep -Fq 'P276 303 S00p p=%02x%02x%02x' "$CTRL"

set_stage "source provenance"
python3 - "$TGCTRLREG" "$TGCTRLHW" "$TGPHYV3" <<'PY'
from pathlib import Path
import re, sys

ctrl = Path(sys.argv[1]).read_text()
ctrl_hw = Path(sys.argv[2]).read_text()
phy = Path(sys.argv[3]).read_text()

ctrl_expected = {
    'DSI_CTRL': 0x004, 'DSI_STATUS': 0x008, 'DSI_FIFO_STATUS': 0x00c,
    'DSI_COMMAND_MODE_DMA_CTRL': 0x03c, 'DSI_DMA_CMD_OFFSET': 0x048,
    'DSI_DMA_CMD_LENGTH': 0x04c, 'DSI_DMA_FIFO_CTRL': 0x050,
    'DSI_ACK_ERR_STATUS': 0x068, 'DSI_TRIG_CTRL': 0x084,
    'DSI_CMD_MODE_DMA_SW_TRIGGER': 0x090, 'DSI_LANE_STATUS': 0x0a8,
    'DSI_DLN0_PHY_ERR': 0x0b4, 'DSI_TIMEOUT_STATUS': 0x0c0,
    'DSI_ERR_INT_MASK0': 0x10c, 'DSI_INT_CTRL': 0x110,
    'DSI_CLK_CTRL': 0x11c, 'DSI_CLK_STATUS': 0x120,
    'DSI_PHY_SW_RESET': 0x12c, 'DSI_DYNAMIC_REFRESH_CTRL': 0x200,
    'DSI_DYNAMIC_REFRESH_STATUS': 0x210, 'DSI_DEBUG_CTRL': 0x2c8,
    'DSI_LOGICAL_LANE_SWAP_CTRL': 0x310, 'DSI_SPLIT_LINK': 0x330,
}

def macro(text, name):
    m = re.search(r'(?m)^#define\s+' + re.escape(name) + r'\s+\(0x([0-9A-Fa-f]+)\)', text)
    if not m:
        raise SystemExit('Phase314 source gate missing controller macro: ' + name)
    return int(m.group(1), 16)

for name, expected in ctrl_expected.items():
    got = macro(ctrl, name)
    if got != expected:
        raise SystemExit(f'Phase314 controller offset mismatch {name}: 0x{got:x} != 0x{expected:x}')

phy_expected = {
    'DSIPHY_CMN_CLK_CFG0': 0x010, 'DSIPHY_CMN_CLK_CFG1': 0x014,
    'DSIPHY_CMN_GLBL_CTRL': 0x018, 'DSIPHY_CMN_RBUF_CTRL': 0x01c,
    'DSIPHY_CMN_VREG_CTRL': 0x020, 'DSIPHY_CMN_CTRL_0': 0x024,
    'DSIPHY_CMN_CTRL_1': 0x028, 'DSIPHY_CMN_CTRL_2': 0x02c,
    'DSIPHY_CMN_LANE_CFG0': 0x030, 'DSIPHY_CMN_LANE_CFG1': 0x034,
    'DSIPHY_CMN_PLL_CNTRL': 0x038, 'DSIPHY_CMN_LANE_CTRL0': 0x098,
    'DSIPHY_CMN_PHY_STATUS': 0x0ec, 'DSIPHY_CMN_LANE_STATUS0': 0x0f4,
    'DSIPHY_CMN_LANE_STATUS1': 0x0f8,
}
for name, expected in phy_expected.items():
    m = re.search(r'(?m)^#define\s+' + re.escape(name) + r'\s+0x([0-9A-Fa-f]+)', phy)
    if not m:
        raise SystemExit('Phase314 source gate missing PHY macro: ' + name)
    got = int(m.group(1), 16)
    if got != expected:
        raise SystemExit(f'Phase314 PHY offset mismatch {name}: 0x{got:x} != 0x{expected:x}')

for token in (
    'DSI_W32(ctrl, DSI_DLN0_PHY_ERR, dln0_phy_err);',
    'DSI_W32(ctrl, DSI_FIFO_STATUS, fifo_status);',
    'DSI_W32(ctrl, DSI_ACK_ERR_STATUS, ack_error);',
    'DSI_W32(ctrl, DSI_TIMEOUT_STATUS, timeout_error);',
    'DSI_W32(ctrl, DSI_CLK_STATUS, clk_error);',
    'DSI_W32(ctrl, DSI_STATUS, dsi_status);',
):
    if token not in ctrl_hw:
        raise SystemExit('Phase314 error-read safety provenance missing: ' + token)

print('Phase314 controller/PHY register provenance gate: PASS')
PY

cp phase313-gki-out/config/final.config /tmp/p314-phase313.config
cp "$CTRL" /tmp/p314-ctrl-before.c
cp "$PHY" /tmp/p314-phy-before.c
cp "$DISPLAY" /tmp/p314-display-before.c
cp "$HW" /tmp/p314-hw-before.c
cp "$DISP" /tmp/p314-disp-before.c

set_stage "apply exhaustive prestate recorder"
python3 -m py_compile scripts/314_apply_f0_full_prestate_recorder.py
python3 scripts/314_apply_f0_full_prestate_recorder.py --root "$ROOT"
# Normalize only indentation inside the two Phase314 raw C templates. This
# never touches pre-existing source/string escapes.
python3 - "$PHY" "$DISPLAY" <<'PY'
from pathlib import Path
import sys

phy = Path(sys.argv[1])
text = phy.read_text()
start = text.index(r'\t\t/* A52_PHASE314_GKI_F0_FULL_PHY_PRESTATE_RECORDER_V1')
end = text.index('\t\ta52_ackfr_record("P276 312T0 ', start)
text = text[:start] + text[start:end].replace(r'\t', '\t') + text[end:]
phy.write_text(text)

disp = Path(sys.argv[2])
text = disp.read_text()
start = text.index('/* A52_PHASE314_GKI_F0_LIFECYCLE_HISTORY_RECORDER_V1')
end = text.index('#define INT_BASE_10', start)
text = text[:start] + text[start:end].replace(r'\t', '\t') + text[end:]
disp.write_text(text)
PY
python3 scripts/314_apply_f0_full_prestate_recorder.py --root "$ROOT" --check-only

cp "$CTRL" /tmp/p314-ctrl-after.c
cp "$PHY" /tmp/p314-phy-after.c
cp "$DISPLAY" /tmp/p314-display-after.c
diff -u /tmp/p314-ctrl-before.c /tmp/p314-ctrl-after.c > /tmp/p314-ctrl.diff || true
diff -u /tmp/p314-phy-before.c /tmp/p314-phy-after.c > /tmp/p314-phy.diff || true
diff -u /tmp/p314-display-before.c /tmp/p314-display-after.c > /tmp/p314-display.diff || true
cmp -s /tmp/p314-hw-before.c "$HW"
cmp -s /tmp/p314-disp-before.c "$DISP"

set_stage "read-only scope audit"
python3 - "$CTRL" "$PHY" "$DISPLAY" <<'PY'
from pathlib import Path
import sys

pairs = [
    (Path('/tmp/p314-ctrl-before.c').read_text(), Path(sys.argv[1]).read_text(), 'dsi_ctrl.c'),
    (Path('/tmp/p314-phy-before.c').read_text(), Path(sys.argv[2]).read_text(), 'dsi_phy.c'),
    (Path('/tmp/p314-display-before.c').read_text(), Path(sys.argv[3]).read_text(), 'dsi_display.c'),
]

protected = [
    'DSI_W32(', 'MDSS_PLL_REG_W(',
    'writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
    'wmb(', 'mb(', 'rmb(',
    'readl_poll_timeout', 'wait_for_completion_timeout(',
    'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'clk_prepare(', 'clk_unprepare(',
    'clk_enable(', 'clk_disable(',
    'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
]
for before, after, label in pairs:
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f'Phase314 read-only scope violation {label}: '
                f'{token} {before.count(token)} -> {after.count(token)}'
            )

combined_before = ''.join(p[0] for p in pairs)
combined_after = ''.join(p[1] for p in pairs)
if combined_after.count('readl_relaxed(') <= combined_before.count('readl_relaxed('):
    raise SystemExit('Phase314 expected read-only MMIO coverage to increase')
if combined_after.count('a52_ackfr_record(') <= combined_before.count('a52_ackfr_record('):
    raise SystemExit('Phase314 expected recorder coverage to increase')

for marker in (
    'A52_PHASE314_GKI_F0_FULL_CTRL_PRESTATE_RECORDER_V1',
    'A52_PHASE314_GKI_F0_FULL_PHY_PRESTATE_RECORDER_V1',
    'A52_PHASE314_GKI_F0_LIFECYCLE_HISTORY_RECORDER_V1',
):
    if combined_after.count(marker) != 1:
        raise SystemExit('Phase314 marker missing/not unique: ' + marker)

print('Phase314 read-only/no-establishment scope audit: PASS')
PY

set_stage "config invariant"
cp /tmp/p314-phase313.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase314-gki-olddefconfig.log 2>&1
cmp -s /tmp/p314-phase313.config "$BUILD/.config"

set_stage "compile Phase314 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase314-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase314-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

require_image_marker() {
  local marker="$1"
  local label="$2"
  printf 'Phase314 Image audit %-10s : ' "$label"
  if grep -aFq -- "$marker" "$IMAGE"; then
    echo PASS
  else
    echo FAIL
    echo "::error::Phase314 Image audit missing marker $label" >&2
    printf '%s\n' "$marker" > /tmp/p314-missing-image-marker.txt
    return 1
  fi
}

set_stage "Image marker audit"
require_image_marker 'P276 314C0 %x %x %x %x %x %x' '314C0'
require_image_marker 'P276 314C7 %x %x %x %x %x %x' '314C7'
require_image_marker 'P276 314S0 %u %u %u %u %u %u' '314S0'
require_image_marker 'P276 314P0 %x %x %x %x %x %x' '314P0'
require_image_marker 'P276 314P4 %x %x %x %x %x %x' '314P4'
require_image_marker 'P276 314H e=%u c=%x l=%x s=%x sp=%u' '314H'
require_image_marker 'P276 314HF e=%u cl=%u pi=%u ul=%u cg=%x' '314HF'
require_image_marker 'P276 312T1 %x %x %x %x %x %x' '312T1'
require_image_marker 'P276 308T q=%u %x %x %x %x %x' '308T'
require_image_marker 'P276 303 S00p p=%02x%02x%02x' '303S00p'

set_stage "assemble Phase314 evidence"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase314-gki-compile.log phase314-gki-olddefconfig.log "$OUT/audit/"
cp scripts/314_apply_f0_full_prestate_recorder.py "$OUT/audit/"
cp /tmp/p314-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$OUT/source/dsi_ctrl.c"
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$DISPLAY" "$OUT/source/dsi_display.c"
cp "$HW" "$OUT/source/dsi_phy_hw_v3_0.c"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp phase313-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE313-BASE-BUILD-IDENTITY.json"

set_stage "repack boot image"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase313-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

set_stage "write build identity"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase314-gki-out')

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

identity = {
    'phase': '314',
    'variant': 'GKI-PHASE313-BASE',
    'name': 'F0-FULL-PRESTATE-RECORDER-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'Phone-tested Phase313 head 39b346184ba10514a4547ceaf9320b66a00ed0c5',
    'behavior': 'read-only recording; no new hardware writes/barriers/delays/clocks/resets/regulators',
    'coverage': [
        'first-F0 controller register prestate',
        'first-F0 controller software/IRQ/DMA state',
        'first-F0 PHY common/status/test-datapath state',
        'inherited Phase307/308/310/312 clock/PLL/lane/timing/DISP_CC state',
        'clock callback + continuous-splash/ULPS/clamp lifecycle history',
        'host-enable continuous-splash skip and resync decision history',
    ],
    'image_sha256': sha(r/'compile/Image'),
    'boot_img_sha256': sha(r/'package/boot.img'),
    'boot_img_size': (r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
PY

set_stage "checksums"
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

set_stage "complete"
echo 'Phase314 exhaustive first-F0 prestate recorder: PASS'
