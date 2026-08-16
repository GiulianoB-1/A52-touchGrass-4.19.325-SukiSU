#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
HDR="$ROOT/include/uapi/drm/drm_mode.h"
TGHDR="$TG/include/uapi/drm/drm_mode.h"

fail_report() {
  set +e
  rm -rf phase272-failure
  mkdir -p phase272-failure/source phase272-failure/config phase272-failure/logs
  cp phase272-compile.log phase272-failure/logs/ 2>/dev/null || true
  cp phase272-parity-before.txt phase272-failure/ 2>/dev/null || true
  cp phase272-parity-after.txt phase272-failure/ 2>/dev/null || true
  cp "$OUT/.config" phase272-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/272_probe_drm_flag_parity.py phase272-failure/ 2>/dev/null || true
  cp scripts/272_drm_vendor_mode_flag_parity.py phase272-failure/ 2>/dev/null || true
  cp "$HDR" phase272-failure/source/gki-drm_mode.h 2>/dev/null || true
  cp "$TGHDR" phase272-failure/source/touchgrass-drm_mode.h 2>/dev/null || true
  for p in \
    "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$ROOT/drivers/gpu/drm/drm_probe_helper.c" \
    "$ROOT/drivers/gpu/drm/drm_connector.c" \
    "$ROOT/drivers/gpu/drm/drm_modes.c" \
    "$ROOT/drivers/a52_display/msm/sde/sde_connector.c"; do
    [ -f "$p" ] && cp "$p" "phase272-failure/source/$(echo "$p" | tr '/' '_')"
  done
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase271 diagnostic baseline first.
bash scripts/271_ci_build.sh

test -s phase271-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
test -s "$HDR"
test -s "$TGHDR"
cp "$OUT/.config" /tmp/phase271-before-phase272.config

# Record the source mismatch proven by the Phase271 hardware trace before
# touching behavior. The pre-fix probe must show that the pinned GKI rejects
# TouchGrass's command-mode panel flag from DRM_MODE_FLAG_ALL.
python3 -m py_compile scripts/272_probe_drm_flag_parity.py
python3 -m py_compile scripts/272_drm_vendor_mode_flag_parity.py
python3 scripts/272_probe_drm_flag_parity.py "$ROOT" "$TG" >/tmp/phase272-probe-before.stdout
cp phase272-probe.txt phase272-parity-before.txt
grep -Fq 'gki_cmd_macro_present=0' phase272-parity-before.txt
grep -Fq 'gki_all_mentions_cmd=0' phase272-parity-before.txt
grep -Fq 'tg_cmd_macro_present=1' phase272-parity-before.txt
grep -Fq 'tg_all_mentions_cmd=1' phase272-parity-before.txt

# Functional fix: restore only the vendor mode-flag definitions and
# DRM_MODE_FLAG_ALL membership present in the exact TouchGrass A52 reference.
# No mode is forced valid and no validation stage is bypassed.
python3 scripts/272_drm_vendor_mode_flag_parity.py "$ROOT" "$TG"
cmp -s /tmp/phase271-before-phase272.config "$OUT/.config"

grep -Fq 'A52_PHASE272_DRM_VENDOR_MODE_FLAG_PARITY_V1' "$HDR"
grep -Eq '^#define[[:space:]]+DRM_MODE_FLAG_CMD_MODE_PANEL[[:space:]]+\(1<<30\)' "$HDR"
grep -Eq '^#define[[:space:]]+DRM_MODE_FLAG_VID_MODE_PANEL[[:space:]]+\(1<<29\)' "$HDR"
grep -Eq '^#define[[:space:]]+DRM_MODE_FLAG_SUPPORTS_RGB[[:space:]]+\(1<<23\)' "$HDR"
grep -Eq '^#define[[:space:]]+DRM_MODE_FLAG_SUPPORTS_YUV[[:space:]]+\(1<<24\)' "$HDR"

python3 scripts/272_probe_drm_flag_parity.py "$ROOT" "$TG" >/tmp/phase272-probe-after.stdout
cp phase272-probe.txt phase272-parity-after.txt
grep -Fq 'gki_cmd_macro_present=1' phase272-parity-after.txt
grep -Fq 'gki_all_mentions_cmd=1' phase272-parity-after.txt
grep -Fq 'tg_cmd_macro_present=1' phase272-parity-after.txt
grep -Fq 'tg_all_mentions_cmd=1' phase272-parity-after.txt

# Rebuild from the same config. The only functional delta is DRM vendor flag
# parity in the UAPI header; all Phase269/270/271 observers remain intact.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase271-before-phase272.config "$OUT/.config"
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase272-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
# Hardware proof still comes from the retained Phase271 validation observer.
for marker in \
  'P271 M id=%u hv=%ux%u r=%u clk=%d fl=%x ty=%x' \
  'P271 V id=%u k=D st=%d' \
  'P271 V id=%u k=S st=%d max=%ux%u' \
  'P271 V id=%u k=F st=%d mf=%x' \
  'P271 V id=%u k=P ret=%d st=%d' \
  'P271 Q id=%u k=C ret=%d st=%d' \
  'P271 Q id=%u k=E eid=%u pc=%x st=%d' \
  'P271 Q id=%u k=R eid=%u cid=%u st=%d' \
  'P270 DSI count id=%u rc=%d cnt=%u disp=%u panel=%u' \
  'P269 CONN n=%u id=%u enc=%u ne=%u nm=%u np=%u st=%u ty=%u' \
  'a52_ackfr_phase269_is_composer_tgid'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase272-out
mkdir -p phase272-out/compile phase272-out/config phase272-out/package \
  phase272-out/audit phase272-out/source
cp "$IMAGE" phase272-out/compile/Image
cp "$OUT/.config" phase272-out/config/final.config
gzip -n -c "$IMAGE" > phase272-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase271-out/package/boot.img \
  --kernel phase272-out/package/Image.gz \
  --output phase272-out/package/boot.img \
  --report phase272-out/package/repack-report.json
cp scripts/272_probe_drm_flag_parity.py phase272-out/audit/
cp scripts/272_drm_vendor_mode_flag_parity.py phase272-out/audit/
cp phase272-parity-before.txt phase272-out/audit/
cp phase272-parity-after.txt phase272-out/audit/
cp phase272-compile.log phase272-out/audit/
cp "$HDR" phase272-out/source/gki-drm_mode.h
cp "$TGHDR" phase272-out/source/touchgrass-drm_mode.h
cp "$ROOT/drivers/gpu/drm/drm_modes.c" phase272-out/source/gki-drm_modes.c

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase272-out')
identity = {
    'phase': 272,
    'name': 'DRM-VENDOR-MODE-FLAG-PARITY',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'hardware_validated': False,
    'phase271_hardware_evidence': {
        'connector_id': 32,
        'modes': ['1080x2400@120', '1080x2400@60'],
        'mode_flags': '0x40000000',
        'first_rejection_stage': 'drm_mode_validate_driver',
        'status': -2,
    },
    'root_cause': 'pinned GKI DRM_MODE_FLAG_ALL lacks TouchGrass Qualcomm/SDE vendor panel-mode flags; command-mode bit30 is rejected by drm_mode_validate_basic as MODE_BAD',
    'touchgrass_parity': {
        'SUPPORTS_RGB': 23,
        'SUPPORTS_YUV': 24,
        'VID_MODE_PANEL': 29,
        'CMD_MODE_PANEL': 30,
        'SEAMLESS_defined_only': 31,
    },
    'functional_change': 'restore exact TouchGrass vendor DRM mode-flag definitions and accept RGB/YUV/VID/CMD flags in DRM_MODE_FLAG_ALL',
    'not_changed': [
        'no mode status forced to MODE_OK',
        'no validation callback bypassed',
        'no connector/encoder/CRTC routing semantics changed',
        'kernel config unchanged',
        'Phase269/270/271 observers retained',
    ],
    'hardware_question': 'Do both A52 command-mode panel modes now pass k=D and continue through the real DRM validation/routing pipeline?',
}
(out / 'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
files = [
    out/'compile/Image', out/'config/final.config', out/'package/Image.gz',
    out/'package/boot.img', out/'package/repack-report.json',
    out/'audit/phase272-parity-before.txt', out/'audit/phase272-parity-after.txt',
]
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY
(cd phase272-out && sha256sum -c SHA256SUMS)
test -s phase272-out/package/boot.img
grep -Fq 'A52_PHASE272_DRM_VENDOR_MODE_FLAG_PARITY_V1' phase272-out/source/gki-drm_mode.h
grep -Fq 'gki_all_mentions_cmd=1' phase272-out/audit/phase272-parity-after.txt

trap - EXIT
echo 'Phase272 DRM vendor mode-flag parity build/repack: PASS'
