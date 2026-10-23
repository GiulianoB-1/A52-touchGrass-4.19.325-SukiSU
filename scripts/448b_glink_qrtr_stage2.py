#!/usr/bin/env python3
"""P448B: bounded Step 2 GLINK/QRTR build-only integration.
GLINK SMEM requires later remoteproc registration; E must not boot peers.
"""
import argparse, importlib.util, re, subprocess, tempfile
from pathlib import Path
TAG="A52_PHASE448B_GLINK_QRTR_V1"
ENABLED=("RPMSG","RPMSG_CHAR","RPMSG_QCOM_GLINK",
         "RPMSG_QCOM_GLINK_SMEM","QRTR","QRTR_SMD",
         "QCOM_SYSMON","QCOM_QMI_HELPERS")
FORBIDDEN=("QCOM_Q6V5_PAS","QCOM_Q6V5_ADSP","QCOM_Q6V5_MSS",
           "QCOM_Q6V5_WCSS","QCOM_WCNSS_PIL","QCOM_RMTFS_MEM",
           "A52_MSS_DEFAULT","A52_IPA_DEFAULT")
G=Path("drivers/rpmsg/qcom_glink_smem.c")
Q=Path("net/qrtr/smd.c")
def check_tg(tg):
    s=(tg/"arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi").read_text()
    for node,client in (("glink_modem: modem","IPCC_CLIENT_MPSS"),
                        ("glink_adsp: adsp","IPCC_CLIENT_LPASS"),
                        ("glink_cdsp: cdsp","IPCC_CLIENT_CDSP")):
        m=re.search(re.escape(node)+r"\s*\{([\s\S]*?)\n\s*\};",s)
        if not m or client not in m.group(1) or 'transport = "smem"' not in m.group(1) or 'qcom,glink-channels = "IPCRTR"' not in m.group(1):
            raise RuntimeError("P448B TG GLINK edge mismatch "+node)
    print("P448B TG PASS: MPSS/ADSP/CDSP IPCC SMEM + IPCRTR (NPU deferred)")
def amend(s, start, end, old, new):
    if TAG in s:return s
    if s.count(start)!=1 or s.count(end)!=1:
        raise RuntimeError("P448B probe scope drift")
    a=s.index(start);b=s.index(end,a);body=s[a:b]
    if body.count(old)!=1:raise RuntimeError("P448B probe insertion anchor drift")
    return s[:a]+body.replace(old,new,1)+s[b:]
def source(root,patch):
    g=(root/G).read_text();q=(root/Q).read_text()
    if patch:
        g=amend(g,"struct qcom_glink *qcom_glink_smem_register(",
                "EXPORT_SYMBOL_GPL(qcom_glink_smem_register);",
                "\treturn glink;\n",
                '\t/* '+TAG+' */\n\tdev_info(dev, "P448 GLINK REGISTER remote=%u result=UP\\n", remote_pid);\n\treturn glink;\n')
        q=amend(q,"static int qcom_smd_qrtr_probe(",
                "static void qcom_smd_qrtr_remove(",
                '\tdev_dbg(&rpdev->dev, "Qualcomm SMD QRTR driver probed\\n");\n',
                '\t/* '+TAG+' */\n\tdev_info(&rpdev->dev, "P448 QRTR IPCRTR endpoint=UP\\n");\n')
        (root/G).write_text(g);(root/Q).write_text(q)
    for s,marker in ((g,"P448 GLINK REGISTER"),(q,"P448 QRTR IPCRTR")):
        if s.count(TAG)!=1 or s.count(marker)!=1:
            raise RuntimeError("P448B compiled source marker missing/duplicated")
    print("P448B SOURCE PASS: passive GLINK/QRTR success markers; no peer autostart")
def config(f,manual_pas=False):
    s=f.read_text().splitlines()
    for k in ENABLED:
        if "CONFIG_"+k+"=y" not in s:
            raise RuntimeError("P448B missing transport "+k)
    if manual_pas and "CONFIG_QCOM_Q6V5_PAS=y" not in s:
        raise RuntimeError("P451 G PAS not built")
    for k in FORBIDDEN:
        if manual_pas and k=="QCOM_Q6V5_PAS":continue
        if "CONFIG_"+k+"=y" in s or "CONFIG_"+k+"=m" in s:
            raise RuntimeError("P448B unsafe remoteproc/IPA active "+k)
    if "CONFIG_A52_WAVE_DEFAULT=2" not in s:
        raise RuntimeError("P448B wave default must stay 2")
    print("P448B CONFIG PASS: GLINK/QRTR built-in, PAS/MSS/IPA/RMTFS OFF")
def boot(p,parser):
    spec=importlib.util.spec_from_file_location("p448boot",parser)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    dtb=module.parse_boot(p.read_bytes())["dtb"]
    with tempfile.TemporaryDirectory() as td:
        f=Path(td)/"dtb";f.write_bytes(dtb)
        proc=subprocess.run(["dtc","-I","dtb","-O","dts",str(f)],
                            text=True,capture_output=True)
    if proc.returncode:raise RuntimeError("P448B DTB parse failure")
    for x in ("qcom,glink","IPCRTR","modem","adsp","cdsp"):
        if x not in proc.stdout:raise RuntimeError("P448B missing packed GLINK token "+x)
    print("P448B DTB PASS: TG GLINK layout present; transport cannot bind without later remoteproc")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--tg",type=Path);p.add_argument("--config",type=Path)
    p.add_argument("--boot",type=Path);p.add_argument("--check-only",action="store_true")
    p.add_argument("--parser",type=Path,default=Path("scripts/38_repack_a52_p1_boot.py"))
    p.add_argument("--manual-pas",action="store_true")
    a=p.parse_args()
    if a.tg:check_tg(a.tg)
    if a.boot:boot(a.boot,a.parser)
    else:source(a.root,not a.check_only)
    if a.config:config(a.config,a.manual_pas)
if __name__=="__main__":main()
