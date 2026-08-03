#!/usr/bin/env python3
import base64, hashlib, zlib
from pathlib import Path
S=Path('scripts')
PAYLOADS={
 '218_apply_keymint_qsee_trace.py':('218_apply_keymint_qsee_trace.py.z64','24ee36d67b6dcb4f36923b913b96a496e3745d956c9ea8a3be97ce9f4839a33d','f07ae99840472fc73f02731953ae975bea50709254d10059ae3d75c2d642bcbc'),
 '218_phase217_wrapper.py':('218_phase217_wrapper.py.z64','9ad51c8526112ed7742a6f5051536de7738aad1391302901a276b827a84f0517','a3df6626fa9aa9c0a541808985b4b660951a5159d3bbe1391bd69696a0aef3e3'),
}
for out,(src,esha,rsha) in PAYLOADS.items():
 enc=(S/src).read_text(encoding='ascii').strip()
 got=hashlib.sha256(enc.encode('ascii')).hexdigest()
 if got!=esha: raise SystemExit(f'{src}: encoded sha256 mismatch: {got}')
 raw=zlib.decompress(base64.b64decode(enc,validate=True))
 got=hashlib.sha256(raw).hexdigest()
 if got!=rsha: raise SystemExit(f'{out}: raw sha256 mismatch: {got}')
 p=S/out; p.write_bytes(raw); p.chmod(0o755)
 print(f'materialized {p} sha256={got} bytes={len(raw)}')
