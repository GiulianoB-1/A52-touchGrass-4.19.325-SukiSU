#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase307-gki-out"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TGPHYV3="$PWD/workspace/touchgrass-a52xq/techpack/display/msm/dsi/dsi_phy_hw_v3_0.c"
COMPAT="$ROOT/a52-port-compat.h"

fail_report() {
  set +e
  rm -rf phase307-gki-failure
  mkdir -p phase307-gki-failure/{logs,audit,source}
  cp phase307-gki-compile.log phase307-gki-olddefconfig.log phase307-gki-failure/logs/ 2>/dev/null || true
  for f in "$CTRL" "$HW" "$PHY" "$PHYV3" "$COMPAT"; do [ -f "$f" ] && cp "$f" phase307-gki-failure/source/ || true; done
  cp /tmp/p307-* phase307-gki-failure/audit/ 2>/dev/null || true
  cp scripts/307_apply_v3_phy_clocklane_correlation.py phase307-gki-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase305 state. Phase306 is intentionally excluded.
bash scripts/305_ci_build.sh

test -s phase305-out/package/boot.img
test -s phase305-out/compile/Image
test -s phase305-out/config/final.config
test "$(stat -c '%s' phase305-out/package/boot.img)" -eq 100663296
for f in "$CTRL" "$HW" "$PHY" "$PHYV3" "$TGPHYV3" "$COMPAT"; do test -s "$f"; done

grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"
grep -Fq 'A52_PHASE304_EXACT_F05A5A_VISIBILITY_V1' "$CTRL"
grep -Fq 'P276 303 S00p p=%02x%02x%02x' "$CTRL"

cp phase305-out/config/final.config /tmp/p307-phase305.config
cp "$CTRL" /tmp/p307-ctrl-before.c
cp "$HW" /tmp/p307-hw-before.c
cp "$PHY" /tmp/p307-phy-before.c
cp "$PHYV3" /tmp/p307-phyv3-gki.c
cp "$TGPHYV3" /tmp/p307-phyv3-touchgrass.c

# Source-level comparison of the actual v3 PHY programming implementation.
sha256sum "$PHYV3" "$TGPHYV3" > /tmp/p307-v3-phy-source.sha256
diff -u "$TGPHYV3" "$PHYV3" > /tmp/p307-v3-phy-source.diff || true
if cmp -s "$TGPHYV3" "$PHYV3"; then
  printf 'byte_identical=true\n' > /tmp/p307-v3-phy-source-summary.txt
else
  printf 'byte_identical=false\n' > /tmp/p307-v3-phy-source-summary.txt
fi

python3 -m py_compile scripts/307_apply_v3_phy_clocklane_correlation.py
python3 scripts/307_apply_v3_phy_clocklane_correlation.py --root "$ROOT"
python3 scripts/307_apply_v3_phy_clocklane_correlation.py --root "$ROOT" --check-only

# Observer-only scope audit. No new MMIO write, clock mutation, reset or delay primitive.
python3 - <<'PY'
from pathlib import Path
pairs = [
    ('/tmp/p307-ctrl-before.c', 'gki/common/drivers/a52_display/msm/dsi/dsi_ctrl.c'),
    ('/tmp/p307-hw-before.c', 'gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c'),
    ('/tmp/p307-phy-before.c', 'gki/common/drivers/a52_display/msm/dsi/dsi_phy.c'),
]
protected = [
    'DSI_W32(', 'writel_relaxed(', 'writel(', 'clk_set_rate(',
    'clk_prepare_enable(', 'clk_disable_unprepare(', 'regulator_enable(',
    'regulator_disable(', 'msleep(', 'usleep_range(', 'udelay(',
]
for before, after in pairs:
    a = Path(before).read_text()
    b = Path(after).read_text()
    for token in protected:
        if a.count(token) != b.count(token):
            raise SystemExit(f'Phase307 observer scope violation {Path(after).name}: {token} {a.count(token)} -> {b.count(token)}')
print('Phase307 observer-only primitive audit: PASS')
PY

# The PHY hardware programming source itself must remain untouched by Phase307.
cmp -s /tmp/p307-phyv3-gki.c "$PHYV3"
# Preserve Phase305 RPMh behavior exactly.
grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"

cp /tmp/p307-phase305.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase307-gki-olddefconfig.log 2>&1
cmp -s /tmp/p307-phase305.config "$BUILD/.config"

set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase307-gki-compile.log
rc=${PIPESTATUS[0]}
set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase307-gki-compile.log | tail -n 300 || true
  exit "$rc"
fi

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P276 307C q=%u st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x' \
  'P276 307P0 q=%u v=%u p=%u s=%u %x %x %x %x' \
  'P276 307P1 q=%u %x %x %x %x %x %x' \
  'P276 307P2 q=%u %x %x %x %x %x %x' \
  'P276 307P3 q=%u %x %x %x' \
  'P276 303 S00p p=%02x%02x%02x' \
  'P276 303 S06 st=%x fs=%x ln=%x ck=%x' \
  'P276 305F x r=%d l=%u b=%u' \
  'P276 280Z q=2'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase307-gki-compile.log phase307-gki-olddefconfig.log "$OUT/audit/"
cp scripts/307_apply_v3_phy_clocklane_correlation.py "$OUT/audit/"
cp /tmp/p307-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HW" "$PHY" "$PHYV3" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase305-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase307-gki-out')
summary = Path('/tmp/p307-v3-phy-source-summary.txt').read_text().strip().split('=',1)[1] == 'true'
repack = json.loads((r/'package/repack-report.json').read_text())
identity = {
  'phase': '307',
  'variant': 'GKI-PHASE305-BASE',
  'name': 'V3-PHY-CLOCKLANE-CORRELATION-V1',
  'git_sha': os.getenv('GITHUB_SHA'),
  'hardware_validated': False,
  'base': 'hardware-tested Phase305 flush-repair lineage; Phase306 solver experiment excluded',
  'observer_only': True,
  'target': 'ctrl0 flags=0x20 msg.flags=0x8 type=0x29 len=3 payload=F0 5A 5A',
  'points': {'q0':'immediately before SW_TRIGGER','q1':'immediately after SW_TRIGGER','q2':'after DMA completion/timeout'},
  'phy_version_expected': 'DSI_PHY_VERSION_3_0 (enum 5, 10-nm)',
  'touchgrass_gki_v3_phy_source_byte_identical': summary,
  'solver_stub_preserved': True,
  'phase305_flush_repair_preserved': True,
  'mmio_writes_added': False,
  'clock_or_regulator_changes_added': False,
  'wait_timeout_reset_changes_added': False,
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
  for p in sorted(files): f.write(hashlib.sha256(p.read_bytes()).hexdigest()+'  ./'+p.relative_to(r).as_posix()+'\n')
PY
(cd "$OUT" && sha256sum -c SHA256SUMS)
trap - EXIT
echo 'Phase307 GKI v3 PHY/clock-lane observer build/repack: PASS'
