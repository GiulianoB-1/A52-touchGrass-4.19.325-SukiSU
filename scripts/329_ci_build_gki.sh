#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p329-phase328-builder.sh
OUT="$PWD/phase329-gki-out"
FAIL="$PWD/phase329-gki-failure"
PHASE328_GOOD_SHA=12463112b74d00b826ae651f4405423923948907
PHASE328_IMAGE_SHA256=db6e6953027c1f5e2d32972156ed64636737c4a967229fd5f9f51dd5f8887bc0
PHASE328_BOOT_SHA256=7037a6ab1eb3b75780419c03d20bb7b85267acf7fbf6d160f9269056ad61da49

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -rf "$FAIL"; mkdir -p "$FAIL/phase329"
    [ -d phase328-gki-failure ] && cp -a phase328-gki-failure/. "$FAIL/" || true
    [ -d phase328-gki-out ] && cp -a phase328-gki-out/. "$FAIL/partial-out/" || true
    cp /tmp/p329-* "$FAIL/phase329/" 2>/dev/null || true
    cp scripts/329_apply_dsi_debugbus_selector_readback.py scripts/329_ci_build_gki.sh "$FAIL/phase329/" 2>/dev/null || true
    for f in "$CTRL" "$HWC" "$PHY" "$PHYV3"; do
      [ -f "$f" ] && cp "$f" "$FAIL/phase329/$(basename "$f")" || true
    done
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/329_apply_dsi_debugbus_selector_readback.py
bash -n scripts/328_ci_build_gki.sh
cp scripts/328_ci_build_gki.sh "$TMP"

# Inject Phase329 only after Phase328 has completed its own byte-identity and
# display-clock scope audits, but before the inherited Phase319 config/build.
python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
anchor = 'stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f"Phase329: expected one inherited config-invariant anchor, found {s.count(anchor)}")
insert = r'''stage "Phase329 DSI debug-bus selector CTL readback observer"
cp "$CTRL" /tmp/p329-ctrl-before.c
cp "$HWC" /tmp/p329-hwc-before.c
cp "$PHY" /tmp/p329-phy-before.c
cp "$PHYV3" /tmp/p329-phyv3-before.c
sha256sum /tmp/p329-ctrl-before.c /tmp/p329-hwc-before.c /tmp/p329-phy-before.c /tmp/p329-phyv3-before.c > /tmp/p329-dsi-before.sha256

python3 scripts/329_apply_dsi_debugbus_selector_readback.py --root "$ROOT" | tee /tmp/p329-apply.log
python3 scripts/329_apply_dsi_debugbus_selector_readback.py --root "$ROOT" --check-only

# Phase329 may alter only the Phase319 debug-bus observer implementation.
cmp -s /tmp/p329-ctrl-before.c "$CTRL"
! cmp -s /tmp/p329-hwc-before.c "$HWC"
cmp -s /tmp/p329-phy-before.c "$PHY"
cmp -s /tmp/p329-phyv3-before.c "$PHYV3"
diff -u /tmp/p329-hwc-before.c "$HWC" > /tmp/p329-hwc.diff || true
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c

grep -Fq 'A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1' "$HWC"
grep -Fq 'A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1' "$HWC"
grep -Fq 'ctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);' "$HWC"
grep -Fq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$HWC"
grep -Fq 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' "$HWC"

stage "config invariant"
'''
s = s.replace(anchor, insert, 1)
p.write_text(s)
PY

bash -n "$TMP"
bash "$TMP"

test -d phase328-gki-out
rm -rf "$OUT"
mv phase328-gki-out "$OUT"
mkdir -p "$OUT/audit" "$OUT/source"
cp scripts/329_apply_dsi_debugbus_selector_readback.py scripts/329_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p329-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$OUT/source/dsi_ctrl_hw_cmn.c"

# The inherited Phase328 identity was produced by a Phase328-shaped builder whose
# compile already contains Phase329. Preserve it as pipeline evidence, then write
# an unambiguous Phase329 identity with the known-good Phase328 base hashes.
mv "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE328-INHERITED-PIPELINE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase329-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn={
  'phase':'329',
  'flavor':'gki',
  'name':'DSI-DEBUGBUS-SELECTOR-READBACK-V1',
  'git_sha':os.getenv('GITHUB_SHA'),
  'hardware_validated':False,
  'base_phase':'328',
  'base_git_sha':'12463112b74d00b826ae651f4405423923948907',
  'base_image_sha256':'db6e6953027c1f5e2d32972156ed64636737c4a967229fd5f9f51dd5f8887bc0',
  'base_boot_img_sha256':'7037a6ab1eb3b75780419c03d20bb7b85267acf7fbf6d160f9269056ad61da49',
  'experiment':'read back DSI_DEBUG_BUS_CTL immediately after each of the six Phase319 selector writes',
  'expected_readbacks':['0x171','0x181','0x191','0x1a1','0x1e1','0x211'],
  'runtime_changes':['observer-only DSI_DEBUG_BUS_CTL readback and P276 329C recorder marker'],
  'forbidden_runtime_changes':['clock','PHY','reset','regulator','delay','retry','DMA repair'],
  'phase328_full_display_clock_port_preserved':True,
  'phase319_marker_preserved':True,
  'autoreboot':'not included; manual reboot/recovery required',
  'image_sha256':sha(r/'compile/Image'),
  'boot_img_sha256':sha(r/'package/boot.img'),
  'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
grep -aFq 'P276 328V s=3 rc=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase329 DSI debug-bus selector CTL readback observer: PASS'
trap - EXIT
