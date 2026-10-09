#!/usr/bin/env python3
"""Phase U1 A52 lagoon DTB high-speed USB2 patch.

Boot header v2 includes an *independent* stock DTB. Editing kernel DTS is
not enough. The two length-preserving modifications below are performed
inside the DTB *before* the boot image's SHA1 id is recomputed by repacker.

Changes only /soc/ssusb@a600000/dwc3@a600000:
 - usb-phy length 8 -> 4, replaced obsolete 2nd phandle by FDT_NOP token.
 - maximum-speed 12 -> 11, "super-speed" -> "high-speed".
No other node or physical address is touched. Strict preflight+reparse.
"""
import argparse
import struct
from pathlib import Path

MAGIC=0xd00dfeed
BEGIN, END_NODE, PROP, NOP, END = 1, 2, 3, 4, 9
TARGET = "/soc/ssusb@a600000/dwc3@a600000"

def u32(blob, off):
    return struct.unpack_from(">I", blob, off)[0]

def walk(blob):
    if len(blob) < 40 or u32(blob, 0) != MAGIC or u32(blob, 4) != len(blob):
        raise ValueError("not exactly one DTB, or DTB size mismatch")
    so, strings, total_strings, total_struct = (
        u32(blob, 8), u32(blob, 12), u32(blob, 32), u32(blob, 36))
    if so + total_struct > len(blob) or strings + total_strings > len(blob):
        raise ValueError("FDT boundaries invalid")
    path=[]; pos=so; stop=so+total_struct
    out=[]
    while pos + 4 <= stop:
        token = u32(blob, pos); pos+=4
        if token == BEGIN:
            e=blob.find(b"\0", pos, stop)
            if e<0: raise ValueError("unclosed node")
            path.append(blob[pos:e].decode("utf8"))
            pos=(e+4)&~3
        elif token == END_NODE:
            if not path: raise ValueError("unbalanced FDT")
            path.pop()
        elif token == PROP:
            if pos + 8 > stop: raise ValueError("bad FDT property")
            n, ix=struct.unpack_from(">II",blob,pos)
            len_off=pos; pos+=8
            if ix >= total_strings: raise ValueError("string index overrun")
            ep=blob.find(b"\0", strings+ix, strings+total_strings)
            if ep < 0: raise ValueError("unclosed property name")
            name=blob[strings+ix:ep].decode("utf8")
            if pos+n > stop: raise ValueError("property overrun")
            out.append(("/"+"/".join(x for x in path if x),name,len_off,pos,n,bytes(blob[pos:pos+n])))
            pos=(pos+n+3)&~3
        elif token == NOP:
            continue
        elif token == END:
            if path: raise ValueError("unfinished FDT nodes")
            return out
        else:
            raise ValueError(f"invalid FDT token {token} at {pos-4:#x}")
    raise ValueError("FDT_END missing")

def select(blob, prop):
    hits=[e for e in walk(blob) if e[0]==TARGET and e[1]==prop]
    if len(hits)!=1: raise ValueError(f"expected one {TARGET}/{prop}, got {len(hits)}")
    return hits[0]

def patch_lagoon_dtb(original: bytes) -> bytes:
    out=bytearray(original)
    usb=select(out,"usb-phy")
    _,_,loc,off,n,v=usb
    if n!=8 or v[0:4]==b"\0\0\0\0":
        raise ValueError("stock USB2+USB3 phandles not found")
    # Preserve the 4-byte USB2 phandle. Former second phandle becomes a NOP.
    struct.pack_into(">I",out,loc,4)
    struct.pack_into(">I",out,off+4,NOP)
    speed=select(out,"maximum-speed")
    _,_,loc2,off2,n2,v2=speed
    if n2!=12 or v2!=b"super-speed\0":
        raise ValueError("stock maximum-speed differs")
    struct.pack_into(">I",out,loc2,len(b"high-speed\0"))
    out[off2:off2+12]=b"high-speed\0\0"
    if not verify_lagoon_dtb(bytes(out)):
        raise ValueError("modified DTB failed FDT parser verification")
    return bytes(out)

def verify_lagoon_dtb(blob):
    ph=select(blob,"usb-phy")
    sp=select(blob,"maximum-speed")
    return ph[4]==4 and ph[5]!=b"\0\0\0\0" and sp[5]==b"high-speed\0"

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--dtb",type=Path,required=True)
    p.add_argument("--output",type=Path)
    p.add_argument("--verify-only",action="store_true")
    x=p.parse_args()
    data=x.dtb.read_bytes()
    if x.verify_only:
        if not verify_lagoon_dtb(data): raise SystemExit("Phase U1 DTB not patched")
        print("U1 DTB verified: USB2-only phandle and HS speed")
    else:
        data=patch_lagoon_dtb(data)
        if not x.output: raise SystemExit("--output required")
        x.output.write_bytes(data)
        print("U1 DTB patched with strict FDT validation")
if __name__=="__main__":
    main()
