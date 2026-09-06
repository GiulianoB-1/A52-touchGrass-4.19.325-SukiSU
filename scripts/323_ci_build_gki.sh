#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p323-phase319-builder.sh
OUT="$PWD/phase323-gki-out"
FAIL="$PWD/phase323-gki-failure"

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$FAIL"
    [ -d phase319-gki-failure ] && cp -a phase319-gki-failure/. "$FAIL/" || true
    mkdir -p "$FAIL/phase323"
    cp /tmp/p323-* "$FAIL/phase323/" 2>/dev/null || true
    cp scripts/323_apply_dsi_phy_parent_enable.py scripts/323_ci_build_gki.sh "$FAIL/phase323/" 2>/dev/null || true
    [ -f "$DISP" ] && cp "$DISP" "$FAIL/phase323/dispcc-lagoon.c" || true
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/323_apply_dsi_phy_parent_enable.py
bash -n scripts/319_ci_build_gki.sh
cp scripts/319_ci_build_gki.sh "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
anchor='stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase323: expected one config-invariant anchor, found {s.count(anchor)}')
insert=r'''stage "Phase323 isolated DSI PHY parent-enable A/B"
cp "$DISP" /tmp/p323-disp-before.c
cp "$CTRL" /tmp/p323-ctrl-before.c
cp "$HWC" /tmp/p323-hwc-before.c
cp "$PHY" /tmp/p323-phy-before.c
cp "$PHYV3" /tmp/p323-phyv3-before.c

# The runtime baseline must be Phase319, not cumulative Phase321/322 state.
! grep -Fq 'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1' "$DISP"
! grep -Fq 'A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1' "$DISP"
! grep -Fq 'A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1' "$DISP"

python3 scripts/323_apply_dsi_phy_parent_enable.py --file "$DISP" | tee /tmp/p323-apply.log
python3 scripts/323_apply_dsi_phy_parent_enable.py --file "$DISP" --check-only
git -C "$ROOT" diff --check -- drivers/clk/qcom/dispcc-lagoon.c
cp "$DISP" /tmp/p323-disp-after.c
diff -u /tmp/p323-disp-before.c /tmp/p323-disp-after.c > /tmp/p323-disp.diff || true

# No DSI controller, DSI PHY or recorder source is allowed to move.
cmp -s /tmp/p323-ctrl-before.c "$CTRL"
cmp -s /tmp/p323-hwc-before.c "$HWC"
cmp -s /tmp/p323-phy-before.c "$PHY"
cmp -s /tmp/p323-phyv3-before.c "$PHYV3"

python3 - <<'P323AUDIT'
from pathlib import Path
b=Path('/tmp/p323-disp-before.c').read_text()
a=Path('/tmp/p323-disp-after.c').read_text()
marker='A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1'
if a.count(marker)-b.count(marker) != 1:
    raise SystemExit('Phase323 audit: marker delta != 1')
if a.count('CLK_OPS_PARENT_ENABLE')-b.count('CLK_OPS_PARENT_ENABLE') != 2:
    raise SystemExit('Phase323 audit: CLK_OPS_PARENT_ENABLE delta != 2')

def block(s,name):
    h=f'static struct clk_rcg2 {name} = {{'
    st=s.find(h)
    if st < 0: raise SystemExit('Phase323 audit: missing '+name)
    en=s.find('\n};',st)
    if en < 0: raise SystemExit('Phase323 audit: unterminated '+name)
    return s[st:en+3]

for name,ops,parent,cmd in (
    ('disp_cc_mdss_byte0_clk_src','clk_byte2_ops','disp_cc_parent_map_1','0x10c4'),
    ('disp_cc_mdss_pclk0_clk_src','clk_pixel_ops','disp_cc_parent_map_5','0x1064'),
):
    bb=block(b,name); ab=block(a,name)
    if 'CLK_OPS_PARENT_ENABLE' in bb or ab.count('CLK_OPS_PARENT_ENABLE') != 1:
        raise SystemExit('Phase323 audit: invalid parent-enable state for '+name)
    for tok in (f'.ops = &{ops},', f'.parent_map = {parent},', f'.cmd_rcgr = {cmd},'):
        if bb.count(tok) != 1 or ab.count(tok) != 1:
            raise SystemExit('Phase323 audit: target identity changed for '+name+': '+tok)

# Phase321 and Phase322 are independent A/Bs and must not be cumulative here.
for forbidden in (
    'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1',
    'A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1',
):
    if forbidden in a:
        raise SystemExit('Phase323 audit: prior experiment marker leaked: '+forbidden)

# Preserve specialized byte/pixel ops and forbid unrelated framework or MMIO interventions.
for token in (
    'clk_rcg2_shared_ops', '.safe_src_index', '.enable_safe_config',
    '.vdd_class', '.num_rate_max', '.rate_max', 'devm_regulator_get(',
    'regulator_set_voltage(', 'regulator_enable(', 'regulator_disable(',
    'regmap_write(', 'regmap_update_bits(', 'clk_set_rate(', 'clk_set_parent(',
    'clk_prepare_enable(', 'clk_disable_unprepare(', 'writel(', 'writel_relaxed(',
    'udelay(', 'ndelay(', 'usleep_range(', 'msleep(', 'reset_control_',
):
    if a.count(token) != b.count(token):
        raise SystemExit('Phase323 audit: forbidden delta: '+token)
print('Phase323 isolated DSI PHY parent-enable source-scope audit: PASS')
P323AUDIT

'''
p.write_text(s.replace(anchor, insert+anchor))
PY

bash -n "$TMP"
bash "$TMP"

test -d phase319-gki-out
rm -rf "$OUT"
mv phase319-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/323_apply_dsi_phy_parent_enable.py scripts/323_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p323-disp-before.c /tmp/p323-disp-after.c /tmp/p323-disp.diff /tmp/p323-apply.log "$OUT/audit/"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"

# Preserve the Phase319-generated identity as provenance, then write Phase323 identity.
cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE319-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase323-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base=json.loads((r/'audit/PHASE319-GENERATED-BASE-IDENTITY.json').read_text())
idn={
    'phase':'323',
    'flavor':'gki',
    'name':'DSI-PHY-PARENT-ENABLE-AB-V1',
    'git_sha':os.getenv('GITHUB_SHA'),
    'hardware_validated':False,
    'base_phase_identity':base,
    'intervention':'disp_cc_mdss_byte0_clk_src and disp_cc_mdss_pclk0_clk_src only: add CLK_OPS_PARENT_ENABLE while preserving clk_byte2_ops/clk_pixel_ops',
    'hypothesis':'DSI PHY-derived RCG set_rate/set_parent programming requires its parent clocks enabled during the callback',
    'phase321_esc0_shared_safe_lifecycle':'absent',
    'phase322_vdd_cx_nominal_vote':'absent',
    'phase319_recorder_preserved':True,
    'vdd_regulator_dsi_phy_dma_delay_retry_reset_changes':'none',
    'image_sha256':sha(r/'compile/Image'),
    'boot_img_sha256':sha(r/'package/boot.img'),
    'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY

# Final source/binary invariants.
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -Fq 'A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1' "$OUT/source/dispcc-lagoon.c"
test "$(grep -F 'CLK_OPS_PARENT_ENABLE' "$OUT/source/dispcc-lagoon.c" | wc -l)" -ge 2
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase323 GKI DSI PHY parent-enable A/B: PASS'
trap - EXIT
