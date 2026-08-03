#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

SCRIPTS = Path('scripts')
PAYLOADS = {
    '217_apply_graphics_service_trace.py': (
        '217_apply_graphics_service_trace.py.z64',
        '2d107af779023f39fde27c0ca31f16ae85ae99c090512da6593d2a898a542247',
        '9ce32caab14993f2513d0788e4fd9513157a76e4f7c3edb909cde9c504db0377',
    ),
    '217_ci.sh': (
        '217_ci.sh.z64',
        '9bdca782316bdd2a5244da26209ec3836754709ba1fb394c5dc83c711704cdc6',
        'c20ba2ab8642d576e437ef1128ad030614b8332bd461cdfe1e7f3edede32cfb9',
    ),
}

for output, (source, expected_encoded_sha, expected_raw_sha) in PAYLOADS.items():
    encoded = (SCRIPTS / source).read_text(encoding='ascii').strip()
    encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
    if encoded_sha != expected_encoded_sha:
        raise SystemExit(f'{source}: encoded sha256 mismatch: {encoded_sha}')
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != expected_raw_sha:
        raise SystemExit(f'{output}: raw sha256 mismatch: {raw_sha}')
    target = SCRIPTS / output
    target.write_bytes(raw)
    target.chmod(0o755)
    print(f'materialized {target} sha256={raw_sha} bytes={len(raw)}')
