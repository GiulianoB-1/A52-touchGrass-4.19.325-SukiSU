#!/usr/bin/env python3
import base64,zlib
from pathlib import Path
p=Path(__file__).with_suffix(Path(__file__).suffix+".z64")
exec(compile(zlib.decompress(base64.b64decode(p.read_text().strip())),str(__file__),"exec"))
