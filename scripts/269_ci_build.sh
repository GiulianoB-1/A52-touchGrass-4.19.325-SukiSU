#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"

fail_report() {
  set +e
  rm -rf phase269-failure
  mkdir -p phase269-failure/source phase269-failure/config phase269-failure/logs
  cp phase269-compile.log phase269-failure/logs/ 2>/dev/null || true
  cp "$OUT/.config" phase269-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/269_phase268_composer_drm_uapi.py phase269-failure/ 2>/dev/null || true
  for p in \
    "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$ROOT/drivers/a52_display/msm/msm_drv.c"; do
    [ -f "$p" ] && cp "$p" "phase269-failure/source/$(basename "$p")"
  done
  grep -RsnE 'P269|A52_PHASE269|DRMPOST 211' \
    "$ROOT/drivers/a52_secure" "$ROOT/drivers/a52_display/msm" \
    > phase269-failure/phase269-markers.txt 2>&1 || true
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase268 source/config/package first.
bash scripts/268_ci_build.sh

test -s phase268-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/phase268-before-phase269.config

python3 -m py_compile scripts/269_phase268_composer_drm_uapi.py
python3 scripts/269_phase268_composer_drm_uapi.py "$ROOT"
cmp -s /tmp/phase268-before-phase269.config "$OUT/.config"

REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
MSM="$ROOT/drivers/a52_display/msm/msm_drv.c"
grep -Fq 'return !strncmp(message, "P269 ", 5) ||' "$REC"
grep -Fq 'if (strncmp(fmt, "P269", 4) &&' "$REC"
grep -Fq '#define A52_R269_IOCTL_LIMIT 1024U' "$MSM"
grep -Fq 'P269 " fmt' "$MSM"
grep -Fq 'A52_R269_REC("CONN n=%u id=%u enc=%u' "$MSM"
grep -Fq 'A52_R269_REC("RES n=%u c=%u k=%u e=%u f=%u' "$MSM"
grep -Fq 'A52_R269_REC("ENC n=%u id=%u crtc=%u' "$MSM"
grep -Fq 'A52_R269_REC("PROP n=%u id=%u fl=0x%x' "$MSM"
grep -Fq 'A52_R269_REC("BLOB n=%u id=%u len=%u' "$MSM"
grep -Fq 'A52_R269_REC("OBJ n=%u id=%u ty=0x%x np=%u' "$MSM"

# Config is intentionally unchanged; rebuild only after the observer is applied.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase268-before-phase269.config "$OUT/.config"
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase269-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P269 IO n=%u nr=0x%x rc=%ld' \
  'P269 RES n=%u c=%u k=%u e=%u f=%u' \
  'P269 CONN n=%u id=%u enc=%u ne=%u nm=%u np=%u st=%u ty=%u' \
  'P269 ENC n=%u id=%u crtc=%u pc=0x%x pcl=0x%x ty=%u' \
  'P269 PROP n=%u id=%u fl=0x%x nv=%u ne=%u name=%.31s' \
  'P269 BLOB n=%u id=%u len=%u scan=%u h=%llx' \
  'P269 OBJ n=%u id=%u ty=0x%x np=%u' \
  'P268 A t=%u cp=%d ex=%d pa=%d/%d/%d dr=%d/%d/%d/%d'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase269-out
mkdir -p phase269-out/compile phase269-out/config phase269-out/package \
  phase269-out/audit phase269-out/source
cp "$IMAGE" phase269-out/compile/Image
cp "$OUT/.config" phase269-out/config/final.config
gzip -n -c "$IMAGE" > phase269-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase268-out/package/boot.img \
  --kernel phase269-out/package/Image.gz \
  --output phase269-out/package/boot.img \
  --report phase269-out/package/repack-report.json
cp scripts/269_phase268_composer_drm_uapi.py phase269-out/audit/
cp phase269-compile.log phase269-out/audit/
cp "$REC" "$MSM" phase269-out/source/

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase269-out')
identity = {
    'phase': 269,
    'name': 'COMPOSER-DRM-UAPI-GOLDEN-DIFF',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'phase268_hardware_sha': 'a5e69f10bbfe67ca5cd0b992c7d1b36e09215113',
    'hardware_validated': False,
    'diagnostic_only': True,
    'device_functional_semantics_changed': False,
    'phase268_stream_preserved': True,
    'observer': 'Composer-only post-drm_ioctl UAPI payload capture',
    'question': 'What is the first DRM UAPI payload divergence versus the hardware-validated TouchGrass golden sequence?',
}
(out / 'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
files = [
    out/'compile/Image', out/'config/final.config', out/'package/Image.gz',
    out/'package/boot.img', out/'package/repack-report.json',
]
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY
(cd phase269-out && sha256sum -c SHA256SUMS)
test -s phase269-out/package/boot.img

trap - EXIT
echo 'Phase269 Composer DRM UAPI observer build/repack: PASS'
