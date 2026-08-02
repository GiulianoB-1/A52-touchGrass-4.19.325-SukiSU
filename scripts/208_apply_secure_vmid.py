#!/usr/bin/env python3
from pathlib import Path
import base64
import hashlib

EXPECTED_SHA256 = '9f018df7ba69d31bb4f6141c94128d02fba3e5343f51028ade0f427c04d053b1'
PAYLOAD_DIR = Path(__file__).with_name('208_secure_vmid_payload_b64')
PARTS = tuple(PAYLOAD_DIR / f'{index:02d}.b64' for index in range(4))

missing = [str(path) for path in PARTS if not path.is_file()]
if missing:
    raise SystemExit(f'Phase208 patcher blocks missing: {missing}')

try:
    source = b''.join(
        base64.b64decode(path.read_text().strip(), validate=True)
        for path in PARTS
    )
except Exception as exc:
    raise SystemExit(f'Phase208 patcher Base64 decode failed: {exc}') from exc

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
