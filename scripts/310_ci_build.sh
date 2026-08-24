#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase310-gki-out"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
CLK="$ROOT/drivers/a52_display/msm/dsi/dsi_clk_manager.c"

fail_report() {
  set +e
  rm -rf phase310-gki-failure
  mkdir -p phase310-gki-failure/{logs,audit,source}
  cp phase310-gki-compile.log phase310-gki-olddefconfig.log phase310-gki-failure/logs/ 2>/dev/null || true
  cp /tmp/p310-* phase310-gki-failure/audit/ 2>/dev/null || true
  cp scripts/310_apply_gki_link_clock_lifecycle.py phase310-gki-failure/audit/ 2>/dev/null || true
  [ -f "$PHY" ] && cp "$PHY" phase310-gki-failure/source/ || true
  [ -f "$CLK" ] && cp "$CLK" phase310-gki-failure/source/ || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

bash scripts/309_ci_build.sh

test -s phase309-gki-out/package/boot.img
test -s phase309-gki-out/compile/Image
test -s phase309-gki-out/config/final.config
test -s "$PHY"
test -s "$CLK"
test "$(stat -c '%s' phase309-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE309_GKI_CLAMP_RELEASE_LATCH_V1' "$PHY"
grep -Fq 'dsi_clk_update_link_clk_state' "$CLK"
grep -Fq 'dsi_link_hs_clk_set_rate' "$CLK"

cp phase309-gki-out/config/final.config /tmp/p310-phase309.config
cp "$PHY" /tmp/p310-phy-before.c
cp "$CLK" /tmp/p310-clk-before.c

python3 -m py_compile scripts/310_apply_gki_link_clock_lifecycle.py
python3 scripts/310_apply_gki_link_clock_lifecycle.py --root "$ROOT"
python3 scripts/310_apply_gki_link_clock_lifecycle.py --root "$ROOT" --check-only
cp "$PHY" /tmp/p310-phy-after.c
cp "$CLK" /tmp/p310-clk-after.c
diff -u /tmp/p310-phy-before.c /tmp/p310-phy-after.c > /tmp/p310-phy.diff || true
diff -u /tmp/p310-clk-before.c /tmp/p310-clk-after.c > /tmp/p310-clk.diff || true

# Phase310 may add only atomics and recorder snapshots around the inherited
# clock-manager path. It must not add/remove any actual clock, MMIO, delay,
# regulator, reset, PLL-resource, or clamp operation.
python3 - "$PHY" "$CLK" <<'PY'
from pathlib import Path
import sys
pairs = [
    (Path('/tmp/p310-phy-before.c').read_text(), Path(sys.argv[1]).read_text(), 'PHY'),
    (Path('/tmp/p310-clk-before.c').read_text(), Path(sys.argv[2]).read_text(), 'CLK'),
]
protected = [
    'DSI_W32(', 'MDSS_PLL_REG_W(', 'writel_relaxed(', 'writel(',
    'clk_set_rate(', 'clk_set_parent(', 'clk_prepare_enable(',
    'clk_disable_unprepare(', 'clk_prepare(', 'clk_unprepare(',
    'clk_enable(', 'clk_disable(', 'regulator_enable(', 'regulator_disable(',
    'reset_control_assert(', 'reset_control_deassert(', 'msleep(',
    'usleep_range(', 'udelay(', 'ndelay(', 'mdss_pll_resource_enable(',
    'phy->hw.ops.clamp_ctrl(',
]
for before, after, label in pairs:
    for token in protected:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f'Phase310 observer scope violation {label}: {token} '
                f'{before.count(token)} -> {after.count(token)}'
            )
required = [
    'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V1',
    'atomic_inc(&a52_p310_sr_in);',
    'atomic_inc(&a52_p310_sr_skip);',
    'atomic_inc(&a52_p310_sr_run);',
    'atomic_inc(&a52_p310_prepare_in);',
    'atomic_inc(&a52_p310_enable_in);',
    'atomic_inc(&a52_p310_hs_start);',
    'atomic_inc(&a52_p310_update_in);',
    'P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d',
    'P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d',
    'P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d',
    'a52_p310_clk_snapshot(index, point);',
]
combined = pairs[0][1] + pairs[1][1]
for token in required:
    if token not in combined:
        raise SystemExit('Phase310 required source token missing: ' + token)
print('Phase310 software-only link-clock lifecycle scope audit: PASS')
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
cp scripts/310_apply_gki_link_clock_lifecycle.py "$OUT/audit/"
cp /tmp/p310-* "$OUT/audit/" 2>/dev/null || true
cp "$PHY" "$OUT/source/dsi_phy.c"
cp "$CLK" "$OUT/source/dsi_clk_manager.c"
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
  'name': 'STICKY-DSI-LINK-CLOCK-LIFECYCLE-V1',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'Phase309 GKI exact-F0 PLL/PHY + persistent clamp release latch',
  'observer_only': True,
  'target': 'ctrl0 flags=0x20 msg.flags=0x8 type=0x29 len=3 payload=F0 5A 5A',
  'exact_f0_points': {'q0':'before SW_TRIGGER','q1':'immediately after SW_TRIGGER','q2':'after DMA completion/timeout'},
  'new_evidence': [
    'HS set-rate entered/run/continuous-splash-skipped/last-rc',
    'HS prepare entered/success/last-rc',
    'HS enable entered/success/last-rc',
    'HS start/stop and disable/unprepare counters',
    'LP start/stop counters',
    'link clock state update count/last request/last rc',
  ],
  'decision': {
    'sr_zero':'HS set-rate path never reached before exact F0',
    'sr_nonzero_skip_nonzero_run_zero':'continuous splash skipped HS source rate programming',
    'run_nonzero_prepare_or_enable_zero':'link-clock transition breaks after rate programming',
    'prepare_enable_success_nonzero_target_still_fails':'clock-manager lifecycle executed; next inspect DISP_CC branch/RCG/parent state directly',
  },
  'mmio_writes_added': False,
  'delay_or_timeout_changes_added': False,
  'clock_operations_added_or_removed': False,
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
echo 'Phase310 GKI sticky DSI link-clock lifecycle build/repack: PASS'
