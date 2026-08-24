#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
OUT="$ROOT/phase308g-golden-out"
FAIL="$ROOT/phase308g-golden-failure"
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
  printf 'PHASE308G_STAGE=%s\n' "$STAGE"
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
      git -C "$KERNEL" status --short || true
      git -C "$KERNEL" diff --check || true
      git -C "$KERNEL" diff --stat || true
    fi
  } > "$FAIL/diagnostics.txt" 2>&1
  [ -d "$OUT" ] && cp -a "$OUT" "$FAIL/partial-out" 2>/dev/null || true
  for f in "$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c" \
           "$KERNEL/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c" \
           "$KERNEL/techpack/display/msm/dsi/dsi_phy.c" \
           "$KERNEL/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c" \
           "$KERNEL/techpack/display/pll/pll_drv.c" \
           "$KERNEL/techpack/display/pll/dsi_pll_10nm.c"; do
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
CTRL="$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"
HW="$KERNEL/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$KERNEL/techpack/display/msm/dsi/dsi_phy.c"
PHYV3="$KERNEL/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
PLLDRV="$KERNEL/techpack/display/pll/pll_drv.c"
PLL10="$KERNEL/techpack/display/pll/dsi_pll_10nm.c"
for f in "$CTRL" "$HW" "$PHY" "$PHYV3" "$PLLDRV" "$PLL10"; do test -s "$f"; done
cp "$CTRL" "$OUT/audit/dsi_ctrl-before.c"
cp "$HW" "$OUT/audit/dsi_ctrl_hw_cmn-before.c"
cp "$PHY" "$OUT/audit/dsi_phy-before.c"
cp "$PHYV3" "$OUT/audit/dsi_phy_hw_v3_0.c"
cp "$PLLDRV" "$OUT/audit/pll_drv-before.c"
cp "$PLL10" "$OUT/audit/dsi_pll_10nm-before.c"
sha256sum "$PHYV3" "$PLLDRV" "$PLL10" > "$OUT/audit/golden-source.sha256"

grep -Eq '^#define[[:space:]]+DSIPHY_LNX_TX_DCTRL\(n\)[[:space:]]+\(0x22C[[:space:]]+\+[[:space:]]+\(0x80[[:space:]]+\*[[:space:]]+\(n\)\)\)' "$PHYV3"
grep -Eq '^#define[[:space:]]+PLL_COMMON_STATUS_ONE[[:space:]]+0x1[aA]0' "$PLL10"
grep -Eq '^#define[[:space:]]+PLL_PLL_OUTDIV_RATE[[:space:]]+0x140' "$PLL10"
grep -Eq '^#define[[:space:]]+PLL_SYSTEM_MUXES[[:space:]]+0x024' "$PLL10"
grep -Fq '((status & BIT(0)) > 0)' "$PLL10"

stage apply-phase307g-observer
python3 -m py_compile scripts/307g_apply_golden_v3_phy_clocklane_reference.py
python3 scripts/307g_apply_golden_v3_phy_clocklane_reference.py --root "$KERNEL"
python3 scripts/307g_apply_golden_v3_phy_clocklane_reference.py --root "$KERNEL" --check-only
cp "$PHY" "$OUT/audit/dsi_phy-after-307g.c"
cp "$PLLDRV" "$OUT/audit/pll_drv-after-307g.c"

stage apply-phase308g-observer
python3 -m py_compile scripts/308g_apply_golden_pll_lock_clamp_reference.py
python3 scripts/308g_apply_golden_pll_lock_clamp_reference.py --root "$KERNEL"
python3 scripts/308g_apply_golden_pll_lock_clamp_reference.py --root "$KERNEL" --check-only
git -C "$KERNEL" diff --check

stage observer-scope-audit
python3 - <<'PY'
from pathlib import Path
out=Path('phase308g-golden-out/audit')
k=Path('workspace/touchgrass-a52xq')
pairs307=[
 (out/'dsi_ctrl-before.c', k/'techpack/display/msm/dsi/dsi_ctrl.c'),
 (out/'dsi_ctrl_hw_cmn-before.c', k/'techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c'),
]
pairs308=[
 (out/'dsi_phy-after-307g.c', k/'techpack/display/msm/dsi/dsi_phy.c'),
 (out/'pll_drv-after-307g.c', k/'techpack/display/pll/pll_drv.c'),
]
protected=['DSI_W32(','MDSS_PLL_REG_W(','writel_relaxed(','writel(','clk_set_rate(',
 'clk_prepare_enable(','clk_disable_unprepare(','regulator_enable(','regulator_disable(',
 'msleep(','usleep_range(','udelay(','ndelay(','mdss_pll_resource_enable(']
for a,b in pairs307:
    x=a.read_text(); y=b.read_text()
    for t in protected:
        if x.count(t)!=y.count(t):
            raise SystemExit(f'Phase308G inherited observer scope violation {b.name}: {t} {x.count(t)} -> {y.count(t)}')
for a,b in pairs308:
    x=a.read_text(); y=b.read_text()
    for t in protected:
        if x.count(t)!=y.count(t):
            raise SystemExit(f'Phase308G observer scope violation {b.name}: {t} {x.count(t)} -> {y.count(t)}')
print('Phase308G observer-only hardware primitive audit: PASS')
PY
cmp -s "$OUT/audit/dsi_phy_hw_v3_0.c" "$PHYV3"
cmp -s "$OUT/audit/dsi_pll_10nm-before.c" "$PLL10"

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
cp "$CTRL" "$HW" "$PHY" "$PHYV3" "$PLLDRV" "$PLL10" "$OUT/source/"
cp scripts/307g_apply_golden_v3_phy_clocklane_reference.py "$OUT/audit/"
cp scripts/308g_apply_golden_pll_lock_clamp_reference.py "$OUT/audit/"

strings "$OUT/Image" > "$OUT/Image.strings.txt"
for marker in \
 'TG307 ARM c=0' 'TG307 C q=%u' 'TG307 C q=2' \
 'TG307 P0 q=%u' 'TG307 P1 q=%u' 'TG307 P2 q=%u' 'TG307 P3 q=%u' \
 'TG308 R i=%u p=1' \
 'TG308 L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x' \
 'TG308 V q=%u vc=%lld ca=%lu c0=%x c1=%x od=%x' \
 'TG308 M q=%u m=%x o=%x c0=%x c1=%x rb=%x' \
 'TG308 T q=%u %x %x %x %x %x ce=%d cr=%d' \
 'TG308 K i=%u e=%u ce=%d cr=%d'; do
  grep -Fq "$marker" "$OUT/Image.strings.txt"
done
grep -Fq 'Linux version 4.19.200-touchGrassKernel+' "$OUT/Image.strings.txt"

stage identity
cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=PHASE308G-GOLDEN-TOUCHGRASS-PLL-LOCK-CLAMP-REFERENCE-V1
behavior_change=none-read-only-exact-F05A5A-plus-software-clamp-latch
touchgrass_base=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
base_observer=Phase307G-exact-F05A5A-v3-PHY-clocklane
points=q0-before-sw-trigger,q1-after-sw-trigger,q2-after-dma-completion
pll_lock=PLL_COMMON_STATUS_ONE-0x1a0-bit0
clamp_latch=software-only-count-after-real-clamp_ctrl-return
mmio_writes_added=no
clock_regulator_reset_timeout_changes=no
flashable=pending-known-good-96MiB-container-repack
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
set +x
echo 'Phase308G Golden TouchGrass PLL lock/handoff + clamp-latch observer build: PASS'
