#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase310-gki-out"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
CLK="$ROOT/drivers/a52_display/msm/dsi/dsi_clk_manager.c"
PLL="$ROOT/drivers/a52_display/pll/dsi_pll_10nm.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"

fail_report() {
  set +e
  rm -rf phase310-gki-failure
  mkdir -p phase310-gki-failure/{logs,audit,source}
  cp phase310-gki-compile.log phase310-gki-olddefconfig.log phase310-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p310-* phase310-gki-failure/audit/ 2>/dev/null || true
  cp scripts/310_apply_gki_link_clock_lifecycle.py scripts/310_sanitize_passive_clock_snapshot.py scripts/310_apply_gki_dispcc_snapshot.py phase310-gki-failure/audit/ 2>/dev/null || true
  [ -f "$PHY" ] && cp "$PHY" phase310-gki-failure/source/ || true
  [ -f "$CLK" ] && cp "$CLK" phase310-gki-failure/source/ || true
  [ -f "$PLL" ] && cp "$PLL" phase310-gki-failure/source/ || true
  [ -f "$DISP" ] && cp "$DISP" phase310-gki-failure/source/ || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

bash scripts/309_ci_build.sh

test -s phase309-gki-out/package/boot.img
test -s phase309-gki-out/compile/Image
test -s phase309-gki-out/config/final.config
test -s "$PHY"
test -s "$CLK"
test -s "$PLL"
test -s "$DISP"
test "$(stat -c '%s' phase309-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE309_GKI_CLAMP_RELEASE_LATCH_V1' "$PHY"
grep -Fq 'dsi_clk_update_link_clk_state' "$CLK"
grep -Fq 'dsi_link_hs_clk_set_rate' "$CLK"
grep -Fq 'vco_10nm_prepare' "$PLL"
grep -Fq 'dsi_pll_10nm_lock_status' "$PLL"
grep -Fq 'disp_cc_lagoon_probe' "$DISP"
grep -Fq 'disp_cc_mdss_byte0_clk' "$DISP"

cp phase309-gki-out/config/final.config /tmp/p310-phase309.config
cp "$PHY" /tmp/p310-phy-before.c
cp "$CLK" /tmp/p310-clk-before.c
cp "$PLL" /tmp/p310-pll-before.c
cp "$DISP" /tmp/p310-disp-before.c

python3 -m py_compile \
  scripts/310_apply_gki_link_clock_lifecycle.py \
  scripts/310_sanitize_passive_clock_snapshot.py \
  scripts/310_apply_gki_dispcc_snapshot.py
python3 scripts/310_apply_gki_link_clock_lifecycle.py --root "$ROOT"
python3 scripts/310_apply_gki_link_clock_lifecycle.py --root "$ROOT" --check-only
# Mandatory safety pass: remove clk_get_rate()/clk_get_parent() from the
# exact-F0 observer because provider queries can execute recalc callbacks and
# alter the handoff state being measured.
python3 scripts/310_sanitize_passive_clock_snapshot.py --root "$ROOT"
python3 scripts/310_sanitize_passive_clock_snapshot.py --root "$ROOT" --check-only
python3 scripts/310_apply_gki_dispcc_snapshot.py --root "$ROOT"
python3 scripts/310_apply_gki_dispcc_snapshot.py --root "$ROOT" --check-only
cp "$PHY" /tmp/p310-phy-after.c
cp "$CLK" /tmp/p310-clk-after.c
cp "$PLL" /tmp/p310-pll-after.c
cp "$DISP" /tmp/p310-disp-after.c
diff -u /tmp/p310-phy-before.c /tmp/p310-phy-after.c > /tmp/p310-phy.diff || true
diff -u /tmp/p310-clk-before.c /tmp/p310-clk-after.c > /tmp/p310-clk.diff || true
diff -u /tmp/p310-pll-before.c /tmp/p310-pll-after.c > /tmp/p310-pll.diff || true
diff -u /tmp/p310-disp-before.c /tmp/p310-disp-after.c > /tmp/p310-disp.diff || true

# Phase310 may add only recorder calls, atomics, software CCF refcount reads,
# and physical regmap reads. No MMIO write, PLL programming, delay/timeout,
# mutating clock operation, provider callback-producing CCF rate/parent query,
# regulator/reset, provider-resource, or clamp primitive count may change.
python3 - "$PHY" "$CLK" "$PLL" "$DISP" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path('/tmp/p310-phy-before.c').read_text(), Path(sys.argv[1]).read_text(), 'PHY'),
    (Path('/tmp/p310-clk-before.c').read_text(), Path(sys.argv[2]).read_text(), 'CLK'),
    (Path('/tmp/p310-pll-before.c').read_text(), Path(sys.argv[3]).read_text(), 'PLL'),
    (Path('/tmp/p310-disp-before.c').read_text(), Path(sys.argv[4]).read_text(), 'DISPCC'),
]
protected = [
    'DSI_W32(', 'MDSS_PLL_REG_W(', 'writel_relaxed(', 'writel(',
    'regmap_write(', 'regmap_update_bits(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'clk_prepare(', 'clk_unprepare(',
    'clk_enable(', 'clk_disable(',
    # These are also protected in Phase310 because a provider rate/parent
    # query can execute callbacks and is not guaranteed observer-only here.
    'clk_get_rate(', 'clk_get_parent(',
    'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(', 'msleep(',
    'usleep_range(', 'udelay(', 'ndelay(', 'mdss_pll_resource_enable(',
    'phy->hw.ops.clamp_ctrl(', 'readl_poll_timeout_atomic(',
]
for before, after, label in pairs:
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f'Phase310 observer scope violation {label}: {token} '
                f'{before.count(token)} -> {after.count(token)}'
            )
required = [
    'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2',
    'A52_PHASE310_PASSIVE_CLOCK_SNAPSHOT_SANITIZER_V1',
    'A52_PHASE310_GKI_LAGOON_DISPCC_SNAPSHOT_V1',
    'atomic_inc(&a52_p310_sr_in);',
    'atomic_inc(&a52_p310_sr_skip);',
    'atomic_inc(&a52_p310_sr_run);',
    'atomic_inc(&a52_p310_prepare_in);',
    'atomic_inc(&a52_p310_enable_in);',
    'atomic_inc(&a52_p310_hs_start);',
    'atomic_inc(&a52_p310_update_in);',
    '__clk_is_enabled(', '__clk_is_prepared(',
    'regmap_read(regmap, A52_P310_DISP_BYTE0_BRANCH, &b)',
    'A52_P310_DISP_PCLK0_BRANCH      0x100c',
    'A52_P310_DISP_BYTE0_BRANCH      0x102c',
    'A52_P310_DISP_BYTE0_INTF_BRANCH 0x1030',
    'A52_P310_DISP_ESC0_BRANCH       0x1034',
    'A52_P310_DISP_PCLK0_CMD         0x1064',
    'A52_P310_DISP_BYTE0_CMD         0x10c4',
    'A52_P310_DISP_ESC0_CMD          0x10e0',
    'P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d',
    'P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d',
    'P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d',
    'P276 310E q=%u em=%x pm=%x',
    'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x',
    'P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x',
    'P276 310PE i=%u e=%u rc=%d',
    'P276 310P q=%u i=%u sr=%d ss=%d so=%d pr=%d ps=%d po=%d en=%d eo=%d',
    'P276 310L q=%u lp=%d lo=%d lr=%d er=%d re=%d hs=%d up=%d on=%d ho=%d',
    'a52_p310_clk_snapshot(index, point);',
    'a52_p310_dispcc_snapshot(point);',
    'a52_p310_pll_lifecycle_snapshot(index, point);',
]
combined = ''.join(after for _, after, _ in pairs)
for token in required:
    if token not in combined:
        raise SystemExit('Phase310 required source token missing: ' + token)
# Explicit final-source ban on the unsafe queries in the injected clock
# observer, independent of the before/after token-count audit above.
clk_after = pairs[1][1]
mark = clk_after.find('A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2')
if mark < 0:
    raise SystemExit('Phase310 clock observer marker missing')
injected = clk_after[mark:]
for forbidden in ('clk_get_rate(', 'clk_get_parent(', 'a52_p310_clk_chain_has('):
    if forbidden in injected:
        raise SystemExit('Phase310 unsafe exact-F0 CCF query remains: ' + forbidden)
print('Phase310 consolidated strictly-passive observer scope audit: PASS')
PY

cp /tmp/p310-phase309.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase310-gki-olddefconfig.log 2>&1
cmp -s /tmp/p310-phase309.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase310-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase310-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d' \
  'P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d' \
  'P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d' \
  'P276 310E q=%u em=%x pm=%x' \
  'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x' \
  'P276 310G q=%u pc=%x pf=%x bc=%x bf=%x ec=%x ef=%x' \
  'P276 310PE i=%u e=%u rc=%d' \
  'P276 310P q=%u i=%u sr=%d ss=%d so=%d pr=%d ps=%d po=%d en=%d eo=%d' \
  'P276 310L q=%u lp=%d lo=%d lr=%d er=%d re=%d hs=%d up=%d on=%d ho=%d' \
  'P276 309T q=%u i=%u ce=%d cr=%d' \
  'P276 308L q=%u i=%u on=%u ho=%u re=%u rr=%u lk=%x' \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 303 S00p p=%02x%02x%02x'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase310-gki-compile.log phase310-gki-olddefconfig.log "$OUT/audit/"
cp scripts/310_apply_gki_link_clock_lifecycle.py scripts/310_sanitize_passive_clock_snapshot.py scripts/310_apply_gki_dispcc_snapshot.py "$OUT/audit/"
cp /tmp/p310-* "$OUT/audit/" 2>/dev/null || true
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$CLK" "$OUT/source/dsi_clk_manager.c"
cp "$PLL" "$OUT/source/dsi_pll_10nm.c"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp phase309-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE309-BASE-BUILD-IDENTITY.json"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase309-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase310-gki-out')
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
  'phase': '310',
  'variant': 'GKI-PHASE309-BASE',
  'name': 'CONSOLIDATED-DSI-LINK-CLOCK-PLL-DISPCC-LIFECYCLE-V4',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'Phase309 GKI exact-F0 PLL/PHY + persistent clamp release latch',
  'observer_only': True,
  'target': 'ctrl0 flags=0x20 msg.flags=0x8 type=0x29 len=3 payload=F0 5A 5A',
  'exact_f0_points': {'q0':'before SW_TRIGGER','q1':'immediately after SW_TRIGGER','q2':'after DMA completion/timeout'},
  'new_evidence': [
    'HS set-rate entered/run/continuous-splash-skipped/last-rc',
    'HS prepare/enable/start/stop and LP start/stop lifecycle counters',
    'link clock state update count/last request/last rc',
    'software CCF prepared/enabled masks only; no rate/parent provider query from exact-F0 observer',
    'physical Lagoon PCLK0/BYTE0/BYTE0_INTF/ESC0 branch registers at q0/q1/q2',
    'physical Lagoon PCLK0/BYTE0/ESC0 RCG CMD/CFG registers at q0/q1/q2',
    '10nm VCO set-rate/prepare/handoff-skip/enable/lock/recalc/unprepare sticky history',
    'ordered sparse PLL lifecycle event records',
  ],
  'golden_context': 'Known-good Golden can report CCF 0/0/0/19.2MHz while succeeding; CCF rate reporting is intentionally not used as a Phase310 discriminator.',
  'passive_safety': 'clk_get_rate/clk_get_parent are forbidden in the injected exact-F0 observer because provider callbacks can alter the handoff state being observed.',
  'decision': {
    'pll_recalc_handoff_then_prepare_skip':'bootloader PLL handoff established and Linux intentionally skipped re-enable',
    'dispcc_branch_or_rcg_bad':'physical DISP_CC state is the first concrete failing frontier',
    'dispcc_and_pll_good_lanes_still_stop':'move below DISP_CC to PHY clock-lane enable/mux sequencing',
    'lock_poll_failure':'PLL lock lifecycle failure despite prior late lock snapshot',
  },
  'mmio_writes_added': False,
  'delay_or_timeout_changes_added': False,
  'mutating_clock_operations_added_or_removed': False,
  'provider_callback_clock_queries_added': False,
  'software_clock_refcount_queries_added': True,
  'read_only_dispcc_regmap_reads_added': True,
  'clock_regulator_reset_changes_added': False,
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
echo 'Phase310 GKI strictly-passive consolidated link-clock + PLL + physical DISP_CC build/repack: PASS'
