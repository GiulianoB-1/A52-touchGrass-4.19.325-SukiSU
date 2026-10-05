#!/usr/bin/env python3
from __future__ import annotations
import argparse,struct,zipfile
from pathlib import Path
PHYS=0xB1AFA000;BYTES=0x2000;COPY=0x1000;SLOT=128;SLOTS=32
MAGIC=0x3733344543415254;COMMIT=0x437c0de5;VER=1
FMT='<10Q6I16sII';SIZE=struct.calcsize(FMT);CRC_OFF=120
STAGES=['META','O0 target','O1 ack pre','O2 ack post','O3 getfd pre','O4 getfd post','O5 do_filp_open pre','O6 do_filp_open post','N0 do_filp_open entry','N1 path_openat entry','N2 alloc_empty_file post','N3 do_open pre','N4 do_open entry','N5 complete_walk post','N6 may_open post','N7 vfs_open pre','V0 vfs_open entry','D0 do_dentry_open entry','D1 inode fops pre','D2 inode fops post','D3 security_file_open pre','D4 security_file_open post','D5 f_op open pre','C0 chrdev_open entry','C1 kobj_lookup pre','C2 kobj_lookup post','C3 cdev fops_get pre','C4 cdev fops_get post','C5 driver open pre','C6 driver open post','D6 f_op open post','O7 open end']
assert len(STAGES)==32 and SIZE==128

def crc32c(data):
 c=0xffffffff
 for b in data:
  c^=b
  for _ in range(8):c=(c>>1)^(0x82f63b78 if c&1 else 0)
 return (~c)&0xffffffff

def parse(b):
 if len(b)!=SIZE:return None
 v=struct.unpack(FMT,b); names=['magic','boot','bt','cnt','a0','a1','pc','lr','sp','pstate','ver','stage','cpu','pid','tgid','slot','comm','crc','commit'];r=dict(zip(names,v));r['comm']=r['comm'].split(b'\0',1)[0].decode(errors='replace');r['raw']=b
 r['struct_ok']=r['magic']==MAGIC and r['ver']==VER and r['stage']<SLOTS and r['slot']==r['stage'] and r['commit']==COMMIT
 r['crc_ok']=r['struct_ok'] and crc32c(b[:CRC_OFF])==r['crc'];return r

def locate(path):
 if path.suffix.lower()=='.zip':
  with zipfile.ZipFile(path) as z:
   for suffix,base in [('phase389-B1400000-B1AFFFFF-7MiB.bin',PHYS-0xB1400000),('samsung-reserved-B1000000-B1AFFFFF-11MiB.bin',PHYS-0xB1000000)]:
    p=[n for n in z.namelist() if n.endswith(suffix)]
    if p:return z.read(p[0]),p[0],base
  raise SystemExit('Phase437: ZIP lacks reserved-RAM dump')
 d=path.read_bytes()
 if len(d)==7*1024*1024:return d,str(path),PHYS-0xB1400000
 if len(d)==11*1024*1024:return d,str(path),PHYS-0xB1000000
 if len(d)>=BYTES:return d,str(path),0
 raise SystemExit('Phase437: unsupported input size')

def choose(a,b,stage,boot=None):
 rows=[parse(a),parse(b)]
 valid=[r for r in rows if r and r['crc_ok'] and (boot is None or r['boot']==boot)]
 if valid:return valid[0],'crc'
 structural=[r for r in rows if r and r['struct_ok'] and (boot is None or r['boot']==boot)]
 if len(structural)==2 and structural[0]['raw']==structural[1]['raw']:
  return structural[0],'dual-identical-crc-bad'
 return None,'none'

def main():
 ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ns=ap.parse_args();data,src,base=locate(ns.input);reg=data[base:base+BYTES]
 if len(reg)!=BYTES:raise SystemExit('Phase437: truncated sideband')
 m,ms=choose(reg[0:SLOT],reg[COPY:COPY+SLOT],0)
 print('source='+src)
 if not m:
  print('meta_valid=False');return 2
 boot=m['boot'];freq=m['a0'];print(f'boot_id=0x{boot:016x} cntfrq={freq}Hz phys=0x{PHYS:x} bytes=0x{BYTES:x} meta={ms}')
 rows=[]
 for st in range(SLOTS):
  a=reg[st*SLOT:(st+1)*SLOT];b=reg[COPY+st*SLOT:COPY+(st+1)*SLOT];r,how=choose(a,b,st,boot)
  if r:r['recovery']=how;rows.append(r)
 rows.sort(key=lambda r:(r['cnt'],r['stage']))
 print('\nARCH COUNTER ORDER:');prev=None
 for r in rows:
  dt=''
  if prev is not None and freq:dt=f' +{(r["cnt"]-prev)/freq*1e6:9.3f}us'
  prev=r['cnt'];warn='' if r['recovery']=='crc' else ' RECOVERED'
  print(f'{r["cnt"]:16d}{dt} {r["bt"]/1e9:12.9f}s S{r["stage"]:02d} {STAGES[r["stage"]]:24s} cpu={r["cpu"]} p={r["pid"]}/{r["tgid"]} {r["comm"]:15s} a0={r["a0"]:#x} a1={r["a1"]:#x}{warn}')
 print('\nSUMMARY:')
 hits={r['stage'] for r in rows}
 if len(rows)>1:
  last=rows[-1];print(f'Last hit: S{last["stage"]} {STAGES[last["stage"]]} at {last["bt"]/1e9:.9f}s')
 else:print('No open-corridor stage hit')
 for a,b,label in [(2,3,'legacy recorder call'),(4,5,'get_unused_fd_flags'),(6,7,'do_filp_open return'),(20,21,'security_file_open'),(24,25,'kobj_lookup'),(26,27,'cdev fops_get'),(28,29,'char driver ->open'),(22,30,'dentry f_op->open')]:
  if a in hits and b not in hits:print('STOP WINDOW: '+label+f' (S{a} hit, S{b} missing)')
 missing=[i for i in range(1,SLOTS) if i not in hits]
 if missing:print('First unhit stage by logical corridor:',f'S{missing[0]} {STAGES[missing[0]]}')
 return 0
if __name__=='__main__':raise SystemExit(main())
