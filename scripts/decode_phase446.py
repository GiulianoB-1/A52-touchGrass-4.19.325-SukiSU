#!/usr/bin/env python3
from __future__ import annotations
import base64, zlib
from pathlib import Path

payload = zlib.decompress(base64.b64decode(Path(__file__).with_name(Path(__file__).name + ".z64").read_text().strip()))
exec(compile(payload.decode("utf-8"), str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
