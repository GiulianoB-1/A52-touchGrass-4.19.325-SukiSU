#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
TG="$PWD/workspace/touchgrass-a52xq"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_panel.c"
PANEL="$ROOT/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
WRAP="$ROOT/drivers/a52_display/msm/samsung/ss_wrapper_common.c"
SDE="$ROOT/drivers/a52_display/msm/sde/sde_connector.c"
HDR="$ROOT/include/uapi/drm/drm_mode.h"

fail_report() {
  set +e
  rm -rf phase276-failure
  mkdir -p phase276-failure/source phase276-failure/config phase276-failure/logs phase276-failure/audit
  cp phase276-compile.log phase276-failure/logs/ 2>/dev/null || true
  cp phase276-dsi-panel-tx-parity-before.txt phase276-failure/audit/ 2>/dev/null || true
  cp "$OUT/.config" phase276-failure/config/final-or-partial.config 2>/dev/null || true
  cp scripts/276_dsi_panel_tx_parity_probe.py phase276-failure/audit/ 2>/dev/null || true
  cp scripts/276_dsi_panel_transfer_frontier.py phase276-failure/audit/ 2>/dev/null || true
  cp scripts/276_audit_candidate.py phase276-failure/audit/ 2>/dev/null || true
  for p in "$REC" "$DSI" "$PANEL" "$WRAP" "$SDE" "$HDR"; do
    [ -f "$p" ] && cp "$p" phase276-failure/source/ || true
  done
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then fail_report; fi; exit "$rc"' EXIT

# Reconstruct the exact hardware-tested Phase275 diagnostic baseline first.
bash scripts/275_ci_build.sh

test -s phase275-out/package/boot.img
test -s "$OUT/arch/arm64/boot/Image"
for p in "$REC" "$DSI" "$PANEL" "$WRAP" "$SDE" "$HDR"; do test -s "$p"; done

cp "$OUT/.config" /tmp/phase275-before-phase276.config
cp "$REC" /tmp/phase275-before-phase276-recorder.c
cp "$DSI" /tmp/phase275-before-phase276-dsi-panel.c
cp "$PANEL" /tmp/phase275-before-phase276-panel-common.c
cp "$WRAP" /tmp/phase275-before-phase276-wrapper.c
cp "$SDE" /tmp/phase275-before-phase276-sde.c
cp "$HDR" /tmp/phase275-before-phase276-drm_mode.h

# Mandatory exact-source gate before adding any deeper observation.
python3 -m py_compile scripts/276_dsi_panel_tx_parity_probe.py
python3 scripts/276_dsi_panel_tx_parity_probe.py "$ROOT" "$TG"
grep -Fq 'function=dsi_panel_tx_cmd_set' phase276-dsi-panel-tx-parity-before.txt
grep -Fq 'exact_match=1' phase276-dsi-panel-tx-parity-before.txt

python3 -m py_compile scripts/276_dsi_panel_transfer_frontier.py
python3 -m py_compile scripts/276_audit_candidate.py
python3 scripts/276_dsi_panel_transfer_frontier.py "$ROOT"

# Observation-only closure: only recorder admission and dsi_panel.c move.
cmp -s /tmp/phase275-before-phase276.config "$OUT/.config"
cmp -s /tmp/phase275-before-phase276-drm_mode.h "$HDR"
cmp -s /tmp/phase275-before-phase276-sde.c "$SDE"
cmp -s /tmp/phase275-before-phase276-panel-common.c "$PANEL"
cmp -s /tmp/phase275-before-phase276-wrapper.c "$WRAP"
! cmp -s /tmp/phase275-before-phase276-recorder.c "$REC"
! cmp -s /tmp/phase275-before-phase276-dsi-panel.c "$DSI"

for token in \
  'A52_PHASE276_DSI_PANEL_TRANSFER_FRONTIER_V1' \
  'return !strncmp(message, "P276 ", 5)' \
  'A52_PHASE275_LEVEL1_KEY_SEND_FRONTIER_V1' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d'; do
  grep -Fq "$token" "$REC"
done

for token in \
  'A52_PHASE276_DSI_PANEL_TX_FRONTIER_V1' \
  'P276 T E ty=%d' \
  'P276 T S ty=%d p=0' \
  'P276 T A n=%x s=%x e=%x p=%x' \
  'P276 T X ty=%d p=0 ex=%u xp=%u' \
  'P276 T L ty=%d p=0 h=%u' \
  'P276 T L ty=%d p=1' \
  'P276 T M0 n=%x s=%x mt=%x l=%x' \
  'P276 T M1 f=%x c=%x z=%x w=%x' \
  'P276 T O i=%d p=0 mt=%u tl=%u fl=%x' \
  'P276 T O i=%d p=1 len=%zd' \
  'P276 T Z ty=%d p=1 rc=%d' \
  'ops->transfer(panel->host, &cmds->msg);'; do
  grep -Fq "$token" "$DSI"
done

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/phase275-before-phase276.config "$OUT/.config"

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase276-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"
for marker in \
  'P276 T E ty=%d' \
  'P276 T L ty=%d p=0 h=%u' \
  'P276 T L ty=%d p=1' \
  'P276 T M0 n=%x s=%x mt=%x l=%x' \
  'P276 T O i=%d p=0 mt=%u tl=%u fl=%x' \
  'P276 T O i=%d p=1 len=%zd' \
  'P275 W T ty=%d p=0' \
  'P274 G K n=%d p=0 lk=%x' \
  'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d' \
  'P271 V id=%u k=D st=%d'; do
  grep -aFq "$marker" "$IMAGE"
done

rm -rf phase276-out
mkdir -p phase276-out/compile phase276-out/config phase276-out/package \
  phase276-out/audit phase276-out/source
cp "$IMAGE" phase276-out/compile/Image
cp "$OUT/.config" phase276-out/config/final.config
cp /tmp/phase275-before-phase276.config phase276-out/audit/phase275-final.config
cp phase276-dsi-panel-tx-parity-before.txt phase276-out/audit/
cp phase276-compile.log phase276-out/audit/
cp scripts/276_dsi_panel_tx_parity_probe.py scripts/276_dsi_panel_transfer_frontier.py scripts/276_audit_candidate.py phase276-out/audit/
cp "$REC" phase276-out/source/a52_ack_secure_flight_recorder.c
cp "$DSI" phase276-out/source/dsi_panel.c
cp "$PANEL" phase276-out/source/ss_dsi_panel_common.c
cp "$WRAP" phase276-out/source/ss_wrapper_common.c
cp "$SDE" phase276-out/source/sde_connector.c
cp "$HDR" phase276-out/source/drm_mode.h

gzip -n -c "$IMAGE" > phase276-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase275-out/package/boot.img \
  --kernel phase276-out/package/Image.gz \
  --output phase276-out/package/boot.img \
  --report phase276-out/package/repack-report.json

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
out = Path('phase276-out')
identity = {
    'phase': 276,
    'name': 'DSI-PANEL-TRANSFER-FRONTIER-V1',
    'git_sha': os.environ.get('GITHUB_SHA'),
    'run_id': os.environ.get('GITHUB_RUN_ID'),
    'hardware_validated': False,
    'base_phase275_hardware_head': 'fefb6bd6042b27ac8ca3c0bb77c019dbc159686f',
    'phase275_hardware_capture': 'A52_RAW_RAMOOPS_20260816_181610.zip',
    'phase275_decode': {
        'valid_records': 1028,
        'first_sequence': 5388,
        'last_sequence': 6519,
        'missing_sequences': 104,
    },
    'target_command': 'TX_LEVEL1_KEY_ENABLE',
    'target_command_numeric_type': 42,
    'phase275_hardware_evidence': [
        'P275 ss_send_cmd vdd_lock returned',
        'P275 PM-resume wait returned',
        'P275 wrapper DSI clock-on returned rc=0',
        'P275 entered dsi_panel_tx_cmd_set(type=42)',
        'P275 did not return from dsi_panel_tx_cmd_set',
        'system_server remained present while SystemUI had never appeared in retained frontier',
    ],
    'golden_touchgrass_runtime': [
        'healthy manufacture-ID read type 351 uses LEVEL1_KEY and completes',
        'healthy sequence proceeds through manufacture-ID types 351, 352, and 353',
    ],
    'preinstrumentation_source_gate': 'reconstructed GKI dsi_panel_tx_cmd_set must exactly match pinned TouchGrass before Phase276 instrumentation',
    'changes': [
        'admit P276 diagnostic records into unchanged R48/RS48 recorder',
        'target-only checkpoints around exclusive wait and Samsung cmd_lock',
        'target-only first-command metadata for TX_LEVEL1_KEY_ENABLE',
        'target-only checkpoints around each mipi host ops->transfer call',
    ],
    'not_changed': [
        'command selection and payload bytes',
        'exclusive wait condition',
        'cmd_lock behavior or ordering',
        'message flags and last-command logic',
        'transfer retry behavior',
        'post-command waits',
        'DSI clock behavior',
        'Phase272 DRM mode-flag parity',
        'Phase273-275 observers',
        'kernel config',
        'R48 v3 / RS48 / CRC32C wire format',
    ],
    'hardware_question': 'Inside dsi_panel_tx_cmd_set(TX_LEVEL1_KEY_ENABLE), does execution stall on Samsung cmd_lock or inside the MIPI host ops->transfer call?',
}
(out/'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True)+'\n')
files = [
    out/'compile/Image', out/'config/final.config', out/'package/Image.gz',
    out/'package/boot.img', out/'package/repack-report.json',
    out/'audit/phase275-final.config', out/'audit/phase276-dsi-panel-tx-parity-before.txt',
    out/'source/a52_ack_secure_flight_recorder.c', out/'source/dsi_panel.c',
    out/'source/ss_dsi_panel_common.c', out/'source/ss_wrapper_common.c',
    out/'source/sde_connector.c', out/'source/drm_mode.h',
]
with (out/'SHA256SUMS').open('w') as f:
    for p in files:
        f.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(out)}\n')
PY

(cd phase276-out && sha256sum -c SHA256SUMS)
python3 scripts/276_audit_candidate.py phase276-out
trap - EXIT
echo 'Phase276 DSI panel transfer frontier build/repack: PASS'
