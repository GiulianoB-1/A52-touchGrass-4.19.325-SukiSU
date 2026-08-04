#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

S = Path('scripts')
parts = tuple(sorted((S / '226_driver_chunks').glob('*.txt')))
if len(parts) != 1:
    raise SystemExit(f'expected 1 Phase 226 driver chunk, got {len(parts)}')
encoded = ''.join(p.read_text(encoding='ascii').strip() for p in parts)
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != 'c391288c91df0f34ba038565b273c3bb8c889676772be41f169558c34d8fef45':
    raise SystemExit('Phase 226 driver encoded sha256 mismatch')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != 'a498d38f9a54c345f00a196b613a8201b51c1bd7bb63c6d3ea3006fe138b005d':
    raise SystemExit('Phase 226 driver raw sha256 mismatch')
exec(compile(raw, 'scripts/226_payload_driver.py', 'exec'), {'__name__': '__main__'})
