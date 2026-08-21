#!/usr/bin/env bash
set -Eeuo pipefail

python3 -m py_compile scripts/295_apply_gki_f05a5a_admission_witness.py

# Reuse the already-audited Phase293 reconstruction/build pipeline, but insert
# the Phase295 one-shot witness immediately after Phase293 source staging and
# before olddefconfig/compilation. The Phase293 target admission and all DSI
# behavior remain unchanged.
TMP="$(mktemp -t a52-p295-ci.XXXXXX.sh)"
trap 'rm -f "$TMP"' EXIT
python3 - <<'PY' > "$TMP"
from pathlib import Path
src = Path('scripts/293_ci_build.sh').read_text()
needle = '''python3 scripts/293_apply_gki_dma_done_reference.py --root "$ROOT" --check-only\n\n# Phase293 may touch only the DSI controller and common HW source. SMMU and the\n'''
replacement = '''python3 scripts/293_apply_gki_dma_done_reference.py --root "$ROOT" --check-only\npython3 scripts/295_apply_gki_f05a5a_admission_witness.py --root "$ROOT"\npython3 scripts/295_apply_gki_f05a5a_admission_witness.py --root "$ROOT" --check-only\n\n# Phase293 may touch only the DSI controller and common HW source. SMMU and the\n'''
if src.count(needle) != 1:
    raise SystemExit('Phase295 could not locate Phase293 pre-build insertion point')
print(src.replace(needle, replacement, 1), end='')
PY
chmod +x "$TMP"
bash "$TMP"

# Phase293 CI has already validated the reconstructed Phase280 lineage, fixed
# 96 MiB repack, configuration identity, forbidden-behavior absence, and all
# original GDM runtime markers. Promote that exact output with the additional
# Phase295 passive witness identity/audit.
rm -rf phase295-out
cp -a phase293-out phase295-out
cp scripts/295_apply_gki_f05a5a_admission_witness.py phase295-out/audit/

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path('phase295-out')
idp = r / 'BUILD-IDENTITY.json'
idn = json.loads(idp.read_text())
idn.update({
    'phase': '295',
    'name': 'GKI-F05A5A-ADMISSION-WITNESS',
    'git_sha': os.getenv('GITHUB_SHA'),
    'base': 'exact Phase293 passive GDM reference on clean Phase280 reconstruction',
    'admission_witness': {
        'marker': 'GDM W00',
        'scope': 'first ctrl0 DSI message with tx_len>=3 and payload prefix F0 5A 5A',
        'controller_flags_filter': 'none',
        'mipi_flags_filter': 'none',
        'packet_type_filter': 'none',
        'writes_or_transport_changes': False,
    },
    'hardware_question': (
        'Does GKI ever present F0 5A 5A to dsi_message_setup_tx_mode, and if so '
        'which incoming flags/msg.flags/type/length differ from Golden before the Phase293 S00 gate?'
    ),
})
idp.write_text(json.dumps(idn, indent=2, sort_keys=True) + '\n')

# Replace the inherited Phase293 checksum manifest with a complete Phase295 one.
manifest = r / 'SHA256SUMS'
files = sorted(p for p in r.rglob('*') if p.is_file() and p != manifest)
with manifest.open('w') as f:
    for p in files:
        rel = p.relative_to(r)
        f.write(hashlib.sha256(p.read_bytes()).hexdigest() + '  ./' + str(rel) + '\n')
PY
(cd phase295-out && sha256sum -c SHA256SUMS)

test "$(stat -c '%s' phase295-out/package/boot.img)" -eq 100663296
cmp -s phase295-out/config/final.config phase295-out/audit/phase280-final.config

grep -Fq 'A52_PHASE295_F05A5A_ADMISSION_WITNESS_V1' phase295-out/source/dsi_ctrl.c
grep -Fq 'GDM W00 c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x' phase295-out/source/dsi_ctrl.c
python3 - <<'PY'
from pathlib import Path
img = Path('phase295-out/compile/Image').read_bytes()
for marker in [
    b'GDM W00 c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x',
    b'GDM S00 c=0 in=%x mf=%x t=%x l=%u',
    b'GDM DONE success=0 target=0/8/20/29/3',
    b'P276 280Z q=2',
]:
    if marker not in img:
        raise SystemExit('Phase295 runtime marker missing: ' + marker.decode())
print('Phase295 compiled admission-witness audit: PASS')
PY

# No later experimental lineage is allowed back into this diagnostic.
for marker in \
  'A52_PHASE281_DSI_DMA_CONSUMPTION_TRACE_V1' \
  'A52_PHASE292_DSI_CHAIN_TAPS_V1' \
  'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1'; do
  if grep -Fq "$marker" phase295-out/source/dsi_ctrl.c; then
    echo "Phase295 refuses later behavioral lineage: $marker" >&2
    exit 1
  fi
done

python3 scripts/295_apply_gki_f05a5a_admission_witness.py --root "$PWD/gki/common" --check-only
trap - EXIT
rm -f "$TMP"
echo 'Phase295 passive GKI F05A5A admission-witness build/repack: PASS'
