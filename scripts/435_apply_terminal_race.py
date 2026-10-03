#!/usr/bin/env python3
from pathlib import Path
import base64,zlib

p=Path(__file__).with_suffix(Path(__file__).suffix+'.z64')
s=zlib.decompress(base64.b64decode(p.read_bytes())).decode()

# Phase435 compile repair: keep the verified payload intact and transform only
# the do_sys_openat2 K3 insertion so GNU89 sees the block-local declaration
# before the first statement.
old = '''        anchor = "\\t\\tstruct file *f = do_filp_open(dfd, tmp, &op);\\n"
        fn = one(fn, anchor,
            "\\t\\tif (a52_p435_target)\\n\\t\\t\\ta52_p435_mark(P435_K3_BEFORE_FILP_OPEN, (u64)(s64)fd, dfd, NULL);\\n" + anchor,
            "before do_filp_open")
'''
new = '''        anchor = "\\t\\tstruct file *f = do_filp_open(dfd, tmp, &op);\\n"
        fn = one(fn, anchor,
            "\\t\\tstruct file *f;\\n"
            "\\t\\tif (a52_p435_target)\\n"
            "\\t\\t\\ta52_p435_mark(P435_K3_BEFORE_FILP_OPEN, (u64)(s64)fd, dfd, NULL);\\n"
            "\\t\\tf = do_filp_open(dfd, tmp, &op);\\n",
            "before do_filp_open")
'''
if old not in s:
    raise RuntimeError("Phase435 loader repair anchor missing")
s=s.replace(old,new,1)

exec(compile(s,str(p)[:-4],"exec"),globals(),globals())
