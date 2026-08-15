#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"

fail_report() {
  set +e
  rm -rf phase270-failure
  mkdir -p phase270-failure/source phase270-failure/config phase270-failure/logs
  cp phase270-compile.log phase270-failure/logs/ 2>/dev/null || true
  cp "$OUT/.config" phase270-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/270_phase269_msm_legacy_force_probe.py phase270-failure/ 2>/dev/null || true
  for p in \
    "$ROOT/drivers/gpu/drm/drm_connector.c" \
    "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$ROOT/drivers/a52_display/msm/msm_drv.c"; do
    [ -f "$p" ] && cp "$p" "phase270-failure/source/$(echo "$p" | tr '/' '_')"
  done
  grep -RsnE 'A52_PHASE270|P269 FPROBE|a52_legacy_msm_composer_probe' \
    "$ROOT/drivers/gpu/drm/drm_connector.c" \
    > phase270-failure/phase270-markers.txt 2>&1 || true
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact Phase269 hardware-observer baseline first.
bash scripts/269_ci_build.sh

test -s phase269-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/phase269-before-phase270.config

python3 -m py_compile scripts/270_phase269_msm_legacy_force_probe.py
python3 scripts/270_phase269_msm_legacy_force_probe.py "$ROOT"
cmp -s /tmp/phase269-before-phase270.config "$OUT/.config"

CONN="$ROOT/drivers/gpu/drm/drm_connector.c"
grep -Fq 'A52_PHASE270_MSM_LEGACY_FORCE_PROBE_V1' "$CONN"
grep -Fq '!strcmp(dev->driver->name, "msm_drm")' "$CONN"
grep -Fq 'is_current_master || a52_legacy_msm_composer_probe' "$CONN"
grep -Fq 'P269 FPROBE id=%u master=0 drv=%.15s' "$CONN"

# Rebuild after the single compatibility change. Configuration must remain exact.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase269-before-phase270.config "$OUT/.config"
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase270-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P269 FPROBE id=%u master=0 drv=%.15s' \
  'P269 CONN n=%u id=%u enc=%u ne=%u nm=%u np=%u st=%u ty=%u' \
  'P269 IO n=%u nr=0x%x rc=%ld' \
  'a52_ackfr_phase269_is_composer_tgid'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase270-out
mkdir -p phase270-out/compile phase270-out/config phase270-out/package \
  phase270-out/audit phase270-out/source
cp "$IMAGE" phase270-out/compile/Image
cp "$OUT/.config" phase270-out/config/final.config
gzip -n -c "$IMAGE" > phase270-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase269-out/package/boot.img \
  --kernel phase270-out/package/Image.gz \
  --output phase270-out/package/boot.img \
  --report phase270-out/package/repack-report.json
cp scripts/270_phase269_msm_legacy_force_probe.py phase270-out/audit/
cp phase270-compile.log phase270-out/audit/
cp "$CONN" phase270-out/source/
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" phase270-out/source/
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" phase270-out/source/

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase270-out')
identity = {
    'phase': 270,
    'name': 'MSM-LEGACY-COMPOSER-FORCED-MODE-PROBE',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'phase269_hardware_result': 'DSI connected, count_encoders=1, but count_modes=0 and encoder_id=0',
    'hardware_validated': False,
    'diagnostic_only': False,
    'device_functional_semantics_changed': True,
    'scope': 'only exec-latched vendor Composer TGID on drm_driver name msm_drm',
    'change': 'restore legacy 4.19 GETCONNECTOR forced fill_modes behavior for scoped Composer client',
    'encoder_fallback_changed': False,
    'phase269_observer_retained': True,
    'success_criteria': 'P269 FPROBE fires; DSI nm changes from 0 toward golden 2; observe whether enc changes from 0 toward golden nonzero without forcing it',
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
(cd phase270-out && sha256sum -c SHA256SUMS)
test -s phase270-out/package/boot.img

trap - EXIT
echo 'Phase270 scoped legacy mode-probe compatibility build/repack: PASS'
