#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase317-gki-out"
FAIL="$PWD/phase317-gki-failure"
DSI="$ROOT/drivers/a52_display/msm/dsi"
CTRL="$DSI/dsi_ctrl.c"
HWC="$DSI/dsi_ctrl_hw_cmn.c"
PHY="$DSI/dsi_phy.c"
PHYV3="$DSI/dsi_phy_hw_v3_0.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
STAGE=startup

stage() { STAGE="$1"; echo "== Phase317 GKI stage: $STAGE =="; }
fail_report() {
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase317-gki-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p317gki-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/317_apply_dsi_internal_debugbus_delta.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase316"
bash scripts/316_ci_build.sh 2>&1 | tee phase317-gki-phase316.log
for f in phase316-gki-out/package/boot.img phase316-gki-out/compile/Image \
  phase316-gki-out/config/final.config "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP"; do test -s "$f"; done
grep -Fq 'A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1' "$HWC"
grep -Fq 'P276 316S q=2' "$CTRL"
cp "$CTRL" /tmp/p317gki-ctrl-before.c
cp "$HWC" /tmp/p317gki-hwc-before.c
cp "$PHY" /tmp/p317gki-phy-before.c
cp "$PHYV3" /tmp/p317gki-phyv3-before.c
cp "$DISP" /tmp/p317gki-disp-before.c

stage "apply q2-only matched debug-bus observer"
python3 -m py_compile scripts/317_apply_dsi_internal_debugbus_delta.py
python3 scripts/317_apply_dsi_internal_debugbus_delta.py --root "$DSI" --flavor gki
python3 scripts/317_apply_dsi_internal_debugbus_delta.py --root "$DSI" --flavor gki --check-only
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl.c drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
cp "$CTRL" /tmp/p317gki-ctrl-after.c
cp "$HWC" /tmp/p317gki-hwc-after.c
diff -u /tmp/p317gki-ctrl-before.c /tmp/p317gki-ctrl-after.c > /tmp/p317gki-ctrl.diff || true
diff -u /tmp/p317gki-hwc-before.c /tmp/p317gki-hwc-after.c > /tmp/p317gki-hwc.diff || true
cmp -s /tmp/p317gki-phy-before.c "$PHY"
cmp -s /tmp/p317gki-phyv3-before.c "$PHYV3"
cmp -s /tmp/p317gki-disp-before.c "$DISP"

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
bctrl=Path('/tmp/p317gki-ctrl-before.c').read_text(); actrl=Path('/tmp/p317gki-ctrl-after.c').read_text()
bhw=Path('/tmp/p317gki-hwc-before.c').read_text(); ahw=Path('/tmp/p317gki-hwc-after.c').read_text()
if actrl.count('DSI_W32(') != bctrl.count('DSI_W32(') or actrl.count('wmb(') != bctrl.count('wmb('):
    raise SystemExit('Phase317 GKI dsi_ctrl.c unexpectedly adds MMIO writes/barriers')
if ahw.count('DSI_W32(') - bhw.count('DSI_W32(') != 2:
    raise SystemExit('Phase317 GKI expected exactly two new DSI_W32 call sites')
if ahw.count('wmb(') - bhw.count('wmb(') != 2:
    raise SystemExit('Phase317 GKI expected exactly two new wmb call sites')
for token in ('writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
              'readl_poll_timeout', 'wait_for_completion_timeout(', 'udelay(', 'ndelay(',
              'usleep_range(', 'msleep(', 'clk_set_rate(', 'clk_set_parent(',
              'clk_prepare_enable(', 'clk_disable_unprepare(', 'regulator_enable(',
              'regulator_disable(', 'reset_control_assert(', 'reset_control_deassert('):
    if ahw.count(token) != bhw.count(token) or actrl.count(token) != bctrl.count(token):
        raise SystemExit('Phase317 GKI forbidden functional delta: '+token)
added=[x[1:] for x in Path('/tmp/p317gki-hwc.diff').read_text().splitlines()
       if x.startswith('+') and not x.startswith('+++')]
for line in added:
    if 'DSI_W32(' in line and 'DSI_DEBUG_BUS_CTL' not in line:
        raise SystemExit('Phase317 GKI added non-debugbus DSI_W32: '+line)
for token in ('A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1',
              'P276 317H ctl=%x','P276 317B b=%u t=%u %x %x %x %x','P276 317Z ctl=%x st=%x'):
    if token not in actrl+ahw: raise SystemExit('Phase317 GKI marker missing: '+token)
print('Phase317 GKI scope audit: PASS')
PY

stage "config invariant"
cp phase316-gki-out/config/final.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase317-gki-olddefconfig.log 2>&1
cmp -s phase316-gki-out/config/final.config "$BUILD/.config"

stage "compile Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase317-gki-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for marker in 'P276 317H ctl=%x' 'P276 317B b=%u t=%u %x %x %x %x' 'P276 317Z ctl=%x st=%x' \
              'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d'; do grep -aFq "$marker" "$IMAGE"; done

stage "assemble evidence and repack"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase317-gki-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/317_apply_dsi_internal_debugbus_delta.py "$OUT/audit/"
cp /tmp/p317gki-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase316-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase317-gki-out')
def sha(p):
 h=hashlib.sha256();h.update(p.read_bytes());return h.hexdigest()
idn={'phase':'317','flavor':'gki','name':'DSI-INTERNAL-DEBUGBUS-DELTA-V1',
 'git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,
 'base':'exact successful Phase316 reconstruction','hook':'q2 after exact F0 completion wait outcome',
 'selector':'((block&3)<<12)|((test&0x3f)<<4)|BIT(0)','space':'4 blocks x 64 test points',
 'writes':'DSI_DEBUG_BUS_CTL selector only plus exact original-selector restore; post-outcome only',
 'functional_clock_phy_reset_regulator_changes':'none',
 'image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),
 'boot_img_size':(r/'package/boot.img').stat().st_size}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase317 GKI matched q2-only internal DSI debug-bus observer: PASS'
