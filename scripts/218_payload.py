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
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != '486c217c936662e54fb9038c023e8cc4661c54fc3fd0e26d21cfe8d017f03ae7':
    raise SystemExit('Phase 226 driver encoded sha256 mismatch')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != 'e4b2ec578d8f1ce8a2d140abc374a0f21abb895f8e57d5d8643c9930a5684d6b':
    raise SystemExit('Phase 226 driver raw sha256 mismatch')
exec(compile(raw, 'scripts/226_payload_driver.py', 'exec'), {'__name__': '__main__'})
