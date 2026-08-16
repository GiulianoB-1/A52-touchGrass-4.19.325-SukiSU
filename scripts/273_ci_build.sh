#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
SDE="$ROOT/drivers/a52_display/msm/sde/sde_connector.c"
HDR="$ROOT/include/uapi/drm/drm_mode.h"

fail_report() {
  set +e
  rm -rf phase273-failure
  mkdir -p phase273-failure/source phase273-failure/config phase273-failure/logs
  cp phase273-compile.log phase273-failure/logs/ 2>/dev/null || true
  cp "$OUT/.config" phase273-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/273_late_boot_frontier_recorder.py phase273-failure/ 2>/dev/null || true
  cp scripts/273_audit_candidate.py phase273-failure/ 2>/dev/null || true
  cp "$REC" phase273-failure/source/a52_ack_secure_flight_recorder.c 2>/dev/null || true
  cp "$SDE" phase273-failure/source/sde_connector.c 2>/dev/null || true
  cp "$HDR" phase273-failure/source/drm_mode.h 2>/dev/null || true
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact Phase272 functional baseline. Phase273 is observation
# only and must retain the hardware-proven DRM mode-flag parity fix unchanged.
bash scripts/272_ci_build.sh

test -s phase272-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
test -s "$REC"
test -s "$SDE"
test -s "$HDR"
cp "$OUT/.config" /tmp/phase272-before-phase273.config
cp "$HDR" /tmp/phase272-before-phase273-drm_mode.h
cp "$REC" /tmp/phase272-before-phase273-recorder.c
cp "$SDE" /tmp/phase272-before-phase273-sde.c

# Phase272 functional parity must already be present before recorder changes.
grep -Fq 'A52_PHASE272_DRM_VENDOR_MODE_FLAG_PARITY_V1' "$HDR"
grep -Fq 'gki_all_mentions_cmd=1' phase272-out/audit/phase272-parity-after.txt

python3 -m py_compile scripts/273_late_boot_frontier_recorder.py
python3 -m py_compile scripts/273_audit_candidate.py
python3 scripts/273_late_boot_frontier_recorder.py "$ROOT"

# The patcher is allowed to modify only recorder/SDE source. Config and DRM
# flag header must stay byte-identical to the reconstructed Phase272 baseline.
cmp -s /tmp/phase272-before-phase273.config "$OUT/.config"
cmp -s /tmp/phase272-before-phase273-drm_mode.h "$HDR"
! cmp -s /tmp/phase272-before-phase273-recorder.c "$REC"
! cmp -s /tmp/phase272-before-phase273-sde.c "$SDE"

# Source gates: retain exact R48/RS48 v3 wire format, add sparse 15-minute
# userspace/display frontier, and sample only the proven P271 SB spam source.
for token in \
  '#define A52_R179_VERSION 3U' \
  '#define A52_R179_RS_ROOTS 48U' \
  '#define A52_R179_PREFIX "R48"' \
  'A52_PHASE273_LATE_BOOT_FRONTIER_RECORDER_V2' \
  'return !strncmp(message, "P273 ", 5)' \
  'A52_R273_FRONTIER_END_S 900U' \
  'A52_R273_SUMMARY_S 15U' \
  'P273 U + k=%s p=%d t=%d pp=%d c=%.15s' \
  'P273 U - k=%s p=%d' \
  'P273 U R k=%s o=%d n=%d pp=%d' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d' \
  'P273 D t=%lu n=%d k=%c fn=%.36s' \
  'P273 R t=%lu kd=%d nm=%d e=%d s=%d' \
  'system_server' 'zygote64' 'com.android.sys' 'surfaceflinger'; do
  grep -Fq "$token" "$REC"
done
for token in \
  'A52_PHASE273_P271_SB_DEDUP_V2' \
  'call <= 8 || sig != prev || !(call & 0x7f)' \
  'P271 SB id=%u eid=%u stp=%u best=%u' \
  'return c_conn->encoder;'; do
  grep -Fq "$token" "$SDE"
done

# olddefconfig must not mutate configuration.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase272-before-phase273.config "$OUT/.config"

# Compile the actual Phase273 Image.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase273-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P273 START h=%u q=%u/%u s=%u' \
  'P273 U + k=%s p=%d t=%d pp=%d c=%.15s' \
  'P273 U - k=%s p=%d' \
  'P273 U R k=%s o=%d n=%d pp=%d' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d' \
  'P273 D t=%lu n=%d k=%c fn=%.36s' \
  'P273 R t=%lu kd=%d nm=%d e=%d s=%d' \
  'P271 SB id=%u eid=%u stp=%u best=%u' \
  'P271 V id=%u k=D st=%d' \
  'P269 CONN n=%u id=%u enc=%u ne=%u nm=%u np=%u st=%u ty=%u'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase273-out
mkdir -p phase273-out/compile phase273-out/config phase273-out/package \
  phase273-out/audit phase273-out/source
cp "$IMAGE" phase273-out/compile/Image
cp "$OUT/.config" phase273-out/config/final.config
cp /tmp/phase272-before-phase273.config phase273-out/audit/phase272-final.config
cp phase272-out/audit/phase272-parity-after.txt phase273-out/audit/
cp phase273-compile.log phase273-out/audit/
cp scripts/273_late_boot_frontier_recorder.py phase273-out/audit/
cp scripts/273_audit_candidate.py phase273-out/audit/
cp "$REC" phase273-out/source/a52_ack_secure_flight_recorder.c
cp "$SDE" phase273-out/source/sde_connector.c
cp "$HDR" phase273-out/source/drm_mode.h

gzip -n -c "$IMAGE" > phase273-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase272-out/package/boot.img \
  --kernel phase273-out/package/Image.gz \
  --output phase273-out/package/boot.img \
  --report phase273-out/package/repack-report.json

python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

out = Path('phase273-out')
identity = {
    'phase': 273,
    'name': 'LATE-BOOT-FRONTIER-RECORDER-V2',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'hardware_validated': False,
    'base_phase272_hardware_head': '7616d96f95bceb1247b9deeec8a31cd42153cb11',
    'wire_format': 'unchanged R48 v3 / RS48 / CRC32C',
    'changes': [
        'sample repeated successful P271 SB callbacks: first 8, transitions, every 128th steady call',
        'scan late Android task frontier for 900 seconds without changing task behavior',
        'track SurfaceFlinger, zygote, system_server, SystemUI and adjacent boot services',
        'retain ever-seen/disappeared process masks and parent PID on transitions',
        'retain compact last DISP scope plus DRM mode/encoder/selection frontier every 15 seconds',
    ],
    'not_changed': [
        'Phase272 DRM mode-flag parity',
        'DRM/DSI/panel/SDE return values or routing semantics',
        'Binder or userspace behavior',
        'scheduler/fork behavior',
        'R48/RS48 recorder wire format',
        'kernel config',
    ],
    'hardware_question': 'After the Phase272 DRM fix, which display/userspace milestone is the last one reached and what is the first persistent late-boot blocker?',
}
(out / 'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
files = [
    out/'compile/Image',
    out/'config/final.config',
    out/'package/Image.gz',
    out/'package/boot.img',
    out/'package/repack-report.json',
    out/'audit/phase272-final.config',
    out/'audit/phase272-parity-after.txt',
    out/'source/a52_ack_secure_flight_recorder.c',
    out/'source/sde_connector.c',
    out/'source/drm_mode.h',
]
with (out/'SHA256SUMS').open('w') as handle:
    for path in files:
        handle.write(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  ./{path.relative_to(out)}\n')
PY

(cd phase273-out && sha256sum -c SHA256SUMS)
python3 scripts/273_audit_candidate.py phase273-out
trap - EXIT
echo 'Phase273 late-boot frontier recorder build/repack: PASS'
