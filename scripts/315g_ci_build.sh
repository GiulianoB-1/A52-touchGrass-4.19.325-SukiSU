#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KERNEL="$ROOT/workspace/touchgrass-a52xq"
OUT="$ROOT/phase315-golden-out"
FAIL="$ROOT/phase315-golden-failure"
CTRL="$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"
HW="$KERNEL/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$KERNEL/techpack/display/msm/dsi/dsi_phy.c"
PHYV3="$KERNEL/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
CTRLREG="$KERNEL/techpack/display/msm/dsi/dsi_ctrl_reg.h"

rm -rf "$OUT" "$FAIL"
mkdir -p "$OUT"/{audit,source,package} "$FAIL"
STAGE=init
printf '%s\n' "$STAGE" > "$FAIL/stage.txt"
exec > >(tee -a "$FAIL/full.log") 2>&1
set -x

stage() {
  STAGE="$1"
  set +x
  printf '%s\n' "$STAGE" | tee "$FAIL/stage.txt"
  printf 'PHASE315G_STAGE=%s\n' "$STAGE"
  set -x
}

on_err() {
  rc=$?
  set +x
  mkdir -p "$FAIL"
  {
    echo "rc=$rc"
    echo "stage=$STAGE"
    date -u
    if [ -d "$KERNEL" ]; then
      echo '--- git status ---'
      git -C "$KERNEL" status --short || true
      echo '--- diff check ---'
      git -C "$KERNEL" diff --check || true
      echo '--- diff stat ---'
      git -C "$KERNEL" diff --stat || true
    fi
  } > "$FAIL/diagnostics.txt" 2>&1
  [ -d "$OUT" ] && cp -a "$OUT" "$FAIL/partial-out" 2>/dev/null || true
  for f in "$CTRL" "$HW" "$PHY" "$PHYV3" "$CTRLREG"; do
    [ -f "$f" ] && { mkdir -p "$FAIL/source"; cp "$f" "$FAIL/source/"; } || true
  done
  exit "$rc"
}
trap on_err ERR

chmod +x scripts/*.sh scripts/*.py

stage reconstruct-4.19.200
./scripts/01_prepare_source.sh
./scripts/03_apply_linux_4.19.153.sh
./scripts/04_apply_linux_4.19.154.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.154 4.19.159
./scripts/checkpoint_resolve_linux_4.19.159.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.159 4.19.164
./scripts/checkpoint_resolve_linux_4.19.164.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.164 4.19.180
./scripts/checkpoint_resolve_linux_4.19.180.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.180 4.19.200
./scripts/checkpoint_resolve_linux_4.19.200.sh

stage freeze-pre-observer
for f in "$CTRL" "$HW" "$PHY" "$PHYV3" "$CTRLREG"; do test -s "$f"; done
cp "$CTRL" "$OUT/audit/dsi_ctrl-before.c"
cp "$HW" "$OUT/audit/dsi_ctrl_hw_cmn-before.c"
cp "$PHY" "$OUT/audit/dsi_phy-before.c"
cp "$PHYV3" "$OUT/audit/dsi_phy_hw_v3_0-before.c"
cp "$CTRLREG" "$OUT/audit/dsi_ctrl_reg.h"
sha256sum "$PHYV3" "$CTRLREG" > "$OUT/audit/source-provenance.sha256"

stage source-provenance
python3 - "$CTRLREG" "$PHYV3" <<'PY'
from pathlib import Path
import re, sys

ctrl = Path(sys.argv[1]).read_text()
phy = Path(sys.argv[2]).read_text()

ctrl_expected = {
    'DSI_CTRL': 0x004, 'DSI_STATUS': 0x008, 'DSI_FIFO_STATUS': 0x00c,
    'DSI_VIDEO_MODE_CTRL': 0x010, 'DSI_VIDEO_MODE_DATA_CTRL': 0x020,
    'DSI_COMMAND_MODE_DMA_CTRL': 0x03c, 'DSI_COMMAND_MODE_MDP_CTRL': 0x040,
    'DSI_COMMAND_MODE_MDP_DCS_CMD_CTRL': 0x044, 'DSI_DMA_CMD_OFFSET': 0x048,
    'DSI_DMA_CMD_LENGTH': 0x04c, 'DSI_DMA_FIFO_CTRL': 0x050,
    'DSI_DMA_NULL_PACKET_DATA': 0x054, 'DSI_ACK_ERR_STATUS': 0x068,
    'DSI_TRIG_CTRL': 0x084, 'DSI_EXT_MUX': 0x088,
    'DSI_CMD_MODE_DMA_SW_TRIGGER': 0x090, 'DSI_LANE_STATUS': 0x0a8,
    'DSI_LANE_CTRL': 0x0ac, 'DSI_LANE_SWAP_CTRL': 0x0b0,
    'DSI_DLN0_PHY_ERR': 0x0b4, 'DSI_LP_TIMER_CTRL': 0x0b8,
    'DSI_HS_TIMER_CTRL': 0x0bc, 'DSI_TIMEOUT_STATUS': 0x0c0,
    'DSI_CLKOUT_TIMING_CTRL': 0x0c4, 'DSI_EOT_PACKET_CTRL': 0x0cc,
    'DSI_ERR_INT_MASK0': 0x10c, 'DSI_INT_CTRL': 0x110,
    'DSI_SOFT_RESET': 0x118, 'DSI_CLK_CTRL': 0x11c,
    'DSI_CLK_STATUS': 0x120, 'DSI_DEBUG_BUS_CTL': 0x124,
    'DSI_DEBUG_BUS_STATUS': 0x128, 'DSI_PHY_SW_RESET': 0x12c,
    'DSI_AXI2AHB_CTRL': 0x130, 'DSI_COMMAND_MODE_MDP_IDLE_CTRL': 0x194,
    'DSI_COMMAND_MODE_MDP_CTRL2': 0x1b8, 'DSI_DSI_TIMING_DB_MODE': 0x1e8,
    'DSI_DYNAMIC_REFRESH_CTRL': 0x200, 'DSI_DYNAMIC_REFRESH_STATUS': 0x210,
    'DSI_COMMAND_MODE_NULL_INSERTION_CTRL': 0x2b4,
    'DSI_READ_BACK_DISABLE_STATUS': 0x2b8, 'DSI_DESKEW_CTRL': 0x2bc,
    'DSI_DESKEW_DELAY_CTRL': 0x2c0, 'DSI_DEBUG_CTRL': 0x2c8,
    'DSI_SECURE_DISPLAY_STATUS': 0x2cc, 'DSI_CPHY_MODE_CTRL': 0x2d8,
    'DSI_LOGICAL_LANE_SWAP_CTRL': 0x310, 'DSI_SPLIT_LINK': 0x330,
}

def ctrl_macro(name):
    m = re.search(r'(?m)^#define\s+' + re.escape(name) + r'\s+\(0x([0-9A-Fa-f]+)\)', ctrl)
    if not m:
        raise SystemExit('Phase315G missing controller macro: ' + name)
    return int(m.group(1), 16)

for name, expected in ctrl_expected.items():
    got = ctrl_macro(name)
    if got != expected:
        raise SystemExit(f'Phase315G controller offset mismatch {name}: 0x{got:x} != 0x{expected:x}')

phy_expected = {
    'DSIPHY_CMN_CLK_CFG0': 0x010, 'DSIPHY_CMN_CLK_CFG1': 0x014,
    'DSIPHY_CMN_GLBL_CTRL': 0x018, 'DSIPHY_CMN_RBUF_CTRL': 0x01c,
    'DSIPHY_CMN_VREG_CTRL': 0x020, 'DSIPHY_CMN_CTRL_0': 0x024,
    'DSIPHY_CMN_CTRL_1': 0x028, 'DSIPHY_CMN_CTRL_2': 0x02c,
    'DSIPHY_CMN_LANE_CFG0': 0x030, 'DSIPHY_CMN_LANE_CFG1': 0x034,
    'DSIPHY_CMN_PLL_CNTRL': 0x038, 'DSIPHY_CMN_LANE_CTRL0': 0x098,
    'DSIPHY_CMN_LANE_CTRL1': 0x09c, 'DSIPHY_CMN_LANE_CTRL2': 0x0a0,
    'DSIPHY_CMN_LANE_CTRL3': 0x0a4, 'DSIPHY_CMN_LANE_CTRL4': 0x0a8,
    'DSIPHY_CMN_TIMING_CTRL_0': 0x0ac, 'DSIPHY_CMN_TIMING_CTRL_9': 0x0d0,
    'DSIPHY_CMN_TIMING_CTRL_11': 0x0d8, 'DSIPHY_CMN_PHY_STATUS': 0x0ec,
    'DSIPHY_CMN_LANE_STATUS0': 0x0f4, 'DSIPHY_CMN_LANE_STATUS1': 0x0f8,
}
for name, expected in phy_expected.items():
    m = re.search(r'(?m)^#define\s+' + re.escape(name) + r'\s+0x([0-9A-Fa-f]+)', phy)
    if not m:
        raise SystemExit('Phase315G missing PHY macro: ' + name)
    got = int(m.group(1), 16)
    if got != expected:
        raise SystemExit(f'Phase315G PHY offset mismatch {name}: 0x{got:x} != 0x{expected:x}')

for token in (
    '#define DSIPHY_LNX_CFG0(n)                         (0x200 + (0x80 * (n)))',
    '#define DSIPHY_LNX_TEST_DATAPATH(n)                (0x210 + (0x80 * (n)))',
    '#define DSIPHY_LNX_LPRX_CTRL(n)                    (0x228 + (0x80 * (n)))',
    '#define DSIPHY_LNX_TX_DCTRL(n)                     (0x22C + (0x80 * (n)))',
):
    if token not in phy:
        raise SystemExit('Phase315G lane-offset provenance missing: ' + token)

print('Phase315G controller/PHY offset provenance: PASS')
PY

stage apply-observer
python3 -m py_compile scripts/315g_apply_golden_f0_full_prestate_reference.py
python3 scripts/315g_apply_golden_f0_full_prestate_reference.py --root "$KERNEL"
python3 scripts/315g_apply_golden_f0_full_prestate_reference.py --root "$KERNEL" --check-only
git -C "$KERNEL" diff --check

stage read-only-scope-audit
python3 - <<'PY'
from pathlib import Path

out = Path('phase315-golden-out/audit')
k = Path('workspace/touchgrass-a52xq/techpack/display/msm/dsi')
pairs = [
    (out/'dsi_ctrl-before.c', k/'dsi_ctrl.c'),
    (out/'dsi_ctrl_hw_cmn-before.c', k/'dsi_ctrl_hw_cmn.c'),
    (out/'dsi_phy-before.c', k/'dsi_phy.c'),
]
protected = [
    'DSI_W32(', 'DSI_DISP_CC_W32(', 'DSI_MMSS_MISC_W32(', 'DSI_MISC_W32(',
    'writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
    'wmb(', 'mb(', 'rmb(', 'readl_poll_timeout',
    'wait_for_completion_timeout(', 'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(', 'clk_disable_unprepare(',
    'clk_prepare(', 'clk_unprepare(', 'clk_enable(', 'clk_disable(',
    'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
]
for before_path, after_path in pairs:
    before = before_path.read_text()
    after = after_path.read_text()
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f'Phase315G read-only violation {after_path.name}: '
                f'{token} {before.count(token)} -> {after.count(token)}'
            )

combined_before = ''.join(p[0].read_text() for p in pairs)
combined_after = ''.join(p[1].read_text() for p in pairs)
if combined_after.count('readl_relaxed(') <= combined_before.count('readl_relaxed('):
    raise SystemExit('Phase315G expected read-only MMIO coverage to increase')
if combined_after.count('pr_info(') <= combined_before.count('pr_info('):
    raise SystemExit('Phase315G expected logging coverage to increase')
print('Phase315G read-only/no-establishment audit: PASS')
PY

stage resukisu-hook
./scripts/07_patch_resukisu_exec_hook.sh

stage kernel-build
set -o pipefail
./scripts/08_build_resukisu_safe_checkpoint.sh 4.19.200 2>&1 | tee "$OUT/golden-build.log"

stage image-audit
IMAGE="$(find artifacts -maxdepth 1 -type f -name 'Image-touchgrass-4.19.200-resukisu-v4.1.0-safe' -print -quit)"
CONFIG="$(find artifacts -maxdepth 1 -type f -name 'config-touchgrass-4.19.200-resukisu-v4.1.0-safe' -print -quit)"
test -n "$IMAGE" -a -s "$IMAGE" -a -n "$CONFIG" -a -s "$CONFIG"
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp "$CTRL" "$HW" "$PHY" "$PHYV3" "$CTRLREG" "$OUT/source/"
cp scripts/315g_apply_golden_f0_full_prestate_reference.py "$OUT/audit/"

strings "$OUT/Image" > "$OUT/Image.strings.txt"
for marker in \
  'TG315 ARM c=0' \
  'TG315 C0 %x %x %x %x %x %x' \
  'TG315 C7 %x %x %x %x %x %x' \
  'TG315 M %x' \
  'TG315 P0 %x %x %x %x %x %x' \
  'TG315 P4 %x %x %x %x %x' \
  'TG315 T0 %x %x %x %x %x %x' \
  'TG315 T1 %x %x %x %x %x %x' \
  'TG315 L%uA' \
  'TG315 L%uB' \
  'TG315 Q%u st=%x ln=%x ck=%x in=%x' \
  'TG315 DONE ret=%d irq=%d'; do
  grep -Fq "$marker" "$OUT/Image.strings.txt"
done
grep -Fq 'Linux version 4.19.200-touchGrassKernel+' "$OUT/Image.strings.txt"

stage identity
cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=PHASE315G-GOLDEN-F0-FULL-PRESTATE-REFERENCE-V1
behavior_change=none-read-only-exact-F05A5A-only
touchgrass_base=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
target=controller0-FETCH_MEMORY-msgflags0x8-type0x29-len3-F05A5A
full_prestate_point=q0-before-sw-trigger
launch_points=q1-immediately-after-trigger,q2-after-normal-completion-wait
coverage=controller-C0-C7,dispcc-MISC_CMD,phy-common,timing0-11,all-lane-config,TX_DCTRL
mmio_writes_added=no
clock_regulator_reset_timeout_changes=no
flashable=pending-known-good-96MiB-container-repack
EOF

sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
set +x
echo 'Phase315G Golden full first-F0 prestate observer build: PASS'
