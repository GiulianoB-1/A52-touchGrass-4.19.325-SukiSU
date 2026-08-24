#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase311-gki-out"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"

fail_report() {
  set +e
  rm -rf phase311-gki-failure
  mkdir -p phase311-gki-failure/{logs,audit,source}
  cp phase311-gki-compile.log phase311-gki-olddefconfig.log phase311-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p311-* phase311-gki-failure/audit/ 2>/dev/null || true
  cp scripts/311_apply_v3_dctrl3_handoff_repair.py phase311-gki-failure/audit/ 2>/dev/null || true
  [ -f "$HW" ] && cp "$HW" phase311-gki-failure/source/ || true
  [ -f "$PHY" ] && cp "$PHY" phase311-gki-failure/source/ || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase310 lineage first. This also
# leaves the fully instrumented source tree and build directory in place.
bash scripts/310_ci_build.sh

test -s phase310-gki-out/package/boot.img
test -s phase310-gki-out/compile/Image
test -s phase310-gki-out/config/final.config
test -s "$HW"
test -s "$PHY"
test "$(stat -c '%s' phase310-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2' "$PHY"
grep -Fq 'u8 tx_dctrl[] = {0x00, 0x00, 0x00, 0x04, 0x01};' "$HW"

cp phase310-gki-out/config/final.config /tmp/p311-phase310.config
cp "$HW" /tmp/p311-hw-before.c

python3 -m py_compile scripts/311_apply_v3_dctrl3_handoff_repair.py
python3 scripts/311_apply_v3_dctrl3_handoff_repair.py --root "$ROOT"
python3 scripts/311_apply_v3_dctrl3_handoff_repair.py --root "$ROOT" --check-only
cp "$HW" /tmp/p311-hw-after.c
diff -u /tmp/p311-hw-before.c /tmp/p311-hw-after.c > /tmp/p311-hw.diff || true

# Strict behavioral-scope audit. Phase311 is allowed to change only the value
# carried through the two already-existing TX_DCTRL3 FreezeIO writes. It must
# not add/remove any MMIO operation, barrier, delay, clock, reset, regulator,
# controller command, interrupt, timeout, or power-management primitive.
python3 - "$HW" <<'PY'
from pathlib import Path
import sys
before = Path('/tmp/p311-hw-before.c').read_text()
after = Path(sys.argv[1]).read_text()

protected = [
    'DSI_W32(', 'DSI_R32(', 'wmb(', 'mb(', 'rmb(',
    'writel(', 'writel_relaxed(', 'readl(', 'readl_relaxed(',
    'readl_poll_timeout', 'udelay(', 'ndelay(', 'usleep_range(', 'msleep(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'clk_prepare(', 'clk_unprepare(',
    'clk_enable(', 'clk_disable(', 'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(',
]
for token in protected:
    if before.count(token) != after.count(token):
        raise SystemExit(
            f'Phase311 scope violation: {token} {before.count(token)} -> {after.count(token)}'
        )

required = [
    'A52_PHASE311_V3_DCTRL3_HANDOFF_REPAIR_AB_V1',
    'u8 tx_dctrl[] = {0x00, 0x00, 0x00, 0x04, 0x01};',
    'reg |= BIT(2);',
    'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg | BIT(0));',
    'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3), reg & ~BIT(0));',
]
for token in required:
    if token not in after:
        raise SystemExit('Phase311 required token missing: ' + token)

# Enforce the exact one-line semantic delta outside comments: one new BIT(2)
# operation, with no new assignment to any other DSI register.
if after.count('reg |= BIT(2);') - before.count('reg |= BIT(2);') != 1:
    raise SystemExit('Phase311 expected exactly one new reg |= BIT(2) operation')

# The clamp-release function must still have exactly the same two DCTRL3 writes.
def span(text):
    start = text.find('void dsi_phy_hw_v3_0_clamp_ctrl(')
    if start < 0:
        raise SystemExit('Phase311 clamp function missing')
    nxt = text.find('\n/**', start + 1)
    if nxt < 0:
        raise SystemExit('Phase311 clamp function end anchor missing')
    return text[start:nxt]

b = span(before)
a = span(after)
write = 'DSI_W32(phy, DSIPHY_LNX_TX_DCTRL(3),'
if b.count(write) != 2 or a.count(write) != 2:
    raise SystemExit(f'Phase311 DCTRL3 write count changed: {b.count(write)} -> {a.count(write)}')
if a.count('reg |= BIT(2);') != 1:
    raise SystemExit('Phase311 repair not uniquely located in clamp release')
print('Phase311 exact one-bit/two-existing-write scope audit: PASS')
PY

cp /tmp/p311-phase310.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase311-gki-olddefconfig.log 2>&1
cmp -s /tmp/p311-phase310.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase311-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase311-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

# All inherited target observers must remain present so a failed A/B is still
# diagnostically complete on the same single phone run.
for marker in \
  'P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d' \
  'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x' \
  'P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x' \
  'P276 310P q=%u i=%u sr=%d ss=%d so=%d pr=%d ps=%d po=%d en=%d eo=%d' \
  'P276 310L q=%u lp=%d lo=%d lr=%d er=%d re=%d hs=%d up=%d on=%d ho=%d' \
  'P276 309T q=%u i=%u ce=%d cr=%d' \
  'P276 308T q=%u %x %x %x %x %x' \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 303 S00p p=%02x%02x%02x'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase311-gki-compile.log phase311-gki-olddefconfig.log "$OUT/audit/"
cp scripts/311_apply_v3_dctrl3_handoff_repair.py "$OUT/audit/"
cp /tmp/p311-* "$OUT/audit/" 2>/dev/null || true
cp "$HW" "$OUT/source/dsi_phy_hw_v3_0.c"
cp "$PHY" "$OUT/source/dsi_phy.c"
cp phase310-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE310-BASE-BUILD-IDENTITY.json"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase310-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase311-gki-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
  'phase': '311',
  'variant': 'GKI-PHASE310-BASE',
  'name': 'V3-LANE3-TX-DCTRL-BIT2-HANDOFF-REPAIR-AB-V1',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'Phase310 strictly-passive consolidated clock/PLL/DISP_CC recorder',
  'behavior_change': True,
  'behavior_change_scope': 'In existing v3 clamp-release callback only: OR BIT(2) into inherited lane-3 TX_DCTRL base before the two pre-existing FreezeIO writes. No new MMIO write.',
  'phase310_runtime_evidence': {
    'tx_dctrl_q0': '0,0,0,0,1',
    'tx_dctrl_q1': '0,0,0,0,1',
    'tx_dctrl_q2': '0,0,0,0,1',
    'clamp_release_count': 1,
    'pll_locked': True,
    'dispcc_branches_enabled': True,
    'hs_clocks_prepare_enable_success': True,
    'lane_status_stays_stop': '0x1f1f',
    'dma_done': False,
  },
  'source_contract': 'A52 v3 lane_settings table is 00,00,00,04,01; current inherited lane3 value is 00. The repair makes the existing bit0 FreezeIO release use base bit2 as normal lane setup does.',
  'mmio_writes_added': 0,
  'mmio_writes_removed': 0,
  'barriers_added': 0,
  'delays_or_timeouts_changed': False,
  'clock_or_pll_operations_changed': False,
  'dsi_payload_or_trigger_changed': False,
  'expected_runtime_discriminator': 'Inherited P276 308T should show lane3=4 at q0/q1/q2. If DMA_DONE/lane movement recovers, DCTRL3 handoff is causal; if not, Phase310 evidence remains available to move below this bit.',
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
echo 'Phase311 GKI v3 lane3 TX_DCTRL handoff repair A/B build/repack: PASS'
