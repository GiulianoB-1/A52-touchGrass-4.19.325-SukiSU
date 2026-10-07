#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path

def die(msg: str) -> None:
    raise SystemExit(msg)

def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--kind",choices=("gki","tg"),required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    central=(a.root/"drivers/a52_display/msm/a52_phase445.c"
             if a.kind=="gki" else
             a.root/"techpack/display/msm/a52_phase446g.c")
    if not central.is_file():
        die(f"missing recorder source: {central}")
    s=central.read_text(errors="replace")
    old_re=r"(?m)^#define\s+P446_MAX_REC\s+2048U?\s*$"
    new="#define P446_MAX_REC 1536U"
    if not a.check_only:
        if new not in s:
            s2,n=re.subn(old_re,new,s,count=1)
            if n!=1:
                die("P446_MAX_REC 2048 anchor missing")
            central.write_text(s2)
            s=s2
    if new not in s:
        die("Phase446d capacity guard missing")
    if "u32 rcg[30];" not in s or "u32 pll0[9];" not in s:
        die("Phase446d extended record missing")
    print("Phase446d capacity guard: PASS record=664 max=1536")

if __name__=="__main__":
    main()
