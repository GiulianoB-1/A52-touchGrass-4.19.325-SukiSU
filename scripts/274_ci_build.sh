#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
SDE="$ROOT/drivers/a52_display/msm/sde/sde_connector.c"
HDR="$ROOT/include/uapi/drm/drm_mode.h"

fail_report() {
  set +e
  rm -rf phase274-failure
  mkdir -p phase274-failure/source phase274-failure/config phase274-failure/logs phase274-failure/audit
  cp phase274-compile.log phase274-failure/logs/ 2>/dev/null || true
  cp phase274-gpara-parity-before.txt phase274-failure/audit/ 2>/dev/null || true
  cp "$OUT/.config" phase274-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/274_gpara_parity_probe.py phase274-failure/audit/ 2>/dev/null || true
  cp scripts/274_panel_gpara_frontier.py phase274-failure/audit/ 2>/dev/null || true
  cp scripts/274_audit_candidate.py phase274-failure/audit/ 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" phase274-failure/source/ || true
  [ -f "$PANEL" ] && cp "$PANEL" phase274-failure/source/ || true
  [ -f "$SDE" ] && cp "$SDE" phase274-failure/source/ || true
  [ -f "$HDR" ] && cp "$HDR" phase274-failure/source/ || true
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact Phase273 hardware-tested image first. Phase274 changes
# diagnostics only. The Phase272 DRM vendor-flag behavior remains untouched.
bash scripts/273_ci_build.sh

test -s phase273-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
test -s "$REC"
test -s "$PANEL"
test -s "$SDE"
test -s "$HDR"
cp "$OUT/.config" /tmp/phase273-before-phase274.config
cp "$REC" /tmp/phase273-before-phase274-recorder.c
cp "$PANEL" /tmp/phase273-before-phase274-panel.c
cp "$SDE" /tmp/phase273-before-phase274-sde.c
cp "$HDR" /tmp/phase273-before-phase274-drm_mode.h

# Mandatory exact-source comparison before touching the new suspected panel
# boundary. The GKI function must equal pinned TouchGrass after removing only
# the pre-existing lifecycle scope line.
python3 -m py_compile scripts/274_gpara_parity_probe.py
python3 scripts/274_gpara_parity_probe.py "$ROOT" "$TG"
grep -Fq 'match_after_removing_existing_scope=1' phase274-gpara-parity-before.txt

python3 -m py_compile scripts/274_panel_gpara_frontier.py
python3 -m py_compile scripts/274_audit_candidate.py
python3 scripts/274_panel_gpara_frontier.py "$ROOT"

# Observation-only closure: config, DRM flag header, and SDE behavior source are
# byte-identical to Phase273. Only recorder timing and Samsung panel diagnostics
# may change.
cmp -s /tmp/phase273-before-phase274.config "$OUT/.config"
cmp -s /tmp/phase273-before-phase274-drm_mode.h "$HDR"
cmp -s /tmp/phase273-before-phase274-sde.c "$SDE"
! cmp -s /tmp/phase273-before-phase274-recorder.c "$REC"
! cmp -s /tmp/phase273-before-phase274-panel.c "$PANEL"

for token in \
  'A52_PHASE274_FRONTIER_TIMEBASE_FIX_V1' \
  'return !strncmp(message, "P274 ", 5)' \
  'if (strncmp(fmt, "P274", 4) &&' \
  'jiffies - a52_r274_frontier_start_jiffies' \
  'P274 START tb=elapsed q=%u/%u s=%u' \
  'P273 U + k=%s p=%d t=%d pp=%d c=%.15s' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d'; do
  grep -Fq "$token" "$REC"
done
for token in \
  'A52_PHASE274_PANEL_GPARA_FRONTIER_V1' \
  'P274 G E n=%d ty=%d lk=%x g=%u p=%u' \
  'P274 G K n=%d p=0 lk=%x' \
  'P274 G K n=%d p=1 lk=%x' \
  'P274 G A n=%d a=%02x l=%d o=%d lp=%d' \
  'P274 G P n=%d i=%d p=0 o=%d' \
  'P274 G P n=%d i=%d p=1 o=%d' \
  'P274 G R n=%d i=%d p=0 l=%d' \
  'P274 G R n=%d i=%d p=1 l=%d' \
  'P274 G Z n=%d cp=%d'; do
  grep -Fq "$token" "$PANEL"
done

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase273-before-phase274.config "$OUT/.config"
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase274-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P274 START tb=elapsed q=%u/%u s=%u' \
  'P274 G E n=%d ty=%d lk=%x g=%u p=%u' \
  'P274 G K n=%d p=0 lk=%x' \
  'P274 G P n=%d i=%d p=0 o=%d' \
  'P274 G R n=%d i=%d p=0 l=%d' \
  'P274 G Z n=%d cp=%d' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d' \
  'P271 V id=%u k=D st=%d' \
  'P271 SB id=%u eid=%u stp=%u best=%u'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase274-out
mkdir -p phase274-out/compile phase274-out/config phase274-out/package \
  phase274-out/audit phase274-out/source
cp "$IMAGE" phase274-out/compile/Image
cp "$OUT/.config" phase274-out/config/final.config
cp /tmp/phase273-before-phase274.config phase274-out/audit/phase273-final.config
cp phase274-gpara-parity-before.txt phase274-out/audit/
cp phase274-compile.log phase274-out/audit/
cp scripts/274_gpara_parity_probe.py scripts/274_panel_gpara_frontier.py scripts/274_audit_candidate.py phase274-out/audit/
cp "$REC" phase274-out/source/a52_ack_secure_flight_recorder.c
cp "$PANEL" phase274-out/source/ss_dsi_panel_common.c
cp "$SDE" phase274-out/source/sde_connector.c
cp "$HDR" phase274-out/source/drm_mode.h

gzip -n -c "$IMAGE" > phase274-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase273-out/package/boot.img \
  --kernel phase274-out/package/Image.gz \
  --output phase274-out/package/boot.img \
  --report phase274-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase274-out')
identity = {
    'phase': 274,
    'name': 'PANEL-GPARA-AND-LATE-BOOT-FRONTIER-V1',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'hardware_validated': False,
    'base_phase273_hardware_head': '8a621ad9db835dc7b465a1582d9a518170d9b131',
    'phase273_hardware_capture': 'A52_RAW_RAMOOPS_20260816_151217.zip',
    'evidence': [
        'Phase272 DRM fix directly passes drm_mode_validate_driver for both 120Hz and 60Hz modes (P271 k=D st=0)',
        'system_server process TGID 1368 is reached',
        'bootanimation process PID/TGID 1267 exits code 11 before replacing Samsung splash',
        'no SystemUI identity is retained in the Phase273 capture',
        'two independent post-Phase272 captures end display progression at ss_panel_data_read_gpara entry',
        'Phase273 frontier worker emitted no retained summaries because absolute unsigned jiffies exceeded its 900-second diagnostic horizon',
    ],
    'touchgrass_comparison': 'exact ss_panel_data_read_gpara body must match pinned TouchGrass after removing only existing lifecycle scope before Phase274 instrumentation',
    'changes': [
        'use elapsed wrap-safe jiffies for the existing Phase273 late-boot worker',
        'admit P274 diagnostic records into the unchanged R48/RS48 recorder',
        'checkpoint exact ss_panel_data_read_gpara around key enable, GPARA position command, and panel RX',
    ],
    'not_changed': [
        'Phase272 DRM mode-flag parity',
        'SDE connector best-encoder behavior and Phase273 sampling',
        'DSI command ordering and payloads',
        'Samsung panel return values, locks, and state transitions',
        'Binder/userspace behavior',
        'kernel config',
        'R48 v3 / RS48 / CRC32C wire format',
    ],
    'hardware_question': 'Does SystemUI ever appear, and inside ss_panel_data_read_gpara is the first non-returning operation key enable, TX_REG_READ_POS, or the actual panel RX command?',
}
(out/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [
    out/'compile/Image', out/'config/final.config', out/'package/Image.gz',
    out/'package/boot.img', out/'package/repack-report.json',
    out/'audit/phase273-final.config', out/'audit/phase274-gpara-parity-before.txt',
    out/'source/a52_ack_secure_flight_recorder.c', out/'source/ss_dsi_panel_common.c',
    out/'source/sde_connector.c', out/'source/drm_mode.h',
]
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY

(cd phase274-out && sha256sum -c SHA256SUMS)
python3 scripts/274_audit_candidate.py phase274-out
trap - EXIT
echo 'Phase274 panel GPARA + late-boot frontier build/repack: PASS'
