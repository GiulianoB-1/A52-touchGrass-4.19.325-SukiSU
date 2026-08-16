#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"

fail_report() {
  set +e
  rm -rf phase271-failure
  mkdir -p phase271-failure/source phase271-failure/config phase271-failure/logs
  cp phase271-compile.log phase271-failure/logs/ 2>/dev/null || true
  cp "$OUT/.config" phase271-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/271_drm_mode_validation_observer.py phase271-failure/ 2>/dev/null || true
  for p in \
    "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" \
    "$ROOT/drivers/gpu/drm/drm_probe_helper.c" \
    "$ROOT/drivers/gpu/drm/drm_connector.c" \
    "$ROOT/drivers/a52_display/msm/sde/sde_connector.c" \
    "$ROOT/drivers/a52_display/msm/dsi/dsi_drm.c" \
    "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"; do
    [ -f "$p" ] && cp "$p" "phase271-failure/source/$(echo "$p" | tr '/' '_')"
  done
  grep -RsnE 'A52_PHASE271|P271' \
    "$ROOT/drivers/a52_secure" "$ROOT/drivers/gpu/drm/drm_probe_helper.c" \
    > phase271-failure/phase271-markers.txt 2>&1 || true
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase270 observer baseline first.
bash scripts/270_ci_build.sh

test -s phase270-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/phase270-before-phase271.config

python3 -m py_compile scripts/271_drm_mode_validation_observer.py
python3 scripts/271_drm_mode_validation_observer.py "$ROOT"
cmp -s /tmp/phase270-before-phase271.config "$OUT/.config"

REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PROBE="$ROOT/drivers/gpu/drm/drm_probe_helper.c"

grep -Fq 'return !strncmp(message, "P271 ", 5) ||' "$REC"
grep -Fq 'if (strncmp(fmt, "P271", 4) &&' "$REC"
grep -Fq 'A52_PHASE271_DRM_MODE_VALIDATION_OBSERVER_V1' "$PROBE"
grep -Fq 'P271 M id=%u hv=%ux%u r=%u clk=%d fl=%x ty=%x' "$PROBE"
grep -Fq 'P271 V id=%u k=D st=%d' "$PROBE"
grep -Fq 'P271 V id=%u k=S st=%d max=%ux%u' "$PROBE"
grep -Fq 'P271 V id=%u k=F st=%d mf=%x' "$PROBE"
grep -Fq 'P271 V id=%u k=P ret=%d st=%d' "$PROBE"
grep -Fq 'P271 V id=%u k=Y st=%d' "$PROBE"
grep -Fq 'P271 Q id=%u k=C ret=%d st=%d' "$PROBE"
grep -Fq 'P271 Q id=%u k=E eid=%u pc=%x st=%d' "$PROBE"
grep -Fq 'P271 Q id=%u k=B eid=%u br=%u st=%d' "$PROBE"
grep -Fq 'P271 Q id=%u k=R eid=%u cid=%u st=%d' "$PROBE"

# Observer-only rebuild. Configuration and validation semantics stay unchanged.
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase270-before-phase271.config "$OUT/.config"
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase271-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P271 M id=%u hv=%ux%u r=%u clk=%d fl=%x ty=%x' \
  'P271 V id=%u k=D st=%d' \
  'P271 V id=%u k=S st=%d max=%ux%u' \
  'P271 V id=%u k=F st=%d mf=%x' \
  'P271 V id=%u k=P ret=%d st=%d' \
  'P271 V id=%u k=Y st=%d' \
  'P271 Q id=%u k=C ret=%d st=%d' \
  'P271 Q id=%u k=E eid=%u pc=%x st=%d' \
  'P271 Q id=%u k=B eid=%u br=%u st=%d' \
  'P271 Q id=%u k=R eid=%u cid=%u st=%d' \
  'P270 DSI count id=%u rc=%d cnt=%u disp=%u panel=%u' \
  'P269 CONN n=%u id=%u enc=%u ne=%u nm=%u np=%u st=%u ty=%u' \
  'a52_ackfr_phase269_is_composer_tgid'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase271-out
mkdir -p phase271-out/compile phase271-out/config phase271-out/package \
  phase271-out/audit phase271-out/source
cp "$IMAGE" phase271-out/compile/Image
cp "$OUT/.config" phase271-out/config/final.config
gzip -n -c "$IMAGE" > phase271-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase270-out/package/boot.img \
  --kernel phase271-out/package/Image.gz \
  --output phase271-out/package/boot.img \
  --report phase271-out/package/repack-report.json
cp scripts/271_drm_mode_validation_observer.py phase271-out/audit/
cp phase271-compile.log phase271-out/audit/
cp "$REC" "$PROBE" phase271-out/source/

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase271-out')
identity = {
    'phase': 271,
    'name': 'DRM-MODE-VALIDATION-OBSERVER',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'phase270_hardware_result': 'DSI panel count=2, dsi_get_modes rc=0/count=2, SDE get_modes returns 2, DRM fill_modes returns 0 and leaves no modes',
    'hardware_validated': False,
    'diagnostic_only': True,
    'device_functional_semantics_changed': False,
    'phase269_observer_retained': True,
    'phase270_observer_retained': True,
    'scope': 'exact exec-latched Composer TGID and DSI connector only',
    'observer': 'DRM validation stages driver/basic, size, flags, connector, encoder, bridge, CRTC, YCbCr420',
    'question': 'Which exact DRM validation stage/status rejects both valid DSI panel modes after SDE returns count=2?',
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
(cd phase271-out && sha256sum -c SHA256SUMS)
test -s phase271-out/package/boot.img

trap - EXIT
echo 'Phase271 DRM mode-validation observer build/repack: PASS'
