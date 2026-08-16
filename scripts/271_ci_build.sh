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
  cp scripts/271b_broad_zero_state_observer.py phase271-failure/ 2>/dev/null || true
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
    "$ROOT/drivers/a52_secure" \
    "$ROOT/drivers/gpu/drm/drm_probe_helper.c" \
    "$ROOT/drivers/gpu/drm/drm_connector.c" \
    "$ROOT/drivers/a52_display/msm/sde/sde_connector.c" \
    > phase271-failure/phase271-markers.txt 2>&1 || true
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase270 observer baseline first.
bash scripts/270_ci_build.sh

test -s phase270-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
cp "$OUT/.config" /tmp/phase270-before-phase271.config

# Preserve the exact per-mode validation observer, then layer the broader
# routing/list/topology observer on top. Both are diagnostics only.
python3 -m py_compile scripts/271_drm_mode_validation_observer.py
python3 -m py_compile scripts/271b_broad_zero_state_observer.py
python3 scripts/271_drm_mode_validation_observer.py "$ROOT"
python3 scripts/271b_broad_zero_state_observer.py "$ROOT"
cmp -s /tmp/phase270-before-phase271.config "$OUT/.config"

REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PROBE="$ROOT/drivers/gpu/drm/drm_probe_helper.c"
CORE="$ROOT/drivers/gpu/drm/drm_connector.c"
SDE="$ROOT/drivers/a52_display/msm/sde/sde_connector.c"

grep -Fq 'return !strncmp(message, "P271 ", 5) ||' "$REC"
grep -Fq 'if (strncmp(fmt, "P271", 4) &&' "$REC"
grep -Fq 'A52_PHASE271_DRM_MODE_VALIDATION_OBSERVER_V1' "$PROBE"
grep -Fq 'A52_PHASE271B_BROAD_ZERO_STATE_OBSERVER_V1' "$PROBE"

for marker in \
  'P271 L id=%u k=G ret=%d m=%u pm=%u st=%u' \
  'P271 L id=%u k=U ret=%d m=%u pm=%u st=%u' \
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
  'P271 Z id=%u hv=%ux%u r=%u fl=%x st=%d' \
  'P271 L id=%u k=B ret=%d m=%u pm=%u st=%u' \
  'P271 L id=%u k=A ret=%d m=%u pm=%u st=%u'; do
  grep -Fq "$marker" "$PROBE"
done

for marker in \
  'P271 T id=%u nc=%d ne=%d nr=%d max=%dx%d' \
  'P271 C id=%u stp=%u best=%u scrtc=%u legacy=%u pe=%x ne=%d' \
  'P271 E id=%u eid=%u pc=%x pcl=%x ec=%u ecs=%u eca=%u' \
  'P271 R cid=%u sp=%u act=%u en=%u' \
  'P271 G id=%u sel=%u stp=%u best=%u scrtc=%u legacy=%u'; do
  grep -Fq "$marker" "$CORE"
done
for marker in \
  'P271 SI id=%u eid=%u pe=%x pc=%x hv2=%u ty=%d' \
  'P271 SB id=%u eid=%u stp=%u best=%u' \
  'P271 SA id=%u eid=%u scrtc=%u'; do
  grep -Fq "$marker" "$SDE"
done

# Observer-only rebuild. Configuration and display semantics stay unchanged.
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
  'P271 L id=%u k=G ret=%d m=%u pm=%u st=%u' \
  'P271 V id=%u k=F st=%d mf=%x' \
  'P271 Q id=%u k=R eid=%u cid=%u st=%d' \
  'P271 C id=%u stp=%u best=%u scrtc=%u legacy=%u pe=%x ne=%d' \
  'P271 G id=%u sel=%u stp=%u best=%u scrtc=%u legacy=%u' \
  'P271 SI id=%u eid=%u pe=%x pc=%x hv2=%u ty=%d' \
  'P271 SB id=%u eid=%u stp=%u best=%u' \
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
cp scripts/271b_broad_zero_state_observer.py phase271-out/audit/
cp phase271-compile.log phase271-out/audit/
cp "$REC" "$PROBE" "$CORE" "$SDE" phase271-out/source/

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase271-out')
identity = {
    'phase': 271,
    'name': 'BROAD-DRM-ZERO-STATE-OBSERVER',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'phase270_hardware_result': 'panel/DSI/SDE expose 2 modes; DRM fill_modes exits with 0 modes for connected DSI',
    'hardware_validated': False,
    'diagnostic_only': True,
    'device_functional_semantics_changed': False,
    'phase269_observer_retained': True,
    'phase270_observer_retained': True,
    'scope': 'exact Composer DSI probe plus boot-time SDE connector attach state',
    'observer': [
        'mode list lifecycle: post get_modes, post list_update, pre/post prune',
        'per-mode validation: driver/basic, size, flags, connector, encoder, bridge, CRTC, YCbCr420',
        'connector state: atomic best_encoder, state CRTC, legacy encoder, possible encoders',
        'encoder state: possible_crtcs/clones, current CRTC, CRTC active/enable state',
        'SDE connector attach, best_encoder and atomic_best_encoder paths',
        'mode_config connector/encoder/CRTC counts and maximum dimensions',
        'Phase269 UAPI and Phase270 DSI/panel observers retained',
    ],
    'question': 'Which validation or routing state turns valid downstream display nodes into zero modes/encoder/crtc IDs seen by legacy Composer?',
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
echo 'Phase271 broad DRM zero-state observer build/repack: PASS'
