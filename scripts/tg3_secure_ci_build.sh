#!/usr/bin/env bash
set -Eeuo pipefail

fail_report() {
  set +e
  rm -rf tg3s-failure
  mkdir -p tg3s-failure
  cp -a tg1-failure tg3s-failure/ 2>/dev/null || true
  cp tg1-compile.log tg3s-failure/ 2>/dev/null || true
  cp scripts/tg2_prepare_generated_tools.py tg3s-failure/ 2>/dev/null || true
  cp scripts/tg3_prepare_generated_tools.py tg3s-failure/ 2>/dev/null || true
  cp scripts/tg3_secure_wdt_prepare.py tg3s-failure/ 2>/dev/null || true
  cp scripts/tg2_finalize_boot.py tg3s-failure/ 2>/dev/null || true
  cp scripts/tg1_apply_critical_flight_recorder.py tg3s-failure/ 2>/dev/null || true
  cp scripts/tg1_decode_critical_bank.py tg3s-failure/ 2>/dev/null || true
  cp gki/common/drivers/watchdog/qcom-wdt.c tg3s-failure/ 2>/dev/null || true
  cp gki/common/drivers/firmware/qcom_scm.c tg3s-failure/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

V3_IMAGE_MARKER='A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V3_WDT_OFF_TYPED_SEAL_210S'
WDT_MARKER='A52_TOUCHGRASS_V3_DIAGNOSTIC_QCOM_WDT_OFF'
SECURE_WDT_MARKER='A52_TOUCHGRASS_V3_SECURE_AND_LOCAL_WDOG_OFF'
SCM_MARKER='A52_TOUCHGRASS_V3_SECURE_WDOG_SCM_OFF'
WDT_LOG='A52 TouchGrass v3: secure watchdog attempted; local watchdog disabled'
WDT_RECORD='WDT secure rc=%d local before=%u after=%u'

python3 scripts/tg1_payload.py
python3 scripts/tg2_prepare_generated_tools.py
python3 scripts/tg3_prepare_generated_tools.py
python3 scripts/tg3_secure_wdt_prepare.py
python3 -m py_compile \
  scripts/tg1_apply_critical_flight_recorder.py \
  scripts/tg1_check_critical_flight_recorder.py \
  scripts/tg1_decode_critical_bank.py \
  scripts/tg2_prepare_generated_tools.py \
  scripts/tg3_prepare_generated_tools.py \
  scripts/tg3_secure_wdt_prepare.py \
  scripts/tg2_finalize_boot.py

grep -Fq "$V3_IMAGE_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "$WDT_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "$SECURE_WDT_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "$SCM_MARKER" scripts/tg1_apply_critical_flight_recorder.py
grep -Fq '.cmd = 0x07' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'A52_TGCR_EVT_SEAL' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'A52_TGCR_REASON_DEADLINE_210S' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq 'msecs_to_jiffies(210000)' scripts/tg1_apply_critical_flight_recorder.py
grep -Fq "0x0002: 'SEAL'" scripts/tg1_decode_critical_bank.py
grep -Fq "3: 'DEADLINE_210S'" scripts/tg1_decode_critical_bank.py

bash scripts/tg1_ci_build.sh

# Audit the exact reconstructed sources that produced the Image.
grep -Fq "$WDT_MARKER" gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq "$SECURE_WDT_MARKER" gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq 'a52_qcom_scm_disable_secure_wdog()' gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq "$WDT_RECORD" gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq 'qcom_wdt_stop(&wdt->wdd)' gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq 'do not register/re-arm' gki/common/drivers/watchdog/qcom-wdt.c
grep -Fq "$SCM_MARKER" gki/common/drivers/firmware/qcom_scm.c
grep -Fq 'int a52_qcom_scm_disable_secure_wdog(void)' gki/common/drivers/firmware/qcom_scm.c
grep -Fq '.svc = QCOM_SCM_SVC_BOOT' gki/common/drivers/firmware/qcom_scm.c
grep -Fq '.cmd = 0x07' gki/common/drivers/firmware/qcom_scm.c
grep -Fq '.args[0] = 1' gki/common/drivers/firmware/qcom_scm.c

rm -rf tg3s-out
cp -a tg1-out tg3s-out
mkdir -p tg3s-out/tools tg3s-out/source tg3s-out/audit

python3 scripts/tg2_finalize_boot.py \
  --source tg1-out/package/boot.img \
  --output tg3s-out/package/boot.img \
  --report tg3s-out/package/pmsg-release-report.json

cp scripts/tg1_decode_critical_bank.py tg3s-out/tools/tg3_secure_decode_critical_bank.py
cp scripts/tg1_apply_critical_flight_recorder.py tg3s-out/audit/tg1_apply_critical_flight_recorder.py
cp scripts/tg3_secure_wdt_prepare.py tg3s-out/audit/tg3_secure_wdt_prepare.py
cp gki/common/drivers/watchdog/qcom-wdt.c tg3s-out/source/qcom-wdt.c
cp gki/common/drivers/firmware/qcom_scm.c tg3s-out/source/qcom_scm.c

python3 - <<'PY'
import json
from pathlib import Path
p = Path('tg3s-out/BUILD-IDENTITY.json')
d = json.loads(p.read_text())
d.update({
    'name': 'TOUCHGRASS-CRITICAL-FLIGHT-RECORDER-V3-SECURE-WDT',
    'revision': 'v3-secure-wdt-r1',
    'hardware_validated': False,
    'v3_run9_hardware_result': 'reset-before-late-android-window',
    'v3_run9_last_crc_valid_r48_ms': 97980.737,
    'pmsg_runtime_size': 0,
    'critical_bank_phys': '0xB1BC0000',
    'critical_bank_bytes': 0x40000,
    'critical_bank_capacity': 1636,
    'critical_bank_is_circular': True,
    'fallback_seal_ms': 210000,
    'fallback_seal_reason': 'DEADLINE_210S',
    'fallback_seal_event': 'SEAL',
    'qcom_watchdog_handoff': 'diagnostic-secure-scm-then-local-stop-and-no-register',
    'qcom_secure_watchdog_scm': {
        'service': 'QCOM_SCM_SVC_BOOT',
        'command': '0x07',
        'arg0': 1,
        'result_record': 'WDT secure rc=%d local before=%u after=%u',
    },
    'recovery_exporter_compatible': True,
    'compiled_v3_marker': 'A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V3_WDT_OFF_TYPED_SEAL_210S',
    'compiled_secure_wdt_marker': 'A52_TOUCHGRASS_V3_SECURE_AND_LOCAL_WDOG_OFF',
    'functional_change': (
        'diagnostic infrastructure only: issue Qualcomm SCM boot-service command 0x07 '
        'with arg0=1 to request secure-watchdog disable, record the SCM return code, '
        'then stop the local qcom watchdog and return before watchdog registration; '
        'retain the v3 210-second typed SEAL fallback and pmsg release'
    ),
})
p.write_text(json.dumps(d, indent=2, sort_keys=True) + '\n')
PY

IMAGE=tg3s-out/compile/Image
test -s "$IMAGE"
grep -aFq "$V3_IMAGE_MARKER" "$IMAGE" || {
  echo "TouchGrass v3-secure audit: v3 recorder marker missing from Image" >&2
  exit 1
}
grep -aFq "$WDT_LOG" "$IMAGE" || {
  echo "TouchGrass v3-secure audit: secure/local watchdog log missing from Image" >&2
  exit 1
}
grep -aFq "$WDT_RECORD" "$IMAGE" || {
  echo "TouchGrass v3-secure audit: secure watchdog recorder format missing from Image" >&2
  exit 1
}
grep -aFq 'F261 c4' "$IMAGE" || {
  echo "TouchGrass v3-secure audit: cumulative Phase261 trace missing from Image" >&2
  exit 1
}

grep -Fxq 'CONFIG_CHR_DEV_SG=y' tg3s-out/config/final.config
grep -Fxq 'CONFIG_QCOM_WDT=y' tg3s-out/config/final.config
grep -Fxq 'CONFIG_QCOM_SCM=y' tg3s-out/config/final.config

python3 - <<'PY'
import hashlib
from pathlib import Path
root = Path('tg3s-out')
sums = root / 'SHA256SUMS'
if sums.exists():
    sums.unlink()
rows = []
for p in sorted(root.rglob('*')):
    if p.is_file() and p.name != 'SHA256SUMS':
        rows.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root)}")
sums.write_text('\n'.join(rows) + '\n')
PY
(cd tg3s-out && sha256sum -c SHA256SUMS)

trap - EXIT
echo 'TouchGrass critical flight recorder v3 secure-watchdog build/repack: PASS'
