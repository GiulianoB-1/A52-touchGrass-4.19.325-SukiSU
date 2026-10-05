#!/usr/bin/env python3
from __future__ import annotations
import base64
import zlib
from pathlib import Path

p = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
if not p.is_file():
    raise SystemExit(f"Phase441 payload missing: {p}")
source = zlib.decompress(base64.b85decode(p.read_text().strip()))
exec(compile(source, str(p), "exec"), globals(), globals())
