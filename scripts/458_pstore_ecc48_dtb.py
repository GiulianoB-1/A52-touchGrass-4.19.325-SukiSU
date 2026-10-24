#!/usr/bin/env python3
"""P458 DAILY: surgical, audited pstore DTB repair in the final boot-v2 image.

Convert reservation-only A52 DTBs into a single active ramoops consumer,
install ecc-size=<48>, and preserve actual vendor record/console/pmsg sizes. This changes
only the embedded FDT and recalculated Android v2 boot id/DTB size.
"""
import argparse, hashlib, json, runpy, struct, subprocess, tempfile
from pathlib import Path

P=runpy.run_path("scripts/38_repack_a52_p1_boot.py")
parse=P["parse_boot"]; boot_id=P["calculate_boot_id"]; align=P["align"]
MARK="P458 RAMOOPS ECC48 DTB"
def run(*args, optional=False):
    p=subprocess.run(list(map(str,args)),capture_output=True,text=True)
    if p.returncode and not optional:
        raise RuntimeError(f"P458 FDT {args} failed: {p.stderr[-600:]}")
    return p.stdout.strip() if not p.returncode else None
def children(f,node):
    s=run("fdtget","-l",f,node,optional=True)
    return s.splitlines() if s else []
def nodes(f,parent="/",depth=0):
    if depth>30:raise RuntimeError("P458 excessive FDT depth")
    yield parent
    for kid in children(f,parent):
        yield from nodes(f,parent.rstrip("/")+"/"+kid,depth+1)
def cells(f,node,prop):
    s=run("fdtget","-t","x",f,node,prop,optional=True)
    return None if s is None else [int(v,16) for v in s.split()]
def inspect(f, patched=False):
    # Exact CI evidence: the packaged TG DTB can contain ONLY the
    # inert a52-ramoops-reserved@b1b00000 node, without compatible=ramoops.
    # Runtime overlays/platform devices must not be inferred from a DTB.
    from u1_patch_boot_dtb import walk
    props=walk(f.read_bytes())
    all_nodes=sorted({entry[0] for entry in props})
    compatible=sorted({e[0] for e in props if e[1]=="compatible"
                       and b"ramoops" in e[5].split(b"\x00")})
    named=[n for n in all_nodes if
           n.rsplit("/",1)[-1].lower()=="a52-ramoops-reserved@b1b00000"]
    # A DT node can be both named "a52-ramoops-reserved" AND have the
    # ramoops compatible property. That is ONE node, not an overlap.
    # Delete only an INDEPENDENT, non-compatible reserved-memory node.
    inert=[n for n in named if n not in compatible]
    if patched:
        if len(compatible)!=1 or inert:
            raise RuntimeError("P458 final FDT must have exactly one active ramoops, no inert duplicate: "+
                               repr((compatible,inert)))
        node=compatible[0]
    else:
        if len(compatible)>1 or len(inert)>1 or not (compatible or inert):
            raise RuntimeError("P458 unexpected source ramoops topology: "+
                               repr((compatible,inert)))
        if compatible and inert and compatible[0]==inert[0]:
            raise RuntimeError("P458 internal duplicate classification error")
        node=compatible[0] if compatible else inert[0]
    reg=cells(f,node,"reg")
    if reg!=[0,0xb1b00000,0,0x100000]:
        raise RuntimeError("P458 unexpected A52 RAMOOPS region "+repr(reg))
    if patched or compatible:
        sizes={k:cells(f,node,k) for k in
               ("record-size","console-size","pmsg-size")}
        for prop,v in sizes.items():
            if (v is None or len(v)!=1 or v[0]<4096 or
                v[0]>reg[-1] or v[0] % 4096 != 0):
                raise RuntimeError("P458 invalid "+prop+" zone size "+repr(v))
        # 5.10 fs/pstore/ram.c explicitly rounds non-power-of-two zone
        # sizes DOWN. 192KiB vendor console becomes 128KiB effectively.
        # Preserve the actual DT properties; do not rewrite the layout.
        actual_total=sum(v[0] for v in sizes.values())
        effective_total=sum(1 << (v[0].bit_length()-1) for v in sizes.values())
        if actual_total>reg[-1]:
            raise RuntimeError("P458 declared zones exceed ramoops region")
        if effective_total>reg[-1]:
            raise RuntimeError("P458 effective zones exceed ramoops region")
    else:
        sizes=None
    ecc=cells(f,node,"ecc-size")
    if patched and ecc!=[48]:
        raise RuntimeError("P458 ECC48 missing: "+repr(ecc))
    return {"primary":compatible[0] if compatible else None,
            "duplicate":inert,"reg":reg,
            "topology":"active-plus-inert" if compatible and inert else
                       "active-named-reserved" if compatible and named else
                       "active" if compatible else "inert-only",
            "record_bytes":sizes["record-size"][0] if sizes else None,
            "console_bytes":sizes["console-size"][0] if sizes else None,
            "pmsg_bytes":sizes["pmsg-size"][0] if sizes else None,
            "effective_console_bytes":(1 << (sizes["console-size"][0].bit_length()-1)) if sizes else None,
            "ecc_size":ecc}
def fix(raw):
    with tempfile.TemporaryDirectory() as tmp:
        f=Path(tmp)/"boot.dtb"; f.write_bytes(raw)
        info=inspect(f,False)
        for unused in info["duplicate"]:
            run("fdtput","-r",f,unused)
        if info["primary"] is None:
            # The original DTB has ONLY a non-compatible reserved node.
            # Convert the existing physical region into a single driver-
            # discoverable ramoops child. Never reserve it twice.
            n="/reserved-memory/ramoops@b1b00000"
            run("fdtput","-c",f,n)
            run("fdtput","-t","s",f,n,"compatible","ramoops")
            run("fdtput","-t","x",f,n,"reg",
                *[hex(x) for x in info["reg"]])
            for prop in ("record-size","console-size","ftrace-size",
                         "pmsg-size"):
                run("fdtput","-t","x",f,n,prop,"0x40000")
            info["converted_inert_to_active"]=True
        else:
            n=info["primary"]
            info["converted_inert_to_active"]=False
        run("fdtput","-t","x",f,n,"ecc-size","0x30")
        post=inspect(f,True)
        if info["primary"] is not None:
            for k in ("record_bytes","console_bytes","pmsg_bytes","reg"):
                if post[k]!=info[k]:
                    raise RuntimeError("P458 vendor ramoops property changed: "+k)
        out=f.read_bytes()
        if out==raw:raise RuntimeError("P458 DTB patch had no effect")
        post["converted_inert_to_active"]=info["converted_inert_to_active"]
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
    if len(dst)>len(old):raise RuntimeError("P458 exceeds boot partition")
    dst+=b"\0"*(len(old)-len(dst))
    final=parse(bytes(dst))
    for key in ("kernel","ramdisk","second","recovery_dtbo","cmdline"):
        if final[key]!=data[key]:raise RuntimeError("P458 changed "+key)
    if final["dtb"]!=dtb or final["id"]!=digest or len(dst)!=len(old):
        raise RuntimeError("P458 boot roundtrip mismatch")
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
    if not a.output or not a.report:raise RuntimeError("P458 needs output/report")
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
