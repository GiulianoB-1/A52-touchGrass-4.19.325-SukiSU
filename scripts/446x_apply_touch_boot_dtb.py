#!/usr/bin/env python3
"""Phase446X pinned-TG R02 STM touch overlay, applied to actual Android boot v2 DTB.

Target verified through TG R02 __fixups__: qupv3_se8_i2c = /soc/i2c@988000.
Sources regulator phandles from the FINAL inherited boot FDT, and creates only
STM pinctrl plus touch child. No changes to GPU, DSI, panel, clocks or reserves.
Adds an extra firmware safeguard: stm,bringup=2 so TG's probe updater also
skips attempting to flash, on top of compile-time -EPERM flash stubs.
"""
from pathlib import Path
import argparse,hashlib,json,re,runpy,subprocess,struct,tempfile

PARSER=runpy.run_path("scripts/38_repack_a52_p1_boot.py")
parse=PARSER["parse_boot"]
align=PARSER["align"]
boot_id=PARSER["calculate_boot_id"]
SHA=lambda data:hashlib.sha256(data).hexdigest()

BUS="/soc/i2c@988000"
PIN="/soc/pinctrl@f100000"
L6="/soc/rsc@18200000/rpmh-regulator-ldoa6/regulator-pm6350-l6"
L11="/soc/rsc@18200000/rpmh-regulator-ldoa11/regulator-pm6350-l11"
TOUCH=BUS+"/touchscreen@49"
ATTN=PIN+"/a52x_fts_attn_irq"
def run(*args):
    p=subprocess.run(args,text=True,capture_output=True)
    if p.returncode:
        raise RuntimeError("P446X FDT tool "+repr(args)+" rc="+str(p.returncode)+" stderr="+p.stderr)
    return p.stdout.strip()
def get(dtb,path,name):
    return run("fdtget","-t","x",str(dtb),path,name)
def put(dtb,path,name,*vals,kind="x"):
    # With fdtput -t x, bare digits are interpreted as HEX, not decimal.
    # Formatting integer cells explicitly as 0x... prevents 0x49 turning
    # into 0x73 and 400000 Hz silently becoming 0x400000 Hz.
    values=[hex(v) if kind=="x" and isinstance(v,int) else str(v)
            for v in vals]
    run("fdtput","-t",kind,str(dtb),path,name,*values)
def empty(dtb,path,name):
    run("fdtput",str(dtb),path,name)
def new_node(dtb,path):
    run("fdtput","-c",str(dtb),path)
def ph(s):
    values=s.split()
    if len(values)!=1:raise RuntimeError("P446X supply phandle has unexpected cell count: "+s)
    return int(values[0],16)
def patch_one(original):
    if original[:4]!=b"\xd0\x0d\xfe\xed":
        raise RuntimeError("not flattened DTB")
    with tempfile.TemporaryDirectory() as tmp:
        f=Path(tmp)/"a52x.dtb";f.write_bytes(original)
        # Disable accidental duplicate driver creation.
        p=subprocess.run(["fdtget",str(f),TOUCH,"compatible"],capture_output=True,text=True)
        if p.returncode==0:
            if p.stdout.strip()=="stm,fts_touch":
                raise RuntimeError("P446X already has touch node: refuse duplicate")
            raise RuntimeError("P446X I2C address collision at 0x49 "+p.stdout)
        if run("fdtget","-t","s",str(f),BUS,"compatible")!="qcom,i2c-geni":
            raise RuntimeError("P446X wrong I2C target, expected qcom,i2c-geni")
        if run("fdtget","-t","s",str(f),BUS,"status") not in ("disabled","okay","ok"):
            raise RuntimeError("P446X SE8 unexpected status")
        tlmm=ph(get(f,PIN,"phandle"))
        avdd=ph(get(f,L6,"phandle"))
        dvdd=ph(get(f,L11,"phandle"))
        # This pinctrl state is TG R02 fragment@59: gpio22 input, bias-disable.
        dts=run("dtc","-I","dtb","-O","dts",str(f))
        old_ph=[int(x,16) for x in re.findall(r"\bphandle\s*=\s*<0x([0-9a-fA-F]+)>",dts)]
        unused=max(old_ph,default=0)+1
        if unused>0xfffffff0:raise RuntimeError("no free phandle")
        new_node(f,ATTN)
        new_node(f,ATTN+"/mux")
        new_node(f,ATTN+"/config")
        put(f,ATTN,"phandle",hex(unused))
        put(f,ATTN+"/mux","pins","gpio22",kind="s")
        put(f,ATTN+"/mux","function","gpio",kind="s")
        put(f,ATTN+"/config","pins","gpio22",kind="s")
        empty(f,ATTN+"/config","input-enable")
        empty(f,ATTN+"/config","bias-disable")
        # The original TG overlay TARGET is qupv3_se8_i2c. Set active I2C,
        # preserving its existing SE8 clocks, pinctrl, IRQ and dma bindings.
        put(f,BUS,"status","okay",kind="s")
        put(f,BUS,"clock-frequency",400000)
        new_node(f,TOUCH)
        put(f,TOUCH,"status","okay",kind="s")
        put(f,TOUCH,"compatible","stm,fts_touch",kind="s")
        put(f,TOUCH,"reg",0x49)
        put(f,TOUCH,"pinctrl-names","default",kind="s")
        put(f,TOUCH,"pinctrl-0",hex(unused))
        put(f,TOUCH,"dvdd-supply",hex(dvdd))
        put(f,TOUCH,"avdd-supply",hex(avdd))
        put(f,TOUCH,"stm,irq_gpio",hex(tlmm),0x16,0)
        put(f,TOUCH,"stm,max_coords",0xfff,0xfff)
        put(f,TOUCH,"stm,firmware_name","tsp_stm/fts5cu56a_a52xq.bin",kind="s")
        put(f,TOUCH,"stm,tclm_level",2)
        put(f,TOUCH,"stm,afe_base",0x14)
        put(f,TOUCH,"stm,bringup",2)
        for prop in (
            "stm,support_fod","stm,enable_settings_aot","support_ear_detect",
            "support_mis_calibration_test","support_dex_mode",
            "support_open_short_test","support_sram_test","support_hall_ic",
            "support_flex_mode"):
            empty(f,TOUCH,prop)
        put(f,TOUCH,"stm,ss_touch_num",1)
        final=f.read_bytes()
        if (ph(get(f,TOUCH,"avdd-supply"))!=avdd or
            ph(get(f,TOUCH,"dvdd-supply"))!=dvdd):
            raise RuntimeError("supply changed during FDT patch")
        if run("fdtget","-t","s",str(f),TOUCH,"compatible")!="stm,fts_touch":
            raise RuntimeError("touch compatible verification fail")
        if run("fdtget","-t","s",str(f),BUS,"status")!="okay":
            raise RuntimeError("SE8 not enabled")
        return final,{"bus":BUS,"firmware_flash_guard":"stm,bringup=2",
                      "avdd_regulator":"pm6350_l6","dvdd_regulator":"pm6350_l11",
                      "tlmm_irq_gpio":22,"pin_phandle":hex(unused),
                      "avdd_phandle":hex(avdd),"dvdd_phandle":hex(dvdd)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--boot",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--report",type=Path,required=True)
    a=ap.parse_args()
    original=a.boot.read_bytes()
    parts=parse(original)
    src_dtb=parts["dtb"]
    if not src_dtb or src_dtb[:4]!=b"\xd0\x0d\xfe\xed":
        raise RuntimeError("P446X boot header contains no single original DTB")
    declared=struct.unpack_from(">I",src_dtb,4)[0]
    if declared!=len(src_dtb):
        raise RuntimeError("P446X boot FDT contains multiple or appended DTBs: fail closed")
    new_dtb,details=patch_one(src_dtb)
    page=parts["page_size"]
    header=bytearray(parts["header_page"])
    struct.pack_into("<I",header,1648,len(new_dtb))
    digest=boot_id([
      (parts["kernel"],parts["kernel_size"]),
      (parts["ramdisk"],parts["ramdisk_size"]),
      (parts["second"],parts["second_size"]),
      (parts["recovery_dtbo"],parts["recovery_dtbo_size"]),
      (new_dtb,len(new_dtb))
    ])
    header[576:608]=digest
    out=bytearray(header)
    for content in (parts["kernel"],parts["ramdisk"],parts["second"],
                    parts["recovery_dtbo"],new_dtb):
        out+=content
        out+=b"\0"*(align(len(out),page)-len(out))
    if len(out)>len(original):
        raise RuntimeError("P446X FDT added bytes exceed partition capacity")
    out+=b"\0"*(len(original)-len(out))
    decoded=parse(bytes(out))
    assert decoded["kernel"]==parts["kernel"]
    assert decoded["ramdisk"]==parts["ramdisk"]
    assert decoded["second"]==parts["second"]
    assert decoded["recovery_dtbo"]==parts["recovery_dtbo"]
    assert decoded["cmdline"]==parts["cmdline"]
    assert decoded["id"]==digest
    assert decoded["dtb"]==new_dtb
    assert len(out)==len(original)
    a.output.write_bytes(out)
    a.report.write_text(json.dumps({
        "status":"P446X applied TG R02 fragment@59/60 to boot FDT",
        "original_dtb_sha256":SHA(src_dtb),
        "new_dtb_sha256":SHA(new_dtb),
        "new_boot_sha256":SHA(out),
        "original_boot_sha256":SHA(original),
        "dtb_original_bytes":len(src_dtb),
        "dtb_new_bytes":len(new_dtb),
        "no_kernel_ramdisk_recovery_cmdline_changes":True,
        **details
    },indent=2,sort_keys=True)+"\n")
    print("P446X FINAL BOOT DTB PATCH PASS: qupv3_se8 active, STM 0x49, TLMM22, PM6350 L6/L11, firmare write read-only; boot ID recalculated")

if __name__=="__main__":main()
