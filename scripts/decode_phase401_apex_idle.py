#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, struct
from pathlib import Path

RAW_BYTES = 0x100000
BASE = 0xF8000
COPY = 0x3C00
SLOT = 64
FIXED_SLOTS = 16
RING_OFF = FIXED_SLOTS * SLOT
RING_SLOTS = (COPY - RING_OFF) // SLOT
MAGIC = 0x3130345044495841
COMMIT = 0x401C0DE5
FMT = "<QQQQIIIIIIII"
EVENTS = {1:"meta",2:"irq",3:"timer",4:"idle_enter",5:"apex_exit",6:"focus",7:"timer_arm"}
FIXED_NAMES = {0:"meta",1:"irq_cpu0",2:"irq_cpu5",3:"irq_cpu7",4:"timer_cpu0",5:"timer_cpu5",6:"timer_cpu7",7:"idle_cpu0",8:"idle_cpu5",9:"idle_cpu7",10:"apex_state"}

def parse(buf):
    if len(buf) != SLOT: return None
    v = struct.unpack(FMT, buf)
    magic, boot_id, cntpct, ns, event, cpu, v0, v1, seq, seqinv, ver, commit = v
    if magic != MAGIC or commit != COMMIT: return None
    if ((seq ^ seqinv) & 0xffffffff) != 0xffffffff: return None
    return dict(boot_id=boot_id,cntpct=cntpct,time_ms=ns/1e6,event=event,
                event_name=EVENTS.get(event,f"event_{event}"),cpu=cpu,
                value0=v0,value1=v1,sequence=seq,version=ver)

def choose(a,b):
    pa,pb=parse(a),parse(b)
    if pa and pb:
        r=dict(pb if pb["sequence"]>pa["sequence"] else pa)
        r["source"]="both"; r["copies_equal"]=a==b; return r
    r=pa or pb
    if r:
        r=dict(r); r["source"]="copy0" if pa else "copy1"; r["copies_equal"]=False
    return r

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("raw",type=Path); ap.add_argument("--json",type=Path)
    a=ap.parse_args()
    d=a.raw.read_bytes()
    if len(d)<RAW_BYTES: raise SystemExit("need >=1 MiB raw ramoops")
    d=d[:RAW_BYTES]
    fixed=[]
    for s in range(FIXED_SLOTS):
        r=choose(d[BASE+s*SLOT:BASE+(s+1)*SLOT],
                 d[BASE+COPY+s*SLOT:BASE+COPY+(s+1)*SLOT])
        if r:
            r["slot"]=s; r["name"]=FIXED_NAMES.get(s,f"fixed_{s}"); fixed.append(r)
    ring={}
    for s in range(RING_SLOTS):
        o=RING_OFF+s*SLOT
        r=choose(d[BASE+o:BASE+o+SLOT],d[BASE+COPY+o:BASE+COPY+o+SLOT])
        if r: ring[r["sequence"]]=r
    ordered=[ring[k] for k in sorted(ring)]
    out={"summary":{"fixed_valid":len(fixed),"ring_valid":len(ordered),
         "ring_slots":RING_SLOTS,"boot_ids":sorted({r["boot_id"] for r in fixed+ordered}),
         "first_time_ms":ordered[0]["time_ms"] if ordered else None,
         "last_time_ms":ordered[-1]["time_ms"] if ordered else None},
         "fixed":fixed,"ring":ordered}
    print(json.dumps(out,indent=2))
    if a.json: a.json.write_text(json.dumps(out,indent=2)+"\n")

if __name__=="__main__": main()
