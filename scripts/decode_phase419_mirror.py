#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, struct
from pathlib import Path

DEBUG_OFFSET = 0x800000
DISK_BYTES = 2 * 1024 * 1024
RAM_OFFSET_IN_11M = 0xA00000
RAM_BYTES = 1 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
MIRROR_OFFSET = 0x400
MIRROR_SLOTS = (HEADER_BYTES - MIRROR_OFFSET) // RECORD_BYTES
MAGIC = 0x3431344D33434553
HDR_COMMIT = 0x414B0071
REC_COMMIT = 0x414C0DE5
HEADER = struct.Struct("<QIIQQIIIIIIII")
RECORD = struct.Struct("<QQQIHH88sII")


def slice_region(data: bytes, off: int, size: int) -> bytes:
    if len(data) == size:
        return data
    if len(data) >= off + size:
        return data[off:off+size]
    raise SystemExit(f"input too small: {len(data)} need {off+size}")


def header(data: bytes) -> dict:
    vals=HEADER.unpack_from(data,0)
    keys=("magic","version","phase","boot_id","global_seq","disk_count","ram_count",
          "disk_capacity","ram_capacity","record_bytes","state","dropped","commit")
    out=dict(zip(keys,vals))
    out["valid"]=bool(out["magic"]==MAGIC and out["version"]==1 and
                      out["phase"]==414 and out["record_bytes"]==RECORD_BYTES and
                      out["commit"]==HDR_COMMIT)
    return out


def records(data: bytes, base: int, slots: int, boot_id: int) -> list[dict]:
    out=[]
    for i in range(slots):
        off=base+i*RECORD_BYTES
        seq,ts,bid,phase,cpu,length,raw,commit,res=RECORD.unpack_from(data,off)
        if commit != REC_COMMIT or phase != 414 or bid != boot_id:
            continue
        length=min(int(length),len(raw))
        text=raw[:length].split(b"\0",1)[0].decode("utf-8",errors="replace")
        out.append({"slot":i,"seq":seq,"ts_ns":ts,"cpu":cpu,"text":text})
    return out


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--debug",type=Path,required=True)
    ap.add_argument("--reserved",type=Path,required=True)
    ap.add_argument("--json",type=Path)
    ns=ap.parse_args()

    disk=slice_region(ns.debug.read_bytes(),DEBUG_OFFSET,DISK_BYTES)
    ram=slice_region(ns.reserved.read_bytes(),RAM_OFFSET_IN_11M,RAM_BYTES)
    dh,rh=header(disk),header(ram)
    bid = rh["boot_id"] if rh["valid"] else dh["boot_id"]
    mirror=records(ram,MIRROR_OFFSET,MIRROR_SLOTS,bid)

    print(f"Phase419 disk_valid={dh['valid']} ram_valid={rh['valid']} boot_id=0x{bid:016x}")
    print(f"header disk_count={dh['disk_count']} ram_count={rh['ram_count']} global_seq={rh['global_seq']}")
    print(f"P419 RAM mirror valid={len(mirror)} slots={MIRROR_SLOTS}")
    for r in mirror:
        print(f"M{r['slot']:02d} seq={r['seq']:06d} {r['ts_ns']/1e9:12.6f}s cpu={r['cpu']} {r['text']}")

    if ns.json:
        ns.json.write_text(json.dumps({"disk_header":dh,"ram_header":rh,"mirror":mirror},
                                      indent=2,sort_keys=True)+"\n")
    return 0 if (dh["valid"] or rh["valid"]) else 2

if __name__=="__main__":
    raise SystemExit(main())
