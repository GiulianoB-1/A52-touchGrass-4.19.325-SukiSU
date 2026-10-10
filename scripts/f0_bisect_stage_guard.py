#!/usr/bin/env python3
import base64,zlib
from pathlib import Path
p=Path(__file__).with_name(Path(__file__).name+'.z64')
exec(compile(zlib.decompress(base64.b64decode(p.read_text().strip())).decode(),str(p.with_suffix('.expanded.py')),'exec'),globals())
