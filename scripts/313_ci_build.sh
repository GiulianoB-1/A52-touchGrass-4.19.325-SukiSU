#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="$PWD/phase313-gki-out"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
GKITIM="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_timing_v3_0.c"
TGPHYV3="$TG/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
TGTIM="$TG/techpack/display/msm/dsi/dsi_phy_timing_v3_0.c"

fail_report() {
  set +e
  rm -rf phase313-gki-failure
  mkdir -p phase313-gki-failure/{logs,audit,source,nested}
  cp phase313-gki-compile.log phase313-gki-olddefconfig.log phase313-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p313-* phase313-gki-failure/audit/ 2>/dev/null || true
  cp scripts/313_apply_v3_timing9_handoff_repair.py phase313-gki-failure/audit/ 2>/dev/null || true
  [ -f "$HW" ] && cp "$HW" phase313-gki-failure/source/ || true
  [ -f "$PHY" ] && cp "$PHY" phase313-gki-failure/source/ || true
  [ -f "$DISP" ] && cp "$DISP" phase313-gki-failure/source/ || true
  for d in phase*-gki-failure; do
    [ -d "$d" ] || continue
    [ "$d" = "phase313-gki-failure" ] && continue
    cp -a "$d" phase313-gki-failure/nested/ || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact successful Phase312 tree first. Phase313 changes one
# source-proven inherited v3 PHY register and nothing else.
set +e
bash scripts/312_ci_build.sh 2>&1 | tee /tmp/p313-phase312.log
phase312_rc=${PIPESTATUS[0]}
set -e
if [ "$phase312_rc" -ne 0 ]; then
  echo "ERROR: Phase312 reconstruction failed rc=$phase312_rc" >&2
  exit "$phase312_rc"
fi

for f in \
  phase312-gki-out/package/boot.img \
  phase312-gki-out/compile/Image \
  phase312-gki-out/config/final.config \
  "$PHY" "$DISP" "$HW" "$GKITIM" "$TGPHYV3" "$TGTIM"; do
  test -s "$f"
done
test "$(stat -c '%s' phase312-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1' "$HW"
grep -Fq 'A52_PHASE312_GKI_F0_PHY_DEPENDENCY_RECORDER_V1' "$PHY"
grep -Fq 'A52_PHASE312_GKI_DISPCC_MISC_CMD_RECORDER_V1' "$DISP"

# Source provenance: both reconstructed GKI and pinned TouchGrass define
# TIMING_CTRL_9 at 0x0d0, calculate lane_v3[9] as 0x02, and normal v3 enable
# writes that timing value directly to the hardware register.
python3 - "$HW" "$TGPHYV3" "$GKITIM" "$TGTIM" <<'PY'
from pathlib import Path
import re, sys

def macro_hex(text, name):
    m = re.search(r'(?m)^#define\s+' + re.escape(name) + r'\s+0x([0-9A-Fa-f]+)', text)
    if not m:
        raise SystemExit('Phase313 source gate missing macro: ' + name)
    return int(m.group(1), 16)

for path in map(Path, sys.argv[1:3]):
    text = path.read_text()
    got = macro_hex(text, 'DSIPHY_CMN_TIMING_CTRL_9')
    if got != 0x0d0:
        raise SystemExit(f'Phase313 TIMING_CTRL_9 offset mismatch {path}: 0x{got:x}')
    direct = 'DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, timing->lane_v3[9]);'
    if direct not in text:
        raise SystemExit(f'Phase313 direct timing9 write missing in {path}')

for path in map(Path, sys.argv[3:5]):
    text = path.read_text()
    if not re.search(r'timing->lane_v3\[9\]\s*=\s*0x02\s*;', text):
        raise SystemExit(f'Phase313 source-required lane_v3[9]=0x02 missing in {path}')

print('Phase313 GKI/Golden timing9 provenance gate: PASS')
PY

cp phase312-gki-out/config/final.config /tmp/p313-phase312.config
cp "$HW" /tmp/p313-hw-before.c
cp "$PHY" /tmp/p313-phy-before.c
cp "$DISP" /tmp/p313-disp-before.c

python3 -m py_compile scripts/313_apply_v3_timing9_handoff_repair.py
python3 scripts/313_apply_v3_timing9_handoff_repair.py --root "$ROOT"
python3 scripts/313_apply_v3_timing9_handoff_repair.py --root "$ROOT" --check-only

cp "$HW" /tmp/p313-hw-after.c
cp "$PHY" /tmp/p313-phy-after.c
cp "$DISP" /tmp/p313-disp-after.c
diff -u /tmp/p313-hw-before.c /tmp/p313-hw-after.c > /tmp/p313-hw.diff || true
cmp -s /tmp/p313-phy-before.c /tmp/p313-phy-after.c
cmp -s /tmp/p313-disp-before.c /tmp/p313-disp-after.c

# One-variable hardware A/B. Exactly one DSI write is added. Existing barrier
# count/order and every other protected primitive must remain unchanged.
python3 - "$HW" <<'PY'
from pathlib import Path
import sys
before = Path('/tmp/p313-hw-before.c').read_text()
after = Path(sys.argv[1]).read_text()

if after.count('DSI_W32(') - before.count('DSI_W32(') != 1:
    raise SystemExit('Phase313 expected exactly one new DSI_W32 source site')
if after.count('DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);') != 1:
    raise SystemExit('Phase313 exact TIMING_CTRL_9=0x02 repair write missing/not unique')
if after.count('A52_PHASE313_V3_TIMING9_HANDOFF_REPAIR_AB_V1') != 1:
    raise SystemExit('Phase313 repair marker missing/not unique')

protected_unchanged = [
    'DSI_R32(', 'MDSS_PLL_REG_W(',
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
for token in protected_unchanged:
    if before.count(token) != after.count(token):
        raise SystemExit(
            f'Phase313 scope violation: {token} {before.count(token)} -> {after.count(token)}'
        )

# The Phase311 FreezeIO release sequence is still two writes with the same two
# barriers; Phase313 only inserts the timing write immediately before it.
required = [
    'reg |= BIT(2);',
    'DSI_W32(phy, DSIPHY_CMN_TIMING_CTRL_9, 0x02);',
    'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));',
    'wmb(); /* Ensure that the freezeio bit is toggled */',
    'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg & ~BIT(0));',
    'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1',
]
for token in required:
    if token not in after:
        raise SystemExit('Phase313 inherited/repaired token missing: ' + token)

print('Phase313 one-write TIMING9 A/B scope audit: PASS')
PY

cp /tmp/p313-phase312.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase313-gki-olddefconfig.log 2>&1
cmp -s /tmp/p313-phase312.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase313-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase313-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
# Retain the phone-visible Phase312 discriminator and inherited F0 observers.
for marker in \
  'P276 312T1 %x %x %x %x %x %x' \
  'P276 312TE1 %x %x %x %x %x %x' \
  'P276 312D q=%u rc=%d m=%x b0=%u b5=%u b7=%u b9=%u' \
  'P276 308T q=%u %x %x %x %x %x' \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 303 S00p p=%02x%02x%02x'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase313-gki-compile.log phase313-gki-olddefconfig.log "$OUT/audit/"
cp scripts/313_apply_v3_timing9_handoff_repair.py "$OUT/audit/"
cp /tmp/p313-* "$OUT/audit/" 2>/dev/null || true
cp "$HW" "$OUT/source/dsi_phy_hw_v3_0.c"
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp "$GKITIM" "$OUT/source/dsi_phy_timing_v3_0.c"
cp phase312-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE312-BASE-BUILD-IDENTITY.json"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase312-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase313-gki-out')

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

identity = {
    'phase': '313',
    'variant': 'GKI-PHASE312-BASE',
    'name': 'V3-TIMING9-HANDOFF-REPAIR-AB-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base': 'Phone-tested Phase312 exact-F0 dependency recorder',
    'evidence': {
        'phase312_hw_timing9': '0x12',
        'phase312_cfg_timing9': '0x02',
        'golden_v3_timing9': '0x02',
        'repair': 'one DSI_W32 to DSIPHY_CMN_TIMING_CTRL_9=0x02 before inherited FreezeIO release',
    },
    'image_sha256': sha(r/'compile/Image'),
    'boot_img_sha256': sha(r/'package/boot.img'),
    'boot_img_size': (r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
sha256sum -c "$OUT/SHA256SUMS"

echo 'Phase313 V3 TIMING_CTRL_9 handoff repair A/B: PASS'
