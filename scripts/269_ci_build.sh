#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"

python3 -m py_compile scripts/269_phase268_crtc_topology_sticky.py
python3 scripts/269_phase268_crtc_topology_sticky.py --self-test

# Reuse the already-proven Phase268 reconstruction driver, but inject Phase269
# immediately before its single final kernel compile. This preserves the exact
# Phase266 -> Phase267 -> Phase268 source chain and still compiles only once.
python3 - <<'PY'
from pathlib import Path
src = Path('scripts/268_ci_build.sh').read_text()
needle = '# One final compile, same config semantics as Phase267R.\n'
if src.count(needle) != 1:
    raise SystemExit(f'expected one Phase268 compile anchor, found {src.count(needle)}')
insert = r'''# Phase269: hardware Phase268 proves Composer reaches DRM, while Phase267
# sticky topology reports 0 CRTCs / 2 encoders / 2 connectors / 8 planes.
# Reuse existing KMSOBJ call sites and add successful DRM resource-return taps.
python3 scripts/269_phase268_crtc_topology_sticky.py "$ROOT"
cmp -s /tmp/phase266-before-diagnostics.config "$OUT/.config"

grep -Fq 'A52_PHASE269_CRTC_TOPOLOGY_STICKY_V1' "$REC"
grep -Fq 'return !strncmp(message, "P269 ", 5) ||' "$REC"
grep -Fq 'strncmp(fmt, "KMSOBJ", 6)' "$REC"
grep -Fq 'strncmp(fmt, "DRMRES 269", 10)' "$REC"
grep -Fq 'P269 A t=%u ts=%d mx=%d ss=%d mc=%d en=%d co=%d' "$REC"
grep -Fq 'P269 B t=%u ps=%d pp=%d li=%d ce=%d cx=%d cr=%d' "$REC"
grep -Fq 'P269 C t=%u rr=%d f=%d c=%d e=%d n=%d rp=%d pc=%d' "$REC"
grep -Fq 'KMSOBJ counts mixers=%u sspp=%u max-crtc=%d enc=%d conn=%d' \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c"

grep -RslF 'DRMRES 269 res g=%d p=%d f=%u c=%u e=%u n=%u' \
  "$ROOT/drivers/gpu/drm" | grep -q .
grep -RslF 'DRMRES 269 plane g=%d p=%d n=%u' \
  "$ROOT/drivers/gpu/drm" | grep -q .

'''
Path('/tmp/phase269-base-build.sh').write_text(src.replace(needle, insert + needle, 1))
PY

bash /tmp/phase269-base-build.sh

IMAGE="$OUT/arch/arm64/boot/Image"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
SDE="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"

test -s "$IMAGE"
for marker in \
  'P269 A t=%u ts=%d mx=%d ss=%d mc=%d en=%d co=%d' \
  'P269 B t=%u ps=%d pp=%d li=%d ce=%d cx=%d cr=%d' \
  'P269 C t=%u rr=%d f=%d c=%d e=%d n=%d rp=%d pc=%d' \
  'KMSOBJ counts mixers=%u sspp=%u max-crtc=%d enc=%d conn=%d' \
  'KMSOBJ plane enter i=%d id=%u primary=%d' \
  'KMSOBJ crtc enter i=%d' \
  'DRMRES 269 res g=%d p=%d f=%u c=%u e=%u n=%u' \
  'DRMRES 269 plane g=%d p=%d n=%u'; do
  grep -aFq "$marker" "$IMAGE"
done

# Convert the Phase268 driver's package into a Phase269-labeled artifact.
rm -rf phase269-out
mv phase268-out phase269-out
cp scripts/269_phase268_crtc_topology_sticky.py phase269-out/audit/
cp "$REC" phase269-out/source/a52_ack_secure_flight_recorder.c
cp "$SDE" phase269-out/source/sde_kms.c

while IFS= read -r src; do
  cp "$src" "phase269-out/source/$(basename "$src")"
done < <(grep -RslF 'A52_PHASE269_DRM_RESOURCE_TRACE_V1' "$ROOT/drivers/gpu/drm")

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase269-out')
identity = {
    'phase': 269,
    'name': 'CRTC-TOPOLOGY-STICKY',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'phase266_base_sha': os.environ.get('PHASE266_BASE_SHA'),
    'phase267_hardware_sha': os.environ.get('PHASE267_HW_SHA'),
    'phase268_hardware_sha': os.environ.get('PHASE268_HW_SHA'),
    'phase268_hardware_result': (
        'vendor composer opens /dev/dri/card0, generic drm_open and MSM open '
        'complete with rc=0; first visible DRM ioctl window succeeds; no '
        'msm_atomic_check observed'
    ),
    'phase267_topology_result': 'DRM object init rc=0 with 0 CRTCs, 2 encoders, 2 connectors, 8 planes',
    'hardware_validated': False,
    'diagnostic_only': True,
    'device_functional_semantics_changed': False,
    'final_kernel_compile_count': 1,
    'question': (
        'Why are zero CRTCs created, and does vendor composer receive '
        'count_crtcs=0 from DRM_IOCTL_MODE_GETRESOURCES?'
    ),
    'instrumentation': [
        'reuse existing KMSOBJ mixer/SSPP/max-CRTC markers',
        'reuse existing KMSOBJ plane primary decisions and CRTC attempts',
        'record successful drm_mode_getresources counts with caller TGID',
        'record successful drm_mode_getplane_res count with caller TGID',
        'retain P269 A/B/C sticky state at ticks 120/150/160/170/180',
        'keep KMSOBJ and DRMRES raw records noncritical',
    ],
}
(out / 'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
files = [
    out/'compile/Image', out/'config/final.config', out/'package/Image.gz',
    out/'package/boot.img', out/'package/repack-report.json'
]
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY

(cd phase269-out && sha256sum -c SHA256SUMS)
test -s phase269-out/package/boot.img

echo 'Phase269 CRTC topology/resource build, closure verification, and repack: PASS'
