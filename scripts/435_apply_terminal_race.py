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

# Strengthen the mandatory overlap audit. Several historical phases retain
# source-level sideband definitions in the modern lineage even though their
# runtime mappers/starters are retired. Make that explicit and fail closed if
# any of those old writers becomes active again.
audit_anchor = '''    # The chosen page is the old Phase341 hardirq sideband.  Its arming path
    # must remain retired in the exact Phase434 lineage before we reuse it.
'''
audit_prefix = '''    # Historical owners that overlap the active P430/P346 ranges remain in
    # source for archaeology, but their mappers/starters must not execute.
    retired = [
        ("Phase357 init-exit", "#define A52_R357_SIDEBAND_PHYS   0xB1BF0000ULL",
         "\\ta52_r357_start();", "0xb1bf0000-0xb1bf7fff"),
        ("Phase343 exec counter", "#define A52_R343_SIDEBAND_PHYS 0xB1BF8000ULL",
         "\\ta52_r343_start();", "0xb1bf8000-0xb1bf9fff"),
        ("Phase342 old sideband", "#define A52_R342_SIDEBAND_PHYS 0xB1BFA000ULL",
         "\\ta52_r342_start();", "0xb1bfa000-0xb1bfbfff"),
    ]
    for name, marker, live_call, span in retired:
        if marker not in rec:
            die("retired-owner marker missing: " + marker)
        if live_call in rec:
            die(name + " writer unexpectedly active")
        lines.append(f"{name:24s} {span} writer RETIRED")

'''
if audit_anchor not in s:
    raise RuntimeError("Phase435 overlap-audit anchor missing")
s=s.replace(audit_anchor,audit_prefix+audit_anchor,1)
s=s.replace(
    '"Phase341 old range        0xb1bfc000-0xb1bff7ff writer RETIRED",',
    '"Phase341/393 old range    0xb1bfc000-0xb1bff7ff writer RETIRED",',
    1,
)

exec(compile(s,str(p)[:-4],"exec"),globals(),globals())
