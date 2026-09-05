#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p321-phase319-builder.sh
OUT="$PWD/phase321-gki-out"
FAIL="$PWD/phase321-gki-failure"

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$FAIL"
    [ -d phase319-gki-failure ] && cp -a phase319-gki-failure/. "$FAIL/" || true
    mkdir -p "$FAIL/phase321"
    cp /tmp/p321-* "$FAIL/phase321/" 2>/dev/null || true
    cp scripts/321_apply_esc0_shared_safe.py scripts/321_ci_build_gki.sh "$FAIL/phase321/" 2>/dev/null || true
    [ -f "$DISP" ] && cp "$DISP" "$FAIL/phase321/dispcc-lagoon.c" || true
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/321_apply_esc0_shared_safe.py
bash -n scripts/319_ci_build_gki.sh

cp scripts/319_ci_build_gki.sh "$TMP"
python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
anchor='stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase321: expected one config-invariant anchor, found {s.count(anchor)}')
insert=r'''stage "Phase321 isolated ESC0 native safe-lifecycle A/B"
printf 'Phase321 runtime PWD=%s\n' "$PWD" | tee /tmp/p321-runtime-source.txt
printf 'Phase321 runtime ROOT=%s\n' "$ROOT" | tee -a /tmp/p321-runtime-source.txt
printf 'Phase321 runtime DISP=%s\n' "$DISP" | tee -a /tmp/p321-runtime-source.txt
readlink -f "$ROOT" | sed 's/^/Phase321 real ROOT=/' | tee -a /tmp/p321-runtime-source.txt
readlink -f "$DISP" | sed 's/^/Phase321 real DISP=/' | tee -a /tmp/p321-runtime-source.txt
sha256sum "$DISP" | tee -a /tmp/p321-runtime-source.txt
stat -c 'Phase321 DISP inode=%i size=%s mtime=%y' "$DISP" | tee -a /tmp/p321-runtime-source.txt
grep -n -m1 'struct parent_map disp_cc_parent_map_1' "$DISP" | tee -a /tmp/p321-runtime-source.txt
grep -n -m1 'static struct clk_rcg2 disp_cc_mdss_esc0_clk_src' "$DISP" | tee -a /tmp/p321-runtime-source.txt
cp "$DISP" /tmp/p321-disp-before.c
cp "$CTRL" /tmp/p321-ctrl-before.c
cp "$HWC" /tmp/p321-hwc-before.c
cp "$PHY" /tmp/p321-phy-before.c
cp "$PHYV3" /tmp/p321-phyv3-before.c
python3 scripts/321_apply_esc0_shared_safe.py --file "$DISP" | tee /tmp/p321-apply.log
python3 scripts/321_apply_esc0_shared_safe.py --file "$DISP" --check-only
git -C "$ROOT" diff --check -- drivers/clk/qcom/dispcc-lagoon.c
cp "$DISP" /tmp/p321-disp-after.c
diff -u /tmp/p321-disp-before.c /tmp/p321-disp-after.c > /tmp/p321-disp.diff || true
cmp -s /tmp/p321-ctrl-before.c "$CTRL"
cmp -s /tmp/p321-hwc-before.c "$HWC"
cmp -s /tmp/p321-phy-before.c "$PHY"
cmp -s /tmp/p321-phyv3-before.c "$PHYV3"
python3 - <<'P321AUDIT'
from pathlib import Path
import re
b=Path('/tmp/p321-disp-before.c').read_text()
a=Path('/tmp/p321-disp-after.c').read_text()
name='static struct clk_rcg2 disp_cc_mdss_esc0_clk_src = {'

def split_esc0(s):
    st=s.find(name)
    if st < 0: raise SystemExit('Phase321 audit: ESC0 block missing')
    en=s.find('\n};', st)
    if en < 0: raise SystemExit('Phase321 audit: ESC0 terminator missing')
    en += 3
    return s[:st], s[st:en], s[en:]

bp,bb,bs=split_esc0(b)
ap,ab,as_=split_esc0(a)
if bp != ap or bs != as_:
    raise SystemExit('Phase321 audit: change escaped ESC0 initializer')
if bb.count('.ops = &clk_rcg2_ops,') != 1:
    raise SystemExit('Phase321 audit: baseline ESC0 clk_rcg2_ops count != 1')
if '.safe_src_index' in bb:
    raise SystemExit('Phase321 audit: baseline ESC0 unexpectedly already safe-indexed')
if ab.count('.ops = &clk_rcg2_shared_ops,') != 1 or '.ops = &clk_rcg2_ops,' in ab:
    raise SystemExit('Phase321 audit: ESC0 shared ops replacement invalid')
if ab.count('.safe_src_index = 0,') != 1:
    raise SystemExit('Phase321 audit: ESC0 safe source index invalid')
if 'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1' not in ab:
    raise SystemExit('Phase321 audit: marker missing')
if '.parent_map = disp_cc_parent_map_1,' not in ab:
    raise SystemExit('Phase321 audit: ESC0 parent map changed')
# Globally, this A/B is allowed to add exactly one shared-ops reference and one safe-src field.
if a.count('clk_rcg2_shared_ops') - b.count('clk_rcg2_shared_ops') != 1:
    raise SystemExit('Phase321 audit: unexpected shared-ops delta')
if a.count('.safe_src_index') - b.count('.safe_src_index') != 1:
    raise SystemExit('Phase321 audit: unexpected safe-src-index delta')
# Explicitly forbid the separate Phase322 candidate from leaking into this test.
for token in ('vdd_cx', '.vdd_class', '.num_rate_max', '.rate_max', 'devm_regulator_get'):
    if a.count(token) != b.count(token):
        raise SystemExit('Phase321 audit: forbidden VDD/rate-vote delta: '+token)
# No timing, reset, regulator, DSI or PHY intervention is permitted here.
for token in ('udelay(', 'ndelay(', 'usleep_range(', 'msleep(', 'reset_control_',
              'regulator_enable(', 'regulator_disable(', 'DSI_W32(', 'writel(',
              'writel_relaxed('):
    if a.count(token) != b.count(token):
        raise SystemExit('Phase321 audit: forbidden functional delta: '+token)
print('Phase321 isolated ESC0 source-scope audit: PASS')
P321AUDIT

'''
p.write_text(s.replace(anchor, insert+anchor))
PY
bash -n "$TMP"

set +e
bash "$TMP"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi

test -d phase319-gki-out
rm -rf "$OUT"
mv phase319-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/321_apply_esc0_shared_safe.py scripts/321_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p321-disp-before.c /tmp/p321-disp-after.c /tmp/p321-disp.diff /tmp/p321-apply.log "$OUT/audit/"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"

# Preserve the Phase319-generated identity as provenance, then write the Phase321 identity.
cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE319-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase321-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base=json.loads((r/'audit/PHASE319-GENERATED-BASE-IDENTITY.json').read_text())
idn={
    'phase':'321',
    'flavor':'gki',
    'name':'ESC0-SHARED-SAFE-LIFECYCLE-AB-V1',
    'git_sha':os.getenv('GITHUB_SHA'),
    'hardware_validated':False,
    'base_phase':'Phase319 GKI six-selector q0/q1/q2 temporal observer, reconstructed from the same verified Phase316 lineage',
    'base_phase_identity':base,
    'intervention':'disp_cc_mdss_esc0_clk_src only: safe_src_index=0 and clk_rcg2_ops -> clk_rcg2_shared_ops',
    'safe_source':'disp_cc_parent_map_1 index 0 = P_BI_TCXO',
    'purpose':'restore Android-common-5.10 native safe-source park/restore lifecycle corresponding to the downstream TouchGrass ESC0 enable_safe_config behavior',
    'vdd_cx_rate_vote_changes':'none',
    'dsi_phy_dma_delay_retry_reset_regulator_changes':'none',
    'phase319_recorder_preserved':True,
    'image_sha256':sha(r/'compile/Image'),
    'boot_img_sha256':sha(r/'package/boot.img'),
    'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY

# Final binary/source invariants.
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -Fq 'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1' "$OUT/source/dispcc-lagoon.c"
grep -Fq '.ops = &clk_rcg2_shared_ops,' "$OUT/source/dispcc-lagoon.c"
grep -Fq '.safe_src_index = 0,' "$OUT/source/dispcc-lagoon.c"
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase321 GKI ESC0 shared safe lifecycle A/B: PASS'
trap - EXIT
