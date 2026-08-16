#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
WRAP="$ROOT/drivers/a52_display/msm/samsung/ss_wrapper_common.c"
SDE="$ROOT/drivers/a52_display/msm/sde/sde_connector.c"
HDR="$ROOT/include/uapi/drm/drm_mode.h"

fail_report() {
  set +e
  rm -rf phase275-failure
  mkdir -p phase275-failure/source phase275-failure/config phase275-failure/logs phase275-failure/audit
  cp phase275-compile.log phase275-failure/logs/ 2>/dev/null || true
  cp phase275-send-path-parity-before.txt phase275-failure/audit/ 2>/dev/null || true
  cp "$OUT/.config" phase275-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/275_send_path_parity_probe.py phase275-failure/audit/ 2>/dev/null || true
  cp scripts/275_level1_key_send_frontier.py phase275-failure/audit/ 2>/dev/null || true
  cp scripts/275_audit_candidate.py phase275-failure/audit/ 2>/dev/null || true
  for p in "$REC" "$PANEL" "$WRAP" "$SDE" "$HDR"; do
    [ -f "$p" ] && cp "$p" phase275-failure/source/ || true
  done
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase274 diagnostic baseline first.
# Phase275 must not alter the Phase272 DRM fix or Phase274 process/GPARA logic.
bash scripts/274_ci_build.sh

test -s phase274-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for p in "$REC" "$PANEL" "$WRAP" "$SDE" "$HDR"; do test -s "$p"; done
cp "$OUT/.config" /tmp/phase274-before-phase275.config
cp "$REC" /tmp/phase274-before-phase275-recorder.c
cp "$PANEL" /tmp/phase274-before-phase275-panel.c
cp "$WRAP" /tmp/phase274-before-phase275-wrapper.c
cp "$SDE" /tmp/phase274-before-phase275-sde.c
cp "$HDR" /tmp/phase274-before-phase275-drm_mode.h

# Evidence-first gate. Phase274 hardware proves the first non-returning call is
# ss_send_cmd(TX_LEVEL1_KEY_ENABLE) during type 351 manufacture-ID read. Before
# instrumenting deeper, compare the exact send path to pinned TouchGrass.
python3 -m py_compile scripts/275_send_path_parity_probe.py
python3 scripts/275_send_path_parity_probe.py "$ROOT" "$TG"
grep -Fq 'function=ss_send_cmd' phase275-send-path-parity-before.txt
grep -Fq 'function=ss_wrapper_dsi_panel_tx_cmd_set' phase275-send-path-parity-before.txt
grep -Fq 'all_exact_match=1' phase275-send-path-parity-before.txt

python3 -m py_compile scripts/275_level1_key_send_frontier.py
python3 -m py_compile scripts/275_audit_candidate.py
python3 scripts/275_level1_key_send_frontier.py "$ROOT"

# Diagnostic-only closure. Config, DRM header, and SDE behavior remain exactly
# Phase274. Only recorder admission plus two Samsung send-path source files move.
cmp -s /tmp/phase274-before-phase275.config "$OUT/.config"
cmp -s /tmp/phase274-before-phase275-drm_mode.h "$HDR"
cmp -s /tmp/phase274-before-phase275-sde.c "$SDE"
! cmp -s /tmp/phase274-before-phase275-recorder.c "$REC"
! cmp -s /tmp/phase274-before-phase275-panel.c "$PANEL"
! cmp -s /tmp/phase274-before-phase275-wrapper.c "$WRAP"

for token in \
  'A52_PHASE275_LEVEL1_KEY_SEND_FRONTIER_V1' \
  'return !strncmp(message, "P275 ", 5)' \
  'A52_PHASE274_FRONTIER_TIMEBASE_FIX_V1' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d'; do
  grep -Fq "$token" "$REC"
done
for token in \
  'A52_PHASE275_SS_SEND_CMD_FRONTIER_V1' \
  'P275 S E ty=%d' \
  'P275 S A ty=%d ex=%u xp=%u' \
  'P275 S L ty=%d p=0' \
  'P275 S L ty=%d p=1' \
  'P275 S M ty=%d p=0' \
  'P275 S M ty=%d p=1' \
  'P275 S W ty=%d p=0' \
  'P275 S W ty=%d p=1' \
  'P275 S Z ty=%d rc=%d' \
  'A52_PHASE274_PANEL_GPARA_FRONTIER_V1'; do
  grep -Fq "$token" "$PANEL"
done
for token in \
  'A52_PHASE275_DSI_TX_WRAPPER_FRONTIER_V1' \
  'P275 W E ty=%d' \
  'P275 W C ty=%d p=0 on=1' \
  'P275 W C ty=%d p=1 on=1 rc=%d' \
  'P275 W T ty=%d p=0' \
  'P275 W T ty=%d p=1 rc=%d' \
  'P275 W C ty=%d p=0 on=0' \
  'P275 W C ty=%d p=1 on=0 rc=%d' \
  'P275 W Z ty=%d rc=%d'; do
  grep -Fq "$token" "$WRAP"
done

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase274-before-phase275.config "$OUT/.config"
make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase275-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P275 S E ty=%d' \
  'P275 S L ty=%d p=0' \
  'P275 S M ty=%d p=0' \
  'P275 S W ty=%d p=0' \
  'P275 W C ty=%d p=0 on=1' \
  'P275 W T ty=%d p=0' \
  'P275 W C ty=%d p=0 on=0' \
  'P274 G K n=%d p=0 lk=%x' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d' \
  'P271 V id=%u k=D st=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase275-out
mkdir -p phase275-out/compile phase275-out/config phase275-out/package phase275-out/audit phase275-out/source
cp "$IMAGE" phase275-out/compile/Image
cp "$OUT/.config" phase275-out/config/final.config
cp /tmp/phase274-before-phase275.config phase275-out/audit/phase274-final.config
cp phase275-send-path-parity-before.txt phase275-out/audit/
cp phase275-compile.log phase275-out/audit/
cp scripts/275_send_path_parity_probe.py scripts/275_level1_key_send_frontier.py scripts/275_audit_candidate.py phase275-out/audit/
cp "$REC" phase275-out/source/a52_ack_secure_flight_recorder.c
cp "$PANEL" phase275-out/source/ss_dsi_panel_common.c
cp "$WRAP" phase275-out/source/ss_wrapper_common.c
cp "$SDE" phase275-out/source/sde_connector.c
cp "$HDR" phase275-out/source/drm_mode.h

gzip -n -c "$IMAGE" > phase275-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase274-out/package/boot.img \
  --kernel phase275-out/package/Image.gz \
  --output phase275-out/package/boot.img \
  --report phase275-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase275-out')
identity = {
    'phase': 275,
    'name': 'LEVEL1-KEY-SEND-FRONTIER-V1',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'hardware_validated': False,
    'base_phase274_hardware_head': 'ab0ab2127f067112ad9b9ffb69185fa9a3d55c64',
    'phase274_hardware_capture': 'A52_RAW_RAMOOPS_20260816_162703.zip',
    'target_command': 'TX_LEVEL1_KEY_ENABLE',
    'phase274_hardware_evidence': {
        'gpara_call': 1,
        'read_type': 351,
        'level_key': 2,
        'level_key_name': 'LEVEL1_KEY',
        'last_returned_checkpoint': 'P274 G K n=1 p=0 lk=2',
        'next_expected_checkpoint_missing': 'P274 G K n=1 p=1 lk=2',
        'crtc_thread': 'crtc_commit:107',
        'timestamp_ms': 277352.617,
        'system_server_pid': 1367,
        'systemui_ever_seen': False,
    },
    'golden_touchgrass_runtime': [
        'same manufacture-ID type 351 proceeds successfully with LEVEL1_KEY and returns byte 0x80',
        'healthy sequence continues through type 352 byte 0x00 and type 353 byte 0x0a',
    ],
    'preinstrumentation_source_gate': [
        'ss_send_cmd equals pinned TouchGrass after removing only the two pre-existing DISP SS_CMD recorder calls',
        'ss_wrapper_dsi_panel_tx_cmd_set equals pinned TouchGrass exactly',
    ],
    'changes': [
        'admit P275 diagnostic records into unchanged R48/RS48 recorder',
        'target-only checkpoints around vdd_lock, PM-resume wait, and wrapper call for TX_LEVEL1_KEY_ENABLE',
        'target-only checkpoints around DSI clock ON, dsi_panel_tx_cmd_set, and DSI clock OFF',
    ],
    'not_changed': [
        'Samsung command selection and payloads',
        'mutex ordering',
        'PM-resume behavior',
        'DSI clock control ordering',
        'dsi_panel_tx_cmd_set semantics',
        'Phase272 DRM mode-flag parity',
        'Phase273/274 process and GPARA observers',
        'kernel config',
        'R48 v3 / RS48 / CRC32C wire format',
    ],
    'hardware_question': 'Inside the proven non-returning TX_LEVEL1_KEY_ENABLE send, is the first non-returning primitive vdd_lock, PM-resume wait, DSI clock-on, dsi_panel_tx_cmd_set, or clock-off?',
}
(out/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [
    out/'compile/Image', out/'config/final.config', out/'package/Image.gz', out/'package/boot.img',
    out/'package/repack-report.json', out/'audit/phase274-final.config',
    out/'audit/phase275-send-path-parity-before.txt', out/'source/a52_ack_secure_flight_recorder.c',
    out/'source/ss_dsi_panel_common.c', out/'source/ss_wrapper_common.c',
    out/'source/sde_connector.c', out/'source/drm_mode.h',
]
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY

(cd phase275-out && sha256sum -c SHA256SUMS)
python3 scripts/275_audit_candidate.py phase275-out
trap - EXIT
echo 'Phase275 LEVEL1-key send frontier build/repack: PASS'
