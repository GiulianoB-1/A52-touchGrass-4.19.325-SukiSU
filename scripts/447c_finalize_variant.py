#!/usr/bin/env python3
"""Phase447C: one independently compiled Image per A/B/C, safe Android v2 cmdline.

Do NOT create three cmdline-only copies of the same Image. P447C verifies
the matching final .config and rewrites the primary header cmdline, where
Samsung ABL actually looked on the previous boots. No firmware/DT writes.
"""
import argparse,hashlib,importlib.util,json,struct
from pathlib import Path
CMD_OFF,CMD_LEN,EXTRA_OFF,EXTRA_LEN=64,512,608,1024
VARIANTS={
 "A":("A-wave2-battery-only",2,0,0),
 "B":("B-wave3-dsp-no-mss",3,0,0),
 "C":("C-wave3-modem-ipa",3,1,1),
}
def sha(x):return hashlib.sha256(x).hexdigest()
def cs(b):return b.split(b"\0",1)[0].decode("ascii","strict")
def cfgval(cfg,name):
    for line in cfg.splitlines():
        if line.startswith("CONFIG_"+name+"="):return line.split("=",1)[1]
        if line=="# CONFIG_"+name+" is not set":return "n"
    raise RuntimeError("P447C missing config "+name)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",required=True,type=Path)
    p.add_argument("--config",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--variant",required=True,choices=VARIANTS)
    p.add_argument("--parser",type=Path,default=Path("scripts/38_repack_a52_p1_boot.py"))
    x=p.parse_args()
    label,wave,mss,ipa=VARIANTS[x.variant]
    cfg=x.config.read_text()
    actual=(int(cfgval(cfg,"A52_WAVE_DEFAULT")),
            int(cfgval(cfg,"A52_MSS_DEFAULT")=="y"),
            int(cfgval(cfg,"A52_IPA_DEFAULT")=="y"))
    if actual!=(wave,mss,ipa):
        raise RuntimeError(f"P447C {x.variant}: .config {actual} != compiled defaults {(wave,mss,ipa)}")
    if cfgval(cfg,"LOG_BUF_SHIFT")!="23":
        raise RuntimeError("P447C must compile CONFIG_LOG_BUF_SHIFT=23 (8MiB)")
    blob=x.source.read_bytes()
    if len(blob)!=100663296 or blob[:8]!=b"ANDROID!" or struct.unpack_from("<I",blob,40)[0]!=2:
        raise RuntimeError("P447C expected 96MiB Android boot v2")
    spec=importlib.util.spec_from_file_location("a52boot",x.parser)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    original=m.parse_boot(blob)
    primary=cs(blob[CMD_OFF:CMD_OFF+CMD_LEN])
    extra=cs(blob[EXTRA_OFF:EXTRA_OFF+EXTRA_LEN])
    # 446 boot header primary is 511/512 bytes, literally ends 'ramoop';
    # extra begins 's.record_size': concatenate WITHOUT a separator first.
    words=(primary+extra).split()
    if "initcall_debug" not in words:
        raise RuntimeError("P447C expected old debug arg in source image")
    dtb=original["dtb"]
    if not (b"ramoops" in dtb and b"b1b00000" in dtb.lower()):
        raise RuntimeError("P447C refusing to remove cmdline ramoops params: DTB ramoops@b1b00000 missing")
    # Protect the working display/USB/touch controls. The wave is COMPILED;
    # deliberately no a52.wave/mss/ipa cmdline token is necessary.
    critical=[
        "a52.446w_handoff=0","a52.touch=1","a52.usb_handover=1",
        "a52.446w_cold=0","a52.446w_tty=3","a52.cb2_reuse=1",
        "console=ttyGS3,115200","loglevel=6",
        "firmware_class.path=/vendor/firmware_mnt/image",
        "no_console_suspend","panic=0",
    ]
    for item in critical[:6]:
        if item not in words:
            raise RuntimeError("P447C missing previously generated must-keep "+item)
    # All ramoops sizes/phys_addr live in the checked DT reserved-memory node.
    omit={"console=null","initcall_debug","ignore_loglevel","loglevel=8",
          "panic=1","pstore.compress=none","pstore.backend=ramoops"}
    remain=[]
    for word in words:
        if word in critical or word in omit or word.startswith("ramoops."):
            continue
        if word.startswith(("a52.wave=","a52.mss=","a52.ipa=")):
            raise RuntimeError("P447C refuses hidden old wave cmdline arg "+word)
        if word.startswith(("console=","loglevel=")):continue
        if word not in remain:remain.append(word)
    first=list(critical)
    spilled=[]
    for word in remain:
        if len((" ".join(first+[word])).encode("ascii"))<=480:
            first.append(word)
        else:spilled.append(word)
    # Android boot v2 tools concatenate cmdline + extra_cmdline literally.
    # Preserve an explicit separator so the last primary token and first
    # extra token cannot merge when the primary field is no longer full.
    xtra=" ".join(spilled)
    p1=" ".join(first)+(" " if xtra else "")
    if (p1+xtra).split()!=first+spilled:
        raise RuntimeError("P447C boot v2 cmdline boundary token lost")
    if len(p1)>=CMD_LEN or len(xtra)>=EXTRA_LEN:
        raise RuntimeError("P447C cmdline overflow")
    if len(p1)>480:
        raise RuntimeError("P447C primary field too long for ABL headroom")
    out=bytearray(blob)
    out[CMD_OFF:CMD_OFF+CMD_LEN]=p1.encode()+b"\0"*(CMD_LEN-len(p1))
    out[EXTRA_OFF:EXTRA_OFF+EXTRA_LEN]=xtra.encode()+b"\0"*(EXTRA_LEN-len(xtra))
    built=bytes(out)
    check=m.parse_boot(built)
    for key in ("kernel","ramdisk","second","recovery_dtbo","dtb","id"):
        if check[key]!=original[key]:
            raise RuntimeError("P447C boot component drift: "+key)
    if built[:CMD_OFF]!=blob[:CMD_OFF] or built[CMD_OFF+CMD_LEN:EXTRA_OFF]!=blob[CMD_OFF+CMD_LEN:EXTRA_OFF] or built[EXTRA_OFF+EXTRA_LEN:]!=blob[EXTRA_OFF+EXTRA_LEN:]:
        raise RuntimeError("P447C changed bytes outside two cmdline fields")
    primary_args=p1.split()
    if not all(k in primary_args for k in critical):
        raise RuntimeError("P447C primary args lost")
    if b"initcall_debug" in built[CMD_OFF:CMD_OFF+CMD_LEN]:
        raise RuntimeError("P447C still has initcall_debug")
    x.out.mkdir(parents=True,exist_ok=True)
    target=x.out/(label+".boot.img")
    target.write_bytes(built)
    report={
      "status":"EXPERIMENTAL_COMPILED_DEFAULTS_NOT_DRIVER_PORTS",
      "variant":x.variant,"label":label,
      "compiled_config":{"CONFIG_A52_WAVE_DEFAULT":wave,
                         "CONFIG_A52_MSS_DEFAULT":mss,
                         "CONFIG_A52_IPA_DEFAULT":ipa,
                         "CONFIG_LOG_BUF_SHIFT":23},
      "primary_cmdline_length":len(p1),"extra_cmdline_length":len(xtra),
      "boot_header_version":2,"primary_cmdline":p1,"extra_cmdline":xtra,
      "source_kernel_sha256":sha(original["kernel"]),
      "image_kernel_sha256":sha(check["kernel"]),
      "dtb_sha256":sha(check["dtb"]),
      "ramdisk_sha256":sha(check["ramdisk"]),
      "boot_image_sha256":sha(built),
      "boot_image_size":len(built),
      "no_nv_writes_from_packaging":True,
      "note":"One independently compiled kernel for this variant. Wave 2/3 actual drivers remain unported; gate ARMED does not mean device bound."
    }
    (x.out/"compiled-variant.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    (x.out/"READ-BEFORE-FLASH.txt").write_text(
        "P447C "+label+": independently compiled wave "+str(wave)+", MSS="+str(mss)+", IPA="+str(ipa)+".\n"
        "ALL NEW PORTS STILL REQUIRE SOURCE/DT/ABI/NO-NV CERTIFICATION.\n"
        "Do not expect battery percentage or remoteproc states from gates alone.\n"
        "Retain a known-good recovery boot image.\n")
    print("P447C BOOT PASS",x.variant,"wave",wave,"mss",mss,"ipa",ipa,
          "primary_length",len(p1),"extra_length",len(xtra),
          "SHA256",sha(built))
if __name__=="__main__":main()
