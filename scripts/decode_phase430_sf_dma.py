#!/usr/bin/env python3
import hashlib
from pathlib import Path

parts = [Path(__file__).with_name("430_parts") / f"decode_{i:02d}.part" for i in range(2)]
code = b"".join(p.read_bytes() for p in parts)
expected = "4bde32c18cc1e2375a60a8c907508935716f5a338b989836acf8ae96ea664905"
actual = hashlib.sha256(code).hexdigest()
if actual != expected:
    raise SystemExit(f"Phase430 decoder parts SHA256 mismatch: {actual}")
exec(compile(code, str(Path(__file__).with_name("decode_phase430_joined.py")), "exec"),
     {"__name__": "__main__", "__file__": __file__})
