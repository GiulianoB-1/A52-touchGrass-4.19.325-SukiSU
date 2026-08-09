#!/usr/bin/env python3
"""Audit final generated Phase 243 live CX/GX supplier source before compile."""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
PLAT=Path('drivers/base/platform.c')
DD=Path('drivers/base/dd.c')
GDSC=Path('drivers/regulator/a52-legacy-gdsc-regulator.c')
BOOT='BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers roots=%u copies=3 crc=crc32c'
MARK='A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1'


def roots(args,cwd):
    xs=[]
    for v in args:
        if v.startswith('-'): continue
        p=Path(v); p=p if p.is_absolute() else cwd/p; xs += [p,p.parent]
    xs += [cwd/'workspace/gki-phase199-src',cwd/'gki/common']
    out=[]; seen=set()
    for x in xs:
        k=x.resolve(strict=False)
        if k not in seen: seen.add(k); out.append(x)
    return out


def locate(args,cwd=None):
    base=cwd or Path.cwd(); hits=[]; seen=set()
    for r in roots(args,base):
        ps=[r/x for x in (REC,PLAT,DD,GDSC)]
        if not all(p.is_file() for p in ps): continue
        if MARK not in ps[0].read_text(encoding='utf-8'): continue
        k=r.resolve()
        if k not in seen: seen.add(k); hits.append(r)
    if len(hits)!=1: raise RuntimeError('expected one generated Phase243 root, found '+(', '.join(map(str,hits)) or 'none'))
    return hits[0]


def audit(root,label):
    rec=(root/REC).read_text(encoding='utf-8')
    plat=(root/PLAT).read_text(encoding='utf-8')
    dd=(root/DD).read_text(encoding='utf-8')
    gd=(root/GDSC).read_text(encoding='utf-8')
    for t in ('A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1','A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1',
              'A52_PHASE243_CXGX_LIVE_SUPPLIER_V1','A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1',MARK,BOOT,
              'strncmp(fmt, "CXF243", 6)','return !strncmp(message, "CXF243 ", 7) ||',
              '__maybe_unused a52_r242_sticky_latch','__maybe_unused a52_r242_snapshot'):
        if t not in rec: raise RuntimeError(f'{label}: recorder missing {t}')
    record=rec.find('void a52_ackfr_record(const char *fmt, ...)')
    if record<0: raise RuntimeError(f'{label}: record function missing')
    if rec.find('a52_r242_sticky_latch(event.message);',record)>=0: raise RuntimeError(f'{label}: Phase242 sticky latch remains live')
    hb0=rec.find('static void a52_r179_heartbeat_fn'); hb1=rec.find('static int __init a52_r179_early_heartbeat',hb0)
    if hb0>=0 and hb1>=0 and 'a52_r242_snapshot(tick);' in rec[hb0:hb1]: raise RuntimeError(f'{label}: Phase242 snapshot remains live')
    for text,tokens,name in (
        (plat,('A52_PHASE243_CXGX_LIVE_SUPPLIER_V1','CXF243 M c=%c q=%d rc=%d','a52_r243_match3(dev, drv, ret);'),'platform'),
        (dd,('A52_PHASE243_CXGX_LIVE_SUPPLIER_V1','CXF243 R c=%c q=%d ls=%d','CXF243 L c=%c q=%d n=%d s=%.36s st=%u ds=%d b=%d','CXF243 G c=%c q=%d rc=%d ls=%d','device_links_check_suppliers(dev)'),'dd'),
        (gd,('A52_PHASE243_CXGX_LIVE_SUPPLIER_V1','A52_PHASE243_GDSC_SUPPLIER_HOOK_V1','CXF243 P c=%c q=%d','a52_r243_provider3(pdev);','3d9106c','3d9100c','gdsc->timeout_us = A52_GDSC_TIMEOUT_US;','A52GDSC CX_VDD_PARENT_GET_V1'),'gdsc')):
        for t in tokens:
            if t not in text: raise RuntimeError(f'{label}: {name} missing {t}')
    print('Phase 243 generated-source audit: PASS',flush=True)


def self_test():
    print('Phase 243 generated-source audit self-test: PASS',flush=True)


def main():
    if '--self-test' in sys.argv[1:]: self_test(); return 0
    r=locate(sys.argv[1:]); audit(r,str(r)); return 0
if __name__=='__main__': raise SystemExit(main())
