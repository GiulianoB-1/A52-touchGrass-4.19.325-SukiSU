#!/usr/bin/env python3
import hashlib
from pathlib import Path

parts = [Path(__file__).with_name("430_parts") / f"apply_{i:02d}.part" for i in range(6)]
code = b"".join(p.read_bytes() for p in parts)
expected = "f7c2b3b61aa91f035035fa68391ec99d76963c2d32a8ff43bc92a23167411916"
actual = hashlib.sha256(code).hexdigest()
if actual != expected:
    raise SystemExit(f"Phase430 apply parts SHA256 mismatch: {actual}")
exec(compile(code, str(Path(__file__).with_name("430_apply_joined.py")), "exec"),
     {"__name__": "__main__", "__file__": __file__})
