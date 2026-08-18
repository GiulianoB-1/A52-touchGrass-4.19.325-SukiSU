#!/usr/bin/env bash
set -Eeuo pipefail

fail_report() {
  set +e
  rm -rf tg2-failure
  mkdir -p tg2-failure
  cp -a tg1-failure tg2-failure/ 2>/dev/null || true
  cp tg1-compile.log tg2-failure/ 2>/dev/null || true
  cp scripts/tg2_prepare_generated_tools.py tg2-failure/ 2>/dev/null || true
  cp scripts/tg2_finalize_boot.py tg2-failure/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

python3 scripts/tg1_payload.py
python3 scripts/tg2_prepare_generated_tools.py
python3 -m py_compile \
  scripts/tg1_apply_critical_flight_recorder.py \
  scripts/tg1_check_critical_flight_recorder.py \
  scripts/tg1_decode_critical_bank.py \
  scripts/tg2_prepare_generated_tools.py \
  scripts/tg2_finalize_boot.py

bash scripts/tg1_ci_build.sh

rm -rf tg2-out
cp -a tg1-out tg2-out
mkdir -p tg2-out/tools

python3 scripts/tg2_finalize_boot.py \
  --source tg1-out/package/boot.img \
  --output tg2-out/package/boot.img \
  --report tg2-out/package/pmsg-release-report.json

cp scripts/tg1_decode_critical_bank.py tg2-out/tools/tg2_decode_critical_bank.py

python3 - <<'PY'
import json
from pathlib import Path
p = Path('tg2-out/BUILD-IDENTITY.json')
d = json.loads(p.read_text())
d.update({
    'name': 'TOUCHGRASS-CRITICAL-FLIGHT-RECORDER-V2',
    'revision': 'v2',
    'hardware_validated': False,
    'v1_pmsg_overlap_rejected': True,
    'pmsg_runtime_size': 0,
    'critical_bank_phys': '0xB1BC0000',
    'critical_bank_bytes': 0x40000,
    'critical_bank_capacity': 1636,
    'critical_bank_is_circular': True,
    'fallback_seal_ms': 90000,
    'fallback_seal_reason': 'DEADLINE_90S',
    'recovery_exporter_compatible': True,
    'functional_change': (
        'diagnostic infrastructure only: disable ramoops pmsg allocation for '
        'exclusive TGCR ownership of the fourth quarter; add recorder-only '
        '90-second fallback seal'
    ),
})
p.write_text(json.dumps(d, indent=2, sort_keys=True) + '\n')
PY

IMAGE=tg2-out/compile/Image
test -s "$IMAGE"
grep -aFq 'A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V1' "$IMAGE"
grep -aFq 'TouchGrass critical bank armed phys=%llx bytes=%lu slots=%u gen=%u' "$IMAGE"
grep -aFq 'SMMU P279 I p=%u' "$IMAGE"

python3 - <<'PY'
import hashlib
from pathlib import Path
root = Path('tg2-out')
sums = root / 'SHA256SUMS'
if sums.exists():
    sums.unlink()
rows = []
for p in sorted(root.rglob('*')):
    if p.is_file() and p.name != 'SHA256SUMS':
        rows.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root)}")
sums.write_text('\n'.join(rows) + '\n')
PY
(cd tg2-out && sha256sum -c SHA256SUMS)

trap - EXIT
echo 'TouchGrass critical flight recorder v2 build/repack: PASS'
