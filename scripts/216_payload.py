#!/usr/bin/env python3
import base64, zlib
from pathlib import Path
for src, dst in [
    ('scripts/216_apply_qsee_deep_trace.py.z64', 'scripts/216_apply_qsee_deep_trace.py'),
    ('scripts/216_ci.sh.z64', 'scripts/216_ci.sh'),
]:
    out = Path(dst)
    out.write_bytes(zlib.decompress(base64.b64decode(Path(src).read_text())))
    out.chmod(0o755)
    print(f'materialized {out}')
