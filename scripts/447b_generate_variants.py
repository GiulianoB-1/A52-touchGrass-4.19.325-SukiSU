#!/usr/bin/env python3
"""Phase447B: three byte-audited Android boot v2 cmdline-only variants.

This produces distinct flashable candidates, not an assertion that drivers are
ported or functional. All use exactly the same kernel, DTB, ramdisk, KSU pin.
"""
import argparse,hashlib,importlib.util,json,struct
from pathlib import Path
MAGIC=b"ANDROID!"
CMD_OFF=64;CMD_LEN=512;EXTRA_OFF=608;EXTRA_LEN=1024
VARIANTS={
 "A-wave2-battery-only":("a52.wave=2",),
 "B-wave3-dsp-no-mss":("a52.wave=3","a52.mss=0","a52.ipa=0"),
 "C-wave3-modem-ipa":("a52.wave=3",),
}
def hash256(a):return hashlib.sha256(a).hexdigest()
def cs(a):return a.split(b"\x00",1)[0].decode("ascii")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--parser",type=Path,default=Path("scripts/38_repack_a52_p1_boot.py"))
    p.add_argument("--require-ports",action="store_true",
                   help="Only package as functional candidates when a checked port certification exists")
    p.add_argument("--certification",type=Path,default=Path("merge/447b-port-certification.json"))
    a=p.parse_args()
    data=a.source.read_bytes()
    if len(data)!=100663296 or not data.startswith(MAGIC) or struct.unpack_from("<I",data,40)[0]!=2:
        raise RuntimeError("P447B expected 96MiB Android boot v2")
    spec=importlib.util.spec_from_file_location("a52_boot",a.parser)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    source=m.parse_boot(data)
    if b"a52.446w_handoff=0" not in data or b"a52.touch=1" not in data:
        raise RuntimeError("P447B must preserve original display and touch config")
    first=cs(data[CMD_OFF:CMD_OFF+CMD_LEN].strip(b"\0"))
    extra=cs(data[EXTRA_OFF:EXTRA_OFF+EXTRA_LEN])
    tokens=set((first+" "+extra).split())
    if any(t.startswith(("a52.wave=","a52.mss=","a52.ipa=")) for t in tokens):
        raise RuntimeError("P447B input already has incompatible wave overrides")
    certified=False
    if a.certification.exists():
        c=json.loads(a.certification.read_text())
        certified=bool(c.get("wave2_runtime_ready") and c.get("wave3_runtime_ready") and c.get("no_nv_write_audited"))
    if a.require_ports and not certified:
        raise RuntimeError("P447B cannot label Wave2/3 functional: verified ports and NV audit missing")
    a.out.mkdir(parents=True,exist_ok=True)
    results={}
    for variant,add in VARIANTS.items():
        blob=bytearray(data)
        newextra=extra+" "+" ".join(add)
        if len(newextra.encode("ascii"))>=EXTRA_LEN:
            raise RuntimeError("P447B extra_cmdline would overflow")
        blob[EXTRA_OFF:EXTRA_OFF+EXTRA_LEN]=newextra.encode()+b"\0"*(EXTRA_LEN-len(newextra))
        rebuilt=bytes(blob)
        outpart=m.parse_boot(rebuilt)
        for key in ("kernel","ramdisk","second","recovery_dtbo","dtb","id"):
            if outpart[key]!=source[key]:raise RuntimeError("P447B "+key+" drift on "+variant)
        if rebuilt[:EXTRA_OFF]!=data[:EXTRA_OFF] or rebuilt[EXTRA_OFF+EXTRA_LEN:]!=data[EXTRA_OFF+EXTRA_LEN:]:
            raise RuntimeError("P447B changed bytes outside extra_cmdline")
        actual=set(outpart["cmdline"].split())
        if not set(add).issubset(actual):raise RuntimeError("P447B token missing "+variant)
        for other,otheradd in VARIANTS.items():
            if other==variant:continue
            must_absent=set(otheradd)-set(add)
            if must_absent.intersection(actual):raise RuntimeError("P447B conflicting "+variant)
        dest=a.out/(variant+".boot.img")
        dest.write_bytes(rebuilt)
        results[variant]={
            "cmdline_added":list(add),
            "sha256":hash256(rebuilt),
            "bytes":len(rebuilt),
            "kernel_sha256":hash256(outpart["kernel"]),
            "dtb_sha256":hash256(outpart["dtb"]),
            "ramdisk_sha256":hash256(outpart["ramdisk"]),
            "unchanged_except_cmdline":True,
            "drivers_functional_certified":certified,
            "functionality_warning":None if certified else
               "EXPERIMENTAL SWITCHES ONLY: no verified working Wave2/3 driver ports. This DOES NOT make battery, DSP or modem functional."
        }
        print(f"P447B VARIANT {variant} PASS cmdline={' '.join(add)} sha256={results[variant]['sha256']}")
    (a.out/"variants.json").write_text(json.dumps(results,indent=2,sort_keys=True)+"\n")
    (a.out/"READ-BEFORE-FLASH.txt").write_text(
        "P447B three cmdline-only boot image experiments.\n"
        "A: a52.wave=2\nB: a52.wave=3 a52.mss=0 a52.ipa=0\nC: a52.wave=3\n"
        "DO NOT ASSUME battery charging or modem functionality from gate flags.\n"
        "Always retain a known-good recovery/boot path. No Wave2/3 driver port is certified in this build.\n"
    )
if __name__=="__main__":main()
