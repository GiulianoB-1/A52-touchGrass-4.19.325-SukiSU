#!/usr/bin/env python3
"""P454: SCM-off bisect from exact #487 G boot, optional ECC16 pstore repair.

No kernel rebuild; only embedded DTB bytes and Android-v2 SHA1 boot-id change.
"""
import argparse, hashlib, json, runpy, struct
from pathlib import Path
from u1_patch_boot_dtb import walk

PARSE=runpy.run_path("scripts/38_repack_a52_p1_boot.py")
parse=PARSE["parse_boot"]
boot_id=PARSE["calculate_boot_id"]
SOURCE_SHA="c633875defc4ff7d573fd73d4d14b631ef17027d9cf1dd0e7ff9317fb7b5de2e"
SCM="/firmware/scm"
RAM="/reserved-memory/a52-ramoops-reserved@b1b00000"
u32=lambda b,i: struct.unpack_from(">I",b,i)[0]

def props(b):
    p={}
    for path,name,_,off,n,value in walk(b):
        key=(path,name)
        if key in p:raise ValueError("duplicate FDT property "+str(key))
        p[key]=(off,bytes(value))
    return p

def scm_span(b):
    pos=u32(b,8);end=pos+u32(b,36);stack=[];found=[]
    while pos<end:
        token_start=pos;tag=u32(b,pos);pos+=4
        if tag==1:
            e=b.find(b"\0",pos,end)
            if e<0:raise ValueError("unterminated FDT node")
            name=b[pos:e].decode("utf8");pos=(e+4)&~3
            path="/"+"/".join(x[0] for x in stack if x[0])
            path=(path.rstrip("/")+"/"+name).rstrip("/") or "/"
            stack.append((name,path))
            if path==SCM:found.append([token_start,None])
        elif tag==2:
            if not stack:raise ValueError("unbalanced DTB")
            if stack[-1][1]==SCM:
                if len(found)!=1:raise ValueError("duplicate SCM")
                found[0][1]=pos
            stack.pop()
        elif tag==3:
            n=u32(b,pos);pos=(pos+8+n+3)&~3
        elif tag==4:pass
        elif tag==9:
            if stack:raise ValueError("unclosed DTB")
            break
        else:raise ValueError("unknown FDT token")
    if len(found)!=1 or found[0][1] is None:raise ValueError("SCM node not found exactly once")
    start,stop=found[0]
    if (stop-start)%4:raise ValueError("SCM node unaligned")
    return start,stop

def transform(dtb,ecc):
    p=props(dtb)
    if p[(SCM,"compatible")][1]!=b"qcom,scm-sm6350\0qcom,scm\0":
        raise ValueError("unexpected SCM compatible")
    if any(path.startswith(SCM+"/") for path,name in p):
        raise ValueError("SCM node unexpectedly has children")
    if p[(RAM,"compatible")][1]!=b"ramoops\0":
        raise ValueError("active ramoops missing")
    if p[(RAM,"reg")][1]!=struct.pack(">4I",0,0xb1b00000,0,0x100000):
        raise ValueError("unexpected ramoops carveout")
    original={"ecc-size":0,"record-size":0x10000,"console-size":0x30000,
              "pmsg-size":0xc0000,"ftrace-size":0}
    for name,value in original.items():
        if p[(RAM,name)][1]!=struct.pack(">I",value):
            raise ValueError("unexpected original ramoops "+name)
    first,last=scm_span(dtb)
    out=bytearray(dtb)
    out[first:last]=struct.pack(">I",4)*((last-first)//4)
    edits={"ecc-size":16,"record-size":0x40000,"console-size":0x40000,
           "pmsg-size":0x40000} if ecc else {}
    for name,value in edits.items():
        struct.pack_into(">I",out,p[(RAM,name)][0],value)
    q=props(bytes(out))
    if set(q)!={k for k in p if k[0]!=SCM}:
        raise ValueError("unintended DTB properties deleted or added")
    for key,(offset,value) in q.items():
        expected=struct.pack(">I",edits[key[1]]) if key[0]==RAM and key[1] in edits else p[key][1]
        if value!=expected:raise ValueError("unexpected DTB change "+str(key))
    return bytes(out)

def build(original,ecc):
    if hashlib.sha256(original).hexdigest()!=SOURCE_SHA:
        raise ValueError("input is not the verified G #487 artifact")
    a=parse(original)
    if a["header_version"]!=2 or a["dtb_size"]!=len(a["dtb"]):
        raise ValueError("unsupported Android boot")
    dtb=transform(a["dtb"],ecc)
    if len(dtb)!=len(a["dtb"]):raise ValueError("FDT size drift")
    out=bytearray(original)
    pos=a["dtb_offset"];out[pos:pos+len(dtb)]=dtb
    digest=boot_id([(a["kernel"],a["kernel_size"]),(a["ramdisk"],a["ramdisk_size"]),
                    (a["second"],a["second_size"]),
                    (a["recovery_dtbo"],a["recovery_dtbo_size"]),(dtb,len(dtb))])
    out[576:608]=digest;out=bytes(out);b=parse(out)
    for name in ("kernel","ramdisk","second","recovery_dtbo","cmdline"):
        if a[name]!=b[name]:raise ValueError("non-DTB component changed "+name)
    if b["dtb"]!=dtb or b["id"]!=digest:
        raise ValueError("boot-id/DTB reparse failed")
    if out[:576]!=original[:576] or out[608:pos]!=original[608:pos] or out[pos+len(dtb):]!=original[pos+len(dtb):]:
        raise ValueError("modification outside DTB/boot-id")
    return out,{"source_sha256":SOURCE_SHA,"image_sha256":hashlib.sha256(out).hexdigest(),
                "kernel_sha256":hashlib.sha256(b["kernel"]).hexdigest(),
                "scm_node_present":False,"ramoops_ecc_size":16 if ecc else 0,
                "record_size":0x40000 if ecc else 0x10000,
                "console_size":0x40000 if ecc else 0x30000,
                "pmsg_size":0x40000 if ecc else 0xc0000,
                "ramoops_phys":"0xB1B00000","ramoops_region_size":0x100000,
                "kernel_and_ramdisk_identical":True,
                "charger_h0":"unchanged; manual opt-in; do not arm",
                "hardware_validated":False}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    x=p.parse_args();raw=x.source.read_bytes();x.out.mkdir(parents=True,exist_ok=True)
    reports=[]
    for label,ecc in (("SCM-OFF",False),("SCM-OFF-PSTORE-ECC16",True)):
        new,meta=build(raw,ecc);name="MANGROVE-452-"+label+"-487.boot.img"
        (x.out/name).write_bytes(new)
        meta["filename"]=name;reports.append(meta)
        print("P454 VERIFIED",name,meta["image_sha256"])
    (x.out/"P454-MANIFEST.json").write_text(json.dumps(reports,indent=2,sort_keys=True)+"\n")
if __name__=="__main__":main()
