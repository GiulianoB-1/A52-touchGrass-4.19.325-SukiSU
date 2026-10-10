#!/usr/bin/env python3
"""Launch canonical compressed F0B probe with narrow anchor compatibility.

Phase446's lifecycle marker is inserted between mode assignment and
dsi_display_set_ctrl_esd_check_flag on GKI. Keep all hardware operations,
record layouts, other stages, and safety guards IDENTICAL.
"""
import base64
import zlib
from pathlib import Path

p = Path(__file__).with_name(Path(__file__).name + ".z64")
source = zlib.decompress(base64.b64decode(p.read_text().strip())).decode()

old = '    if n != 1: raise RuntimeError(f"F0-BISECT {why}: expected one anchor, found {n}")'
new = r'''    if n != 1 and why == "entry":
        # Phase446h inserted a lifecycle marker between these original lines.
        # Keep that marker and insert P1 directly after mode assignment instead.
        needle = "\tmode = display->panel->cur_mode;"
        if n == 0 and s.count(needle) == 1:
            return s.replace(needle, needle +
                "\n\tf0b_snap(display, 1, 0); /* prepare-entry; NOT autorefresh proof */", 1)
    if n != 1: raise RuntimeError(f"F0-BISECT {why}: expected one anchor, found {n}")'''
if source.count(old) != 1:
    raise RuntimeError("F0B compressed source 'once' contract changed")
source = source.replace(old, new, 1)
exec(compile(source, str(p.with_suffix(".expanded.py")), "exec"), globals())
