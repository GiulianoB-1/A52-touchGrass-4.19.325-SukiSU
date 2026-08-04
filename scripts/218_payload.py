#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

S = Path('scripts')
parts = tuple(sorted((S / '225_driver_chunks').glob('*.txt')))
if len(parts) != 2:
    raise SystemExit(f'expected 2 Phase 225 driver chunks, got {len(parts)}')
encoded = ''.join(p.read_text(encoding='ascii').strip() for p in parts)
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != '6c33b4fc209922e7fd0058f076dfd5a61385dd20c82d7155369219617fbf87c3':
    raise SystemExit('Phase 225 driver encoded sha256 mismatch')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != '36f27ad53528c6dc46b021ae984da5bd69e612781d58ef2e9282311d5b82c28e':
    raise SystemExit('Phase 225 driver raw sha256 mismatch')
exec(compile(raw, 'scripts/225_payload_driver.py', 'exec'), {'__name__': '__main__'})
