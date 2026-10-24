#!/usr/bin/env python3
"""P451: convert the *packaged* Android boot-v2 FDT to exclusive native AOSS
and manually started SM6350 ADSP PAS, retaining original image kernel/ramdisk.

Requires fdtget/fdtput/dtc in CI. Exact TG 6bf351bd phandles, IRQ and rails.
This script must never touch modem/CDSP, charge control or firmware partitions.
"""
import argparse,hashlib,json,re,runpy,struct,subprocess,tempfile
from pathlib import Path

PARSER=runpy.run_path("scripts/38_repack_a52_p1_boot.py")
parse=PARSER["parse_boot"]; boot_id=PARSER["calculate_boot_id"]; align=PARSER["align"]
AOSS="/soc/qcom,qmp-aop@c300000"
LPASS="/soc/qcom,lpass@3000000"
SCM="/firmware/scm"
TAG="P451_DSP_EXCLUSIVE_AOSS_ADSP_V1"
def sha(x):return hashlib.sha256(x).hexdigest()
def run(*args):
    x=subprocess.run([str(z) for z in args],capture_output=True,text=True)
    if x.returncode:raise RuntimeError("P451 FDT "+repr(args)+" rc="+str(x.returncode)+" err="+x.stderr[-700:])
    return x.stdout.strip()
def optional(*args):
    x=subprocess.run([str(z) for z in args],capture_output=True,text=True)
    return x.stdout.strip() if x.returncode==0 else None
def ph(dtb,node,prop):
    v=run("fdtget","-t","x",dtb,node,prop).split()
    if len(v)!=1:raise RuntimeError("P451 invalid phandle "+node+"/"+prop+" = "+repr(v))
    return int(v[0],16)
def cells(dtb,node,prop):
    return [int(x,16) for x in run("fdtget","-t","x",dtb,node,prop).split()]
def putx(dtb,node,prop,*v):
    run("fdtput","-t","x",dtb,node,prop,*[hex(z) for z in v])
def puts(dtb,node,prop,*v):
    run("fdtput","-t","s",dtb,node,prop,*v)
def new(dtb,node):
    run("fdtput","-c",dtb,node)
def children(dtb,node):
    t=optional("fdtget","-l",dtb,node)
    return t.splitlines() if t else []
def walk(dtb,start="/",depth=0):
    if depth>25:raise RuntimeError("P451 DTB unexpectedly deep")
    yield start
    for sub in children(dtb,start):
        yield from walk(dtb,(start.rstrip("/")+"/"+sub),depth+1)
def glink_tg_adsp(dtb):
    # Final 451-DAILY-431 (artifact 12526483712) has this exact path:
    # /soc/qcom,glink/adsp, qcom,remote-pid=2 and TG LPASS IPCC IRQ.
    # Avoid a recursive fdtget listing that missed an existing node.
    node="/soc/qcom,glink/adsp"
    pid=cells(dtb,node,"qcom,remote-pid")
    if pid!=[2]:
        raise RuntimeError("P451 TG ADSP GLINK remote-pid mismatch "+repr(pid))
    if run("fdtget","-t","s",dtb,"/soc/qcom,glink","compatible")!="qcom,glink":
        raise RuntimeError("P451 TG GLINK parent compatible drift")
    for prop in ("mboxes","interrupt-parent","interrupts"):
        if not cells(dtb,node,prop):
            raise RuntimeError("P451 TG ADSP GLINK missing "+prop)
    print("P451 TG PACKED GLINK FOUND path="+node+" remote_pid=2")
    return node
def preflight(dtb):
    if run("fdtget","-t","s",dtb,AOSS,"compatible")!="qcom,qmp-mbox":
        raise RuntimeError("P451 refusing AOSS whose TG compatible already changed")
    if run("fdtget","-t","s",dtb,LPASS,"compatible")!="qcom,pil-tz-generic":
        raise RuntimeError("P451 refusing unexpected original LPASS compatible")
    a=ph(dtb,AOSS,"phandle")
    cx=ph(dtb,LPASS,"vdd_lpi_cx-supply")
    mx=ph(dtb,LPASS,"vdd_lpi_mx-supply")
    if cx==mx or a in (cx,mx):raise RuntimeError("P451 broken AOSS/rail phandle contract")
    for prop in ("qcom,vdd_lpi_cx-uV-uA","qcom,vdd_lpi_mx-uV-uA"):
        values=cells(dtb,LPASS,prop)
        if len(values)!=2 or values[0]!=384:
            raise RuntimeError("P451 level regulator not TG TURBO 0x180: "+prop+" "+str(values))
    if optional("fdtget","-t","s",dtb,SCM,"compatible") is not None:
        raise RuntimeError("P452 SCM node must be absent in original TG DTB")
    if optional("fdtget","-l",dtb,"/firmware") is None:
        raise RuntimeError("P452 requires existing firmware parent")
    glink=glink_tg_adsp(dtb)
    for prop in ("mboxes","interrupt-parent","interrupts"):
        if optional("fdtget","-t","x",dtb,glink,prop) is None:
            raise RuntimeError("P451 TG GLINK lacks "+prop)
    if "stop" in run("fdtget","-t","s",dtb,LPASS,"qcom,smem-state-names").split():
        raise RuntimeError("P451 unexpected premodified smem-state")
    return dict(aoss=a,cx=cx,mx=mx,glink=glink)
def patch_fdt(raw):
    if raw[:4]!=b"\xd0\x0d\xfe\xed" or struct.unpack_from(">I",raw,4)[0]!=len(raw):
        raise RuntimeError("P451 boot must have exactly one DTB")
    with tempfile.TemporaryDirectory() as d:
        file=Path(d)/"work.dtb";file.write_bytes(raw)
        meta=preflight(file)
        # P452: upstream 5.10 SCM requires an OF platform device. TG has
        # no SCM node (its downstream SCM is initialized independently).
        # No download-mode enable is written by this DTB operation.
        new(file,SCM)
        puts(file,SCM,"compatible","qcom,scm-sm6350","qcom,scm")
        putx(file,SCM,"#reset-cells",1)
        print("P452 SCM DTB installed /firmware/scm, reset_cells=1")
        # Upstream AOSS owns QMP; TG mailbox provider must not also bind.
        puts(file,AOSS,"compatible","qcom,sm6350-aoss-qmp")
        putx(file,AOSS,"#power-domain-cells",1)
        putx(file,AOSS,"#clock-cells",0)
        # The native AOSS driver is the only QMP hardware owner.
        # However, the final TG DTB has seven pre-existing mboxes clients
        # pointing at this phandle, some potentially active for thermal/
        # memory-offline support. Retain #mbox-cells as inert parser ABI;
        # it does NOT create a mailbox provider in qcom_aoss.
        # Native migration of those seven consumers remains separate work.
        if cells(file,AOSS,"#mbox-cells")!=[1]:
            raise RuntimeError("P451 missing legacy AOP consumer parser contract")
        # 5.10 Q6v5 PAS uses legacy cx/px regulator_get names. These aliases
        # point to the UNCHANGED TG lcx.lvl and lmx.lvl regulator phandles.
        puts(file,LPASS,"compatible","qcom,sm6350-adsp-pas")
        putx(file,LPASS,"cx-supply",meta["cx"])
        putx(file,LPASS,"px-supply",meta["mx"])
        putx(file,LPASS,"power-domains",meta["aoss"],1)  # AOSS_QMP_LS_LPASS=1
        puts(file,LPASS,"power-domain-names","load_state")
        puts(file,LPASS,"interrupt-names","wdog","fatal","ready","handover","stop-ack")
        puts(file,LPASS,"qcom,smem-state-names","stop")
        puts(file,LPASS,"firmware-name","adsp.mdt")
        # qcom_add_glink_subdev binds specifically child 'glink-edge'.
        edge=LPASS+"/glink-edge"
        if optional("fdtget","-t","x",file,edge,"qcom,remote-pid") is not None:
            raise RuntimeError("P451 duplicate GLINK edge already in PAS")
        new(file,edge)
        putx(file,edge,"qcom,remote-pid",2)
        for prop in ("mboxes","interrupt-parent","interrupts"):
            putx(file,edge,prop,*cells(file,meta["glink"],prop))
        oldkids=children(file,meta["glink"])
        copied=0
        for kid in oldkids:
            prev=meta["glink"]+"/"+kid
            channels=optional("fdtget","-t","s",file,prev,"qcom,glink-channels")
            if not channels:continue
            dst=edge+"/"+kid
            new(file,dst)
            # Copy TG channel children EXACTLY (including fastrpc compatible,
            # QRTR low-latency and SSR notify-edges), not only intents.
            # fdtget/fdtput -t bx round-trips bytes, including boolean props.
            for prop in run("fdtget","-p",file,prev).splitlines():
                if prop in ("phandle","linux,phandle"):continue
                original=run("fdtget","-t","bx",file,prev,prop)
                run("fdtput","-t","bx",file,dst,prop,*original.split())
                actual=run("fdtget","-t","bx",file,dst,prop)
                if actual!=original:
                    raise RuntimeError("P451 GLINK child byte mismatch "+prev+"/"+prop)
            copied+=1
        if copied!=4:
            raise RuntimeError("P451 expected 4 TG ADSP channels, got "+str(copied))
        # One transport owner only, no phantom old platform transport.
        puts(file,meta["glink"],"status","disabled")
        newdtb=file.read_bytes()
        info=validate(file)
        info.update({"original_tg_glink_node":meta["glink"],"copied_channels":copied,
                     "tg_original_lcx_phandle":hex(meta["cx"]),
                     "tg_original_lmx_phandle":hex(meta["mx"])})
        return newdtb,info
def validate(file):
    compat=run("fdtget","-t","s",file,SCM,"compatible").split()
    if compat!=["qcom,scm-sm6350","qcom,scm"]:
        raise RuntimeError("P452 upstream SCM compatible mismatch "+repr(compat))
    if cells(file,SCM,"#reset-cells")!=[1]:
        raise RuntimeError("P452 SCM reset cells mismatch")
    if run("fdtget","-t","s",file,AOSS,"compatible")!="qcom,sm6350-aoss-qmp":
        raise RuntimeError("P451 AOSS not exclusive native")
    if cells(file,AOSS,"#mbox-cells")!=[1]:
        raise RuntimeError("P451 existing AOP mailbox client parser contract lost")
    if cells(file,AOSS,"#power-domain-cells")!=[1]:
        raise RuntimeError("P451 AOSS missing genpd single cell")
    if run("fdtget","-t","s",file,LPASS,"compatible")!="qcom,sm6350-adsp-pas":
        raise RuntimeError("P451 PAS compatible not live")
    if run("fdtget","-t","s",file,LPASS,"power-domain-names")!="load_state":
        raise RuntimeError("P451 load_state name missing")
    if cells(file,LPASS,"power-domains")[1:]!=[1]:
        raise RuntimeError("P451 AOSS LPASS index differs from 5.10")
    if run("fdtget","-t","s",file,LPASS,"interrupt-names").split()!=["wdog","fatal","ready","handover","stop-ack"]:
        raise RuntimeError("P451 5.10 Q6v5 IRQ names invalid")
    if run("fdtget","-t","s",file,LPASS,"qcom,smem-state-names")!="stop":
        raise RuntimeError("P451 5.10 stop state name invalid")
    if cells(file,LPASS+"/glink-edge","qcom,remote-pid")!=[2]:
        raise RuntimeError("P451 GLINK remote PID not 2")
    if cells(file,LPASS,"cx-supply")==cells(file,LPASS,"px-supply"):
        raise RuntimeError("P451 lcx/lmx references collided")
    return {"status":"P452 SCM + P451 DSP DTB PAS/AOSS/GLINK audited",
            "scm_node":SCM,"scm_compatible":compat,
            "aoss":"qcom,sm6350-aoss-qmp","adsp":"qcom,sm6350-adsp-pas",
            "load_state_name":"adsp","ssr_name":"lpass",
            "autoboot":"disabled by PAS descriptor",
            "regulator_vote":{"lcx":384,"lmx":384,"max":65535},
            "glink_pid":2}
def config(path):
    if not path:return
    cfg=path.read_text()
    for k in ("A52_WAVE_DEFAULT=2","QCOM_AOSS_QMP=y","QCOM_Q6V5_PAS=y",
              "REMOTEPROC=y","RPMSG_QCOM_GLINK_SMEM=y"):
        if "CONFIG_"+k+"\n" not in cfg:raise RuntimeError("P451 config missing "+k)
    for k in ("A52_MSS_DEFAULT","A52_IPA_DEFAULT","QCOM_Q6V5_MSS",
              "QCOM_WCNSS_PIL","QCOM_RMTFS_MEM"):
        if "CONFIG_"+k+"=y\n" in cfg:raise RuntimeError("P451 dangerous feature enabled "+k)
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--boot",type=Path,required=True)
    p.add_argument("--output",type=Path)
    p.add_argument("--report",type=Path)
    p.add_argument("--config",type=Path)
    p.add_argument("--check-only",action="store_true")
    x=p.parse_args()
    config(x.config)
    original=x.boot.read_bytes();data=parse(original)
    if x.check_only:
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"packed.dtb";f.write_bytes(data["dtb"])
            obj=validate(f)
        print("P451 DSP BOOT AUDIT PASS",json.dumps(obj,sort_keys=True))
        return
    if not x.output or not x.report:raise RuntimeError("P451 requires output and report")
    newdtb,info=patch_fdt(data["dtb"])
    page=data["page_size"];header=bytearray(data["header_page"])
    struct.pack_into("<I",header,1648,len(newdtb))
    digest=boot_id([
        (data["kernel"],data["kernel_size"]),
        (data["ramdisk"],data["ramdisk_size"]),
        (data["second"],data["second_size"]),
        (data["recovery_dtbo"],data["recovery_dtbo_size"]),
        (newdtb,len(newdtb))])
    header[576:608]=digest
    result=bytearray(header)
    for chunk in (data["kernel"],data["ramdisk"],data["second"],data["recovery_dtbo"],newdtb):
        result+=chunk
        result+=b"\0"*(align(len(result),page)-len(result))
    if len(result)>len(original):raise RuntimeError("P451 DTB exceeds boot partition")
    result+=b"\0"*(len(original)-len(result))
    final=parse(bytes(result))
    for key in ("kernel","ramdisk","second","recovery_dtbo","cmdline"):
        if final[key]!=data[key]:raise RuntimeError("P451 modified "+key)
    if final["dtb"]!=newdtb or final["id"]!=digest or len(result)!=len(original):
        raise RuntimeError("P451 boot FDT/id final mismatch")
    x.output.write_bytes(result)
    info.update({"boot_sha256":sha(result),"dtb_sha256":sha(newdtb),
                 "kernel_unchanged":True,"ramdisk_unchanged":True,
                 "original_boot_sha256":sha(original)})
    x.report.write_text(json.dumps(info,indent=2,sort_keys=True)+"\n")
    print("P452 SCM+DSP BOOT PATCH PASS: firmware/scm, native AOSS, manual SM6350 PAS, lcx/lmx 384, GLINK pid2, preserved kernel/ramdisk")
if __name__=="__main__":main()
