#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
OUT="$ROOT/phase307-golden-out"
FAIL="$ROOT/phase307-golden-failure"
rm -rf "$OUT" "$FAIL"
mkdir -p "$OUT"/{audit,source,package} "$FAIL"

on_err() {
  rc=$?
  mkdir -p "$FAIL"
  { echo "rc=$rc"; date -u; [ -d "$KERNEL" ] && git -C "$KERNEL" diff --check || true; } > "$FAIL/diagnostics.txt" 2>&1
  exit "$rc"
}
trap on_err ERR

chmod +x scripts/*.sh scripts/*.py

# Exact reviewed TouchGrass 4.19.200 reconstruction used by the known-good control.
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

CTRL="$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"
HW="$KERNEL/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$KERNEL/techpack/display/msm/dsi/dsi_phy.c"
PHYV3="$KERNEL/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
for f in "$CTRL" "$HW" "$PHY" "$PHYV3"; do test -s "$f"; done
cp "$CTRL" "$OUT/audit/dsi_ctrl-before.c"
cp "$HW" "$OUT/audit/dsi_ctrl_hw_cmn-before.c"
cp "$PHY" "$OUT/audit/dsi_phy-before.c"
cp "$PHYV3" "$OUT/audit/dsi_phy_hw_v3_0.c"
sha256sum "$PHYV3" > "$OUT/audit/touchgrass-v3-phy-source.sha256"

python3 -m py_compile scripts/307g_apply_golden_v3_phy_clocklane_reference.py
python3 scripts/307g_apply_golden_v3_phy_clocklane_reference.py --root "$KERNEL"
python3 scripts/307g_apply_golden_v3_phy_clocklane_reference.py --root "$KERNEL" --check-only

git -C "$KERNEL" diff --check
python3 - <<'PY'
from pathlib import Path
out=Path('phase307-golden-out/audit')
k=Path('workspace/touchgrass-a52xq/techpack/display/msm/dsi')
pairs=[(out/'dsi_ctrl-before.c',k/'dsi_ctrl.c'),(out/'dsi_ctrl_hw_cmn-before.c',k/'dsi_ctrl_hw_cmn.c'),(out/'dsi_phy-before.c',k/'dsi_phy.c')]
protected=['DSI_W32(','writel_relaxed(','writel(','clk_set_rate(','clk_prepare_enable(','clk_disable_unprepare(','regulator_enable(','regulator_disable(','msleep(','usleep_range(','udelay(']
for a,b in pairs:
    x=a.read_text(); y=b.read_text()
    for t in protected:
        if x.count(t)!=y.count(t):
            raise SystemExit(f'Phase307G observer scope violation {b.name}: {t} {x.count(t)} -> {y.count(t)}')
print('Phase307G observer-only primitive audit: PASS')
PY
# Programming implementation itself is untouched.
cmp -s "$OUT/audit/dsi_phy_hw_v3_0.c" "$PHYV3"

./scripts/07_patch_resukisu_exec_hook.sh
set -o pipefail
./scripts/08_build_resukisu_safe_checkpoint.sh 4.19.200 2>&1 | tee "$OUT/golden-build.log"
IMAGE="$(find artifacts -maxdepth 1 -type f -name 'Image-touchgrass-4.19.200-resukisu-v4.1.0-safe' -print -quit)"
CONFIG="$(find artifacts -maxdepth 1 -type f -name 'config-touchgrass-4.19.200-resukisu-v4.1.0-safe' -print -quit)"
test -n "$IMAGE" -a -s "$IMAGE" -a -n "$CONFIG" -a -s "$CONFIG"
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp "$CTRL" "$HW" "$PHY" "$PHYV3" "$OUT/source/"
cp scripts/307g_apply_golden_v3_phy_clocklane_reference.py "$OUT/audit/"

strings "$OUT/Image" > "$OUT/Image.strings.txt"
for marker in 'TG307 ARM c=0' 'TG307 C q=%u' 'TG307 C q=2' 'TG307 P0 q=%u' 'TG307 P1 q=%u' 'TG307 P2 q=%u' 'TG307 P3 q=%u'; do
  grep -Fq "$marker" "$OUT/Image.strings.txt"
done
grep -Fq 'Linux version 4.19.200-touchGrassKernel+' "$OUT/Image.strings.txt"

cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=PHASE307-GOLDEN-TOUCHGRASS-V3-PHY-CLOCKLANE-REFERENCE-V1
behavior_change=none-read-only-exact-F05A5A-only
touchgrass_base=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
phy=DSI_PHY_VERSION_3_0-10nm
points=q0-before-sw-trigger,q1-after-sw-trigger,q2-after-dma-completion
mmio_writes_added=no
clock_regulator_reset_timeout_changes=no
flashable=pending-known-good-96MiB-container-repack
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
echo 'Phase307 Golden TouchGrass v3 PHY/clock-lane observer build: PASS'
