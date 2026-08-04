#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

S = Path('scripts')
parts = tuple(sorted((S / '224_driver_chunks').glob('*.txt')))
if len(parts) != 4:
    raise SystemExit(f'expected 4 Phase 224 driver chunks, got {len(parts)}')
encoded = ''.join(p.read_text(encoding='ascii').strip() for p in parts)
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != 'bc86a1476940d5bc5638610161c37d682f5a5b983abcec0da9b9f0564ce0fbd9':
    raise SystemExit('Phase 224 driver encoded sha256 mismatch')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != '3ad7114f17ddf72d3799b1cfd3f261318f9d2b27d686d9d146925fc53e476763':
    raise SystemExit('Phase 224 driver raw sha256 mismatch')
exec(compile(raw, 'scripts/224_payload_driver.py', 'exec'), {'__name__': '__main__'})
