#!/usr/bin/env python3
from pathlib import Path
import base64
import re
import zlib

p = Path(__file__).with_suffix(Path(__file__).suffix + '.z64')
s = zlib.decompress(base64.b64decode(p.read_bytes())).decode()

# The verified payload reached the source apply step, but two functions contain
# multiple generic return anchors. For these named frontier exits, choose the
# final occurrence rather than weakening any other source audit.
old_one = '''def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        die(f"{label}: expected exactly one anchor, found {n}")
    return s.replace(old, new, 1)
'''
new_one = '''def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        if n == 2 and label in {
            "change state return",
            "gmu normal return",
            "gmu start return",
            "gmu return",
        }:
            pos = s.rfind(old)
            return s[:pos] + new + s[pos + len(old):]
        die(f"{label}: expected exactly one anchor, found {n}")
    return s.replace(old, new, 1)
'''
if old_one not in s:
    raise RuntimeError("Phase436 wrapper: one() repair anchor missing")
s = s.replace(old_one, new_one, 1)

# GNU89: the old payload emitted a mark before a block-local 'int ret = ...'
# declaration. Rewrite only that generator block so the declaration remains
# first and the PRE/POST marks surround the actual call.
start = s.find('        anchor = "\\t\\tint ret = gmu_core_start(device);\\n"\n')
if start < 0:
    raise RuntimeError("Phase436 wrapper: GMU-core declaration anchor missing")
end_marker = '            "gmu core start")\n'
end = s.find(end_marker, start)
if end < 0:
    raise RuntimeError("Phase436 wrapper: GMU-core block end missing")
end += len(end_marker)
fixed = '''        anchor = "\\t\\tint ret = gmu_core_start(device);\\n"
        fn = one(fn, anchor,
            "\\t\\tint ret;\\n\\n"
            "\\t\\tif (a52_p436_target_current())\\n"
            "\\t\\t\\ta52_p436_mark(P436_P3_GMU_CORE_START_PRE, device->state, 0, NULL);\\n"
            "\\t\\tret = gmu_core_start(device);\\n"
            "\\t\\tif (a52_p436_target_current())\\n"
            "\\t\\t\\ta52_p436_mark(P436_P4_GMU_CORE_START_POST, (u64)(s64)ret,\\n"
            "\\t\\t\\t\\tdevice->state, NULL);\\n",
            "gmu core start")
'''
s = s[:start] + fixed + s[end:]

exec(compile(s, str(p)[:-4], "exec"), globals(), globals())
