#!/usr/bin/env python3
"""P446X: apply TG A52 R02 STM touchscreen fragment to actual v2 boot DTB.

The inherited 446T boot DTB disables qupv3_se8_i2c at 0x988000 and lacks
touchscreen@49. The stock device's separate dtbo may also apply this overlay
at boot, but the standalone GKI boot FDT must be self-sufficient; all
injected nodes use the same names/coordinates as TG R02. Do not edit
regulator or clock parents, and refuse unexpected FDTs.
Repack the DTB into boot v2 with a recomputed SHA1 boot ID. Preserve all
other component bytes and partition size.
"""
import argparse,hashlib,json,re,runpy,struct,subprocess,tempfile
from pathlib import Path

MAGIC=b"\xd0\x0d\xfe\xed"
I2C="/soc/i2c@988000"
TLMM="/soc/pinctrl@f100000"
L6="/soc/rsc@18200000/rpmh-regulator-ldoa6/regulator-pm6350-l6"
L11="/soc/rsc@18200000/rpmh-regulator-ldoa11/regulator-pm6350-l11"
TS=I2C+"/touchscreen@49"
PIN=TLMM+"/a52xq_fts_attn_irq"

def cmd(argv):
    p=subprocess.run(argv,text=True,capture_output=True)
    if p.returncode:
        raise RuntimeError("failed "+" ".join(map(str,argv))+": "+p.stderr[:650])
    return p.stdout.strip()

def fget(path,node,prop):
    vals=cmd(["fdtget","-t","x",str(path),node,prop]).split()
    if not vals:raise RuntimeError(f"empty FDT value {node}/{prop}")
    return [int(x,16) for x in vals]

def put(path,node,prop,*vals,t="x"):
    cmd(["fdtput","-t",t,str(path),node,prop,*[str(v) for v in vals]])

def boolean(path,node,prop):
    cmd(["fdtput",str(path),node,prop])

def patch_dtb(original):
    if original[:4]!=MAGIC or len(original)<40 or struct.unpack_from(">I",original,4)[0]!=len(original):
        raise RuntimeError("expected exactly one DTB in boot v2")
    with tempfile.TemporaryDirectory() as temp:
        dt=Path(temp)/"boot.dtb";dt.write_bytes(original)
        text=cmd(["dtc","-I","dtb","-O","dts",str(dt)])
        if re.search(r'touchscreen@49\s*\{',text):
            raise RuntimeError("P446X FDT already defines touchscreen@49: review board overlay before modifying")
        for node in (I2C,TLMM,L6,L11):
            cmd(["fdtget","-p",str(dt),node])
        bus_status=cmd(["fdtget","-t","s",str(dt),I2C,"status"])
        if bus_status not in ("disabled","okay","ok"):
            raise RuntimeError("unexpected qupv3_se8 I2C state "+bus_status)
        gpio=fget(dt,TLMM,"phandle")[0]
        l6=fget(dt,L6,"phandle")[0]
        l11=fget(dt,L11,"phandle")[0]
        # Unique phandle for R02 fragment@59/attn_irq; don't copy TG's
        # literal phandle 0x60, which would collide with the GKI base DTB.
        used=[int(v,16) for v in re.findall(r'\bphandle\s*=\s*<0x([0-9a-fA-F]+)>',text)]
        pn=max(used+[0x800])+1
        if pn >= 0xfffffff0:raise RuntimeError("FDT phandle space exhausted")
        cmd(["fdtput","-c",str(dt),PIN])
        cmd(["fdtput","-c",str(dt),PIN+"/mux"])
        cmd(["fdtput","-c",str(dt),PIN+"/config"])
        put(dt,PIN,"phandle",pn)
        put(dt,PIN+"/mux","pins","gpio22",t="s")
        put(dt,PIN+"/mux","function","gpio",t="s")
        put(dt,PIN+"/config","pins","gpio22",t="s")
        boolean(dt,PIN+"/config","input-enable")
        boolean(dt,PIN+"/config","bias-disable")
        put(dt,I2C,"status","okay",t="s")
        # TG R02 fragment@60 has 400kHz and 100us SDA delay.
        put(dt,I2C,"samsung,i2c-sda-delay",0x64)
        put(dt,I2C,"samsung,i2c-max-bus-freq",0x61a80)
        cmd(["fdtput","-c",str(dt),TS])
        put(dt,TS,"status","okay",t="s")
        put(dt,TS,"compatible","stm,fts_touch",t="s")
        put(dt,TS,"reg",0x49)
        put(dt,TS,"pinctrl-names","default",t="s")
        put(dt,TS,"pinctrl-0",pn)
        put(dt,TS,"avdd-supply",l6)
        put(dt,TS,"dvdd-supply",l11)
        put(dt,TS,"stm,irq_gpio",gpio,0x16,0)
        put(dt,TS,"stm,max_coords",0xfff,0xfff)
        put(dt,TS,"stm,firmware_name","tsp_stm/fts5cu56a_a52xq.bin",t="s")
        put(dt,TS,"stm,tclm_level",2)
        put(dt,TS,"stm,afe_base",0x14)
        put(dt,TS,"stm,bringup",0)
        put(dt,TS,"stm,ss_touch_num",1)
        for prop in ("stm,support_fod","stm,enable_settings_aot",
                     "support_ear_detect","support_mis_calibration_test",
                     "support_dex_mode","support_open_short_test",
                     "support_sram_test","support_hall_ic","support_flex_mode"):
            boolean(dt,TS,prop)
        after=dt.read_bytes()
        if struct.unpack_from(">I",after,4)[0]!=len(after):
            raise RuntimeError("modified FDT length invalid")
        if fget(dt,TS,"avdd-supply")!=[l6] or fget(dt,TS,"dvdd-supply")!=[l11]:
            raise RuntimeError("touch regulator phandle verification failed")
        if fget(dt,TS,"stm,irq_gpio")!=[gpio,0x16,0]:
            raise RuntimeError("touch GPIO verification failed")
        return after,{"bus":I2C,"touch":TS,"i2c_addr":"0x49",
                     "gpio":22,"gpio_phandle":hex(gpio),
                     "pinctrl_phandle":hex(pn),
                     "avdd_phandle":hex(l6),"dvdd_phandle":hex(l11),
                     "dtb_old_sha256":hashlib.sha256(original).hexdigest(),
                     "dtb_new_sha256":hashlib.sha256(after).hexdigest(),
                     "old_size":len(original),"new_size":len(after)}

def repack(source,dtb):
    ns=runpy.run_path("scripts/38_repack_a52_p1_boot.py")
    parse=ns["parse_boot"];align=ns["align"]
    src=parse(source);page=src["page_size"]
    header=bytearray(src["header_page"])
    struct.pack_into("<I",header,1648,len(dtb))
    parts=[(src["kernel"],src["kernel_size"]),
           (src["ramdisk"],src["ramdisk_size"]),
           (src["second"],src["second_size"]),
           (src["recovery_dtbo"],src["recovery_dtbo_size"]),
           (dtb,len(dtb))]
    header[576:608]=ns["calculate_boot_id"](parts)
    out=bytearray(header)
    for blob,_ in parts:
        out.extend(blob)
        out.extend(b"\0"*(align(len(out),page)-len(out)))
    if len(out)>len(source):
        raise RuntimeError("P446X patched DTB exceeds boot partition size")
    out.extend(b"\0"*(len(source)-len(out)))
    dest=parse(bytes(out))
    if dest["dtb"]!=dtb or dest["kernel"]!=src["kernel"] or dest["ramdisk"]!=src["ramdisk"] or dest["recovery_dtbo"]!=src["recovery_dtbo"] or dest["second"]!=src["second"]:
        raise RuntimeError("non-DTB boot payload unexpectedly modified")
    if dest["id"]!=header[576:608]:raise RuntimeError("boot ID checksum invalid")
    return bytes(out)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--source",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--report",type=Path,required=True)
    a=p.parse_args()
    before=a.source.read_bytes()
    boot=runpy.run_path("scripts/38_repack_a52_p1_boot.py")["parse_boot"](before)
    dtb,info=patch_dtb(boot["dtb"])
    result=repack(before,dtb)
    a.output.write_bytes(result)
    info.update({"status":"P446X TOUCH R02 APPLIED TO FINAL BOOT DTB",
                 "new_boot_sha256":hashlib.sha256(result).hexdigest(),
                 "kernel_ramdisk_preserved":True,"partition_size_preserved":True,
                 "boot_id_recalculated":True})
    a.report.write_text(json.dumps(info,indent=2,sort_keys=True)+"\n")
    print(json.dumps(info,sort_keys=True))
if __name__=="__main__":main()
