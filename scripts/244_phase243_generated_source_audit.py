#!/usr/bin/env python3
"""Audit final generated Phase244 subsys-initcall/GDSC registration source."""
from __future__ import annotations
import sys
from pathlib import Path

REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
INIT=Path('init/main.c')
PLAT=Path('drivers/base/platform.c')
DD=Path('drivers/base/dd.c')
GDSC=Path('drivers/regulator/a52-legacy-gdsc-regulator.c')
BOOT='BOOT rs=ready phase=244 focus=gdsc-subsys-initcall roots=%u copies=3 crc=crc32c'
MARK='A52_PHASE244_GDSC_SUBSYS_INITCALL_IDENTITY_V1'


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
        ps=[r/x for x in (REC,INIT,PLAT,DD,GDSC)]
        if not all(p.is_file() for p in ps): continue
        if MARK not in ps[0].read_text(encoding='utf-8'): continue
        k=r.resolve()
        if k not in seen: seen.add(k); hits.append(r)
    if len(hits)!=1: raise RuntimeError('expected one generated Phase244 root, found '+(', '.join(map(str,hits)) or 'none'))
    return hits[0]


def audit(root,label):
    rec=(root/REC).read_text(encoding='utf-8'); init=(root/INIT).read_text(encoding='utf-8')
    plat=(root/PLAT).read_text(encoding='utf-8'); dd=(root/DD).read_text(encoding='utf-8'); gd=(root/GDSC).read_text(encoding='utf-8')
    for t in ('A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1','A52_PHASE243_CXGX_LIVE_SUPPLIER_V1',
              'A52_PHASE244_GDSC_SUBSYS_INITCALL_V1',MARK,BOOT,
              'strncmp(fmt, "CXF244", 6)','return !strncmp(message, "CXF244 ", 7) ||'):
        if t not in rec: raise RuntimeError(f'{label}: recorder missing {t}')
    for t in ('A52_PHASE244_SUBSYS_LEVEL_ENTRY_V1','#include <linux/a52_ack_secure_flight_recorder.h>',
              'int a52_r244_i;','if (level == 4)','a52_r244_i < 3','CXF244 V q=%d l=%d','initcall_levels[level]','"subsys",'):
        if t not in init: raise RuntimeError(f'{label}: init/main missing {t}')
    for t in ('A52_PHASE244_GDSC_INIT_REGISTER_V1','CXF244 I q=%d s=E','CXF244 I q=%d s=B',
              'CXF244 I q=%d s=X rc=%d','platform_driver_register(&a52_legacy_gdsc_driver)',
              'subsys_initcall(a52_legacy_gdsc_init)','A52GDSC driver-register enter','A52GDSC driver-register exit rc=%d',
              'A52GDSC CX_VDD_PARENT_GET_V1','A52GDSC CX_VDD_PARENT_VOTE_V1'):
        if t not in gd: raise RuntimeError(f'{label}: gdsc missing {t}')
    for text,tokens,name in (
        (plat,('A52_PHASE243_CXGX_LIVE_SUPPLIER_V1','CXF243 M c=%c q=%d rc=%d'),'platform'),
        (dd,('A52_PHASE243_CXGX_LIVE_SUPPLIER_V1','CXF243 R c=%c q=%d ls=%d','CXF243 G c=%c q=%d rc=%d'),'dd')):
        for t in tokens:
            if t not in text: raise RuntimeError(f'{label}: inherited {name} missing {t}')
    if 'return platform_driver_register(&a52_legacy_gdsc_driver)' in gd:
        raise RuntimeError(f'{label}: unexpected GDSC return-shape rewrite')
    print('Phase 244 generated-source audit: PASS',flush=True)


def self_test(): print('Phase 244 generated-source audit self-test: PASS',flush=True)

def main():
    if '--self-test' in sys.argv[1:]: self_test(); return 0
    r=locate(sys.argv[1:]); audit(r,str(r)); return 0
if __name__=='__main__': raise SystemExit(main())
