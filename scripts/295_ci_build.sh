#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
DISPLAY="$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

fail_report() {
  set +e
  rm -rf phase295-failure
  mkdir -p phase295-failure/{source,logs,audit}
  cp phase295-compile.log phase295-failure/logs/ 2>/dev/null || true
  for f in "$DISPLAY" "$CTRL" "$HW" "$REC"; do
    [ -f "$f" ] && cp "$f" phase295-failure/source/ || true
  done
  cp scripts/295_apply_gki_display_probe_enodata.py phase295-failure/audit/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact successful Phase293 software image first. Phase295 is
# observation-only and changes only dsi_display.c after that reconstruction.
bash scripts/293_ci_build.sh

test -s phase293-out/package/boot.img
test "$(stat -c '%s' phase293-out/package/boot.img)" -eq 100663296
test -s "$OUT/arch/arm64/boot/Image"
for f in "$DISPLAY" "$CTRL" "$HW" "$REC"; do test -s "$f"; done

grep -Fq 'A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1' "$CTRL"
grep -Fq 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' "$CTRL"
grep -Fq 'A52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_dev_probe");' "$DISPLAY"

cp "$OUT/.config" /tmp/p295-base.config
cp "$DISPLAY" /tmp/p295-display-before.c
cp "$CTRL" /tmp/p295-ctrl-before.c
cp "$HW" /tmp/p295-hw-before.c
cp "$REC" /tmp/p295-rec-before.c

python3 -m py_compile scripts/295_apply_gki_display_probe_enodata.py
python3 scripts/295_apply_gki_display_probe_enodata.py --root "$ROOT"
python3 scripts/295_apply_gki_display_probe_enodata.py --root "$ROOT" --check-only

# Phase295 may modify only dsi_display.c. The Phase293 controller/HW recorder,
# timeout-retention backend, and kernel config remain byte-for-byte identical.
! cmp -s /tmp/p295-display-before.c "$DISPLAY"
cmp -s /tmp/p295-ctrl-before.c "$CTRL"
cmp -s /tmp/p295-hw-before.c "$HW"
cmp -s /tmp/p295-rec-before.c "$REC"

# Refuse any accidental display behavior experiment.
for marker in \
  'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1' \
  'A52_PHASE282' \
  'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1' \
  'A52_PHASE292_DSI_CHAIN_TAPS_V1'; do
  if grep -Fq "$marker" "$DISPLAY"; then
    echo "Phase295 refuses unrelated display behavior: $marker" >&2
    exit 1
  fi
done

# Preserve exact Phase293 configuration.
make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
cmp -s /tmp/p295-base.config "$OUT/.config"

make -C "$ROOT" O="$OUT" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase295-compile.log

IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase295-out
mkdir -p phase295-out/{compile,config,package,audit,source}
cp "$IMAGE" phase295-out/compile/Image
cp "$OUT/.config" phase295-out/config/final.config
cp /tmp/p295-base.config phase295-out/audit/phase293-final.config
cp /tmp/p295-display-before.c phase295-out/audit/dsi-display-before.c
cp /tmp/p295-ctrl-before.c phase295-out/audit/dsi-ctrl-before.c
cp /tmp/p295-hw-before.c phase295-out/audit/dsi-ctrl-hw-before.c
cp /tmp/p295-rec-before.c phase295-out/audit/recorder-before.c
cp phase295-compile.log phase295-out/audit/
cp scripts/295_apply_gki_display_probe_enodata.py phase295-out/audit/
cp "$DISPLAY" phase295-out/source/dsi_display.c
cp "$CTRL" phase295-out/source/dsi_ctrl.c
cp "$HW" phase295-out/source/dsi_ctrl_hw_cmn.c
cp "$REC" phase295-out/source/a52_ack_secure_flight_recorder.c

gzip -n -c "$IMAGE" > phase295-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase293-out/package/boot.img \
  --kernel phase295-out/package/Image.gz \
  --output phase295-out/package/boot.img \
  --report phase295-out/package/repack-report.json

test "$(stat -c '%s' phase295-out/package/boot.img)" -eq 100663296

python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

out = Path('phase295-out')
identity = {
    'phase': 295,
    'name': 'GKI-DISPLAY-PROBE-ENODATA-FRONTIER',
    'git_sha': os.getenv('GITHUB_SHA'),
    'run_id': os.getenv('GITHUB_RUN_ID'),
    'hardware_validated': False,
    'base': 'exact successful Phase293 reconstruction',
    'functional_change': 'instrumentation-only',
    'display_control_flow_changed': False,
    'panel_packets_changed': False,
    'dsi_register_writes_added': False,
    'clock_changes_added': False,
    'waits_or_delays_added': False,
    'reset_or_recovery_added': False,
    'recorder_transport': 'existing R48/RS48; Phase295 records intentionally use admitted P276 prefix',
    'hardware_evidence_20260821': {
        'preserved_raw_sha256': '3bde7a6b9d3c91ca7249c868fee056b85df4ccda2b596087b5593342b6913f48',
        'raw_snapshot_bytes': 1048576,
        'crc32c_valid_unique_records': 1027,
        'sequence_range': '982..2028',
        'phase293_gdm_records': 0,
        'phase276_deep_target_records': 0,
        'display_probe_exit_rc': -61,
        'display_probe_exit_errno': 'ENODATA',
        'display_probe_failure_monotonic_ms': 'about 17827.7',
        'finding': 'GKI exits dsi_display_dev_probe with -ENODATA before the Phase293 FETCH_MEMORY transaction is admitted.',
    },
    'trace_schema': {
        '295P': 'dsi_display_dev_probe boundary and dsi_display_init return',
        '295I': 'dsi_display_init: _dev_init and component_add returns',
        '295D': '_dsi_display_dev_init: parse_dt and resource-init returns',
        '295T': 'parse_dt controller/PHY counts and phandle resolution',
        '295R': 'resource init: dsi_ctrl_get, dsi_phy_get, dsi_panel_get, lane-map and clocks',
        '295C': 'clock count and individual devm_clk_get results',
    },
    'hardware_question': 'Which exact dsi_display_dev_probe subcall first produces the preserved -ENODATA before Phase293 can reach the target memory-DMA transaction?',
}
(out / 'BUILD-IDENTITY.json').write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')

files = [
    'compile/Image', 'config/final.config', 'package/Image.gz', 'package/boot.img',
    'package/repack-report.json', 'audit/phase293-final.config',
    'audit/dsi-display-before.c', 'audit/dsi-ctrl-before.c',
    'audit/dsi-ctrl-hw-before.c', 'audit/recorder-before.c',
    'audit/phase295-compile.log', 'audit/295_apply_gki_display_probe_enodata.py',
    'source/dsi_display.c', 'source/dsi_ctrl.c', 'source/dsi_ctrl_hw_cmn.c',
    'source/a52_ack_secure_flight_recorder.c', 'BUILD-IDENTITY.json',
]
with (out / 'SHA256SUMS').open('w') as handle:
    for name in files:
        path = out / name
        handle.write(hashlib.sha256(path.read_bytes()).hexdigest() + '  ./' + name + '\n')
PY
(cd phase295-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path

root = Path('phase295-out')
src = (root / 'source/dsi_display.c').read_text()
img = (root / 'compile/Image').read_bytes()
required = [
    'A52_PHASE295_DISPLAY_PROBE_ENODATA_V1',
    'P276 295P s=0 i=%d be=%d pn=%d fr=%d',
    'P276 295P s=1 rc=%d',
    'P276 295I s=1 rc=%d',
    'P276 295D s=1 rc=%d cc=%d',
    'P276 295D s=2 rc=%d',
    'P276 295T s=0 cc=%d pc=%u',
    'P276 295R s=0 i=%d e=%d',
    'P276 295R s=1 i=%d e=%d',
    'P276 295R s=2 e=%d',
    'P276 295R s=3 rc=%d',
    'P276 295R s=4 rc=%d',
    'P276 295R s=9 rc=%d i=%d',
    'P276 295C s=0 n=%d',
    'P276 295C s=1 i=%d e=%d n=%.24s',
    'P276 295C s=9 rc=%d',
]
for marker in required:
    if marker not in src:
        raise SystemExit('Phase295 source marker missing: ' + marker)
for marker in required[1:]:
    if marker.encode() not in img:
        raise SystemExit('Phase295 runtime marker missing from Image: ' + marker)

before = (root / 'audit/dsi-display-before.c').read_text()
behavior = [
    'DSI_W32(', 'writel(', 'writel_relaxed(', 'clk_set_rate(',
    'wait_for_completion', 'msleep(', 'usleep_range(', 'udelay(',
    'gpio_set_value(', 'dsi_ctrl_cmd_transfer(', 'dsi_panel_tx_cmd_set(',
]
for token in behavior:
    if before.count(token) != src.count(token):
        raise SystemExit(f'Phase295 functional token changed: {token}: {before.count(token)} -> {src.count(token)}')
print('Phase295 compiled passive ENODATA frontier audit: PASS')
PY

python3 scripts/295_apply_gki_display_probe_enodata.py --root "$ROOT" --check-only

trap - EXIT
echo 'Phase295 passive display-probe ENODATA build/repack: PASS'
