#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
RCGH="$ROOT/drivers/clk/qcom/clk-rcg.h"
RCG2="$ROOT/drivers/clk/qcom/clk-rcg2.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
TMP=/tmp/p328-phase319-builder.sh
OUT="$PWD/phase328-gki-out"
FAIL="$PWD/phase328-gki-failure"
GKI_RCGH_SHA256=0cff335e676f7cb690195b4c73bfa81db7def58debccc7b1574a06acb274f27a
GKI_RCG2_SHA256=064a5aba4c0e87072bf3f18e5dd5788e7aeeb5c3a04a751f87552693ef21a9ba
PHASE319_DISP_SHA256=303249b7a91455f3df51035359ff3b55c234f19fc30bc7b4be43d0ffa6629f4a

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -rf "$FAIL"; mkdir -p "$FAIL/phase328"
    [ -d phase319-gki-failure ] && cp -a phase319-gki-failure/. "$FAIL/" || true
    cp /tmp/p328-* "$FAIL/phase328/" 2>/dev/null || true
    cp scripts/328_apply_full_touchgrass_display_clock_port.py scripts/328_ci_build_gki.sh "$FAIL/phase328/" 2>/dev/null || true
    for f in "$RCGH" "$RCG2" "$DISP" "$CTRL" "$HWC" "$PHY" "$PHYV3"; do
      [ -f "$f" ] && cp "$f" "$FAIL/phase328/$(basename "$f")" || true
    done
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/328_apply_full_touchgrass_display_clock_port.py
bash -n scripts/319_ci_build_gki.sh
cp scripts/319_ci_build_gki.sh "$TMP"

python3 - "$TMP" "$GKI_RCGH_SHA256" "$GKI_RCG2_SHA256" "$PHASE319_DISP_SHA256" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
hsha, rsha, dsha = sys.argv[2:]
s = p.read_text()
anchor = 'stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase328: expected one config-invariant anchor, found {s.count(anchor)}')
insert = rf'''stage "Phase328 full TouchGrass Lagoon display-clock semantic port"
RCGH="$ROOT/drivers/clk/qcom/clk-rcg.h"
RCG2="$ROOT/drivers/clk/qcom/clk-rcg2.c"
cp "$RCGH" /tmp/p328-rcgh-before.h
cp "$RCG2" /tmp/p328-rcg2-before.c
cp "$DISP" /tmp/p328-disp-before.c
cp "$CTRL" /tmp/p328-ctrl-before.c
cp "$HWC" /tmp/p328-hwc-before.c
cp "$PHY" /tmp/p328-phy-before.c
cp "$PHYV3" /tmp/p328-phyv3-before.c

# Prove that the broad port starts from the exact clean Phase319 source, not
# from Phase321-327 experimental runtime deltas.
printf '%s  %s\n' '{hsha}' "$RCGH" | sha256sum -c -
printf '%s  %s\n' '{rsha}' "$RCG2" | sha256sum -c -
printf '%s  %s\n' '{dsha}' "$DISP" | sha256sum -c -
for marker in \
  A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1 \
  A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1 \
  A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1 \
  A52_PHASE324_PARENT_ENABLE_SPLIT_AB_V1 \
  A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1 \
  A52_PHASE326_RECORDER_FINALIZE_AUTOREBOOT_V1 \
  A52_PHASE327_PREPANIC_AUTOREBOOT_V2; do
  ! grep -R -Fq "$marker" "$RCGH" "$RCG2" "$DISP" "$CTRL" "$HWC" "$PHY" "$PHYV3"
done

python3 scripts/328_apply_full_touchgrass_display_clock_port.py --root "$ROOT" | tee /tmp/p328-apply.log
python3 scripts/328_apply_full_touchgrass_display_clock_port.py --root "$ROOT" --check-only

git -C "$ROOT" diff --check -- \
  drivers/clk/qcom/clk-rcg.h drivers/clk/qcom/clk-rcg2.c drivers/clk/qcom/dispcc-lagoon.c
cp "$RCGH" /tmp/p328-rcgh-after.h
cp "$RCG2" /tmp/p328-rcg2-after.c
cp "$DISP" /tmp/p328-disp-after.c
diff -u /tmp/p328-rcgh-before.h /tmp/p328-rcgh-after.h > /tmp/p328-rcgh.diff || true
diff -u /tmp/p328-rcg2-before.c /tmp/p328-rcg2-after.c > /tmp/p328-rcg2.diff || true
diff -u /tmp/p328-disp-before.c /tmp/p328-disp-after.c > /tmp/p328-disp.diff || true

# The broad port is intentionally broad only within Qualcomm display clocks.
# DSI controller/PHY source and the Phase319 recorder must remain byte-identical.
cmp -s /tmp/p328-ctrl-before.c "$CTRL"
cmp -s /tmp/p328-hwc-before.c "$HWC"
cmp -s /tmp/p328-phy-before.c "$PHY"
cmp -s /tmp/p328-phyv3-before.c "$PHYV3"

grep -Fq 'A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1' "$RCGH"
grep -Fq 'A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1' "$RCG2"
grep -Fq 'A52_PHASE328_FULL_TOUCHGRASS_DISPLAY_CLOCK_PORT_V1' "$DISP"
test "$(grep -F '.enable_safe_config = true,' "$DISP" | wc -l)" -eq 11
test "$(grep -F 'clk_rcg2_current_config(rcg, &f)' "$RCG2" | wc -l)" -eq 2
grep -Fq $'\t.enable = clk_rcg2_enable,' "$RCG2"
grep -Fq $'\t.disable = clk_rcg2_disable,' "$RCG2"
grep -Fq 'RCG_D_OFFSET(rcg), mask, ~f->n' "$RCG2"
grep -Fq $'\t.cal_l = 0x31,' "$DISP"
grep -Fq 'RPMH_REGULATOR_LEVEL_NOM' "$DISP"
! sed -n '/frac_table_pixel/,/};/p' "$RCG2" | grep -Fq '{ 2, 3 }'

'''
p.write_text(s.replace(anchor, insert + anchor))
PY

bash -n "$TMP"
bash "$TMP"

test -d phase319-gki-out
rm -rf "$OUT"
mv phase319-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/328_apply_full_touchgrass_display_clock_port.py scripts/328_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p328-* "$OUT/audit/" 2>/dev/null || true
cp "$RCGH" "$RCG2" "$DISP" "$OUT/source/"

cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE319-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase328-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base=json.loads((r/'audit/PHASE319-GENERATED-BASE-IDENTITY.json').read_text())
idn={
  'phase':'328',
  'flavor':'gki',
  'name':'FULL-TOUCHGRASS-DISPLAY-CLOCK-SEMANTIC-PORT-V1',
  'git_sha':os.getenv('GITHUB_SHA'),
  'hardware_validated':False,
  'base_phase_identity':base,
  'source_reference':'TouchGrass 6bf351bdf18bdb228db79e66f14a7a9c0178e5d7',
  'strategy':'broad compatible port first, then subtract feature groups after hardware success',
  'ported_groups':[
    'clk_rcg2 current_freq + enable_safe_config state',
    'safe CXO park/restore generic RCG enable-disable lifecycle',
    'safe deferred generic set-rate programming',
    'safe-aware recalc-rate behavior',
    'TouchGrass current-config redundant-update suppression for BYTE2 and PIXEL',
    'TouchGrass D_VAL programming (~N)',
    'HW_CLK_CTRL flag behavior',
    'TouchGrass pixel fraction table (remove GKI-only 2/3)',
    'enable_safe_config on all 11 Lagoon RCGs',
    'Lagoon PLL cal_l=0x31',
    'persistent VDD_CX NOMINAL compatibility vote'
  ],
  'vdd_note':'GKI 5.10 lacks Samsung downstream clk_init_data vdd_class/rate_max; persistent NOMINAL is the upper-bound compatibility equivalent for this boot experiment',
  'phase321_to_phase327_experimental_runtime_deltas':'absent; reconstructed from clean Phase319 before Phase328',
  'phase319_recorder_preserved':True,
  'autoreboot':'not included; manual reboot/recovery remains required because Phase327 retention helper did not return',
  'image_sha256':sha(r/'compile/Image'),
  'boot_img_sha256':sha(r/'package/boot.img'),
  'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
grep -aFq 'P276 328V s=3 rc=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase328 full TouchGrass display-clock semantic port: PASS'
trap - EXIT
