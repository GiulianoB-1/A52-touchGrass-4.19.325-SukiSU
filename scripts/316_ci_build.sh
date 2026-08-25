#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase316-gki-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
STAGE="startup"

stage() {
  STAGE="$1"
  echo "== Phase316 stage: $STAGE =="
}

fail_report() {
  set +e
  rm -rf phase316-gki-failure
  mkdir -p phase316-gki-failure/{logs,audit,source,nested,compile,package-meta}
  printf '%s\n' "$STAGE" > phase316-gki-failure/FAILED-STAGE.txt
  cp phase316-gki-compile.log phase316-gki-olddefconfig.log phase316-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p316-* phase316-gki-failure/audit/ 2>/dev/null || true
  cp scripts/316_apply_f0_launch_fault_window_recorder.py phase316-gki-failure/audit/ 2>/dev/null || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" phase316-gki-failure/compile/Image || true
  [ -f "$OUT/BUILD-IDENTITY.json" ] && cp "$OUT/BUILD-IDENTITY.json" phase316-gki-failure/package-meta/ || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP"; do
    [ -f "$f" ] && cp "$f" phase316-gki-failure/source/ || true
  done
  for d in phase*-gki-failure; do
    [ -d "$d" ] || continue
    [ "$d" = phase316-gki-failure ] && continue
    cp -a "$d" phase316-gki-failure/nested/ 2>/dev/null || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase314"
set +e
bash scripts/314_ci_build.sh 2>&1 | tee /tmp/p316-phase314.log
rc314=${PIPESTATUS[0]}
set -e
if [ "$rc314" -ne 0 ]; then
  echo "ERROR: Phase314 reconstruction failed rc=$rc314" >&2
  exit "$rc314"
fi

for f in \
  phase314-gki-out/package/boot.img \
  phase314-gki-out/compile/Image \
  phase314-gki-out/config/final.config \
  "$CTRL" "$HWC" "$PHY" "$PHYV3" "$DISP"; do
  test -s "$f"
done
test "$(stat -c '%s' phase314-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE314_GKI_F0_FULL_CTRL_PRESTATE_RECORDER_V1' "$CTRL"
grep -Fq 'A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1' "$HWC"
grep -Fq 'A52_PHASE313_V3_TIMING9_HANDOFF_REPAIR_AB_V1' "$PHYV3"
grep -Fq 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1' "$PHYV3"

cp phase314-gki-out/config/final.config /tmp/p316-phase314.config
cp "$CTRL" /tmp/p316-ctrl-before.c
cp "$HWC" /tmp/p316-hwc-before.c
cp "$PHYV3" /tmp/p316-phyv3-before.c
cp "$PHY" /tmp/p316-phy-before.c
cp "$DISP" /tmp/p316-disp-before.c

stage "apply Golden-parity baseline + fault recorder"
python3 -m py_compile scripts/316_apply_f0_launch_fault_window_recorder.py
python3 scripts/316_apply_f0_launch_fault_window_recorder.py --root "$ROOT"
python3 scripts/316_apply_f0_launch_fault_window_recorder.py --root "$ROOT" --check-only

cp "$CTRL" /tmp/p316-ctrl-after.c
cp "$HWC" /tmp/p316-hwc-after.c
cp "$PHYV3" /tmp/p316-phyv3-after.c
diff -u /tmp/p316-ctrl-before.c /tmp/p316-ctrl-after.c > /tmp/p316-ctrl.diff || true
diff -u /tmp/p316-hwc-before.c /tmp/p316-hwc-after.c > /tmp/p316-hwc.diff || true
diff -u /tmp/p316-phyv3-before.c /tmp/p316-phyv3-after.c > /tmp/p316-phyv3.diff || true
cmp -s /tmp/p316-phy-before.c "$PHY"
cmp -s /tmp/p316-disp-before.c "$DISP"

stage "scope audit"
python3 - "$CTRL" "$HWC" "$PHYV3" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path('/tmp/p316-ctrl-before.c').read_text(), Path(sys.argv[1]).read_text(), 'dsi_ctrl.c'),
    (Path('/tmp/p316-hwc-before.c').read_text(), Path(sys.argv[2]).read_text(), 'dsi_ctrl_hw_cmn.c'),
    (Path('/tmp/p316-phyv3-before.c').read_text(), Path(sys.argv[3]).read_text(), 'dsi_phy_hw_v3_0.c'),
]

# Baseline correction: Golden proved the two earlier source-default A/B writes
# are not Golden splash-handoff parity. TIMING9=0x12 and DCTRL3=0x00 are the
# successful inherited values, so Phase316 intentionally removes those two
# experimental effects before adding observation-only fault-window records.
b0, a0 = pairs[2][0], pairs[2][1]
if b0.count('DSI_W32(') - a0.count('DSI_W32(') != 1:
    raise SystemExit('Phase316 expected exactly one DSI_W32 removal (TIMING9)')
if 'DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);' in a0:
    raise SystemExit('Phase316 Phase313 TIMING9 repair still active')
pos = a0.index('A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1')
if '\treg |= BIT(2);\n' in a0[pos:pos+1400]:
    raise SystemExit('Phase316 Phase311 DCTRL3 repair still active')

# The new recorder itself may only add reads/records. No new write, barrier,
# delay, clock, reset or regulator operation is allowed in CTRL/HWC.
protected = [
    'DSI_W32(', 'writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
    'wmb(', 'mb(', 'rmb(', 'readl_poll_timeout', 'wait_for_completion_timeout(',
    'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(', 'clk_disable_unprepare(',
    'clk_prepare(', 'clk_unprepare(', 'clk_enable(', 'clk_disable(',
    'regulator_enable(', 'regulator_disable(', 'reset_control_assert(', 'reset_control_deassert(',
]
for before, after, label in pairs[:2]:
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(f'Phase316 recorder scope violation {label}: {token} {before.count(token)} -> {after.count(token)}')

before_reads = sum(x.count('DSI_R32(') + x.count('DSI_DISP_CC_R32(') for x,_,_ in pairs)
after_reads = sum(y.count('DSI_R32(') + y.count('DSI_DISP_CC_R32(') for _,y,_ in pairs)
if after_reads <= before_reads:
    raise SystemExit('Phase316 expected MMIO read coverage to increase')

all_after = ''.join(y for _,y,_ in pairs)
for marker in (
    'A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1',
    'P276 316C q=%u st=%x fs=%x ck=%x ln=%x in=%x em=%x',
    'P276 316M q=%u m=%x b0=%u b5=%u b7=%u b9=%u',
    'P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u',
    'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d',
):
    if marker not in all_after:
        raise SystemExit('Phase316 marker missing: ' + marker)
print('Phase316 Golden-parity + read-only fault-window scope audit: PASS')
PY

stage "config invariant"
cp /tmp/p316-phase314.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase316-gki-olddefconfig.log 2>&1
cmp -s /tmp/p316-phase314.config "$BUILD/.config"

stage "compile Phase316 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase316-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase316-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi
IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

require_image_marker() {
  local marker="$1" label="$2"
  printf 'Phase316 Image audit %-8s : ' "$label"
  if grep -aFq -- "$marker" "$IMAGE"; then echo PASS; else
    echo FAIL
    echo "::error::Phase316 Image audit missing $label" >&2
    printf '%s\n' "$marker" > /tmp/p316-missing-image-marker.txt
    return 1
  fi
}

stage "Image marker audit"
require_image_marker 'P276 316C q=%u st=%x fs=%x ck=%x ln=%x in=%x em=%x' '316C'
require_image_marker 'P276 316E q=%u ack=%x to=%x pe=%x ct=%x cc=%x lc=%x' '316E'
require_image_marker 'P276 316D q=%u dc=%x df=%x of=%x le=%x sw=%x tr=%x' '316D'
require_image_marker 'P276 316M q=%u m=%x b0=%u b5=%u b7=%u b9=%u' '316M'
require_image_marker 'P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u' '316K'
require_image_marker 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' '316S'
require_image_marker 'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' '307C'
require_image_marker 'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x' '310D'
require_image_marker 'P276 303 S00p p=%02x%02x%02x' '303F0'

stage "assemble evidence"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase316-gki-compile.log phase316-gki-olddefconfig.log "$OUT/audit/"
cp scripts/316_apply_f0_launch_fault_window_recorder.py "$OUT/audit/"
cp /tmp/p316-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$OUT/source/dsi_ctrl.c"
cp "$HWC" "$OUT/source/dsi_ctrl_hw_cmn.c"
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$PHYV3" "$OUT/source/dsi_phy_hw_v3_0.c"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp phase314-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE314-BASE-BUILD-IDENTITY.json"

stage "repack boot image"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase314-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

stage "build identity"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase316-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
identity = {
  'phase':'316',
  'name':'GKI-F0-LAUNCH-FAULT-WINDOW-RECORDER-V1',
  'git_sha':os.getenv('GITHUB_SHA'),
  'hardware_validated':False,
  'base':'Phase314 reconstructed; Phase311/313 A/B effects restored to Golden inherited splash parity',
  'golden_runtime_reference':{'lane3_tx_dctrl':'0x00','timing9':'0x12'},
  'behavior':'removes two superseded experimental effects; new Phase316 instrumentation is MMIO-read/recorder-only',
  'points':['q0-before-SW_TRIGGER','q1-immediately-after-SW_TRIGGER','q2-DMA-completion-or-timeout'],
  'coverage':['real DSI disp_cc_base MISC_CMD','controller status/FIFO/clock/lane/IRQ/error state','DMA programming/trigger state','CLK_STATUS discriminating bits','inherited Phase307 PHY q0/q1/q2','inherited Phase310 physical DISP_CC/RCG/PLL lifecycle'],
  'image_sha256':sha(r/'compile/Image'),
  'boot_img_sha256':sha(r/'package/boot.img'),
  'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity,indent=2,sort_keys=True)+'\n')
PY

stage "checksums"
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage "complete"
echo 'Phase316 Golden-parity exact-F0 launch fault-window recorder: PASS'
