#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import collections
import json
import re
import struct
from pathlib import Path

RAW_BASE=0xB1B00000
TASK_PHYS=0xB1BF0000
DMA_PHYS=0xB1BF8000
TASK_OFF=TASK_PHYS-RAW_BASE
DMA_OFF=DMA_PHYS-RAW_BASE
TASK_COPY=0x2A00
TASK_SLOT=256
TASK_SLOTS=42
WITNESS_OFF=TASK_OFF+0x7E00
WITNESS_STRIDE=0x80
WITNESS_SIZE=80
DMA_COPY=0x800
TASK_MAGIC=0x303334534e454353
TASK_COMMIT=0x430c0de5
WITNESS_MAGIC=0x3133345745584543
WITNESS_COMMIT=0x431c0de5
DMA_MAGIC=0x303334414d445349
DMA_COMMIT=0x430d0de5
VERSION=1
TASK_FMT=struct.Struct('<'+'Q'*15+'I'*17+'16s32s8sIII')
WITNESS_FMT=struct.Struct('<5Q10I')
ROLE={2:'surfaceflinger',3:'composer',4:'system_server'}
SNAP={0:'SF+0',1:'SF+1s',2:'SF+3s',3:'SF+5s',4:'SF+20s',5:'SF+60s',6:'first-atomic'}
STAGE={1:'SF_SEEN',2:'SNAP_BEGIN',3:'TASKS_DONE',4:'EXEC_WINDOW',5:'EXEC_DONE'}
DMA_POINT={0:'pre-trigger',1:'post-trigger',2:'+10us',3:'+100us',4:'+1ms',5:'+10ms',6:'timeout/final'}

def crc32c(data:bytes)->int:
    crc=0xffffffff
    for b in data:
        crc ^= b
        for _ in range(8):
            crc=(crc>>1) ^ (0x82f63b78 if crc&1 else 0)
    return (~crc)&0xffffffff

def cstr(b): return b.split(b'\0',1)[0].decode('utf-8','replace')
def majority3(a,b,c): return bytes(((x&y)|(x&z)|(y&z)) for x,y,z in zip(a,b,c))

def parse_task(raw:bytes):
    if len(raw)!=TASK_SLOT:return None
    v=TASK_FMT.unpack(raw)
    qs=v[:15];us=v[15:32];comm,ws,res,crc,commit,ver=v[32:]
    good=(qs[0]==TASK_MAGIC and commit==TASK_COMMIT and ver==VERSION and crc32c(raw[:244])==crc)
    return dict(valid=good,raw=raw,magic=qs[0],ns=qs[1],wchan=qs[2],user_pc=qs[3],user_lr=qs[4],user_sp=qs[5],runtime=qs[6],stack=list(qs[7:15]),
        snapshot=us[0],role=us[1],pid=us[2],tgid=us[3],ppid=us[4],state=us[5],exit_state=us[6],flags=us[7],on_cpu=us[8],on_rq=us[9],cpu=us[10],stack_nr=us[11],
        nr_threads=us[12],live_threads=us[13],nvcsw=us[14],nivcsw=us[15],slot=us[16],comm=cstr(comm),wchan_symbol=cstr(ws),crc=crc,commit=commit,version=ver)

def parse_witness(raw:bytes):
    if len(raw)!=WITNESS_SIZE:return None
    v=WITNESS_FMT.unpack(raw)
    magic,cntpct,sf_ns,loops,write_ns,seq,seqinv,cpu,stage,snapshot,cntfrq,crc,commit,ver,res=v
    good=(magic==WITNESS_MAGIC and commit==WITNESS_COMMIT and ver==VERSION and
          ((seq^seqinv)&0xffffffff)==0xffffffff and crc32c(raw[:64])==crc)
    return dict(valid=good,raw=raw,cntpct=cntpct,sf_start_ns=sf_ns,loops=loops,write_ns=write_ns,
        sequence=seq,cpu=cpu,stage=stage,snapshot=snapshot,cntfrq=cntfrq,crc=crc,commit=commit,version=ver)

def load_syms(path):
    add=[];names=[];by={}
    if not path:return add,names,by
    for line in path.read_text(errors='replace').splitlines():
        p=line.split()
        if len(p)<3:continue
        try:a=int(p[0],16)
        except:continue
        add.append(a);names.append(p[2]);by[p[2]]=a
    return add,names,by

def infer_slide(recs,by):
    vals=[];rx=re.compile(r'^([^+]+)\+0x([0-9a-fA-F]+)')
    for r in recs:
        if not r or not r['valid'] or not r['wchan'] or not r['wchan_symbol']:continue
        m=rx.match(r['wchan_symbol'])
        if not m or m.group(1) not in by:continue
        vals.append(r['wchan']-by[m.group(1)]-int(m.group(2),16))
    if not vals:return None,[]
    c=collections.Counter(vals);return c.most_common(1)[0][0],c.most_common()

def sym(pc,add,names,slide):
    if not pc or slide is None or not add:return f'0x{pc:x}' if pc else '0'
    x=pc-slide;i=bisect.bisect_right(add,x)-1
    return f'{names[i]}+0x{x-add[i]:x}' if i>=0 else f'0x{pc:x}'

def parse_dma(raw:bytes):
    if len(raw)<1272:return None
    magic,capture_ns,iova=struct.unpack_from('<QQQ',raw,0)
    vals=struct.unpack_from('<8I',raw,24)
    sample_count,wait_ret,irq_trig,bad_iova,child_cfg,num_tcs,ncpt,valid=vals
    crc,commit,ver=struct.unpack_from('<III',raw,1260)
    good=(magic==DMA_MAGIC and commit==DMA_COMMIT and ver==VERSION and crc32c(raw[:1260])==crc)
    o=56;samples=[];sfmt=struct.Struct('<Q17I')
    for i in range(7):
        x=sfmt.unpack_from(raw,o);o+=sfmt.size
        samples.append(dict(ns=x[0],point=x[1],status=x[2],fifo=x[3],clk_ctrl=x[4],clk_status=x[5],int_ctrl=x[6],lane=x[7],dma_ctrl=x[8],dma_offset=x[9],dma_length=x[10],sw=x[11],trig=x[12],ack=x[13],timeout=x[14],phy=x[15],axi=x[16],errmask=x[17]))
    tcs=[]
    for t in range(2):
        control,status,enable,wait=struct.unpack_from('<4I',raw,o);o+=16
        cmds=[]
        for n in range(16):
            a,d,s=struct.unpack_from('<III',raw,o);o+=12;cmds.append((a,d,s))
        tcs.append(dict(control=control,status=status,enable=enable,wait=wait,cmd=cmds))
    clk=list(struct.unpack_from('<20I',raw,o));o+=80
    pwr=list(struct.unpack_from('<4I',raw,o));o+=16
    u=list(struct.unpack_from('<28I6Q',raw,o));o+=160
    keys=['valid','rpm_ret','sid','sme','cb','irq','smr','s2cr','cbar','cba2r','cbfrsynra','sctlr','actlr','tcr','tcr2','mair0','mair1','contextidr','fsr','fsynr0','gfsr','gfsynr0','gfsynr1','gfsynr2','tlbstatus','gtlbstatus','atos_status','atos_timeout','ttbr0','ttbr1','far','sw_phys','hw_phys','par']
    return dict(valid_crc=good,raw=raw[:1272],magic=magic,capture_ns=capture_ns,iova=iova,sample_count=sample_count,wait_ret=wait_ret,irq_trig=irq_trig,bad_iova=bad_iova,child_cfg=child_cfg,num_tcs=num_tcs,ncpt=ncpt,valid=valid,samples=samples,tcs=tcs,clk=clk,pwr=pwr,smmu=dict(zip(keys,u)),crc=crc,commit=commit,version=ver)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('ramoops',type=Path);ap.add_argument('--system-map',type=Path);ap.add_argument('--json',type=Path);ns=ap.parse_args()
    b=ns.ramoops.read_bytes()
    if len(b)<0x100000:raise SystemExit(f'short ramoops {len(b)}')

    recs=[];meta=[]
    for i in range(TASK_SLOTS):
        copies=[parse_task(b[TASK_OFF+k*TASK_COPY+i*TASK_SLOT:TASK_OFF+k*TASK_COPY+(i+1)*TASK_SLOT]) for k in range(3)]
        good=[x for x in copies if x and x['valid']];fused=False
        if good:r=good[0]
        else:
            raw=majority3(*(x['raw'] for x in copies));r=parse_task(raw);fused=True
            if not r['valid']:r=None
        recs.append(r);meta.append(dict(slot=i,valid_copies=sum(bool(x and x['valid']) for x in copies),copy_equal=(copies[0]['raw']==copies[1]['raw']==copies[2]['raw']),fused=fused,accepted=bool(r)))

    add,names,by=load_syms(ns.system_map);slide,cands=infer_slide(recs,by)
    print(f'Phase431 task slots accepted={sum(bool(x) for x in recs)}/{TASK_SLOTS}; slide={hex(slide) if slide is not None else "unknown"}')
    if cands:print('slide candidates:',cands[:5])
    for sid in range(7):
        grp=[r for r in recs if r and r['snapshot']==sid]
        print(f'\n=== task snapshot {sid} {SNAP[sid]} records={len(grp)} ===')
        for r in sorted(grp,key=lambda z:z['slot']):
            print(f"{ROLE.get(r['role'],r['role'])} pid={r['pid']} tgid={r['tgid']} comm={r['comm']} state=0x{r['state']:x} cpu={r['cpu']} oncpu={r['on_cpu']} rq={r['on_rq']} w={r['wchan_symbol'] or hex(r['wchan'])} nv={r['nvcsw']}/{r['nivcsw']}")
            for j,pc in enumerate(r['stack'][:min(r['stack_nr'],8)]):print(f'  K{j:02d} {sym(pc,add,names,slide)}')

    wcopies=[parse_witness(b[WITNESS_OFF+k*WITNESS_STRIDE:WITNESS_OFF+k*WITNESS_STRIDE+WITNESS_SIZE]) for k in range(3)]
    wgood=[x for x in wcopies if x and x['valid']]
    wfused=False
    if wgood:w=wgood[0]
    else:
        raw=majority3(*(x['raw'] for x in wcopies));w=parse_witness(raw);wfused=True
        if not w['valid']:w=None
    print(f'\nPhase431 witness valid_copies={sum(bool(x and x["valid"]) for x in wcopies)}/3 fused={wfused}')
    if w:
        rel_ms=(w['write_ns']-w['sf_start_ns'])/1e6 if w['sf_start_ns'] else -1
        counter_ms=w['cntpct']*1000.0/w['cntfrq'] if w['cntfrq'] else -1
        print(f"seq={w['sequence']} stage={STAGE.get(w['stage'],w['stage'])} snapshot={w['snapshot']} cpu={w['cpu']} loops={w['loops']} rel_ms={rel_ms:.3f} cntpct_ms={counter_ms:.3f}")

    dcopies=[parse_dma(b[DMA_OFF+k*DMA_COPY:DMA_OFF+k*DMA_COPY+1272]) for k in range(3)]
    good=[x for x in dcopies if x and x['valid_crc']];fused=False
    if good:d=good[0]
    else:
        raw=majority3(*(x['raw'] for x in dcopies));d=parse_dma(raw);fused=True
        if not d['valid_crc']:d=None
    print(f'\nPhase430/431 DMA valid_copies={sum(bool(x and x["valid_crc"]) for x in dcopies)}/3 equal={dcopies[0]["raw"]==dcopies[1]["raw"]==dcopies[2]["raw"]} fused={fused}')
    if d:
        print(f'iova=0x{d["iova"]:x} bad_iova={d["bad_iova"]} wait_ret={d["wait_ret"]} irq={d["irq_trig"]} samples={d["sample_count"]} child_cfg=0x{d["child_cfg"]:x} num_tcs={d["num_tcs"]} ncpt={d["ncpt"]}')
        base=None
        for s in d['samples'][:min(d['sample_count'],7)]:
            if base is None:base=s['ns']
            print(f"{DMA_POINT.get(s['point'],s['point']):>13} +{(s['ns']-base)/1000:9.1f}us INT=0x{s['int_ctrl']:08x} done={(s['int_ctrl']&1)!=0} en={(s['int_ctrl']&2)!=0} STATUS=0x{s['status']:x} FIFO=0x{s['fifo']:x} CLK=0x{s['clk_status']:x} ACK=0x{s['ack']:x} TO=0x{s['timeout']:x}")
        for ti,t in enumerate(d['tcs'][:min(d['num_tcs'],2)]):
            print(f'TCS{ti}: ctrl=0x{t["control"]:x} st=0x{t["status"]:x} en=0x{t["enable"]:x} wait=0x{t["wait"]:x}')
            for n,(a,v,st) in enumerate(t['cmd'][:min(d['ncpt'],16)]):
                if a or v or st:print(f'  cmd{n:02d}: addr=0x{a:x} data=0x{v:x} status=0x{st:x}')
        m=d['smmu']
        print(f'SMMU valid={m["valid"]} sid=0x{m["sid"]:x} sme={m["sme"]} cb={m["cb"]} irq={m["irq"]} FSR=0x{m["fsr"]:x} FSYNR0=0x{m["fsynr0"]:x} FAR=0x{m["far"]:x} GFSR=0x{m["gfsr"]:x} TLB=0x{m["tlbstatus"]:x}/0x{m["gtlbstatus"]:x}')
        print(f'TTBR0=0x{m["ttbr0"]:x} TCR=0x{m["tcr"]:x} MAIR0=0x{m["mair0"]:x} SWphys=0x{m["sw_phys"]:x} HWphys=0x{m["hw_phys"]:x} PAR=0x{m["par"]:x} ATOS_timeout={m["atos_timeout"]} ATSR=0x{m["atos_status"]:x}')

    out=dict(task_meta=meta,task_records=[r for r in recs if r],slide=slide,witness=w,dma=d)
    if ns.json:ns.json.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')

if __name__=='__main__':
    main()
