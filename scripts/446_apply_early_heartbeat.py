#!/usr/bin/env python3
from __future__ import annotations
import base64, zlib
from pathlib import Path
PAYLOAD = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
src = zlib.decompress(base64.b64decode(PAYLOAD.read_text().strip()))
code = compile(src, str(PAYLOAD), "exec")
exec(code, {"__name__": "__main__", "__file__": __file__})
