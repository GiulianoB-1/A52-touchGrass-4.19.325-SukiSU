#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p322-phase319-builder.sh
OUT="$PWD/phase322-gki-out"
FAIL="$PWD/phase322-gki-failure"
trap 'rc=$?; if [ "$rc" -ne 0 ]; then mkdir -p "$FAIL"; [ -d phase319-gki-failure ] && cp -a phase319-gki-failure/. "$FAIL/" || true; mkdir -p "$FAIL/phase322"; cp /tmp/p322-* "$FAIL/phase322/" 2>/dev/null || true; cp scripts/322_* "$FAIL/phase322/" 2>/dev/null || true; [ -f "$DISP" ] && cp "$DISP" "$FAIL/phase322/dispcc-lagoon.c" || true; fi; exit "$rc"' EXIT

python3 -m py_compile scripts/322_apply_dispcc_vdd_cx_nominal.py
bash -n scripts/319_ci_build_gki.sh
cp scripts/319_ci_build_gki.sh "$TMP"
python3 - "$TMP" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text(); anchor='stage "config invariant"\n'
if s.count(anchor)!=1: raise SystemExit('Phase322: config anchor count != 1')
insert=r'''stage "Phase322 isolated DISPCC VDD_CX NOMINAL vote A/B"
cp "$DISP" /tmp/p322-disp-before.c
cp "$CTRL" /tmp/p322-ctrl-before.c
cp "$HWC" /tmp/p322-hwc-before.c
cp "$PHY" /tmp/p322-phy-before.c
cp "$PHYV3" /tmp/p322-phyv3-before.c
python3 scripts/322_apply_dispcc_vdd_cx_nominal.py --file "$DISP" | tee /tmp/p322-apply.log
python3 scripts/322_apply_dispcc_vdd_cx_nominal.py --file "$DISP" --check-only
git -C "$ROOT" diff --check -- drivers/clk/qcom/dispcc-lagoon.c
cp "$DISP" /tmp/p322-disp-after.c
diff -u /tmp/p322-disp-before.c /tmp/p322-disp-after.c > /tmp/p322-disp.diff || true
cmp -s /tmp/p322-ctrl-before.c "$CTRL"
cmp -s /tmp/p322-hwc-before.c "$HWC"
cmp -s /tmp/p322-phy-before.c "$PHY"
cmp -s /tmp/p322-phyv3-before.c "$PHYV3"
! grep -Fq 'A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1' "$DISP"
! grep -Fq '.ops = &clk_rcg2_shared_ops,' "$DISP"
grep -Fq 'A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1' "$DISP"
grep -Fq 'RPMH_REGULATOR_LEVEL_NOM, INT_MAX' "$DISP"
grep -Fq 'regulator_enable(vdd_cx)' "$DISP"
echo 'Phase322 isolated VDD_CX source-scope audit: PASS'

'''
p.write_text(s.replace(anchor,insert+anchor))
PY
bash -n "$TMP"
bash "$TMP"

test -d phase319-gki-out
rm -rf "$OUT"; mv phase319-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/322_apply_dispcc_vdd_cx_nominal.py scripts/322_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p322-disp-before.c /tmp/p322-disp-after.c /tmp/p322-disp.diff /tmp/p322-apply.log "$OUT/audit/"
cp "$DISP" "$OUT/source/dispcc-lagoon.c"
cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE319-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase322-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base=json.loads((r/'audit/PHASE319-GENERATED-BASE-IDENTITY.json').read_text())
idn={'phase':'322','flavor':'gki','name':'DISPCC-VDD-CX-NOMINAL-VOTE-AB-V1','git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,'base_phase_identity':base,'intervention':'DISP_CC provider only: acquire vdd_cx, set RPMH_REGULATOR_LEVEL_NOM, enable and hold for provider lifetime','phase321_esc0_shared_safe_lifecycle':'absent/reverted by branching from Phase319','dynamic_per_clock_vdd_framework_port':'none','dsi_phy_dma_clock_topology_delay_retry_reset_changes':'none','runtime_marker':'P276 322V','image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),'boot_img_size':(r/'package/boot.img').stat().st_size}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -aFq 'P276 322V s=3 rc=%d' "$OUT/compile/Image"
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
echo 'Phase322 GKI DISPCC VDD_CX NOMINAL vote A/B: PASS'
trap - EXIT
