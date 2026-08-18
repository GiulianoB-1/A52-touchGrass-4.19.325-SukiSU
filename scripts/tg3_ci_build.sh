#!/usr/bin/env bash
set -Eeuo pipefail

fail_report() {
  set +e
  rm -rf tg3-failure
  mkdir -p tg3-failure
  cp -a tg1-failure tg3-failure/ 2>/dev/null || true
  cp tg1-compile.log tg3-failure/ 2>/dev/null || true
  cp scripts/tg2_prepare_generated_tools.py tg3-failure/ 2>/dev/null || true
  cp scripts/tg3_prepare_generated_tools.py tg3-failure/ 2>/dev/null || true
  cp scripts/tg2_finalize_boot.py tg3-failure/ 2>/dev/null || true
  cp scripts/tg1_apply_critical_flight_recorder.py tg3-failure/ 2>/dev/null || true
  cp scripts/tg1_decode_critical_bank.py tg3-failure/ 2>/dev/null || true
  cp gki/common/drivers/watchdog/qcom-wdt.c tg3-failure/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

V3_IMAGE_MARKER='A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V3_WDT_OFF_TYPED_SEAL_210S'
WDT_MARKER='A52_TOUCHGRASS_V3_DIAGNOSTIC_QCOM_WDT_OFF'
WDT_LOG='A52 TouchGrass v3: QCOM watchdog disabled for late recorder'

python3 scripts/tg1_payload.py
python3 scripts/tg2_prepare_generated_tools.py
python3 scripts/tg3_prepare_generated_tools.py
python3 -m py_compile \
  scripts/tg1_apply_critical_flight_recorder.py \
  scripts/tg1_check_critical_flight_recorder.py \
  scripts/tg1_decode_critical_bank.py \
  scripts/tg2_prepare_generated_tools.py \
  scripts/tg3_prepare_generated_tools.py \
  scripts/tg2_finalize_boot.py

grep -Fq "$V3_IMAGE_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "$WDT_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'A52_TGCR_EVT_SEAL' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'A52_TGCR_REASON_DEADLINE_210S' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'msecs_to_jiffies(210000)' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "0x0002: 'SEAL'" scripts/tg1_decode_critical_bank.py
grep -Fq "3: 'DEADLINE_210S'" scripts/tg1_decode_critical_bank.py

bash scripts/tg1_ci_build.sh

# The v1 wrapper must leave the v2/v3 generated-tool overlays intact.
grep -Fq "$V3_IMAGE_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "$WDT_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'msecs_to_jiffies(210000)' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "$WDT_MARKER" gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq 'qcom_wdt_stop(&wdt->wdd)' gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq 'do not register/re-arm' gki/common/drivers/watchdog/qcom-wdt.c

rm -rf tg3-out
cp -a tg1-out tg3-out
mkdir -p tg3-out/tools tg3-out/source

python3 scripts/tg2_finalize_boot.py \
  --source tg1-out/package/boot.img \
  --output tg3-out/package/boot.img \
  --report tg3-out/package/pmsg-release-report.json

cp scripts/tg1_decode_critical_bank.py tg3-out/tools/tg3_decode_critical_bank.py
cp gki/common/drivers/watchdog/qcom-wdt.c tg3-out/source/qcom-wdt.c

python3 - <<'PY'
import json
from pathlib import Path
p = Path('tg3-out/BUILD-IDENTITY.json')
d = json.loads(p.read_text())
d.update({
    'name': 'TOUCHGRASS-CRITICAL-FLIGHT-RECORDER-V3',
    'revision': 'v3',
    'hardware_validated': False,
    'v2_hardware_result': 'did-not-reach-late-android-window',
    'v2_last_crc_valid_r48_ms': 106140.059,
    'pmsg_runtime_size': 0,
    'critical_bank_phys': '0xB1BC0000',
    'critical_bank_bytes': 0x40000,
    'critical_bank_capacity': 1636,
    'critical_bank_is_circular': True,
    'fallback_seal_ms': 210000,
    'fallback_seal_reason': 'DEADLINE_210S',
    'fallback_seal_event': 'SEAL',
    'qcom_watchdog_handoff': 'diagnostic-stop-and-no-register',
    'recovery_exporter_compatible': True,
    'compiled_v3_marker': 'A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V3_WDT_OFF_TYPED_SEAL_210S',
    'functional_change': (
        'diagnostic infrastructure only: keep ramoops pmsg disabled for exclusive '
        'TGCR ownership; restore the previously proven Phase261 qcom watchdog stop '
        'so the boot can reach the late composer window; type fallback seals as SEAL; '
        'move recorder fallback seal to 210 seconds'
    ),
})
p.write_text(json.dumps(d, indent=2, sort_keys=True) + '\n')
PY

IMAGE=tg3-out/compile/Image
test -s "$IMAGE"
grep -aFq "$V3_IMAGE_MARKER" "$IMAGE" || {
  echo "TouchGrass v3 audit: compiled v3 marker missing from Image" >&2
  exit 1
}
grep -aFq "$WDT_LOG" "$IMAGE" || {
  echo "TouchGrass v3 audit: watchdog-stop marker missing from Image" >&2
  exit 1
}
grep -aFq 'F261 c4' "$IMAGE" || {
  echo "TouchGrass v3 audit: cumulative Phase261 trace missing from Image" >&2
  exit 1
}

grep -Fxq 'CONFIG_CHR_DEV_SG=y' tg3-out/config/final.config
grep -Fxq 'CONFIG_QCOM_WDT=y' tg3-out/config/final.config

python3 - <<'PY'
import hashlib
from pathlib import Path
root = Path('tg3-out')
sums = root / 'SHA256SUMS'
if sums.exists():
    sums.unlink()
rows = []
for p in sorted(root.rglob('*')):
    if p.is_file() and p.name != 'SHA256SUMS':
        rows.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root)}")
sums.write_text('\n'.join(rows) + '\n')
PY
(cd tg3-out && sha256sum -c SHA256SUMS)

trap - EXIT
echo 'TouchGrass critical flight recorder v3 build/repack: PASS'
