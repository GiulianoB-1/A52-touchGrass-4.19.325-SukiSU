#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="$PWD/phase312-gki-out"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
CTRL22="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_2_2.c"
TGPHYV3="$TG/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
TGCTRL22="$TG/techpack/display/msm/dsi/dsi_ctrl_hw_2_2.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"

fail_report() {
  set +e
  rm -rf phase312-gki-failure
  mkdir -p phase312-gki-failure/{logs,audit,source,nested}
  cp phase312-gki-compile.log phase312-gki-olddefconfig.log phase312-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p312-* phase312-gki-failure/audit/ 2>/dev/null || true
  cp scripts/312_apply_f0_source_dependency_recorder.py phase312-gki-failure/audit/ 2>/dev/null || true
  [ -f "$PHY" ] && cp "$PHY" phase312-gki-failure/source/ || true
  [ -f "$DISP" ] && cp "$DISP" phase312-gki-failure/source/ || true
  [ -f "$PHYV3" ] && cp "$PHYV3" phase312-gki-failure/source/ || true
  [ -f "$CTRL22" ] && cp "$CTRL22" phase312-gki-failure/source/ || true
  for d in phase*-gki-failure; do
    [ -d "$d" ] || continue
    [ "$d" = "phase312-gki-failure" ] && continue
    cp -a "$d" phase312-gki-failure/nested/ || true
  done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact phone-tested Phase311 tree first. Phase312 is a pure
# observer layer on top of the confirmed DCTRL3=0x04 handoff repair.
set +e
bash scripts/311_ci_build.sh 2>&1 | tee /tmp/p312-phase311.log
phase311_rc=${PIPESTATUS[0]}
set -e
if [ "$phase311_rc" -ne 0 ]; then
  echo "ERROR: Phase311 reconstruction failed rc=$phase311_rc" >&2
  exit "$phase311_rc"
fi

for f in \
  phase311-gki-out/package/boot.img \
  phase311-gki-out/compile/Image \
  phase311-gki-out/config/final.config \
  "$PHY" "$DISP" "$PHYV3" "$CTRL22" "$TGPHYV3" "$TGCTRL22" "$HW"; do
  test -s "$f"
done
test "$(stat -c '%s' phase311-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1' "$HW"
grep -Fq 'A52_PHASE310_GKI_LAGOON_DISPCC_SNAPSHOT_V1' "$DISP"
grep -Fq 'A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1' "$PHY"
grep -Fq 'P276 308T q=%u %x %x %x %x %x' "$PHY"

# Gate every new Phase312 hard-coded MMIO offset against BOTH reconstructed
# GKI and the pinned TouchGrass source. This is a build-stopping provenance
# check, not an assumption that the current inherited register values match.
python3 - "$PHYV3" "$TGPHYV3" "$CTRL22" "$TGCTRL22" <<'PY'
from pathlib import Path
import re, sys

phy_expected = {
    **{f'DSIPHY_CMN_TIMING_CTRL_{i}': 0x0ac + 4*i for i in range(12)},
    'DSIPHY_LNX_CFG0': 0x200,
    'DSIPHY_LNX_CFG1': 0x204,
    'DSIPHY_LNX_CFG2': 0x208,
    'DSIPHY_LNX_CFG3': 0x20c,
    'DSIPHY_LNX_PIN_SWAP': 0x214,
    'DSIPHY_LNX_HSTX_STR_CTRL': 0x218,
    'DSIPHY_LNX_OFFSET_TOP_CTRL': 0x21c,
    'DSIPHY_LNX_OFFSET_BOT_CTRL': 0x220,
    'DSIPHY_LNX_LPTX_STR_CTRL': 0x224,
    'DSIPHY_LNX_LPRX_CTRL': 0x228,
}

def macro_hex(text, name):
    m = re.search(r'(?m)^#define\s+' + re.escape(name) + r'(?:\(n\))?\s+[^\n]*?0x([0-9A-Fa-f]+)', text)
    if not m:
        raise SystemExit('Phase312 source gate missing macro: ' + name)
    return int(m.group(1), 16)

for path in map(Path, sys.argv[1:3]):
    text = path.read_text()
    for name, want in phy_expected.items():
        got = macro_hex(text, name)
        if got != want:
            raise SystemExit(f'Phase312 PHY offset mismatch {path}:{name} got=0x{got:x} want=0x{want:x}')

for path in map(Path, sys.argv[3:5]):
    text = path.read_text()
    got = macro_hex(text, 'DISP_CC_MISC_CMD_REG_OFF')
    if got != 0:
        raise SystemExit(f'Phase312 DISP_CC MISC_CMD mismatch {path}: got=0x{got:x}')

print('Phase312 GKI/Golden source-derived register provenance gate: PASS')
PY

cp phase311-gki-out/config/final.config /tmp/p312-phase311.config
cp "$PHY" /tmp/p312-phy-before.c
cp "$DISP" /tmp/p312-disp-before.c
cp "$HW" /tmp/p312-hw-before.c

python3 -m py_compile scripts/312_apply_f0_source_dependency_recorder.py
python3 scripts/312_apply_f0_source_dependency_recorder.py --root "$ROOT"
python3 scripts/312_apply_f0_source_dependency_recorder.py --root "$ROOT" --check-only
cp "$PHY" /tmp/p312-phy-after.c
cp "$DISP" /tmp/p312-disp-after.c
cp "$HW" /tmp/p312-hw-after.c
diff -u /tmp/p312-phy-before.c /tmp/p312-phy-after.c > /tmp/p312-phy.diff || true
diff -u /tmp/p312-disp-before.c /tmp/p312-disp-after.c > /tmp/p312-disp.diff || true
diff -u /tmp/p312-hw-before.c /tmp/p312-hw-after.c > /tmp/p312-hw.diff || true
cmp -s /tmp/p312-hw-before.c /tmp/p312-hw-after.c

# Strict observer-only scope. Phase312 may add only readl_relaxed/regmap_read,
# software iteration, cfg-field reads and recorder calls. It may not alter any
# MMIO write, barrier, delay/timeout, command, interrupt, clock/PLL, reset,
# regulator, power-management, clamp, or provider-resource primitive.
python3 - "$PHY" "$DISP" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path('/tmp/p312-phy-before.c').read_text(), Path(sys.argv[1]).read_text(), 'PHY'),
    (Path('/tmp/p312-disp-before.c').read_text(), Path(sys.argv[2]).read_text(), 'DISPCC'),
]
protected = [
    'DSI_W32(', 'DSI_R32(', 'MDSS_PLL_REG_W(',
    'writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
    'wmb(', 'mb(', 'rmb(',
    'readl_poll_timeout', 'wait_for_completion_timeout(',
    'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'clk_prepare(', 'clk_unprepare(',
    'clk_enable(', 'clk_disable(', 'clk_get_rate(', 'clk_get_parent(',
    'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
    'mdss_pll_resource_enable(', 'phy->hw.ops.clamp_ctrl(',
]
for before, after, label in pairs:
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f'Phase312 observer scope violation {label}: {token} '
                f'{before.count(token)} -> {after.count(token)}'
            )

phy_before, phy_after, _ = pairs[0]
disp_before, disp_after, _ = pairs[1]
if phy_after.count('readl_relaxed(') - phy_before.count('readl_relaxed(') != 22:
    raise SystemExit('Phase312 expected exactly 22 new PHY readl_relaxed source sites')
if disp_after.count('regmap_read(') - disp_before.count('regmap_read(') != 1:
    raise SystemExit('Phase312 expected exactly one new DISP_CC regmap_read source site')
if phy_after.count('a52_ackfr_record(') - phy_before.count('a52_ackfr_record(') != 7:
    raise SystemExit('Phase312 expected exactly seven new PHY recorder source sites')
if disp_after.count('a52_ackfr_record(') - disp_before.count('a52_ackfr_record(') != 1:
    raise SystemExit('Phase312 expected exactly one new DISP_CC recorder source site')

required = [
    'A52_PHASE312_GKI_F0_PHY_DEPENDENCY_RECORDER_V1',
    'A52_PHASE312_GKI_DISPCC_MISC_CMD_RECORDER_V1',
    'if (point == 0) {',
    'P276 312T0 %x %x %x %x %x %x',
    'P276 312T1 %x %x %x %x %x %x',
    'P276 312TE0 %x %x %x %x %x %x',
    'P276 312TE1 %x %x %x %x %x %x',
    'P276 312L0 l=%u %x %x %x %x %x',
    'P276 312L1 l=%u %x %x %x %x %x',
    'P276 312LE l=%u %x %x %x %x %x %x',
    'P276 312D q=%u rc=%d m=%x b0=%u b5=%u b7=%u b9=%u',
    'phy->cfg.timing.lane_v3[11]',
    'phy->cfg.lanecfg.lane[lane][3]',
    'phy->cfg.strength.lane[lane][1]',
    'regmap_read(regmap, A52_P312_DISP_MISC_CMD, &misc)',
]
combined = phy_after + disp_after
for token in required:
    if token not in combined:
        raise SystemExit('Phase312 required observer token missing: ' + token)

# Existing Phase311 repair and exact trigger observers must remain untouched.
for token in [
    'P276 308T q=%u %x %x %x %x %x',
    'P276 307P0 q=%u v=%u p=%u s=%u %x %x %x %x',
    'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x',
]:
    if token not in combined:
        raise SystemExit('Phase312 inherited observer missing: ' + token)
print('Phase312 strictly-passive read/record-only scope audit: PASS')
PY

cp /tmp/p312-phase311.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase312-gki-olddefconfig.log 2>&1
cmp -s /tmp/p312-phase311.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase312-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase312-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P276 312T0 %x %x %x %x %x %x' \
  'P276 312T1 %x %x %x %x %x %x' \
  'P276 312TE0 %x %x %x %x %x %x' \
  'P276 312TE1 %x %x %x %x %x %x' \
  'P276 312L0 l=%u %x %x %x %x %x' \
  'P276 312L1 l=%u %x %x %x %x %x' \
  'P276 312LE l=%u %x %x %x %x %x %x' \
  'P276 312D q=%u rc=%d m=%x b0=%u b5=%u b7=%u b9=%u' \
  'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x' \
  'P276 308T q=%u %x %x %x %x %x' \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 303 S00p p=%02x%02x%02x'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase312-gki-compile.log phase312-gki-olddefconfig.log "$OUT/audit/"
cp scripts/312_apply_f0_source_dependency_recorder.py "$OUT/audit/"
cp /tmp/p312-* "$OUT/audit/" 2>/dev/null || true
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp "$PHYV3" "$OUT/source/dsi_phy_hw_v3_0.c"
cp "$CTRL22" "$OUT/source/dsi_ctrl_hw_2_2.c"
cp phase311-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE311-BASE-BUILD-IDENTITY.json"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase311-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase312-gki-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
  'phase': '312',
  'variant': 'GKI-PHASE311-BASE',
  'name': 'SOURCE-DERIVED-F0-INHERITED-PHY-DISPCC-DEPENDENCY-RECORDER-V1',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'Phone-tested Phase311 lane3 TX_DCTRL bit2 handoff repair A/B',
  'observer_only': True,
  'behavior_change': False,
  'target': 'ctrl0 flags=0x20 msg.flags=0x8 type=0x29 len=3 payload=F0 5A 5A',
  'capture_point': 'q0 only for new Phase312 records: exact pre-SW_TRIGGER boundary',
  'source_basis': [
    'TouchGrass dsi_phy_enable computes/populates phy->cfg timing before continuous-splash skips dsi_phy_enable_hw',
    'TouchGrass dsi_phy_hw_v3_0_enable programs CMN_TIMING_CTRL_0..11 from cfg.timing.lane_v3',
    'TouchGrass dsi_phy_hw_v3_0_lane_settings programs per-lane CFG0..3, PIN_SWAP, HSTX, offsets, LPTX/LPRX and TX_DCTRL',
    'A52 ctrl hw v2.4 reuses v2.2 DISP_CC MISC_CMD phy-reset and clk-gating callbacks',
  ],
  'new_runtime_records_q0': 20,
  'new_mmio_read_transactions_q0': 63,
  'new_phy_readl_relaxed_source_sites': 22,
  'new_dispcc_regmap_read_source_sites': 1,
  'mmio_writes_added': 0,
  'mmio_writes_removed': 0,
  'barriers_added': 0,
  'delays_or_timeouts_changed': False,
  'clock_or_pll_operations_changed': False,
  'dsi_payload_or_trigger_changed': False,
  'new_evidence': [
    'DISP_CC_MISC_CMD raw value plus ctrl0 bits 0/5/7/9',
    'CMN_TIMING_CTRL_0..11 actual HW values',
    'phy->cfg.timing.lane_v3[0..11] software values normal v3 enable would write',
    'all 5 lanes actual CFG0..3/PIN_SWAP/HSTX/OFFSET_TOP/OFFSET_BOT/LPTX/LPRX',
    'all 5 lanes cfg.lanecfg[0..3] and cfg.strength[0..1] software values',
    'all inherited Phase307/308/309/310/311 observers remain present',
  ],
  'expected_discriminator': 'Find the first exact q0 mismatch between inherited HW and the normal-v3 source-derived software/fixed state. Phase311 already proves TX_DCTRL3=0x04 alone is insufficient.',
  'boot_bytes': (r/'package/boot.img').stat().st_size,
  'boot_sha256': hashlib.sha256((r/'package/boot.img').read_bytes()).hexdigest(),
  'image_sha256': hashlib.sha256((r/'compile/Image').read_bytes()).hexdigest(),
  'dtb_preserved': repack['invariants']['dtb_preserved'],
  'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
  'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files=[p for p in r.rglob('*') if p.is_file() and p.name!='SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
  for p in sorted(files):
    f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
PY
(cd "$OUT" && sha256sum -c SHA256SUMS)
trap - EXIT
echo 'Phase312 GKI source-derived exact-F0 dependency recorder build/repack: PASS'
