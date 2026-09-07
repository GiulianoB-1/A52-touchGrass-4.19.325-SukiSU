#!/usr/bin/env bash
set -Eeuo pipefail

: "${PHASE324_VARIANT:?PHASE324_VARIANT must be byte0 or pclk0}"
case "$PHASE324_VARIANT" in
  byte0|pclk0) ;;
  *) echo "Phase324: invalid PHASE324_VARIANT=$PHASE324_VARIANT" >&2; exit 2 ;;
esac

ROOT="$PWD/gki/common"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP="/tmp/p324-${PHASE324_VARIANT}-phase319-builder.sh"
OUT="$PWD/phase324-${PHASE324_VARIANT}-gki-out"
FAIL="$PWD/phase324-${PHASE324_VARIANT}-gki-failure"
PREFIX="/tmp/p324-${PHASE324_VARIANT}"

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$FAIL"
    [ -d phase319-gki-failure ] && cp -a phase319-gki-failure/. "$FAIL/" || true
    mkdir -p "$FAIL/phase324"
    cp ${PREFIX}-* "$FAIL/phase324/" 2>/dev/null || true
    cp scripts/324_apply_parent_enable_split.py scripts/324_ci_build_gki.sh "$FAIL/phase324/" 2>/dev/null || true
    [ -f "$DISP" ] && cp "$DISP" "$FAIL/phase324/dispcc-lagoon.c" || true
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/324_apply_parent_enable_split.py
bash -n scripts/319_ci_build_gki.sh
cp scripts/319_ci_build_gki.sh "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
anchor='stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase324: expected one config-invariant anchor, found {s.count(anchor)}')
insert=r'''stage "Phase324 isolated BYTE0/PCLK0 parent-enable split A/B"
cp "$DISP" "${PREFIX}-disp-before.c"
cp "$CTRL" "${PREFIX}-ctrl-before.c"
cp "$HWC" "${PREFIX}-hwc-before.c"
cp "$PHY" "${PREFIX}-phy-before.c"
cp "$PHYV3" "${PREFIX}-phyv3-before.c"

# Every matrix leg starts from the exact Phase319 runtime baseline.
for marker in \
  A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1 \
  A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1 \
  A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1 \
  A52_PHASE324_BYTE0_PARENT_ENABLE_ONLY_V1 \
  A52_PHASE324_PCLK0_PARENT_ENABLE_ONLY_V1; do
  ! grep -Fq "$marker" "$DISP"
done

python3 scripts/324_apply_parent_enable_split.py --file "$DISP" --target "$PHASE324_VARIANT" | tee "${PREFIX}-apply.log"
python3 scripts/324_apply_parent_enable_split.py --file "$DISP" --target "$PHASE324_VARIANT" --check-only
git -C "$ROOT" diff --check -- drivers/clk/qcom/dispcc-lagoon.c
cp "$DISP" "${PREFIX}-disp-after.c"
diff -u "${PREFIX}-disp-before.c" "${PREFIX}-disp-after.c" > "${PREFIX}-disp.diff" || true

# DSI transaction code, PHY code and recorder remain untouched.
cmp -s "${PREFIX}-ctrl-before.c" "$CTRL"
cmp -s "${PREFIX}-hwc-before.c" "$HWC"
cmp -s "${PREFIX}-phy-before.c" "$PHY"
cmp -s "${PREFIX}-phyv3-before.c" "$PHYV3"

python3 - <<'P324AUDIT'
import os
from pathlib import Path
v=os.environ['PHASE324_VARIANT']
b=Path(f'/tmp/p324-{v}-disp-before.c').read_text()
a=Path(f'/tmp/p324-{v}-disp-after.c').read_text()
if a.count('CLK_OPS_PARENT_ENABLE')-b.count('CLK_OPS_PARENT_ENABLE') != 1:
    raise SystemExit('Phase324 audit: global parent-enable delta != 1')

def block(s,name):
    h=f'static struct clk_rcg2 {name} = {{'
    st=s.find(h)
    if st < 0: raise SystemExit('Phase324 audit: missing '+name)
    en=s.find('\n};',st)
    if en < 0: raise SystemExit('Phase324 audit: unterminated '+name)
    return s[st:en+3]

targets={
 'byte0':('disp_cc_mdss_byte0_clk_src','clk_byte2_ops','disp_cc_parent_map_1','0x10c4','A52_PHASE324_BYTE0_PARENT_ENABLE_ONLY_V1'),
 'pclk0':('disp_cc_mdss_pclk0_clk_src','clk_pixel_ops','disp_cc_parent_map_5','0x1064','A52_PHASE324_PCLK0_PARENT_ENABLE_ONLY_V1'),
}
for key,(name,ops,parent,cmd,marker) in targets.items():
    bb=block(b,name); ab=block(a,name)
    for tok in (f'.ops = &{ops},',f'.parent_map = {parent},',f'.cmd_rcgr = {cmd},'):
        if bb.count(tok)!=1 or ab.count(tok)!=1:
            raise SystemExit('Phase324 audit: target identity changed for '+key+': '+tok)
    if key == v:
        if ab.count('CLK_OPS_PARENT_ENABLE') != 1 or a.count(marker) != 1:
            raise SystemExit('Phase324 audit: selected target not uniquely enabled: '+key)
    else:
        if 'CLK_OPS_PARENT_ENABLE' in ab or marker in a:
            raise SystemExit('Phase324 audit: non-selected target moved: '+key)

for forbidden in (
 'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1',
 'A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1',
 'A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1',
):
    if forbidden in a:
        raise SystemExit('Phase324 audit: prior experiment marker leaked: '+forbidden)

for token in (
 'clk_rcg2_shared_ops','.safe_src_index','.enable_safe_config',
 '.vdd_class','.num_rate_max','.rate_max','devm_regulator_get(',
 'regulator_set_voltage(','regulator_enable(','regulator_disable(',
 'regmap_write(','regmap_update_bits(','clk_set_rate(','clk_set_parent(',
 'clk_prepare_enable(','clk_disable_unprepare(','writel(','writel_relaxed(',
 'udelay(','ndelay(','usleep_range(','msleep(','reset_control_',
):
    if a.count(token) != b.count(token):
        raise SystemExit('Phase324 audit: forbidden delta: '+token)
print(f'Phase324 {v} isolated source-scope audit: PASS')
P324AUDIT

'''
p.write_text(s.replace(anchor,insert+anchor))
PY

bash -n "$TMP"
bash "$TMP"

test -d phase319-gki-out
rm -rf "$OUT"
mv phase319-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/324_apply_parent_enable_split.py scripts/324_ci_build_gki.sh "$OUT/audit/"
cp "${PREFIX}-disp-before.c" "${PREFIX}-disp-after.c" "${PREFIX}-disp.diff" "${PREFIX}-apply.log" "$OUT/audit/"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE319-GENERATED-BASE-IDENTITY.json"

python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
v=os.environ['PHASE324_VARIANT']
r=Path(f'phase324-{v}-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base=json.loads((r/'audit/PHASE319-GENERATED-BASE-IDENTITY.json').read_text())
idn={
 'phase':'324',
 'flavor':'gki',
 'variant':v,
 'name':f'{v.upper()}-PARENT-ENABLE-ONLY-V1',
 'git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,
 'base_phase_identity':base,
 'intervention':f'{v} only: add CLK_OPS_PARENT_ENABLE; the other Phase323 target remains at exact Phase319 flags',
 'purpose':'decompose Phase323 selector-state movement into BYTE0 versus PCLK0 causality',
 'phase319_recorder_preserved':True,
 'phase321_esc0_shared_safe_lifecycle':'absent',
 'phase322_vdd_cx_nominal_vote':'absent',
 'phase323_combined_parent_enable':'absent',
 'dsi_phy_regulator_timing_mmio_other_clock_changes':'none',
 'image_sha256':sha(r/'compile/Image'),
 'boot_img_sha256':sha(r/'package/boot.img'),
 'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
python3 scripts/324_apply_parent_enable_split.py --file "$OUT/source/dispcc-lagoon.c" --target "$PHASE324_VARIANT" --check-only
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo "Phase324 $PHASE324_VARIANT parent-enable-only: PASS"
trap - EXIT
