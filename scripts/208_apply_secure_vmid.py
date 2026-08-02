#!/usr/bin/env python3
from pathlib import Path
import hashlib

EXPECTED_SHA256 = '9f018df7ba69d31bb4f6141c94128d02fba3e5343f51028ade0f427c04d053b1'
PAYLOAD_DIR = Path(__file__).with_name('208_secure_vmid_payload')
PARTS = tuple(PAYLOAD_DIR / f'{index:02d}.pyfrag' for index in range(6))

missing = [str(path) for path in PARTS if not path.is_file()]
if missing:
    raise SystemExit(f'Phase208 patcher fragments missing: {missing}')

source = b''.join(path.read_bytes() for path in PARTS)
actual = hashlib.sha256(source).hexdigest()
if actual != EXPECTED_SHA256:
    raise SystemExit(
        f'Phase208 patcher checksum mismatch: {actual} != {EXPECTED_SHA256}'
    )

namespace = {
    '__name__': '__main__',
    '__file__': str(Path(__file__)),
}
exec(compile(source, str(Path(__file__)), 'exec'), namespace)
