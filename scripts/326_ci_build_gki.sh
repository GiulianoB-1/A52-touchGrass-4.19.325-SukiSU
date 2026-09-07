#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
RCG2="$ROOT/drivers/clk/qcom/clk-rcg2.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
BASE=/tmp/p326-phase325-builder.sh
OUT="$PWD/phase326-gki-out"
FAIL="$PWD/phase326-gki-failure"

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$FAIL/phase326"
    [ -d phase325-gki-failure ] && cp -a phase325-gki-failure/. "$FAIL/" || true
    cp /tmp/p326-* "$FAIL/phase326/" 2>/dev/null || true
    cp scripts/326_apply_recorder_finalize_autoreboot.py scripts/326_ci_build_gki.sh "$FAIL/phase326/" 2>/dev/null || true
    [ -f "$CTRL" ] && cp "$CTRL" "$FAIL/phase326/dsi_ctrl.c" || true
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/325_apply_touchgrass_rcg_current_config.py scripts/326_apply_recorder_finalize_autoreboot.py
bash -n scripts/325_ci_build_gki.sh
cp scripts/325_ci_build_gki.sh "$BASE"

python3 - "$BASE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
anchor = 'bash -n "$TMP"\nbash "$TMP"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase326: expected one Phase325 execute anchor, found {s.count(anchor)}')
wrapper = r"""bash -n "$TMP"
python3 - "$TMP" <<'P326INNER'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
anchor = 'stage "config invariant"\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase326 inner: expected one config-invariant anchor, found {s.count(anchor)}')
insert = r'''stage "Phase326 q2-final recorder autoreboot"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
RCG2="$ROOT/drivers/clk/qcom/clk-rcg2.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
cp "$CTRL" /tmp/p326-ctrl-before.c
cp "$HWC" /tmp/p326-hwc-before.c
cp "$RCG2" /tmp/p326-rcg2-before.c
cp "$DISP" /tmp/p326-disp-before.c
cp "$PHY" /tmp/p326-phy-before.c
cp "$PHYV3" /tmp/p326-phyv3-before.c

# Phase326 is test-control-only. It must start from the exact Phase325 semantic
# port and the preserved Phase319 q2 recorder, then change dsi_ctrl.c only.
grep -Fq 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1' "$RCG2"
test "$(grep -F 'clk_rcg2_current_config(rcg, &f)' "$RCG2" | wc -l)" -eq 2
grep -Fq 'a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);' "$CTRL"
! grep -Fq 'A52_PHASE326_RECORDER_FINALIZE_AUTOREBOOT_V1' "$CTRL"

python3 scripts/326_apply_recorder_finalize_autoreboot.py --root "$ROOT" | tee /tmp/p326-apply.log
python3 scripts/326_apply_recorder_finalize_autoreboot.py --root "$ROOT" --check-only
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl.c
cp "$CTRL" /tmp/p326-ctrl-after.c
diff -u /tmp/p326-ctrl-before.c /tmp/p326-ctrl-after.c > /tmp/p326-ctrl.diff || true

# No clock semantic, Lagoon definition, low-level controller HW, or PHY source
# may move in this phase. The reboot is armed only after the recorder verdict.
cmp -s /tmp/p326-hwc-before.c "$HWC"
cmp -s /tmp/p326-rcg2-before.c "$RCG2"
cmp -s /tmp/p326-disp-before.c "$DISP"
cmp -s /tmp/p326-phy-before.c "$PHY"
cmp -s /tmp/p326-phyv3-before.c "$PHYV3"
grep -Fq '#define A52_P326_REBOOT_DELAY_MS 3000U' "$CTRL"
grep -Fq 'P276 326R final q2=1 delay_ms=%u' "$CTRL"
grep -Fq 'kernel_restart("a52_phase326_capture_done")' "$CTRL"
test "$(grep -F 'a52_p326_schedule_reboot();' "$CTRL" | wc -l)" -eq 1

'''
p.write_text(s.replace(anchor, insert + anchor))
P326INNER
bash -n "$TMP"
bash "$TMP"
"""
p.write_text(s.replace(anchor, wrapper))
PY

bash -n "$BASE"
bash "$BASE"

test -d phase325-gki-out
rm -rf "$OUT"
mv phase325-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/326_apply_recorder_finalize_autoreboot.py scripts/326_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p326-ctrl-before.c /tmp/p326-ctrl-after.c /tmp/p326-ctrl.diff /tmp/p326-apply.log "$OUT/audit/"
cp "$CTRL" "$OUT/source/dsi_ctrl.c"

cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE325-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase326-gki-out')
def sha(p):
    h = hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base = json.loads((r/'audit/PHASE325-GENERATED-BASE-IDENTITY.json').read_text())
idn = {
    'phase': '326',
    'flavor': 'gki',
    'name': 'RECORDER-FINALIZE-AUTOREBOOT-V1',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base_phase_identity': base,
    'functional_display_delta_from_phase325': 'none before recorder completion',
    'intervention': 'After the armed Phase319 q2 recorder and existing completion/timeout diagnostic tail return, schedule one-shot delayed kernel_restart',
    'trigger': 'exact first-F0 target q2 snapshot recorded',
    'reboot_delay_ms': 3000,
    'reboot_reason': 'a52_phase326_capture_done',
    'phase325_touchgrass_rcg_current_config_semantics': 'preserved',
    'phase319_q0_q1_q2_recorder': 'preserved',
    'clock_phy_regulator_reset_delay_retry_changes_before_verdict': 'none',
    'image_sha256': sha(r/'compile/Image'),
    'boot_img_sha256': sha(r/'package/boot.img'),
    'boot_img_size': (r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')
PY

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
grep -Fq 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1' "$OUT/source/clk-rcg2.c"
grep -Fq 'A52_PHASE326_RECORDER_FINALIZE_AUTOREBOOT_V1' "$OUT/source/dsi_ctrl.c"
grep -aFq 'P276 326R final q2=1 delay_ms=%u' "$OUT/compile/Image"
grep -aFq 'P276 326R reboot now' "$OUT/compile/Image"
grep -aFq 'a52_phase326_capture_done' "$OUT/compile/Image"
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$OUT/compile/Image"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$OUT/compile/Image"
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase326 GKI q2-final recorder autoreboot: PASS'
trap - EXIT
