#!/usr/bin/env bash
set -Eeuo pipefail

BASE=/tmp/p327-phase326-builder.sh
OUT="$PWD/phase327-gki-out"
FAIL="$PWD/phase327-gki-failure"

cleanup_failure() {
  rc=$?
  if [ "$rc" -ne 0 ]; then
    mkdir -p "$FAIL/phase327"
    [ -d phase326-gki-failure ] && cp -a phase326-gki-failure/. "$FAIL/" || true
    cp /tmp/p327-* "$FAIL/phase327/" 2>/dev/null || true
    cp scripts/327_apply_prepanic_autoreboot_route.py scripts/327_ci_build_gki.sh "$FAIL/phase327/" 2>/dev/null || true
  fi
  exit "$rc"
}
trap cleanup_failure EXIT

python3 -m py_compile scripts/327_apply_prepanic_autoreboot_route.py
bash -n scripts/326_ci_build_gki.sh
cp scripts/326_ci_build_gki.sh "$BASE"

python3 - "$BASE" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
anchor = 'python3 scripts/326_apply_recorder_finalize_autoreboot.py --root "$ROOT" --check-only\n'
if s.count(anchor) != 1:
    raise SystemExit(f'Phase327: expected one Phase326 patch-check anchor, found {s.count(anchor)}')
insert = r'''python3 scripts/327_apply_prepanic_autoreboot_route.py --root "$ROOT" | tee /tmp/p327-apply.log
python3 scripts/327_apply_prepanic_autoreboot_route.py --root "$ROOT" --check-only
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl.c
cp "$CTRL" /tmp/p327-ctrl-after.c
grep -Fq 'A52_PHASE327_PREPANIC_AUTOREBOOT_ROUTE_V2' "$CTRL"
grep -Fq 'P276 327R retained q2=1 route=done' "$CTRL"

'''
p.write_text(s.replace(anchor, anchor + insert))
PY

bash -n "$BASE"
bash "$BASE"

test -d phase326-gki-out
rm -rf "$OUT"
mv phase326-gki-out "$OUT"
mkdir -p "$OUT/audit"
cp scripts/327_apply_prepanic_autoreboot_route.py scripts/327_ci_build_gki.sh "$OUT/audit/"
[ -f /tmp/p327-apply.log ] && cp /tmp/p327-apply.log "$OUT/audit/"
[ -f /tmp/p327-ctrl-after.c ] && cp /tmp/p327-ctrl-after.c "$OUT/audit/"

grep -Fq 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1' "$OUT/source/clk-rcg2.c"
grep -Fq 'A52_PHASE326_RECORDER_FINALIZE_AUTOREBOOT_V1' "$OUT/source/dsi_ctrl.c"
grep -Fq 'A52_PHASE327_PREPANIC_AUTOREBOOT_ROUTE_V2' "$OUT/source/dsi_ctrl.c"
grep -aFq 'P276 327R retained q2=1 route=done' "$OUT/compile/Image"
grep -aFq 'P276 326R final q2=1 delay_ms=%u' "$OUT/compile/Image"
grep -aFq 'P276 326R reboot now' "$OUT/compile/Image"
grep -aFq 'a52_phase326_capture_done' "$OUT/compile/Image"

cp "$OUT/BUILD-IDENTITY.json" "$OUT/audit/PHASE326-GENERATED-BASE-IDENTITY.json"
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase327-gki-out')
def sha(p):
    h = hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
base = json.loads((r/'audit/PHASE326-GENERATED-BASE-IDENTITY.json').read_text())
idn = {
    'phase': '327',
    'flavor': 'gki',
    'name': 'PREPANIC-AUTOREBOOT-ROUTE-V2',
    'git_sha': os.getenv('GITHUB_SHA'),
    'hardware_validated': False,
    'base_phase_identity': base,
    'functional_display_delta_from_phase325': 'none before recorder completion',
    'phase326_failure_evidence': 'q2 and retention reached; done-label finalizer not reached on failing path',
    'intervention': 'After exact target q2 and timeout-retention snapshot, route to done before Samsung SDE_DBG_DUMP panic path so existing one-shot 3-second reboot work can execute',
    'runtime_marker_before_route': 'P276 327R retained q2=1 route=done',
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
rm -f "$OUT/SHA256SUMS"
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo 'Phase327 GKI pre-panic route to q2-final autoreboot: PASS'
trap - EXIT
