#!/usr/bin/env python3
"""Phase448A1: TG-anchored Qualcomm IPC core probe diagnostics, no remote boot.

Standalone experimental D candidate only. Never patch AOSS compatible
qcom,qmp-mbox to upstream QMP until mailbox ABI is proven compatible.
Never enable PAS, MSS, ADSP, CDSP, IPA or rmtfs as part of this step.
"""
import argparse
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile

TAG = "A52_PHASE448A1_IPC_STAGE_V1"
TG = Path("arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi")
PATHS = {
 "ipcc": Path("drivers/mailbox/qcom-ipcc.c"),
 "smem": Path("drivers/soc/qcom/smem.c"),
 "smp2p": Path("drivers/soc/qcom/smp2p.c"),
 "aoss": Path("drivers/soc/qcom/qcom_aoss.c"),
}
SCOPE = {
 "ipcc": ("static int qcom_ipcc_probe(", "\nstatic int qcom_ipcc_remove("),
 "smem": ("static int qcom_smem_probe(", "\nstatic int qcom_smem_remove("),
 "smp2p": ("static int qcom_smp2p_probe(", "\nstatic int qcom_smp2p_remove("),
 "aoss": ("static int qmp_probe(", "\nstatic int qmp_remove("),
}
LOG = {
 "ipcc": '\tdev_info(&pdev->dev, "P448 IPC ipcc=PROBED irq=%d\\n", ipcc->irq);',
 "smem": '\tdev_info(&pdev->dev, "P448 IPC smem=PROBED hwlock=%d version=%#x\\n", hwlock_id, version);',
 "smp2p": '\tdev_info(&pdev->dev, "P448 IPC smp2p=PROBED remote=%u local=%u smem=%u,%u node=%pOF\\n",\n\t\t smp2p->remote_pid, smp2p->local_pid, smp2p->smem_items[0],\n\t\t smp2p->smem_items[1], pdev->dev.of_node);',
 "aoss": '\tdev_info(&pdev->dev, "P448 IPC aoss=PROBED node=%pOF\\n", pdev->dev.of_node);'
}
TG_ASSERT = [
 ('qcom,ipcc@408000', 'reg = <0x408000 0x1000>', 'GIC_SPI 228'),
 ('smem@80900000', 'reg = <0x0 0x80900000 0x0 0x200000>'),
 ('qcom,smp2p-mpss', 'qcom,smem = <435>, <428>', 'qcom,remote-pid = <1>'),
 ('qcom,smp2p-adsp', 'qcom,smem = <443>, <429>', 'qcom,remote-pid = <2>'),
 ('qcom,smp2p-cdsp', 'qcom,smem = <94>, <432>', 'qcom,remote-pid = <5>'),
 ('qcom,qmp-aop@c300000', 'compatible = "qcom,qmp-mbox"'),
]

def tg_check(root):
    d=(root/TG).read_text()
    for signatures in TG_ASSERT:
        for s in signatures:
            if s not in d:raise RuntimeError("P448 TG DT drift: "+s)
    for name in ("master-kernel","slave-kernel","ipa","wlan","sleepstate","rdbg"):
        if 'qcom,entry-name = "'+name+'"' not in d:
            raise RuntimeError("P448 TG SMP2P entry missing: "+name)
    print("P448 TG PASS: IPCC 0x408000 IRQ228, SMEM 0x80900000, 3 SMP2P edge IDs and PIDs")
    print("P448 AOSS BLOCKED: TG qcom,qmp-mbox unmatched by 5.10 qcom_aoss; no alias guessed")

def patch(src, name):
    if TAG in src:return src
    start,stop=SCOPE[name]
    if src.count(start)!=1 or src.count(stop)!=1:
        raise RuntimeError("P448 "+name+" probe scope drift")
    a=src.index(start)
    b=src.index(stop,a)
    body=src[a:b]
    needle="\n\treturn 0;\n"
    if body.count(needle)!=1:
        raise RuntimeError("P448 "+name+" success return drift "+str(body.count(needle)))
    body=body.replace(needle,"\n\t/* "+TAG+" */\n"+LOG[name]+needle,1)
    return src[:a]+body+src[b:]

def check(root):
    for name,path in PATHS.items():
        s=(root/path).read_text()
        if s.count(TAG)!=1 or s.count("P448 IPC ")!=1:
            raise RuntimeError("P448 "+name+" marker absent or duplicated")
    if '"qcom,qmp-mbox"' in (root/PATHS["aoss"]).read_text():
        raise RuntimeError("P448 unsafe AOSS compatible injected")
    print("P448 IPC SOURCE PASS: upstream core probe markers; no unverified AOSS alias")

def config_check(path,manual_pas=False):
    s=path.read_text()
    if "CONFIG_A52_WAVE_DEFAULT=2\n" not in s:
        raise RuntimeError("P448 D must remain wave=2: generic W3 gates OFF")
    for name in ("MAILBOX","HWSPINLOCK","HWSPINLOCK_QCOM",
                 "QCOM_IPCC","QCOM_SMEM","QCOM_SMP2P","QCOM_AOSS_QMP"):
        if "CONFIG_"+name+"=y\n" not in s:raise RuntimeError("P448 IPC disabled: "+name)
    if manual_pas and "CONFIG_QCOM_Q6V5_PAS=y\n" not in s:
        raise RuntimeError("P451 G PAS must be built-in")
    for name in ("QCOM_Q6V5_PAS","QCOM_Q6V5_ADSP","QCOM_Q6V5_MSS",
                 "QCOM_Q6V5_WCSS","QCOM_WCNSS_PIL","QCOM_RMTFS_MEM",
                 "A52_MSS_DEFAULT","A52_IPA_DEFAULT"):
        if manual_pas and name=="QCOM_Q6V5_PAS":continue
        if "CONFIG_"+name+"=y\n" in s or "CONFIG_"+name+"=m\n" in s:
            raise RuntimeError("P448 D must not boot remoteproc/IPA/rmtfs: "+name)
    print("P448 IPC CONFIG PASS: only upstream IPC built in; PAS/MSS/IPA/rmtfs OFF")

def boot_check(boot,parser,manual_pas=False):
    spec=importlib.util.spec_from_file_location("a52boot",parser)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dtb=mod.parse_boot(boot.read_bytes())["dtb"]
    with tempfile.TemporaryDirectory() as t:
        p=Path(t)/"out.dtb";p.write_bytes(dtb)
        proc=subprocess.run(["dtc","-I","dtb","-O","dts",str(p)],
                            capture_output=True,text=True)
        if proc.returncode:raise RuntimeError("P448 DTB invalid: "+proc.stderr[-400:])
        dts=proc.stdout
    for node,ids,pid in (("qcom,smp2p-mpss",(435,428),1),
                         ("qcom,smp2p-adsp",(443,429),2),
                         ("qcom,smp2p-cdsp",(94,432),5)):
        match=re.search(re.escape(node)+r"\s*\{([\s\S]{0,2000}?)\n\s*\};",dts)
        if not match:raise RuntimeError("P448 packed DTB lacks "+node)
        blk=match.group(1)
        # dtc prints single-cell PID 1 as 0x01, not necessarily 0x1.
        # Compare the actual parsed u32 cells rather than a literal hex format.
        def cells(prop):
            assignment=re.search(r"(?m)^\s*"+re.escape(prop)+r"\s*=\s*<([^>]*)>\s*;",blk)
            if assignment is None:
                raise RuntimeError("P448 compiled DTB property missing "+node+":"+prop)
            try:
                return tuple(int(value,0) for value in assignment.group(1).split())
            except ValueError as exc:
                raise RuntimeError("P448 invalid compiled DTB cells "+node+":"+prop) from exc
        if cells("qcom,smem")!=ids:
            raise RuntimeError("P448 compiled DTB SMP2P IDs drift "+node)
        if cells("qcom,local-pid")!=(0,):
            raise RuntimeError("P448 compiled DTB local PID drift "+node)
        if cells("qcom,remote-pid")!=(pid,):
            raise RuntimeError("P448 compiled DTB remote PID drift "+node)
    expected=("qcom,ipcc","qcom,smem","master-kernel","slave-kernel")
    if manual_pas:
        expected+=("qcom,sm6350-aoss-qmp","qcom,sm6350-adsp-pas","glink-edge")
    else:
        expected+=("qcom,qmp-mbox",)
    for tok in expected:
        if tok not in dts:raise RuntimeError("P448 packed DTB missing "+tok)
    print("P448 IPC DTB PASS: TG IDs; native manual PAS="+str(manual_pas))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--tg",type=Path)
    p.add_argument("--config",type=Path)
    p.add_argument("--boot",type=Path)
    p.add_argument("--parser",type=Path,default=Path("scripts/38_repack_a52_p1_boot.py"))
    p.add_argument("--check-only",action="store_true")
    p.add_argument("--manual-pas",action="store_true")
    x=p.parse_args()
    if x.tg:tg_check(x.tg)
    if x.boot:
        boot_check(x.boot,x.parser,x.manual_pas)
        if x.config:config_check(x.config,x.manual_pas)
        return
    if not x.check_only:
        updates={p:patch((x.root/p).read_text(),name) for name,p in PATHS.items()}
        for p,txt in updates.items():(x.root/p).write_text(txt)
    check(x.root)
    if x.config:config_check(x.config,x.manual_pas)

if __name__=="__main__":main()
