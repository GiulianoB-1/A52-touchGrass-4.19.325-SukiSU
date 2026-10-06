#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, struct
from pathlib import Path

MAGIC=b"ANDROID!"
EXTRA_OFF=608
EXTRA_LEN=1024
CMD_OFF=64
CMD_LEN=512

def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def cstr(b: bytes) -> bytes:
    return b.split(b"\0",1)[0]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--report",type=Path,required=True)
    ap.add_argument("--append",default="clk_ignore_unused")
    ap.add_argument("--expect-source-sha256")
    a=ap.parse_args()

    src=a.source.read_bytes()
    if a.expect_source_sha256 and sha(src)!=a.expect_source_sha256:
        raise SystemExit(f"source sha mismatch: {sha(src)} != {a.expect_source_sha256}")
    if len(src)<1660 or src[:8]!=MAGIC:
        raise SystemExit("not Android boot image")
    hdr_ver=struct.unpack_from("<I",src,40)[0]
    if hdr_ver!=2:
        raise SystemExit(f"expected boot header v2, got {hdr_ver}")

    cmd1=cstr(src[CMD_OFF:CMD_OFF+CMD_LEN])
    extra=cstr(src[EXTRA_OFF:EXTRA_OFF+EXTRA_LEN])
    token=a.append.encode("ascii")
    full=cmd1+extra
    words=full.decode("ascii","strict").split()
    if a.append in words:
        raise SystemExit(f"{a.append} already present")

    suffix=b" "+token
    if len(extra)+len(suffix) >= EXTRA_LEN:
        raise SystemExit("extra_cmdline has no room for token")

    out=bytearray(src)
    new_extra=extra+suffix
    out[EXTRA_OFF:EXTRA_OFF+EXTRA_LEN]=new_extra+b"\0"*(EXTRA_LEN-len(new_extra))
    out=bytes(out)

    # Strong one-variable audit: no byte outside extra_cmdline may change.
    if src[:EXTRA_OFF] != out[:EXTRA_OFF] or src[EXTRA_OFF+EXTRA_LEN:] != out[EXTRA_OFF+EXTRA_LEN:]:
        raise SystemExit("bytes outside extra_cmdline changed")
    if out[CMD_OFF:CMD_OFF+CMD_LEN] != src[CMD_OFF:CMD_OFF+CMD_LEN]:
        raise SystemExit("primary cmdline changed")
    if out[576:608] != src[576:608]:
        raise SystemExit("boot id changed")
    changed=[i for i,(x,y) in enumerate(zip(src,out)) if x!=y]
    if not changed or min(changed)<EXTRA_OFF or max(changed)>=EXTRA_OFF+EXTRA_LEN:
        raise SystemExit("unexpected changed-byte range")

    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_bytes(out)
    report={
      "status":"cmdline-only-patch",
      "token":a.append,
      "source_sha256":sha(src),
      "output_sha256":sha(out),
      "bytes":len(out),
      "primary_cmdline_unchanged":True,
      "boot_id_unchanged":True,
      "all_bytes_outside_extra_cmdline_unchanged":True,
      "changed_byte_count":len(changed),
      "changed_min_offset":min(changed),
      "changed_max_offset":max(changed),
      "original_cmdline":(cmd1+extra).decode("ascii","replace"),
      "new_cmdline":(cmd1+new_extra).decode("ascii","replace"),
    }
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report,indent=2,sort_keys=True))

if __name__=="__main__":
    main()
