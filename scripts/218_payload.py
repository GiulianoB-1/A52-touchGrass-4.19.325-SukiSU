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
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != '00b79b65878bbd24edb1136d4fe8f5ccf39c3cc065da34b7d1f41b16e4b77076':
    raise SystemExit('Phase 226 driver encoded sha256 mismatch')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != '80060d9dc39472aaef2019bcc435ea0db85b94102616a71a56cb750ce357b0e4':
    raise SystemExit('Phase 226 driver raw sha256 mismatch')
exec(compile(raw, 'scripts/226_payload_driver.py', 'exec'), {'__name__': '__main__'})
