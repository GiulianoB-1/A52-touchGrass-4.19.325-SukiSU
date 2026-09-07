#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
RCG2="$ROOT/drivers/clk/qcom/clk-rcg2.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p325-phase319-builder.sh
OUT="$PWD/phase325-gki-out"
FAIL="$PWD/phase325-gki-failure"
GKI_RCG2_SHA256=064a5aba4c0e87072bf3f18e5dd5788e7aeeb5c3a04a751f87552693ef21a9ba

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$FAIL/phase325"
    [ -d phase319-gki-failure ] && cp -a phase319-gki-failure/. "$FAIL/" || true
    cp /tmp/p325-* "$FAIL/phase325/" 2>/dev/null || true
    cp scripts/325_apply_touchgrass_rcg_current_config.py scripts/325_ci_build_gki.sh "$FAIL/phase325/" 2>/dev/null || true
    [ -f "$RCG2" ] && cp "$RCG2" "$FAIL/phase325/clk-rcg2.c" || true
    [ -f "$DISP" ] && cp "$DISP" "$FAIL/phase325/dispcc-lagoon.c" || true
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/325_apply_touchgrass_rcg_current_config.py
bash -n scripts/319_ci_build_gki.sh
cp scripts/319_ci_build_gki.sh "$TMP"

python3 - "$TMP" "$GKI_RCG2_SHA256" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
expected_sha = sys.argv[2]
s = p.read_text()
anchor = 'stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase325: expected one config-invariant anchor, found {s.count(anchor)}')
insert = rf'''stage "Phase325 exact TouchGrass redundant-RCG-update semantic port"
RCG2="$ROOT/drivers/clk/qcom/clk-rcg2.c"
cp "$RCG2" /tmp/p325-rcg2-before.c
cp "$DISP" /tmp/p325-disp-before.c
cp "$CTRL" /tmp/p325-ctrl-before.c
cp "$HWC" /tmp/p325-hwc-before.c
cp "$PHY" /tmp/p325-phy-before.c
cp "$PHYV3" /tmp/p325-phyv3-before.c

# Phase319 must leave the common RCG framework byte-identical to the exact
# pinned f960ed... source audited in Phase320 before this semantic port.
printf '%s  %s\n' '{expected_sha}' "$RCG2" | sha256sum -c -
! grep -Fq 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1' "$RCG2"
! grep -Fq 'static bool clk_rcg2_current_config(' "$RCG2"

# Prior heuristic experiments are not cumulative runtime inputs here.
! grep -Fq 'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1' "$DISP"
! grep -Fq 'A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1' "$DISP"
! grep -Fq 'A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1' "$DISP"
! grep -Fq 'A52_PHASE324_PARENT_ENABLE_SPLIT_AB_V1' "$DISP"

python3 scripts/325_apply_touchgrass_rcg_current_config.py --file "$RCG2" | tee /tmp/p325-apply.log
python3 scripts/325_apply_touchgrass_rcg_current_config.py --file "$RCG2" --check-only
git -C "$ROOT" diff --check -- drivers/clk/qcom/clk-rcg2.c
cp "$RCG2" /tmp/p325-rcg2-after.c
diff -u /tmp/p325-rcg2-before.c /tmp/p325-rcg2-after.c > /tmp/p325-rcg2.diff || true

# No Lagoon definition, DSI controller or DSI PHY source may move in Phase325.
cmp -s /tmp/p325-disp-before.c "$DISP"
cmp -s /tmp/p325-ctrl-before.c "$CTRL"
cmp -s /tmp/p325-hwc-before.c "$HWC"
cmp -s /tmp/p325-phy-before.c "$PHY"
cmp -s /tmp/p325-phyv3-before.c "$PHYV3"

python3 - <<'P325AUDIT'
from pathlib import Path
b = Path('/tmp/p325-rcg2-before.c').read_text()
a = Path('/tmp/p325-rcg2-after.c').read_text()
marker = 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1'
call = 'clk_rcg2_current_config(rcg, &f)'
helper = 'static bool clk_rcg2_current_config('
if a.count(marker) - b.count(marker) != 1:
    raise SystemExit('Phase325 audit: marker delta != 1')
if a.count(helper) - b.count(helper) != 1:
    raise SystemExit('Phase325 audit: helper delta != 1')
if a.count(call) - b.count(call) != 2:
    raise SystemExit('Phase325 audit: current-config call delta != 2')

def fn(text, name):
    pos = text.find(name + '(')
    if pos < 0:
        raise SystemExit('Phase325 audit: missing function ' + name)
    start = text.rfind('\n', 0, pos) + 1
    brace = text.find('{{', pos)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == '{{': depth += 1
        elif text[i] == '}}':
            depth -= 1
            if depth == 0:
                return text[start:i+1]
    raise SystemExit('Phase325 audit: unterminated function ' + name)

for name in ('clk_byte2_set_rate', 'clk_pixel_set_rate'):
    if fn(b, name).count(call) != 0 or fn(a, name).count(call) != 1:
        raise SystemExit('Phase325 audit: guard scope mismatch for ' + name)

# The new semantic must not alter the shared/safe lifecycle, generic RCG ops,
# clock flags, or any unrelated framework mechanism.
for token in (
    'clk_rcg2_shared_ops', 'clk_rcg2_shared_set_rate',
    'clk_rcg2_shared_enable', 'clk_rcg2_shared_disable',
    'safe_src_index', 'CLK_OPS_PARENT_ENABLE', 'clk_rcg2_ops',
    'clk_byte2_ops', 'clk_pixel_ops', 'udelay(', 'usleep_range(',
    'msleep(', 'reset_control_', 'regulator_',
):
    if a.count(token) != b.count(token):
        raise SystemExit('Phase325 audit: forbidden unrelated delta: ' + token)
print('Phase325 exact source-scope semantic audit: PASS')
P325AUDIT

'''
p.write_text(s.replace(anchor, insert + anchor))
PY

bash -n "$TMP"
bash "$TMP"

test -d phase319-gki-out
rm -rf "$OUT"
mv phase319-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/325_apply_touchgrass_rcg_current_config.py scripts/325_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p325-rcg2-before.c /tmp/p325-rcg2-after.c /tmp/p325-rcg2.diff /tmp/p325-apply.log "$OUT/audit/"
cp "$RCG2" "$OUT/source/clk-rcg2.c"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"

cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE319-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase325-gki-out')
def sha(p):
    h = hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base = json.loads((r/'audit/PHASE319-GENERATED-BASE-IDENTITY.json').read_text())
idn = {
    'phase': '325',
    'flavor': 'gki',
    'name': 'TOUCHGRASS-RCG-CURRENT-CONFIG-SEMANTIC-PORT-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base_phase_identity': base,
    'source_reference': 'TouchGrass 6bf351bdf18bdb228db79e66f14a7a9c0178e5d7 drivers/clk/qcom/clk-rcg2.c',
    'intervention': 'Port clk_rcg2_current_config plus redundant-update guards in clk_byte2_set_rate and clk_pixel_set_rate only',
    'hypothesis': 'TouchGrass avoids an unnecessary BYTE0/PCLK0 CMD_UPDATE when the RCG registers already match the requested configuration; GKI currently reissues that update',
    'phase321_esc0_shared_safe_lifecycle': 'absent',
    'phase322_vdd_cx_nominal_vote': 'absent',
    'phase323_parent_enable': 'absent',
    'phase324_parent_enable_split': 'absent',
    'safe_config_parent_enable_vdd_dsi_phy_delay_retry_reset_changes': 'none',
    'phase319_recorder_preserved': True,
    'image_sha256': sha(r/'compile/Image'),
    'boot_img_sha256': sha(r/'package/boot.img'),
    'boot_img_size': (r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -Fq 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1' "$OUT/source/clk-rcg2.c"
test "$(grep -F 'clk_rcg2_current_config(rcg, &f)' "$OUT/source/clk-rcg2.c" | wc -l)" -eq 2
! grep -Fq 'CLK_OPS_PARENT_ENABLE' "$OUT/source/dispcc-lagoon.c"
! grep -Fq 'A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1' "$OUT/source/dispcc-lagoon.c"
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase325 GKI exact TouchGrass RCG current-config semantic port: PASS'
trap - EXIT
