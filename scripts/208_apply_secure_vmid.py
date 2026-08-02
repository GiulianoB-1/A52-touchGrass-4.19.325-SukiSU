#!/usr/bin/env python3
from pathlib import Path
import base64
import hashlib

BASE_SHA256 = '9f018df7ba69d31bb4f6141c94128d02fba3e5343f51028ade0f427c04d053b1'
FINAL_SHA256 = '967f1cb16351401084662271d2fc33b586cc8063be07f7d344eec54c894e3b36'
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

base_actual = hashlib.sha256(source).hexdigest()
if base_actual != BASE_SHA256:
    raise SystemExit(
        f'Phase208 base patcher checksum mismatch: {base_actual} != {BASE_SHA256}'
    )

old_include = b'#include <soc/qcom/secure_buffer.h>'
new_include = b'#include "../../../../a52-compat/include/soc/qcom/secure_buffer.h"'
if source.count(old_include) != 1:
    raise SystemExit(
        f'Phase208 secure-buffer include match count: {source.count(old_include)}'
    )
source = source.replace(old_include, new_include, 1)

final_actual = hashlib.sha256(source).hexdigest()
if final_actual != FINAL_SHA256:
    raise SystemExit(
        f'Phase208 final patcher checksum mismatch: {final_actual} != {FINAL_SHA256}'
    )

namespace = {
    '__name__': '__main__',
    '__file__': str(Path(__file__)),
}
exec(compile(source, str(Path(__file__)), 'exec'), namespace)
