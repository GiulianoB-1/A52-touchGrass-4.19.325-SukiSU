#!/usr/bin/env python3
"""P453 DAILY: surgical, audited pstore DTB repair in the final boot-v2 image.

Remove the redundant A52 reserved-memory node; retain the sole ramoops
consumer, install ecc-size=<16>, preserve 3x256KiB zones. This changes
only the embedded FDT and recalculated Android v2 boot id/DTB size.
"""
import argparse, hashlib, json, runpy, struct, subprocess, tempfile
from pathlib import Path

P=runpy.run_path("scripts/38_repack_a52_p1_boot.py")
parse=P["parse_boot"]; boot_id=P["calculate_boot_id"]; align=P["align"]
MARK="P453 RAMOOPS ECC16 DTB"
def run(*args, optional=False):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True)
    if p.returncode and not optional:
        raise RuntimeError(f"P453 FDT {args} failed: {p.stderr[-600:]}")
    return p.stdout.strip() if not p.returncode else None
def children(f,node):
    s=run("fdtget","-l",f,node,optional=True)
    return s.splitlines() if s else []
def nodes(f,parent="/",depth=0):
    if depth>30:raise RuntimeError("P453 excessive FDT depth")
    yield parent
    for kid in children(f,parent):
        yield from nodes(f,parent.rstrip("/")+"/"+kid,depth+1)
def cells(f,node,prop):
    s=run("fdtget","-t","x",f,node,prop,optional=True)
    return None if s is None else [int(v,16) for v in s.split()]
def inspect(f, patched=False):
    all_nodes=list(nodes(f))
    primary=[n for n in all_nodes if n.rsplit("/",1)[-1].lower().startswith("ramoops@")
             and "b1b00000" in n.lower()]
    if len(primary)!=1:
        raise RuntimeError("P453 expected exactly one main ramoops@B1B00000: "+repr(primary))
    node=primary[0]
    compat=run("fdtget","-t","s",f,node,"compatible",optional=True)
    if compat is None or "ramoops" not in compat.split():
        raise RuntimeError("P453 main node not a ramoops compatible: "+str(compat))
    duplicate=[n for n in all_nodes if n.rsplit("/",1)[-1].lower()
               =="a52-ramoops-reserved@b1b00000"]
    if len(duplicate)!=(0 if patched else 1):
        raise RuntimeError("P453 duplicate reserved node count "+str(len(duplicate)))
    sizes={k:cells(f,node,k) for k in ("record-size","console-size","pmsg-size")}
    for prop,vals in sizes.items():
        if vals!=[0x40000]:
            raise RuntimeError(f"P453 {prop} expected 256KiB, got {vals}")
    ecc=cells(f,node,"ecc-size")
    if patched and ecc!=[16]:
        raise RuntimeError("P453 ramoops ECC not 16: "+repr(ecc))
    reg=cells(f,node,"reg")
    if not reg or reg[-1]<0xc0000:
        raise RuntimeError("P453 ramoops region too small: "+repr(reg))
    return {"primary":node,"duplicate":duplicate,"reg":reg,
            "record_bytes":sizes["record-size"][0],
            "console_bytes":sizes["console-size"][0],
            "pmsg_bytes":sizes["pmsg-size"][0],"ecc_size":ecc}
def fix(raw):
    with tempfile.TemporaryDirectory() as tmp:
        f=Path(tmp)/"boot.dtb";f.write_bytes(raw)
        info=inspect(f,False)
        run("fdtput","-r",f,info["duplicate"][0])
        run("fdtput","-t","x",f,info["primary"],"ecc-size","0x10")
        post=inspect(f,True)
        out=f.read_bytes()
        if out==raw:raise RuntimeError("P453 DTB patch had no effect")
        return out,post
def repack(old,data,dtb):
    page=data["page_size"]; head=bytearray(data["header_page"])
    struct.pack_into("<I",head,1648,len(dtb))
    digest=boot_id([(data["kernel"],data["kernel_size"]),
                    (data["ramdisk"],data["ramdisk_size"]),
                    (data["second"],data["second_size"]),
                    (data["recovery_dtbo"],data["recovery_dtbo_size"]),
                    (dtb,len(dtb))])
    head[576:608]=digest
    dst=bytearray(head)
    for content in (data["kernel"],data["ramdisk"],data["second"],
                    data["recovery_dtbo"],dtb):
        dst+=content
        dst+=b"\0"*(align(len(dst),page)-len(dst))
    if len(dst)>len(old):raise RuntimeError("P453 exceeds boot partition")
    dst+=b"\0"*(len(old)-len(dst))
    final=parse(bytes(dst))
    for key in ("kernel","ramdisk","second","recovery_dtbo","cmdline"):
        if final[key]!=data[key]:raise RuntimeError("P453 changed "+key)
    if final["dtb"]!=dtb or final["id"]!=digest or len(dst)!=len(old):
        raise RuntimeError("P453 boot roundtrip mismatch")
    return bytes(dst)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--boot",type=Path,required=True)
    p.add_argument("--output",type=Path)
    p.add_argument("--report",type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    old=a.boot.read_bytes(); data=parse(old)
    if a.check_only:
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"final.dtb";f.write_bytes(data["dtb"])
            info=inspect(f,True)
        print(MARK+" VERIFIED "+json.dumps(info,sort_keys=True))
        return
    if not a.output or not a.report:raise RuntimeError("P453 needs output/report")
    dtb,info=fix(data["dtb"])
    result=repack(old,data,dtb)
    a.output.write_bytes(result)
    info.update({"tag":MARK,"boot_sha256":hashlib.sha256(result).hexdigest(),
                 "dtb_sha256":hashlib.sha256(dtb).hexdigest(),
                 "runtime_pstore_registered":False,
                 "accept_boot":"pstore: Registered ramoops"})
    a.report.write_text(json.dumps(info,indent=2,sort_keys=True)+"\n")
    print(MARK+" PACKAGED "+json.dumps(info,sort_keys=True))
if __name__=="__main__":main()
