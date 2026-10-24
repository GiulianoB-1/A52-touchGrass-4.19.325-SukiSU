#!/usr/bin/env python3
"""P458 DSP: surgical disabled /firmware/scm node on FINAL boot-v2 DTB.

The P457 boot map is preserved; no runtime SCM platform device exists until
the explicit one-shot sysfs request. Keep P458 pstore ECC48 untouched.
"""
import argparse
import hashlib
import json
import runpy
import subprocess
import tempfile
from pathlib import Path

lib=runpy.run_path("scripts/458_pstore_ecc48_dtb.py")
parse=lib["parse"]
repack=lib["repack"]
pstore_inspect=lib["inspect"]
SCM="/firmware/scm"
def command(*argv, allow_fail=False):
    r=subprocess.run([str(x) for x in argv],capture_output=True,text=True)
    if r.returncode and not allow_fail:
        raise RuntimeError("P458 SCM DT "+repr(argv)+" rc="+str(r.returncode)+" "+r.stderr[-800:])
    return r.stdout.strip() if r.returncode==0 else None

def check(f):
    compat=command("fdtget","-t","s",f,SCM,"compatible")
    status=command("fdtget","-t","s",f,SCM,"status")
    resets=command("fdtget","-t","x",f,SCM,"#reset-cells")
    if compat.split()!=["qcom,scm-sm6350","qcom,scm"] or status!="disabled" or resets!="1":
        raise RuntimeError("P458 SCM DT must be disabled with exact two compatibles and reset-cells=1")
    # P458 ECC48 and duplicate-reservation removal must survive repack.
    info=pstore_inspect(f,patched=True)
    if info["ecc_size"] != [48] or info["duplicate"]:
        raise RuntimeError("P458 lost pstore ECC48 / single reservation")
    return dict(scm_node=SCM,compatible=compat.split(),status=status,
                reset_cells=1,ramoops=info)

def patch(dtb):
    with tempfile.TemporaryDirectory() as d:
        f=Path(d)/"final.dtb"
        f.write_bytes(dtb)
        if command("fdtget","-l",f,"/firmware",allow_fail=True) is None:
            raise RuntimeError("P458 missing /firmware parent")
        if command("fdtget","-t","s",f,SCM,"compatible",allow_fail=True) is not None:
            raise RuntimeError("P458 refuses already-existing SCM node; source must be exact P457")
        command("fdtput","-c",f,SCM)
        command("fdtput","-t","s",f,SCM,"compatible",
                "qcom,scm-sm6350","qcom,scm")
        command("fdtput","-t","x",f,SCM,"#reset-cells","0x1")
        command("fdtput","-t","s",f,SCM,"status","disabled")
        info=check(f)
        return f.read_bytes(),info

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--boot",type=Path,required=True)
    p.add_argument("--output",type=Path)
    p.add_argument("--report",type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    original=a.boot.read_bytes()
    data=parse(original)
    if a.check_only:
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"packed.dtb";f.write_bytes(data["dtb"])
            info=check(f)
        print("P458 SCM DISABLED DTB PASS "+json.dumps(info,sort_keys=True))
        return
    if not a.output or not a.report:
        raise RuntimeError("P458 requires --output and --report")
    dtb,info=patch(data["dtb"])
    result=repack(original,data,dtb)
    a.output.write_bytes(result)
    check_data=parse(result)
    if check_data["dtb"]!=dtb or check_data["kernel"]!=data["kernel"] or check_data["ramdisk"]!=data["ramdisk"]:
        raise RuntimeError("P458 SCM boot-v2 roundtrip changed kernel/ramdisk")
    info.update(boot_sha256=hashlib.sha256(result).hexdigest(),
                dtb_sha256=hashlib.sha256(dtb).hexdigest(),
                scm_runtime_creator="sysfs_only",
                runtime_probe_at_boot="disabled",
                no_auto_adsp_boot=True)
    a.report.write_text(json.dumps(info,indent=2,sort_keys=True)+"\n")
    print("P458 SCM DISABLED DTB PACKAGED "+json.dumps(info,sort_keys=True))

if __name__=="__main__":main()
