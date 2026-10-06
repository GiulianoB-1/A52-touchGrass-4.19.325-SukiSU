#!/usr/bin/env python3
from __future__ import annotations
import base64, zlib
from pathlib import Path
payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
source = zlib.decompress(base64.b64decode(payload_path.read_text().strip())).decode("utf-8")
exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
