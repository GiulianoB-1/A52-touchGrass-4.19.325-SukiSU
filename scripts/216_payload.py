#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

SCRIPTS = Path('scripts')


def materialize_chunks(prefix: str, output: str, expected_z64_sha256: str, expected_raw_sha256: str) -> None:
    parts = sorted(SCRIPTS.glob(prefix + '.part*'))
    if not parts:
        raise SystemExit(f'no payload chunks found for {prefix}')
    encoded = ''.join(part.read_text(encoding='ascii').strip() for part in parts)
    encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
    if encoded_sha != expected_z64_sha256:
        raise SystemExit(f'{prefix}: encoded sha256 {encoded_sha} != {expected_z64_sha256}')
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != expected_raw_sha256:
        raise SystemExit(f'{prefix}: raw sha256 {raw_sha} != {expected_raw_sha256}')
    out = SCRIPTS / output
    out.write_bytes(raw)
    out.chmod(0o755)
    print(f'materialized {out} sha256={raw_sha} bytes={len(raw)} chunks={len(parts)}')


materialize_chunks(
    '216_apply_qsee_deep_trace.py.z64',
    '216_apply_qsee_deep_trace.py',
    '9021298d16284e2a29e37f7381e8233b668bc4fa41a8eceae0ccdde4268d11cc',
    'a4850304d99f9b0f8d091553f4bd7e34a5668d11c1272a41a9953e7c90c9c343',
)

ci_encoded = (SCRIPTS / '216_ci.sh.z64').read_text(encoding='ascii').strip()
ci_encoded_sha = hashlib.sha256(ci_encoded.encode('ascii')).hexdigest()
if ci_encoded_sha != '280f74e00a6f4ea320b543e7db2e2d51a4a3ba42af41d009f572faebe8d691f6':
    raise SystemExit(f'216_ci.sh.z64: encoded sha256 mismatch: {ci_encoded_sha}')
ci_raw = zlib.decompress(base64.b64decode(ci_encoded, validate=True))
ci_raw_sha = hashlib.sha256(ci_raw).hexdigest()
if ci_raw_sha != 'a9b10bb1c6903f4fddcc13074c8fd7c3e44387d18709bc5135c036c8fc062e1e':
    raise SystemExit(f'216_ci.sh: raw sha256 mismatch: {ci_raw_sha}')

ci_text = ci_raw.decode('utf-8')
old_gate = "grep -Fq 'A52_R210_RS_PARITY 48U' \"$REC\""
new_gate = "grep -Fq 'A52_R179_RS_ROOTS 48U' \"$REC\""
if ci_text.count(old_gate) != 1:
    raise SystemExit('expected exactly one stale Phase 216 RS48 source gate')
ci_text = ci_text.replace(old_gate, new_gate)
ci_raw = ci_text.encode('utf-8')
patched_ci_sha = hashlib.sha256(ci_raw).hexdigest()

ci_out = SCRIPTS / '216_ci.sh'
ci_out.write_bytes(ci_raw)
ci_out.chmod(0o755)
print(
    f'materialized {ci_out} original_sha256={ci_raw_sha} '
    f'patched_sha256={patched_ci_sha} bytes={len(ci_raw)}'
)
