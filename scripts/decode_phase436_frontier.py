#!/usr/bin/env python3
from pathlib import Path
import base64,zlib
p=Path(__file__).with_suffix(Path(__file__).suffix+'.z64')
s=zlib.decompress(base64.b64decode(p.read_bytes())).decode()
exec(compile(s,str(p)[:-4],"exec"),globals(),globals())
