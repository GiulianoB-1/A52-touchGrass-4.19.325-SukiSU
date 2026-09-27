#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,struct
from pathlib import Path

RAW=0x100000
BASE=0xFC000
COPY=0x1C00
P399_BYTES=3*64
P400_BYTES=4*64
RING=COPY-P399_BYTES-P400_BYTES
P400_IRQ_MAGIC=0x3030345152495741
P400_IRQ_COMMIT=0x4001c0de
P400_EXEC_MAGIC=0x3030344345585741
P400_EXEC_COMMIT=0x4002c0de
CPUS=(0,5,7)

def parse_irq(buf):
    if len(buf)!=64: return None
    v=struct.unpack("<QQQIIIIIIIIII",buf)
    magic,cntpct,rseq,cpu,irq,hwirq,count,cntfrq,pc,irqoff,ver,commit,res=v
    if magic!=P400_IRQ_MAGIC or commit!=P400_IRQ_COMMIT: return None
    if cpu not in CPUS or not cntfrq: return None
    return dict(kind="irq",cpu=cpu,irq=irq,hwirq=hwirq,count=count,
                cntpct=cntpct,cntfrq=cntfrq,counter_ms=cntpct*1000.0/cntfrq,
                r48_sequence=rseq,preempt_count=pc,irqs_disabled=bool(irqoff),
                version=ver)

def parse_exec(buf):
    if len(buf)!=64: return None
    v=struct.unpack("<QQQQIIIIIIII",buf)
    magic,cntpct,loops,start,cpu,seq,seqinv,cntfrq,pc,irqoff,ver,commit=v
    if magic!=P400_EXEC_MAGIC or commit!=P400_EXEC_COMMIT: return None
    if ((seq^seqinv)&0xffffffff)!=0xffffffff or not cntfrq: return None
    return dict(kind="exec",cpu=cpu,sequence=seq,loops=loops,cntpct=cntpct,
                start_cntpct=start,cntfrq=cntfrq,
                elapsed_ms=(cntpct-start)*1000.0/cntfrq,
                counter_ms=cntpct*1000.0/cntfrq,
                preempt_count=pc,irqs_disabled=bool(irqoff),version=ver)

def choose(d,o0,o1,parser):
    a=parser(d[o0:o0+64]); b=parser(d[o1:o1+64])
    if a and b:
        key="count" if a["kind"]=="irq" else "sequence"
        row=b if b[key]>a[key] else a
        row["source"]="both"
        return row
    row=a or b
    if row: row["source"]="copy0" if a else "copy1"
    return row

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("raw",type=Path)
    ap.add_argument("--json",type=Path)
    a=ap.parse_args()
    d=a.raw.read_bytes()
    if len(d)<RAW: raise SystemExit("need >=1 MiB raw ramoops")
    d=d[:RAW]
    rows=[]
    for slot,cpu in enumerate(CPUS):
        o0=BASE+RING+slot*64
        o1=BASE+COPY+RING+slot*64
        r=choose(d,o0,o1,parse_irq)
        if r: rows.append(r)
    o0=BASE+RING+3*64
    o1=BASE+COPY+RING+3*64
    r=choose(d,o0,o1,parse_exec)
    if r: rows.append(r)
    print(json.dumps(rows,indent=2))
    if a.json: a.json.write_text(json.dumps(rows,indent=2)+"\n")

if __name__=="__main__":
    main()
