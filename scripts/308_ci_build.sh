#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="$PWD/phase308-gki-out"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TGPHYV3="$TG/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"

find_one() {
  local root="$1" name="$2"
  local result
  result="$(find "$root" -type f -name "$name" -print -quit)"
  [ -n "$result" ] || { echo "Phase308 missing $name under $root" >&2; return 1; }
  printf '%s\n' "$result"
}

fail_report() {
  set +e
  rm -rf phase308-gki-failure
  mkdir -p phase308-gki-failure/{logs,audit,source}
  cp phase308-gki-compile.log phase308-gki-olddefconfig.log phase308-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p308-* phase308-gki-failure/audit/ 2>/dev/null || true
  cp scripts/308_apply_pll_lock_clamp_observer.py phase308-gki-failure/audit/ 2>/dev/null || true
  [ -f "$PHY" ] && cp "$PHY" phase308-gki-failure/source/ || true
  [ -n "${PLLDRV:-}" ] && [ -f "$PLLDRV" ] && cp "$PLLDRV" phase308-gki-failure/source/ || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact Phase307 observer base and preserve its tested F0 target.
bash scripts/307_ci_build.sh

test -s phase307-gki-out/package/boot.img
test -s phase307-gki-out/compile/Image
test -s phase307-gki-out/config/final.config
test "$(stat -c '%s' phase307-gki-out/package/boot.img)" -eq 100663296
for f in "$PHY" "$PHYV3" "$TGPHYV3"; do test -s "$f"; done
grep -Fq 'A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1' "$PHY"
grep -Fq 'P276 307P0 q=%u v=%u p=%u s=%u %x %x %x %x' "$PHY"

PLLDRV="$(find_one "$ROOT" mdss-pll.c)"
PLL10="$(find_one "$ROOT" mdss-dsi-pll-10nm.c)"
TGPLLDRV="$(find_one "$TG" mdss-pll.c)"
TGPLL10="$(find_one "$TG" mdss-dsi-pll-10nm.c)"

# Gate every hard-coded Phase308 register offset against the actual sources.
grep -Eq '^#define[[:space:]]+DSIPHY_LNX_TX_DCTRL\(n\)[[:space:]]+\(0x22C[[:space:]]+\+[[:space:]]+\(0x80[[:space:]]+\*[[:space:]]+\(n\)\)\)' "$PHYV3"
grep -Eq '^#define[[:space:]]+DSIPHY_LNX_TX_DCTRL\(n\)[[:space:]]+\(0x22C[[:space:]]+\+[[:space:]]+\(0x80[[:space:]]+\*[[:space:]]+\(n\)\)\)' "$TGPHYV3"
grep -Eq '^#define[[:space:]]+PLL_COMMON_STATUS_ONE[[:space:]]+0x1[aA]0' "$PLL10"
grep -Eq '^#define[[:space:]]+PLL_PLL_OUTDIV_RATE[[:space:]]+0x140' "$PLL10"
grep -Eq '^#define[[:space:]]+PLL_SYSTEM_MUXES[[:space:]]+0x024' "$PLL10"
grep -Eq '^#define[[:space:]]+PLL_COMMON_STATUS_ONE[[:space:]]+0x1[aA]0' "$TGPLL10"
grep -Fq 'readl_poll_timeout_atomic(pll->pll_base + PLL_COMMON_STATUS_ONE' "$PLL10"
grep -Fq '((status & BIT(0)) > 0)' "$PLL10"

# Static provider provenance. Evidence only, not a parity build gate.
{
  echo "gki_mdss_pll=$PLLDRV"
  echo "golden_mdss_pll=$TGPLLDRV"
  echo "gki_10nm=$PLL10"
  echo "golden_10nm=$TGPLL10"
} > /tmp/p308-pll-paths.txt
sha256sum "$PLLDRV" "$TGPLLDRV" "$PLL10" "$TGPLL10" > /tmp/p308-pll-source.sha256
diff -u "$TGPLLDRV" "$PLLDRV" > /tmp/p308-mdss-pll.diff || true
diff -u "$TGPLL10" "$PLL10" > /tmp/p308-10nm-pll.diff || true
{
  if cmp -s "$TGPLLDRV" "$PLLDRV"; then echo 'mdss_pll_byte_identical=true'; else echo 'mdss_pll_byte_identical=false'; fi
  if cmp -s "$TGPLL10" "$PLL10"; then echo 'dsi_pll_10nm_byte_identical=true'; else echo 'dsi_pll_10nm_byte_identical=false'; fi
} > /tmp/p308-pll-source-summary.txt

cp phase307-gki-out/config/final.config /tmp/p308-phase307.config
cp "$PHY" /tmp/p308-phy-before.c
cp "$PLLDRV" /tmp/p308-plldrv-before.c
cp "$PLL10" /tmp/p308-pll10-before.c
cp "$PHYV3" /tmp/p308-phyv3-before.c

python3 -m py_compile scripts/308_apply_pll_lock_clamp_observer.py
python3 scripts/308_apply_pll_lock_clamp_observer.py --root "$ROOT"
python3 scripts/308_apply_pll_lock_clamp_observer.py --root "$ROOT" --check-only

# Strict observer scope: no new MMIO writes, delays, resets, clock votes,
# regulator votes, or PLL resource-enable calls are permitted.
python3 - "$PHY" "$PLLDRV" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path('/tmp/p308-phy-before.c'), Path(sys.argv[1])),
    (Path('/tmp/p308-plldrv-before.c'), Path(sys.argv[2])),
]
protected = [
    'DSI_W32(', 'MDSS_PLL_REG_W(', 'writel_relaxed(', 'writel(',
    'clk_set_rate(', 'clk_prepare_enable(', 'clk_disable_unprepare(',
    'regulator_enable(', 'regulator_disable(', 'msleep(', 'usleep_range(',
    'udelay(', 'ndelay(', 'mdss_pll_resource_enable(',
]
for before, after in pairs:
    a = before.read_text()
    b = after.read_text()
    for token in protected:
        if a.count(token) != b.count(token):
            raise SystemExit(
                f'Phase308 observer scope violation {after.name}: '
                f'{token} {a.count(token)} -> {b.count(token)}'
            )
print('Phase308 observer-only hardware primitive audit: PASS')
PY

# The actual v3 programming and 10-nm PLL implementation remain byte untouched.
cmp -s /tmp/p308-phyv3-before.c "$PHYV3"
cmp -s /tmp/p308-pll10-before.c "$PLL10"

cp /tmp/p308-phase307.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase308-gki-olddefconfig.log 2>&1
cmp -s /tmp/p308-phase307.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase308-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase308-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P276 308R i=%u p=1' \
  'P276 308L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x' \
  'P276 308V q=%u vc=%lld ca=%lu c0=%x c1=%x od=%x' \
  'P276 308M q=%u m=%x o=%x c0=%x c1=%x rb=%x' \
  'P276 308T q=%u %x %x %x %x %x' \
  'P276 308K i=%u e=%u q=%u %x %x %x %x %x' \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 303 S00p p=%02x%02x%02x'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase308-gki-compile.log phase308-gki-olddefconfig.log "$OUT/audit/"
cp scripts/308_apply_pll_lock_clamp_observer.py "$OUT/audit/"
cp /tmp/p308-* "$OUT/audit/" 2>/dev/null || true
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$PHYV3" "$OUT/source/dsi_phy_hw_v3_0.c"
cp "$PLLDRV" "$OUT/source/mdss-pll.c"
cp "$PLL10" "$OUT/source/mdss-dsi-pll-10nm.c"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase307-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase308-gki-out')
summary = {}
for line in Path('/tmp/p308-pll-source-summary.txt').read_text().splitlines():
    k, v = line.split('=', 1)
    summary[k] = (v == 'true')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
  'phase': '308',
  'variant': 'GKI-PHASE307-BASE',
  'name': 'PLL-LOCK-HANDOFF-TXDCTRL-CLAMP-OBSERVER-V1',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'Phase307 exact F0 v3 PHY/clock-lane observer',
  'observer_only': True,
  'target': 'ctrl0 flags=0x20 msg.flags=0x8 type=0x29 len=3 payload=F0 5A 5A',
  'exact_f0_points': {'q0':'before SW_TRIGGER','q1':'immediately after SW_TRIGGER','q2':'after DMA completion/timeout'},
  'synthetic_q1_5_delay_added': False,
  'synthetic_q1_5_reason': 'preserve failing launch timing; clamp path is observed at its real callback instead',
  'pll_lock_source': 'PLL_COMMON_STATUS_ONE@0x1a0 bit0',
  'tx_dctrl_source': 'DSIPHY_LNX_TX_DCTRL(n)=0x22C+0x80*n, n=0..4',
  'pll_provider_state': ['pll_on','handoff_resources','resource_enable','resource_ref_cnt','vco_current_rate','vco_cached_rate','cached_cfg0','cached_cfg1','cached_outdiv'],
  'pll_physical_state': ['COMMON_STATUS_ONE','SYSTEM_MUXES','PLL_OUTDIV_RATE','PHY_CMN_CLK_CFG0','PHY_CMN_CLK_CFG1','PHY_CMN_RBUF_CTRL'],
  'clamp_observer': 'TX_DCTRL0..4 immediately before and after existing dsi_phy_set_clamp_state clamp_ctrl callback',
  'mdss_pll_source_byte_identical_to_golden': summary.get('mdss_pll_byte_identical'),
  'dsi_pll_10nm_source_byte_identical_to_golden': summary.get('dsi_pll_10nm_byte_identical'),
  'mmio_writes_added': False,
  'delay_or_timeout_changes_added': False,
  'clock_or_regulator_changes_added': False,
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
echo 'Phase308 GKI PLL lock/handoff + TX_DCTRL/clamp observer build/repack: PASS'
