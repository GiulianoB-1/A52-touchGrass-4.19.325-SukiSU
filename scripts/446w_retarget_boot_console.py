#!/usr/bin/env python3
"""Retarget ONLY the early ttyGS0 console to ttyGS3 in Android boot header v2.

Exactly one occurrence must exist among primary cmdline and extra_cmdline.
No other byte may change, including boot id, DTB, ramdisk, or payload.
Rollback is identical operation --from 3 --to 0.
"""
import argparse,hashlib,struct,json
from pathlib import Path
CMD_OFF,CMD_LEN=64,512
EXTRA_OFF,EXTRA_LEN=608,1024
def cstr(b):return b.split(b'\0',1)[0]
def run(src,frm,to):
    if src[:8]!=b"ANDROID!" or len(src)<2048 or struct.unpack_from('<I',src,40)[0]!=2:
        raise RuntimeError("expected Android header v2")
    old=f"console=ttyGS{frm},115200".encode()
    new=f"console=ttyGS{to},115200".encode()
    if len(old)!=len(new):raise RuntimeError("console replacement not length preserving")
    out=bytearray(src)
    matches=[]
    for off,size in ((CMD_OFF,CMD_LEN),(EXTRA_OFF,EXTRA_LEN)):
        text=cstr(src[off:off+size])
        if text.count(old):
            if text.count(old)!=1:raise RuntimeError("duplicate console token in field")
            pos=text.index(old)
            matches.append(off+pos)
    if len(matches)!=1:raise RuntimeError(f"expected one original console, got {len(matches)}")
    pos=matches[0]
    out[pos:pos+len(old)]=new
    if any(src[i]!=out[i] for i in range(len(src)) if not pos<=i<pos+len(old)):
        raise RuntimeError("other boot image bytes changed")
    if out[576:608]!=src[576:608]:raise RuntimeError("boot header ID changed")
    return bytes(out),pos
def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--from-line',type=int,default=0);p.add_argument('--to-line',type=int,default=3)
    a=p.parse_args();src=a.source.read_bytes()
    data,pos=run(src,a.from_line,a.to_line)
    a.output.write_bytes(data)
    rec={"status":"console-token-only","source_sha256":hashlib.sha256(src).hexdigest(),
         "output_sha256":hashlib.sha256(data).hexdigest(),
         "position":pos,"from":a.from_line,"to":a.to_line,
         "all_other_bytes_unchanged":True,"boot_id_unchanged":True}
    a.report.write_text(json.dumps(rec,sort_keys=True,indent=2)+"\n")
    print(json.dumps(rec))
if __name__=='__main__':main()
