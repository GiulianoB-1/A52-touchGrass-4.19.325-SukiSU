#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, struct
from pathlib import Path

RAW_BYTES=0x100000
BASE=0xFC000
TOTAL=0x3800
COPY=TOTAL//2
WITNESS_SLOT=64
WITNESS_SLOTS=3
WITNESS_BYTES=WITNESS_SLOT*WITNESS_SLOTS
RING=COPY-WITNESS_BYTES
MAGIC=0x3939335749525148
COMMIT=0x399C0DE5
FMT="<QQQQIIIIIIII"
CPUS=(0,5,7)

def parse(buf):
    if len(buf)!=64: return None
    v=struct.unpack(FMT,buf)
    magic,ns,jif,rseq,cpu,tick,tinv,slot,ver,commit,pc,irqoff=v
    if magic!=MAGIC or commit!=COMMIT or ((tick ^ tinv)&0xffffffff)!=0xffffffff:
        return None
    if slot>=3 or cpu!=CPUS[slot]: return None
    return dict(cpu=cpu,tick=tick,time_ms=ns/1e6,monotonic_ns=ns,jiffies64=jif,
                r48_sequence=rseq,slot=slot,version=ver,preempt_count=pc,
                irqs_disabled=bool(irqoff))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("raw",type=Path)
    ap.add_argument("--json",type=Path)
    a=ap.parse_args()
    d=a.raw.read_bytes()[:RAW_BYTES]
    if len(d)<RAW_BYTES: raise SystemExit("need 1 MiB raw ramoops")
    rows=[]
    for s,cpu in enumerate(CPUS):
        o0=BASE+RING+s*64
        o1=BASE+COPY+RING+s*64
        p0=parse(d[o0:o0+64]); p1=parse(d[o1:o1+64])
        row=p0 or p1
        source="copy0" if p0 else ("copy1" if p1 else "none")
        if p0 and p1:
            source="both"
            if p1["tick"]>p0["tick"]: row=p1
        if row:
            row["source"]=source
            rows.append(row)
    print(json.dumps(rows,indent=2))
    if a.json: a.json.write_text(json.dumps(rows,indent=2)+"\n")
if __name__=="__main__": main()
