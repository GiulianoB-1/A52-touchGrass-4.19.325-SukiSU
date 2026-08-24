#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase309-gki-out"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"

fail_report() {
  set +e
  rm -rf phase309-gki-failure
  mkdir -p phase309-gki-failure/{logs,audit,source}
  cp phase309-gki-compile.log phase309-gki-olddefconfig.log phase309-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p309-* phase309-gki-failure/audit/ 2>/dev/null || true
  cp scripts/309_apply_gki_clamp_release_latch.py phase309-gki-failure/audit/ 2>/dev/null || true
  [ -f "$PHY" ] && cp "$PHY" phase309-gki-failure/source/ || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

bash scripts/308_ci_build.sh

test -s phase308-gki-out/package/boot.img
test -s phase308-gki-out/compile/Image
test -s phase308-gki-out/config/final.config
test -s "$PHY"
test "$(stat -c '%s' phase308-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE308_PLL_LOCK_CLAMP_OBSERVER_V1' "$PHY"
grep -Fq 'P276 308T q=%u %x %x %x %x %x' "$PHY"

cp phase308-gki-out/config/final.config /tmp/p309-phase308.config
cp "$PHY" /tmp/p309-phy-before.c

python3 -m py_compile scripts/309_apply_gki_clamp_release_latch.py
python3 scripts/309_apply_gki_clamp_release_latch.py --root "$ROOT"
python3 scripts/309_apply_gki_clamp_release_latch.py --root "$ROOT" --check-only
cp "$PHY" /tmp/p309-phy-after.c
diff -u /tmp/p309-phy-before.c /tmp/p309-phy-after.c > /tmp/p309-phy.diff || true

# Phase309 may add only software atomics/recorder output around the existing
# clamp callback. No HW write, delay, timeout, clock, regulator, reset, or
# provider-resource primitive count may change from the hardware-tested 308.
python3 - "$PHY" <<'PY'
from pathlib import Path
import sys
before = Path('/tmp/p309-phy-before.c').read_text()
after = Path(sys.argv[1]).read_text()
protected = [
    'DSI_W32(', 'MDSS_PLL_REG_W(', 'writel_relaxed(', 'writel(',
    'clk_set_rate(', 'clk_prepare_enable(', 'clk_disable_unprepare(',
    'regulator_enable(', 'regulator_disable(', 'reset_control_assert(',
    'reset_control_deassert(', 'msleep(', 'usleep_range(', 'udelay(',
    'ndelay(', 'mdss_pll_resource_enable(',
]
for token in protected:
    if before.count(token) != after.count(token):
        raise SystemExit(
            f'Phase309 observer scope violation: {token} '
            f'{before.count(token)} -> {after.count(token)}'
        )
required = [
    'phy->hw.ops.clamp_ctrl(&phy->hw, enable);',
    'atomic_inc(&a52_p309_clamp_enable[phy->index]);',
    'atomic_inc(&a52_p309_clamp_release[phy->index]);',
    'P276 309K i=%u e=%u ce=%d cr=%d',
    'P276 309T q=%u i=%u ce=%d cr=%d',
]
for token in required:
    if token not in after:
        raise SystemExit('Phase309 required source token missing: ' + token)
print('Phase309 software-only clamp latch scope audit: PASS')
PY

cp /tmp/p309-phase308.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase309-gki-olddefconfig.log 2>&1
cmp -s /tmp/p309-phase308.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase309-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase309-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P276 309K i=%u e=%u ce=%d cr=%d' \
  'P276 309T q=%u i=%u ce=%d cr=%d' \
  'P276 308T q=%u %x %x %x %x %x' \
  'P276 308L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x' \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 303 S00p p=%02x%02x%02x'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase309-gki-compile.log phase309-gki-olddefconfig.log "$OUT/audit/"
cp scripts/309_apply_gki_clamp_release_latch.py "$OUT/audit/"
cp /tmp/p309-* "$OUT/audit/" 2>/dev/null || true
cp "$PHY" "$OUT/source/dsi_phy.c"
cp phase308-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE308-BASE-BUILD-IDENTITY.json"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase308-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase309-gki-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
  'phase': '309',
  'variant': 'GKI-PHASE308-BASE',
  'name': 'PERSISTENT-CLAMP-RELEASE-LATCH-V1',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'Phase308 GKI exact-F0 PLL/lock + TX_DCTRL/clamp observer',
  'observer_only': True,
  'target': 'ctrl0 flags=0x20 msg.flags=0x8 type=0x29 len=3 payload=F0 5A 5A',
  'exact_f0_points': {'q0':'before SW_TRIGGER','q1':'immediately after SW_TRIGGER','q2':'after DMA completion/timeout'},
  'clamp_latch_semantics': 'ce/cr atomics increment only after the existing clamp_ctrl callback returns',
  'golden_discriminator': 'Golden Phase308G has ce=0 cr=1 before exact target; compare GKI Phase309 q0 cr',
  'decision': {'cr_eq_0':'GKI missed Golden FreezeIO/clamp release path','cr_ge_1':'clamp callback parity; proceed to Lagoon DISP_CC/link-clock observer'},
  'existing_phase308_txdctrl_reads_preserved': True,
  'existing_phase308_pll_reads_preserved': True,
  'mmio_writes_added': False,
  'delay_or_timeout_changes_added': False,
  'clock_regulator_reset_changes_added': False,
  'software_state_added': ['atomic clamp_enable[2]','atomic clamp_release[2]'],
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
echo 'Phase309 GKI persistent clamp-release latch build/repack: PASS'
