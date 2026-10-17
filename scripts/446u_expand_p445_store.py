#!/usr/bin/env python3
"""446U Part A5: grow the P445 section directory from 120 to 240.

Keep the physical P445 reservation and payload size unchanged. The directory
needs 16 KiB instead of 8 KiB; payload starts after the new header. Compile
time BUILD_BUG_ON protects header capacity.
"""
import argparse
from pathlib import Path
P=Path("drivers/a52_display/msm/a52_phase445.c")
MARK="A52_PHASE446U_P445_240_SECTIONS_V1"
def main():
    a=argparse.ArgumentParser()
    a.add_argument("--root",type=Path,required=True)
    a.add_argument("--check-only",action="store_true")
    o=a.parse_args()
    file=o.root/P
    s=file.read_text()
    if not o.check_only and MARK not in s:
        before="#define P445_HEADER_BYTES SZ_8K\n#define P445_MAX_SECTIONS 120U"
        after="/* "+MARK+" */\n#define P445_HEADER_BYTES SZ_16K\n#define P445_MAX_SECTIONS 240U"
        if s.count(before)!=1:raise RuntimeError("446U P445 exact bounded-cap anchor absent")
        s=s.replace(before,after,1)
        file.write_text(s)
    for t in (MARK,"#define P445_HEADER_BYTES SZ_16K",
              "#define P445_MAX_SECTIONS 240U",
              "BUILD_BUG_ON(sizeof(struct p445_header)>P445_HEADER_BYTES)",
              "P445_RAM_BYTES 0x00098000U"):
        if t not in s:raise RuntimeError("446U P445 missing "+t)
    print("P446U P445 CAP PASS: 240 entries in 16KiB header; original 0x98000 reservation retained")
if __name__=="__main__":main()
