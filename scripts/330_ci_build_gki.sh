#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p330-phase329-builder.sh
OUT="$PWD/phase330-gki-out"
FAIL="$PWD/phase330-gki-failure"
PHASE328_GOOD_SHA=12463112b74d00b826ae651f4405423923948907
PHASE328_IMAGE_SHA256=db6e6953027c1f5e2d32972156ed64636737c4a967229fd5f9f51dd5f8887bc0
PHASE328_BOOT_SHA256=7037a6ab1eb3b75780419c03d20bb7b85267acf7fbf6d160f9269056ad61da49

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -rf "$FAIL"; mkdir -p "$FAIL/phase330"
    [ -d phase329-gki-failure ] && cp -a phase329-gki-failure/. "$FAIL/phase329-inherited-failure/" || true
    [ -d phase329-gki-out ] && cp -a phase329-gki-out/. "$FAIL/partial-out/" || true
    cp /tmp/p330-* "$FAIL/phase330/" 2>/dev/null || true
    cp scripts/330_apply_dsi_early_breadcrumb_recorder.py scripts/330_ci_build_gki.sh "$FAIL/phase330/" 2>/dev/null || true
    for f in "$CTRL" "$HWC" "$PHY" "$PHYV3"; do
      [ -f "$f" ] && cp "$f" "$FAIL/phase330/$(basename "$f")" || true
    done
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/330_apply_dsi_early_breadcrumb_recorder.py
bash -n scripts/329_ci_build_gki.sh
cp scripts/329_ci_build_gki.sh "$TMP"

# Phase329 already injects its selector-CTL observer into the Phase328 generated
# builder before config/build. Extend that same generated block with Phase330 so
# the final kernel is built once with both Phase329 and Phase330 instrumentation.
python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
anchor = '''grep -Fq 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' "$HWC"

''' + "'''"
if s.count(anchor) != 1:
    raise SystemExit(f"Phase330: expected one Phase329 generated-builder tail anchor, found {s.count(anchor)}")
phase330 = r'''grep -Fq 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' "$HWC"

stage "Phase330 DSI early breadcrumb recorder"
cp "$CTRL" /tmp/p330-ctrl-before.c
cp "$HWC" /tmp/p330-hwc-before.c
cp "$PHY" /tmp/p330-phy-before.c
cp "$PHYV3" /tmp/p330-phyv3-before.c
sha256sum /tmp/p330-ctrl-before.c /tmp/p330-hwc-before.c /tmp/p330-phy-before.c /tmp/p330-phyv3-before.c > /tmp/p330-dsi-before.sha256

python3 scripts/330_apply_dsi_early_breadcrumb_recorder.py --root "$ROOT" | tee /tmp/p330-apply.log
python3 scripts/330_apply_dsi_early_breadcrumb_recorder.py --root "$ROOT" --check-only

# Phase330 is recorder-only and may alter only the inherited Phase319/329 helper.
cmp -s /tmp/p330-ctrl-before.c "$CTRL"
! cmp -s /tmp/p330-hwc-before.c "$HWC"
cmp -s /tmp/p330-phy-before.c "$PHY"
cmp -s /tmp/p330-phyv3-before.c "$PHYV3"
diff -u /tmp/p330-hwc-before.c "$HWC" > /tmp/p330-hwc.diff || true
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c

grep -Fq 'A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1' "$HWC"
grep -Fq 'A52_PHASE330_DSI_EARLY_BREADCRUMB_RECORDER_V1' "$HWC"
grep -Fq 'P276 330A q=%u' "$HWC"
grep -Fq 'P276 330B q=%u c=%x' "$HWC"
grep -Fq 'P276 330C q=%u i=%u c=%x v=%x' "$HWC"
grep -Fq 'P276 330D q=%u c=%x z=%x' "$HWC"

'''
replacement = phase330 + "'''"
p.write_text(s.replace(anchor, replacement, 1))
PY

bash -n "$TMP"
bash "$TMP"

test -d phase329-gki-out
rm -rf "$OUT"
mv phase329-gki-out "$OUT"
mkdir -p "$OUT/audit" "$OUT/source"
cp scripts/330_apply_dsi_early_breadcrumb_recorder.py scripts/330_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p330-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$OUT/source/dsi_ctrl_hw_cmn.c"

# Preserve the inherited Phase329 identity as build-lineage evidence, then emit
# a Phase330 identity for the actually compiled image.
mv "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE329-INHERITED-PIPELINE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase330-gki-out')
def sha(p):
    h = hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn = {
  'phase':'330',
  'flavor':'gki',
  'name':'DSI-EARLY-BREADCRUMB-RECORDER-V1',
  'git_sha':os.getenv('GITHUB_SHA'),
  'hardware_validated':False,
  'base_phase':'329',
  'phase328_good_git_sha':'12463112b74d00b826ae651f4405423923948907',
  'phase328_image_sha256':'db6e6953027c1f5e2d32972156ed64636737c4a967229fd5f9f51dd5f8887bc0',
  'phase328_boot_img_sha256':'7037a6ab1eb3b75780419c03d20bb7b85267acf7fbf6d160f9269056ad61da49',
  'experiment':'breadcrumb the inherited Phase319/329 six-selector observer before the saved CTL read, after it, after each selector read, and after selector restore',
  'runtime_changes':['P276 330A/330B/330C/330D recorder breadcrumbs only'],
  'forbidden_runtime_changes':['clock','PHY','reset','regulator','delay','retry','DMA repair','selector set'],
  'phase329_selector_readback_preserved':True,
  'phase328_full_display_clock_port_preserved':True,
  'autoreboot':'not included; manual reboot/recovery required',
  'image_sha256':sha(r/'compile/Image'),
  'boot_img_sha256':sha(r/'package/boot.img'),
  'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True)+'\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' "$OUT/compile/Image"
grep -aFq 'P276 330A q=%u' "$OUT/compile/Image"
grep -aFq 'P276 330B q=%u c=%x' "$OUT/compile/Image"
grep -aFq 'P276 330C q=%u i=%u c=%x v=%x' "$OUT/compile/Image"
grep -aFq 'P276 330D q=%u c=%x z=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
grep -aFq 'P276 328V s=3 rc=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase330 DSI early breadcrumb recorder: PASS'
trap - EXIT
