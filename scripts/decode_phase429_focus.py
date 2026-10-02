#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import struct
from pathlib import Path

MAGIC = 0x34323943454E5355
COMMIT = 0x429C0DE5
VERSION = 1
RAW_BASE = 0xB1B00000
SIDE_PHYS = 0xB1BF0000
SIDE_OFF = SIDE_PHYS - RAW_BASE
COPY_STRIDE = 0x4000
SLOT_BYTES = 320
SNAPS = 8
PER = 6
SLOTS = SNAPS * PER
FMT = struct.Struct('<' + 'Q'*19 + 'I'*19 + '16s48s28s')
ROLE = {1:'apexd',2:'surfaceflinger',3:'composer',4:'system_server'}
TARGETS = [30,60,90,120,150,170,190,200]


def cstr(b: bytes) -> str:
    return b.split(b'\0',1)[0].decode('utf-8','replace')


def load_symbols(path: Path | None):
    if not path:
        return [], []
    addrs=[]; names=[]
    for line in path.read_text(errors='replace').splitlines():
        p=line.split()
        if len(p) < 3:
            continue
        try: a=int(p[0],16)
        except ValueError: continue
        addrs.append(a); names.append(p[2])
    return addrs,names


def sym_lookup(addr:int, addrs, names, slide:int|None):
    if not addr or not addrs or slide is None:
        return f'0x{addr:x}' if addr else '0'
    stat=addr-slide
    i=bisect.bisect_right(addrs,stat)-1
    if i < 0:
        return f'0x{addr:x}'
    return f'{names[i]}+0x{stat-addrs[i]:x}'


def infer_slide(records, addrs, names):
    if not addrs:
        return None
    byname={n:a for a,n in zip(addrs,names)}
    for r in records:
        if not r:
            continue
        ws=r['wchan_symbol']
        if not ws or not r['wchan']:
            continue
        base=ws.split('+',1)[0].split('.',1)[0]
        if base in byname:
            return r['wchan']-byname[base]
    return None


def parse_slot(blob:bytes, off:int):
    if off < 0 or off+SLOT_BYTES > len(blob):
        return None
    v=FMT.unpack_from(blob,off)
    q=v[:19]; u=v[19:38]; comm,wchan_sym,_=v[38:]
    if q[0] != MAGIC or u[17] != COMMIT or u[18] != VERSION:
        return None
    return {
        'magic':q[0],'ns':q[1],'wchan':q[2],'user_pc':q[3],'user_lr':q[4],
        'user_sp':q[5],'runtime':q[6],'stack':list(q[7:19]),
        'snapshot':u[0],'role':u[1],'pid':u[2],'tgid':u[3],'ppid':u[4],
        'state':u[5],'exit_state':u[6],'flags':u[7],'on_cpu':u[8],'on_rq':u[9],
        'cpu':u[10],'stack_nr':u[11],'nr_threads':u[12],'live_threads':u[13],
        'nvcsw':u[14],'nivcsw':u[15],'slot':u[16],
        'comm':cstr(comm),'wchan_symbol':cstr(wchan_sym),
        'raw':blob[off:off+SLOT_BYTES],
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('ramoops',type=Path,help='ramoops-raw-1MiB.bin')
    ap.add_argument('--system-map',type=Path)
    ns=ap.parse_args()

    b=ns.ramoops.read_bytes()
    if len(b) < SIDE_OFF + 0x8000:
        raise SystemExit(f'ramoops too small: {len(b)} bytes')

    recs=[]
    mismatch=[]
    for i in range(SLOTS):
        a=parse_slot(b,SIDE_OFF+i*SLOT_BYTES)
        z=parse_slot(b,SIDE_OFF+COPY_STRIDE+i*SLOT_BYTES)
        if a and z and a['raw'] != z['raw']:
            mismatch.append(i)
        recs.append(a or z)

    addrs,names=load_symbols(ns.system_map)
    slide=infer_slide(recs,addrs,names)
    valid=sum(r is not None for r in recs)
    print(f'Phase429 valid slots: {valid}/{SLOTS}; copy mismatches: {mismatch or "none"}')
    if slide is not None:
        print(f'inferred KASLR slide: 0x{slide:x}')

    for sid in range(SNAPS):
        group=[r for r in recs if r and r['snapshot']==sid]
        print(f'\n=== snapshot {sid} target={TARGETS[sid]}s records={len(group)} ===')
        for r in sorted(group,key=lambda x:x['slot']):
            role=ROLE.get(r['role'],str(r['role']))
            print(f"[{r['slot']}] {role} pid={r['pid']} tgid={r['tgid']} comm={r['comm']} "
                  f"state=0x{r['state']:x} cpu={r['cpu']} oncpu={r['on_cpu']} rq={r['on_rq']} "
                  f"w={r['wchan_symbol'] or hex(r['wchan'])} nv={r['nvcsw']}/{r['nivcsw']} "
                  f"runtime_ns={r['runtime']}")
            print(f"    user pc/lr/sp = 0x{r['user_pc']:x} 0x{r['user_lr']:x} 0x{r['user_sp']:x}")
            n=min(r['stack_nr'],12)
            for j,pc in enumerate(r['stack'][:n]):
                print(f"    K{j:02d} {sym_lookup(pc,addrs,names,slide)}")
    return 0


if __name__=='__main__':
    raise SystemExit(main())
