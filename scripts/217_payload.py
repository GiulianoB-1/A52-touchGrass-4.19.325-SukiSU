#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

SCRIPTS = Path('scripts')

PAYLOADS = [
    ('217_apply_graphics_service_trace.py.z64', '217_apply_graphics_service_trace.py', 'ff3a4623af127414fe5211a2405537282065faa87392562990806c7d103a4c3b', '2816a4a34369be2c9be59fd1161882cd7f53ef79db15f8428d012801a80dec4f'),
    ('217_ci.sh.z64', '217_ci.sh', '4de7e2e77d478a218d3b4dc46dd722b987a4acad53e3c06a4f4c84dc65fa7c55', '77995f6864e56b06f1969f6c3c4adec14e4827e70a479a3337aa91e7c3a79769'),
]

for source, output, expected_encoded_sha, expected_raw_sha in PAYLOADS:
    encoded = (SCRIPTS / source).read_text(encoding='ascii').strip()
    encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
    if encoded_sha != expected_encoded_sha:
        raise SystemExit(f'{source}: encoded sha256 mismatch: {encoded_sha}')
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != expected_raw_sha:
        raise SystemExit(f'{output}: raw sha256 mismatch: {raw_sha}')
    out = SCRIPTS / output
    out.write_bytes(raw)
    out.chmod(0o755)
    print(f'materialized {out} sha256={raw_sha} bytes={len(raw)}')
