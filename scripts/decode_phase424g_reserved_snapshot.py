#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

MAGIC=0x3432344750414E53  # "SNAPG424"
VERSION=1
COPY_OFFSETS=(0x0000,0x1000)
FULL_SAMSUNG_BYTES=11*1024*1024
GOLDEN_REGION_BYTES=7*1024*1024
FULL_TO_GOLDEN_OFF=0x400000

GROUPS=(
    ("D",8),("C0",8),("C1",8),("P",4),
    ("V0",8),("V1",4),("R0",8),("R1",6),
)
N_U32=sum(n for _,n in GROUPS)
SNAP_BYTES=8+N_U32*4
PERSIST_BYTES=8+4+4+SNAP_BYTES+4+4


def crc32c(data:bytes)->int:
    crc=0xffffffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc=(crc>>1) ^ (0x82f63b78 if crc&1 else 0)
    return (~crc)&0xffffffff


def parse_copy(data:bytes,off:int)->dict:
    raw=data[off:off+PERSIST_BYTES]
    if len(raw)!=PERSIST_BYTES:
        return {"offset":off,"valid":False,"reason":"short"}

    magic,version,bytes_total=struct.unpack_from("<QII",raw,0)
    if magic!=MAGIC:
        return {
            "offset":off,"valid":False,
            "reason":f"magic 0x{magic:016x}",
        }
    if version!=VERSION or bytes_total!=PERSIST_BYTES:
        return {
            "offset":off,"valid":False,
            "reason":f"version={version} bytes={bytes_total}",
        }

    ns=struct.unpack_from("<Q",raw,16)[0]
    vals=list(struct.unpack_from("<"+"I"*N_U32,raw,24))
    crc,crc_inv=struct.unpack_from("<II",raw,PERSIST_BYTES-8)
    calc=crc32c(raw[8:PERSIST_BYTES-8])
    if crc_inv != ((~crc)&0xffffffff):
        return {"offset":off,"valid":False,"reason":"crc inverse mismatch"}
    if crc!=calc:
        return {
            "offset":off,"valid":False,
            "reason":f"crc stored=0x{crc:08x} calc=0x{calc:08x}",
        }

    groups={}
    pos=0
    for name,n in GROUPS:
        groups[name]=vals[pos:pos+n]
        pos+=n

    return {
        "offset":off,
        "valid":True,
        "ns":ns,
        "groups":groups,
        "crc32c":crc,
    }


def main()->int:
    ap=argparse.ArgumentParser(
        description="Decode Phase424G Golden snapshot from Samsung reserved RAM"
    )
    ap.add_argument("reserved_bin",type=Path,
        help="7 MiB phase389-B1400000... dump or full 11 MiB Samsung reserved dump")
    ap.add_argument("--json",type=Path)
    ap.add_argument("--text-out",type=Path)
    ns=ap.parse_args()

    blob=ns.reserved_bin.read_bytes()
    if len(blob)==GOLDEN_REGION_BYTES:
        base=0
    elif len(blob)>=FULL_SAMSUNG_BYTES:
        base=FULL_TO_GOLDEN_OFF
    else:
        raise SystemExit(
            f"unsupported dump size {len(blob)}; expected 7 MiB or >=11 MiB"
        )

    copies=[parse_copy(blob,base+o) for o in COPY_OFFSETS]
    valid=[c for c in copies if c["valid"]]
    summary={
        "input_bytes":len(blob),
        "base_offset":base,
        "persist_bytes":PERSIST_BYTES,
        "copies":copies,
        "valid_copy_count":len(valid),
    }

    if not valid:
        print(json.dumps(summary,indent=2,sort_keys=True))
        raise SystemExit("No valid Phase424G reserved-RAM snapshot found")

    # Copies should be identical; use copy0 when valid, otherwise copy1.
    chosen=valid[0]
    if len(valid)==2:
        summary["copies_identical"]=(
            valid[0]["ns"]==valid[1]["ns"] and
            valid[0]["groups"]==valid[1]["groups"] and
            valid[0]["crc32c"]==valid[1]["crc32c"]
        )

    lines=[f"TG424 T ns={chosen['ns']}"]
    for name,_ in GROUPS:
        vals=chosen["groups"][name]
        lines.append("TG424 "+name+" "+" ".join(f"{v:x}" for v in vals))
    text="\n".join(lines)+"\n"

    print(json.dumps(summary,indent=2,sort_keys=True))
    print(text,end="")
    if ns.text_out:
        ns.text_out.write_text(text)
    if ns.json:
        ns.json.write_text(json.dumps({
            "summary":summary,
            "selected":chosen,
        },indent=2,sort_keys=True)+"\n")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
