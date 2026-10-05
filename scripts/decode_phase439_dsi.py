#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, struct, zipfile
from pathlib import Path

GAPLOG_BYTES=2*1024*1024
GAPLOG_OFFSET_IN_11M=9*1024*1024
HEADER_BYTES=4096
RECORD_BYTES=128
MAGIC=0x323933474F4C5047
VERSION=1
COMMIT=0x392C0DE5
CAPACITY=(GAPLOG_BYTES-HEADER_BYTES)//RECORD_BYTES

RX_S=re.compile(r'^P439 S i=(\d+) e=(\d+) p=(\d+) us=(\d+) st=([0-9a-f]+) fs=([0-9a-f]+) cc=([0-9a-f]+) ck=([0-9a-f]+) in=([0-9a-f]+) ln=([0-9a-f]+) db=([0-9a-f]+)$',re.I)
RX_D=re.compile(r'^P439 D i=(\d+) e=(\d+) dc=([0-9a-f]+) o=([0-9a-f]+) l=([0-9a-f]+) sw=([0-9a-f]+) tg=([0-9a-f]+) ae=([0-9a-f]+) to=([0-9a-f]+) pe=([0-9a-f]+) ax=([0-9a-f]+)$',re.I)
RX_Q=re.compile(r'^P439 Q i=(\d+) e=(\d+) s1=([0-9a-f]+) s2=([0-9a-f]+) dm=([0-9a-f]+) lc=([0-9a-f]+)$',re.I)
RX_P=re.compile(r'^P439 P i=(\d+) e=(\d+) ps=([0-9a-f]+) pc=([0-9a-f]+) c0=([0-9a-f]+) rb=([0-9a-f]+) cf=([0-9a-f]+) l0=([0-9a-f]+) l1=([0-9a-f]+)$',re.I)
RX_B=re.compile(r'^P439 B b=(\d+) t=(\d+) c=([0-9a-f]+) v=([0-9a-f]+)$',re.I)
RX_R=re.compile(r'^P439 R (pre|post) (.*)$',re.I)


def crc32c(data:bytes)->int:
    c=0xffffffff
    for b in data:
        c^=b
        for _ in range(8):
            c=(c>>1)^(0x82f63b78 if c&1 else 0)
    return (~c)&0xffffffff


def find_dump(path:Path)->bytes:
    raw=path.read_bytes()
    if raw[:4]==b'PK\x03\x04':
        with zipfile.ZipFile(path) as z:
            names=z.namelist()
            cand=[n for n in names if 'reserved' in n.lower() and ('11mib' in n.lower() or 'b1000000' in n.lower())]
            if not cand:
                cand=[n for n in names if 'phase389' in n.lower() and n.lower().endswith('.bin')]
            if not cand:
                raise SystemExit('no Samsung reserved-RAM dump found in zip')
            return z.read(cand[0])
    return raw


def choose(raw:bytes)->bytes:
    if len(raw)==GAPLOG_BYTES:return raw
    if len(raw)>=GAPLOG_OFFSET_IN_11M+GAPLOG_BYTES:
        return raw[GAPLOG_OFFSET_IN_11M:GAPLOG_OFFSET_IN_11M+GAPLOG_BYTES]
    raise SystemExit(f'unsupported input size {len(raw)}')


def parse_kv_tail(tail:str)->dict[str,int]:
    out={}
    for m in re.finditer(r'([A-Za-z0-9_]+)=([0-9a-f]+)',tail,re.I):
        k,v=m.group(1),m.group(2)
        # P439 reset records are emitted with %x. Parse every field as hex so
        # values such as 100/104 retain their register meaning.
        out[k]=int(v,16)
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument('capture',type=Path);ap.add_argument('--json',type=Path);a=ap.parse_args()
    data=choose(find_dump(a.capture))
    magic,ver,rb,cap,_r,boot,ws,wsi,drop,dropi=struct.unpack_from('<QIIIIQQQQQ',data,0)
    if magic!=MAGIC or ver!=VERSION or rb!=RECORD_BYTES or cap!=CAPACITY:
        raise SystemExit('Phase392 header/geometry mismatch')
    count=min(ws,CAPACITY); first=ws-count+1 if count else 0; records=[]
    for seq in range(first,ws+1):
        slot=(seq-1)%CAPACITY; off=HEADER_BYTES+slot*RECORD_BYTES; rec=data[off:off+RECORD_BYTES]
        rseq,ts,cpu,l,_=struct.unpack_from('<QQHHI',rec,0); sc,cm=struct.unpack_from('<II',rec,120)
        if cm!=COMMIT or sc!=crc32c(rec[:120]) or rseq!=seq:continue
        msg=rec[24:24+min(l,96)].split(b'\0',1)[0].decode('utf-8','replace')
        if msg.startswith('P439 '):records.append({'seq':seq,'ts_ns':ts,'cpu':cpu,'text':msg})

    samples={}; debug_bus={}; resets=[]
    for r in records:
        t=r['text']; m=RX_S.match(t)
        if m:
            i=int(m.group(1));samples.setdefault(i,{})
            samples[i].update(epoch=int(m.group(2)),point=int(m.group(3)),us=int(m.group(4)),status=int(m.group(5),16),fifo=int(m.group(6),16),clk_ctrl=int(m.group(7),16),clk_status=int(m.group(8),16),int_ctrl=int(m.group(9),16),lane_status=int(m.group(10),16),dbg171=int(m.group(11),16));continue
        m=RX_D.match(t)
        if m:
            i=int(m.group(1));samples.setdefault(i,{})
            keys=['dma_ctrl','dma_offset','dma_length','sw_trigger','trig_ctrl','ack_err','timeout','phy_err','axi2ahb']
            samples[i]['epoch']=int(m.group(2));samples[i].update({k:int(m.group(j+3),16) for j,k in enumerate(keys)});continue
        m=RX_Q.match(t)
        if m:
            i=int(m.group(1));samples.setdefault(i,{})
            samples[i].update(epoch=int(m.group(2)),sched1=int(m.group(3),16),sched2=int(m.group(4),16),disp_misc=int(m.group(5),16),lane_ctrl=int(m.group(6),16));continue
        m=RX_P.match(t)
        if m:
            i=int(m.group(1));samples.setdefault(i,{})
            keys=['pll_status_one','phy_pll_ctrl','phy_ctrl0','phy_rbuf','phy_clk_cfg1','phy_lane0','phy_lane1']
            samples[i]['epoch']=int(m.group(2));samples[i].update({k:int(m.group(j+3),16) for j,k in enumerate(keys)});continue
        m=RX_B.match(t)
        if m:debug_bus[f'{int(m.group(1))}:{int(m.group(2))}']={'ctl':int(m.group(3),16),'value':int(m.group(4),16)};continue
        m=RX_R.match(t)
        if m:resets.append({'when':m.group(1).lower(),'fields':parse_kv_tail(m.group(2))})

    print(f'Phase439 boot_id={boot} write_seq={ws} p439_records={len(records)} debug_bus={len(debug_bus)}/256')
    if samples:
        print(' idx ep point   us   STATUS   INT_CTRL  CLK_STAT  CLK_CTRL  DBG171    SCHED1    SCHED2    DISP_MISC')
        for i in sorted(samples):
            s=samples[i]
            print(f" {i:3d} {s.get('epoch',-1):2d} {s.get('point',-1):5d} {s.get('us',-1):5d} {s.get('status',0):08x} {s.get('int_ctrl',0):08x} {s.get('clk_status',0):08x} {s.get('clk_ctrl',0):08x} {s.get('dbg171',0):08x} {s.get('sched1',0):08x} {s.get('sched2',0):08x} {s.get('disp_misc',0):08x}")
    if resets:
        print('\nSchedule-reset A/B:')
        for x in resets:print(x['when'],x['fields'])
    if debug_bus:
        print('\nDebug-bus sweep (block:test=value):')
        for k in sorted(debug_bus,key=lambda x:tuple(map(int,x.split(':')))):
            print(f" {k}={debug_bus[k]['value']:08x}")
    print('\nEvent stream:')
    for r in records:print(f"{r['ts_ns']/1e9:12.6f}s seq={r['seq']:6d} cpu={r['cpu']:2d} {r['text']}")
    out={'boot_id':boot,'write_seq':ws,'records':records,'samples':samples,'debug_bus':debug_bus,'schedule_reset':resets}
    if a.json:a.json.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

if __name__=='__main__':main()
