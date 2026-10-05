#!/usr/bin/env python3
from __future__ import annotations

import base64
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")
exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
