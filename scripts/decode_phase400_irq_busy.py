#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, struct
from pathlib import Path

RAW_BYTES=0x100000
BASE=0xFC000
TOTAL=0x3800
COPY=TOTAL//2
SLOT=64
P399_BYTES=3*SLOT
P400_IRQ_SLOTS=8
P400_IRQ_BYTES=P400_IRQ_SLOTS*SLOT
P400_BUSY_BYTES=SLOT
FIXED=P399_BYTES+P400_IRQ_BYTES+P400_BUSY_BYTES
RING=COPY-FIXED
RING_SLOTS=RING//SLOT
R341_MAGIC=0x3134335244353241
R341_COMMIT=0x341C0DE5
P399_MAGIC=0x3939335749525148
P399_COMMIT=0x399C0DE5
P400_IRQ_MAGIC=0x3030345152495248
P400_IRQ_COMMIT=0x40010DE5
P400_BUSY_MAGIC=0x3030345953554248
P400_BUSY_COMMIT=0x40020DE5
CPUS399=(0,5,7)


def parse_r341(b):
    if len(b)!=64: return None
    v=struct.unpack('<QQQQQIIIIII', b)
    magic,ns,seq,jif,reserved,ver,index,event,cpu,tick,commit=v
    if magic!=R341_MAGIC or commit!=R341_COMMIT: return None
    return dict(index=index,time_ms=ns/1e6,monotonic_ns=ns,r48_sequence=seq,
                jiffies64=jif,event=event,cpu=cpu,tick=tick,
                pid=(reserved>>32)&0xffffffff,
                preempt_count=(reserved>>1)&0x7fffffff,
                irqs_disabled=bool(reserved&1),version=ver)


def parse_p399(b):
    if len(b)!=64: return None
    v=struct.unpack('<QQQQIIIIIIII',b)
    magic,ns,jif,rseq,cpu,tick,tinv,slot,ver,commit,pc,irqoff=v
    if magic!=P399_MAGIC or commit!=P399_COMMIT or ((tick^tinv)&0xffffffff)!=0xffffffff:
        return None
    if slot>=3 or cpu!=CPUS399[slot]: return None
    return dict(cpu=cpu,tick=tick,time_ms=ns/1e6,monotonic_ns=ns,
                jiffies64=jif,r48_sequence=rseq,slot=slot,version=ver,
                preempt_count=pc,irqs_disabled=bool(irqoff))


def parse_irq(b):
    if len(b)!=64: return None
    v=struct.unpack('<QQQQIIIIIIII',b)
    magic,ns,cntpct,rseq,irq,hwirq,cpu,count,pc,irqoff,ver,commit=v
    if magic!=P400_IRQ_MAGIC or commit!=P400_IRQ_COMMIT or cpu>=8: return None
    return dict(cpu=cpu,count=count,time_ms=ns/1e6,monotonic_ns=ns,cntpct=cntpct,
                r48_sequence=rseq,irq=irq,hwirq=hwirq,preempt_count=pc,
                irqs_disabled=bool(irqoff),version=ver)


def parse_busy(b):
    if len(b)!=64: return None
    v=struct.unpack('<QQQQQIIIIII',b)
    magic,it,cntpct,bootns,rseq,cpu,checkpoint,pc,irqoff,ver,commit=v
    if magic!=P400_BUSY_MAGIC or commit!=P400_BUSY_COMMIT: return None
    return dict(cpu=cpu,iteration=it,cntpct=cntpct,time_ms=bootns/1e6,
                boottime_ns=bootns,r48_sequence=rseq,checkpoint=checkpoint,
                done=bool(checkpoint&0x80000000),checkpoint_value=checkpoint&0x7fffffff,
                preempt_count=pc,irqs_disabled=bool(irqoff),version=ver)


def choose(a,b,parser):
    opts=[('or',bytes(x|y for x,y in zip(a,b))),('copy0',a),('copy1',b),('and',bytes(x&y for x,y in zip(a,b)))]
    for src,buf in opts:
        r=parser(buf)
        if r is not None:
            r['source']=src
            return r
    return None


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('raw',type=Path)
    ap.add_argument('--json',type=Path)
    a=ap.parse_args()
    d=a.raw.read_bytes()[:RAW_BYTES]
    if len(d)<RAW_BYTES: raise SystemExit('need 1 MiB raw ramoops')

    ring=[]
    for slot in range(RING_SLOTS):
        o0=BASE+slot*SLOT; o1=BASE+COPY+slot*SLOT
        r=choose(d[o0:o0+SLOT],d[o1:o1+SLOT],parse_r341)
        if r and r['index']%RING_SLOTS==slot: ring.append(r)
    ring.sort(key=lambda x:x['index'])

    timer=[]
    for s,cpu in enumerate(CPUS399):
        off=RING+s*SLOT
        r=choose(d[BASE+off:BASE+off+SLOT],d[BASE+COPY+off:BASE+COPY+off+SLOT],parse_p399)
        if r: timer.append(r)

    irq=[]
    irqbase=RING+P399_BYTES
    for cpu in range(8):
        off=irqbase+cpu*SLOT
        r=choose(d[BASE+off:BASE+off+SLOT],d[BASE+COPY+off:BASE+COPY+off+SLOT],parse_irq)
        if r: irq.append(r)

    busyoff=RING+P399_BYTES+P400_IRQ_BYTES
    busy=choose(d[BASE+busyoff:BASE+busyoff+SLOT],d[BASE+COPY+busyoff:BASE+COPY+busyoff+SLOT],parse_busy)

    out={'geometry':{'copy_bytes':COPY,'ring_bytes':RING,'ring_slots':RING_SLOTS},
         'timer_witnesses':timer,'generic_irq_witnesses':irq,'busy_witness':busy,
         'ring':ring}
    print(json.dumps({k:v for k,v in out.items() if k!='ring'},indent=2))
    if ring:
        print(f'R341 ring: {len(ring)} records, idx {ring[0]["index"]}..{ring[-1]["index"]}, time {ring[0]["time_ms"]:.3f}..{ring[-1]["time_ms"]:.3f} ms')
        for r in ring[-36:]:
            print(f'{r["time_ms"]:12.3f} idx={r["index"]:4d} ev={r["event"]} cpu={r["cpu"]} tick={r["tick"]:3d} pid={r["pid"]:5d} src={r["source"]}')
    if a.json: a.json.write_text(json.dumps(out,indent=2)+'\n')

if __name__=='__main__': main()