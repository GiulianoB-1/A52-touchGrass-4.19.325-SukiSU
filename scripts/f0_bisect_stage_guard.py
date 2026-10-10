#!/usr/bin/env python3
"""TG P6 twin: same V2 guard transform as GKI; no GKI Phase446 marker."""
import base64,zlib
from pathlib import Path
from f0_bisect_v2_transform import transform
p=Path(__file__).with_name(Path(__file__).name+'.z64')
source=zlib.decompress(base64.b64decode(p.read_text().strip())).decode()
source=transform(source)
exec(compile(source,str(p.with_suffix('.expanded.py')),'exec'),globals())
