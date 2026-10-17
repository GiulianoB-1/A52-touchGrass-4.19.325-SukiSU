#!/usr/bin/env python3
"""Switch 446U USB handover bit in the Android boot header-v2 extra cmdline.

Only the literal a52.usb_handover=0/1 byte changes. No repacking and no
DTB/ramdisk/kernel modifications. Run on a local copy of boot.img.
"""
import argparse,hashlib,struct
from pathlib import Path
OFF,LEN=608,1024
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--image",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--enable",choices=["0","1"],required=True)
    a=ap.parse_args()
    b=bytearray(a.image.read_bytes())
    if b[:8]!=b"ANDROID!" or struct.unpack_from("<I",b,40)[0]!=2:
        raise SystemExit("446U only supports the A52 Android boot v2 header")
    extra=bytes(b[OFF:OFF+LEN])
    raw=extra.split(b"\0",1)[0]
    tokens=raw.split()
    old=[t for t in tokens if t.startswith(b"a52.usb_handover=")]
    if len(old)!=1 or old[0] not in (b"a52.usb_handover=0",b"a52.usb_handover=1"):
        raise SystemExit("446U boot image requires exactly one handover 0/1 flag")
    new=b"a52.usb_handover="+a.enable.encode()
    pos=raw.index(old[0])
    newraw=raw[:pos]+new+raw[pos+len(old[0]):]
    assert len(newraw)==len(raw)
    b[OFF:OFF+LEN]=newraw+b"\0"*(LEN-len(newraw))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_bytes(b)
    print("P446U USB TOGGLE enable=%s sha256=%s output=%s"%(a.enable,hashlib.sha256(b).hexdigest(),a.output))
if __name__=="__main__":main()
