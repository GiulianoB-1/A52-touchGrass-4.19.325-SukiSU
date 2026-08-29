#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase319-gki-out"
FAIL="$PWD/phase319-gki-failure"
DSI="$ROOT/drivers/a52_display/msm/dsi"
CTRL="$DSI/dsi_ctrl.c"
HWC="$DSI/dsi_ctrl_hw_cmn.c"
PHY="$DSI/dsi_phy.c"
PHYV3="$DSI/dsi_phy_hw_v3_0.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
STAGE=startup

stage() { STAGE="$1"; echo "== Phase319 GKI stage: $STAGE =="; }
fail_report() {
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase319-gki-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p319gki-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/319_apply_dsi_sixpoint_temporal_observer.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase316"
bash scripts/316_ci_build.sh 2>&1 | tee phase319-gki-phase316.log
for f in phase316-gki-out/package/boot.img phase316-gki-out/compile/Image \
  phase316-gki-out/config/final.config "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP"; do test -s "$f"; done
grep -Fq 'A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1' "$HWC"
grep -Fq 'P276 316S q=2' "$CTRL"
! grep -Fq 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' "$CTRL"
! grep -Fq 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' "$HWC"
cp "$CTRL" /tmp/p319gki-ctrl-before.c
cp "$HWC" /tmp/p319gki-hwc-before.c
cp "$PHY" /tmp/p319gki-phy-before.c
cp "$PHYV3" /tmp/p319gki-phyv3-before.c
cp "$DISP" /tmp/p319gki-disp-before.c

stage "apply six-selector q0/q1/q2 temporal observer"
python3 -m py_compile scripts/319_apply_dsi_sixpoint_temporal_observer.py
python3 scripts/319_apply_dsi_sixpoint_temporal_observer.py --root "$ROOT" --flavor gki
python3 scripts/319_apply_dsi_sixpoint_temporal_observer.py --root "$ROOT" --flavor gki --check-only
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl.c drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
cp "$CTRL" /tmp/p319gki-ctrl-after.c
cp "$HWC" /tmp/p319gki-hwc-after.c
diff -u /tmp/p319gki-ctrl-before.c /tmp/p319gki-ctrl-after.c > /tmp/p319gki-ctrl.diff || true
diff -u /tmp/p319gki-hwc-before.c /tmp/p319gki-hwc-after.c > /tmp/p319gki-hwc.diff || true
cmp -s /tmp/p319gki-phy-before.c "$PHY"
cmp -s /tmp/p319gki-phyv3-before.c "$PHYV3"
cmp -s /tmp/p319gki-disp-before.c "$DISP"

stage "strict scope audit"
python3 - <<'PY'
from pathlib import Path
bctrl=Path('/tmp/p319gki-ctrl-before.c').read_text(); actrl=Path('/tmp/p319gki-ctrl-after.c').read_text()
bhw=Path('/tmp/p319gki-hwc-before.c').read_text(); ahw=Path('/tmp/p319gki-hwc-after.c').read_text()

if actrl.count('DSI_W32(') != bctrl.count('DSI_W32(') or actrl.count('wmb(') != bctrl.count('wmb('):
    raise SystemExit('Phase319 GKI dsi_ctrl.c added write/barrier')
if ahw.count('DSI_W32(') - bhw.count('DSI_W32(') != 2:
    raise SystemExit('Phase319 GKI expected exactly two new DSI_W32 call sites')
if ahw.count('wmb(') - bhw.count('wmb(') != 2:
    raise SystemExit('Phase319 GKI expected exactly two new wmb call sites')

for token in ('writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
              'readl_poll_timeout', 'wait_for_completion_timeout(', 'udelay(', 'ndelay(',
              'usleep_range(', 'msleep(', 'clk_set_rate(', 'clk_set_parent(',
              'clk_prepare_enable(', 'clk_disable_unprepare(', 'regulator_enable(',
              'regulator_disable(', 'reset_control_assert(', 'reset_control_deassert('):
    if ahw.count(token) != bhw.count(token) or actrl.count(token) != bctrl.count(token):
        raise SystemExit('Phase319 GKI forbidden functional delta: ' + token)

added=[x[1:] for x in Path('/tmp/p319gki-hwc.diff').read_text().splitlines()
       if x.startswith('+') and not x.startswith('+++')]
for line in added:
    if 'DSI_W32(' in line and 'DSI_DEBUG_BUS_CTL' not in line:
        raise SystemExit('Phase319 GKI added non-debugbus DSI_W32: ' + line)

combined=actrl+ahw
for token in (
    'A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1',
    '0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211',
    'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x',
    'a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);',
):
    if token not in combined:
        raise SystemExit('Phase319 GKI marker missing: ' + token)
if ahw.count('a52_p319_debugbus_snapshot(ctrl, 0);') != 2:
    raise SystemExit('Phase319 GKI q0 hook count is not exactly two trigger paths')
if ahw.count('a52_p319_debugbus_snapshot(ctrl, 1);') != 2:
    raise SystemExit('Phase319 GKI q1 hook count is not exactly two trigger paths')
if 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' in combined:
    raise SystemExit('Phase319 GKI inherited forbidden Phase317 full sweep')
print('Phase319 GKI six-selector temporal scope audit: PASS')
PY

stage "config invariant"
cp phase316-gki-out/config/final.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase319-gki-olddefconfig.log 2>&1
cmp -s phase316-gki-out/config/final.config "$BUILD/.config"

stage "compile Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase319-gki-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for marker in \
  'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x' \
  'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d'; do
  grep -aFq "$marker" "$IMAGE"
done
! grep -aFq 'P276 317B b=%u t=%u %x %x %x %x' "$IMAGE"

stage "assemble evidence and repack"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase319-gki-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/319_apply_dsi_sixpoint_temporal_observer.py "$OUT/audit/"
cp /tmp/p319gki-* "$OUT/audit/" 2>/dev/null || true
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
r=Path('phase319-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn={
 'phase':'319','flavor':'gki','name':'DSI-SIXPOINT-TEMPORAL-OBSERVER-V1',
 'git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,
 'base':'exact successful Phase316 reconstruction',
 'target':'controller0 exact F0 5A 5A',
 'hooks':['q0 immediately before SW_TRIGGER','q1 immediately after SW_TRIGGER','q2 after completion outcome'],
 'selectors':['0x0171','0x0181','0x0191','0x01a1','0x01e1','0x0211'],
 'phase317_basis':'six selectors are members of the measured Golden/GKI Phase317 q2 raw-delta set',
 'writes':'DSI_DEBUG_BUS_CTL selector only plus exact original-selector restore at each snapshot',
 'runtime_selector_writes_per_snapshot':7,
 'functional_clock_phy_reset_regulator_delay_retry_changes':'none',
 'phase317_full_sweep_inherited':False,
 'image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),
 'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase319 GKI six-selector q0/q1/q2 temporal observer: PASS'
