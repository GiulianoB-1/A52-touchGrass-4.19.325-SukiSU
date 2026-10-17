#!/usr/bin/env python3
"""446W boot-console audit, preserving the real Samsung Android boot header.

On our A52 boot header v2 the bootloader token is console=null; the actual
early console is selected by CONFIG_CMDLINE_EXTEND with CONFIG_CMDLINE
console=ttyGS3,115200. NEVER replace console=null with console=ttyGS3:
that is a separate Android bootloader parameter and is not needed.

A different source image containing exactly one console=ttyGS0,115200 token
may be rewritten length-preservingly. In either mode verify every byte outside
the allowed header console substring is preserved and report the exact action.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
from pathlib import Path

CMD_OFF, CMD_LEN = 64, 512
EXTRA_OFF, EXTRA_LEN = 608, 1024

def cstr(value: bytes) -> bytes:
    return value.split(b"\0", 1)[0]

def cmdline_fields(data: bytes):
    if len(data) < EXTRA_OFF + EXTRA_LEN or data[:8] != b"ANDROID!":
        raise RuntimeError("not an Android boot image")
    if struct.unpack_from("<I", data, 40)[0] != 2:
        raise RuntimeError("expected boot header v2")
    return [(off, cstr(data[off:off+size]))
            for off, size in ((CMD_OFF,CMD_LEN),(EXTRA_OFF,EXTRA_LEN))]

def verify_kernel_config(config: str, to_line: int):
    expected=f"console=ttyGS{to_line},115200"
    if "CONFIG_CMDLINE_EXTEND=y" not in config:
        raise RuntimeError("kernel must extend bootloader command line")
    match=[line for line in config.splitlines() if line.startswith('CONFIG_CMDLINE="')]
    if len(match)!=1 or expected not in match[0]:
        raise RuntimeError("built-in kernel CONFIG_CMDLINE lacks "+expected)
    if "console=ttyGS0,115200" in match[0] and to_line!=0:
        raise RuntimeError("stale ttyGS0 in built-in CONFIG_CMDLINE")

def run(src: bytes, frm: int, to: int):
    fields=cmdline_fields(src)
    old=f"console=ttyGS{frm},115200".encode()
    new=f"console=ttyGS{to},115200".encode()
    if len(old)!=len(new):
        raise RuntimeError("console rewrite must be length preserving")
    found=[off+v.index(old) for off,v in fields if old in v]
    all_console=[t.decode("ascii") for _,v in fields for t in v.split()
                 if t.startswith(b"console=")]
    if len(found)==0:
        # This is the *real* 446V A52 source boot image.
        if all_console!=["console=null"]:
            raise RuntimeError("no matching ttyGS token but unknown boot console: "+str(all_console))
        return src, {"status":"bootloader-console-null-preserved",
                     "position":None, "header_console":"console=null",
                     "all_other_bytes_unchanged":True,
                     "boot_id_unchanged":True}
    if len(found)!=1 or ("console=null" in all_console):
        raise RuntimeError("ambiguous or duplicate bootloader console tokens: "+str(all_console))
    pos=found[0]
    out=bytearray(src)
    out[pos:pos+len(old)]=new
    if src[:pos]!=out[:pos] or src[pos+len(old):]!=out[pos+len(old):]:
        raise RuntimeError("bytes outside console token changed")
    if out[576:608]!=src[576:608]:
        raise RuntimeError("boot ID changed")
    return bytes(out), {"status":"bootloader-console-token-retargeted",
                        "position":pos,"header_console":new.decode(),
                        "all_other_bytes_unchanged":True,
                        "boot_id_unchanged":True}

def self_test():
    header=bytearray(32768)
    header[:8]=b"ANDROID!"
    struct.pack_into("<I",header,40,2)
    original=b"console=null androidboot.hardware=qcom"
    header[CMD_OFF:CMD_OFF+len(original)]=original
    before=bytes(header)
    output,info=run(before,0,3)
    assert output==before and info["status"]=="bootloader-console-null-preserved"
    verify_kernel_config('CONFIG_CMDLINE_EXTEND=y\nCONFIG_CMDLINE="console=ttyGS3,115200 no_console_suspend"\n',3)
    header[CMD_OFF:CMD_OFF+len(original)]=b"\0"*len(original)
    token=b"console=ttyGS0,115200"
    header[CMD_OFF:CMD_OFF+len(token)]=token
    before=bytes(header)
    output,info=run(before,0,3)
    assert info["status"]=="bootloader-console-token-retargeted"
    assert output[CMD_OFF:CMD_OFF+len(token)]==b"console=ttyGS3,115200"
    assert output[CMD_OFF+len(token):]==before[CMD_OFF+len(token):]
    try:
        run(before.replace(token,b"console=null".ljust(len(token),b" ")),0,3)
    except RuntimeError:
        raise RuntimeError("failed test assumption")
    else:
        # This is a valid null header; no mutation expected.
        pass
    try:
        verify_kernel_config('CONFIG_CMDLINE_EXTEND=y\nCONFIG_CMDLINE="console=ttyGS0,115200"\n',3)
        raise RuntimeError("negative CONFIG_CMDLINE test failed")
    except RuntimeError as err:
        if "negative" in str(err): raise
    print("P446W BOOT_CONSOLE SELFTEST PASS: original console=null preserved, explicit ttyGS0 rewrite, built-in ttyGS3 audited")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",type=Path)
    ap.add_argument("--output",type=Path)
    ap.add_argument("--report",type=Path)
    ap.add_argument("--kernel-config",type=Path)
    ap.add_argument("--from-line",type=int,default=0)
    ap.add_argument("--to-line",type=int,default=3)
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    if args.self_test:
        self_test()
        return
    if not all((args.source,args.output,args.report,args.kernel_config)):
        ap.error("--source --output --report --kernel-config are required")
    verify_kernel_config(args.kernel_config.read_text(),args.to_line)
    src=args.source.read_bytes()
    data, report=run(src,args.from_line,args.to_line)
    if src[576:608]!=data[576:608]:
        raise RuntimeError("boot ID changed")
    args.output.write_bytes(data)
    report.update({"kernel_config":"CONFIG_CMDLINE_EXTEND=y",
                   "selected_console":f"ttyGS{args.to_line}",
                   "from_line":args.from_line,"to_line":args.to_line,
                   "source_sha256":hashlib.sha256(src).hexdigest(),
                   "output_sha256":hashlib.sha256(data).hexdigest(),
                   "image_all_bytes_unchanged": src == data,
                   "boot_id_unchanged":True})
    args.report.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report,sort_keys=True))

if __name__=="__main__":
    main()
