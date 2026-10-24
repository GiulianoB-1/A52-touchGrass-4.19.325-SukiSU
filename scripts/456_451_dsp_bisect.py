#!/usr/bin/env python3
"""P456 controlled source bisects from 451-DSP-434.

A: 451 + only P452 UFS-ICE guard.
B: 451 + only P452 qcom SMMU and nine availability freezes.
C: 451 + only P452 H0 manual charger (guard-first/v2 fixes).
D: full Phase452 G phase chain with ICE source rolled back to P451,
   retaining SCM/SMMU/freeze/H0 sources, but P451 DSP packaging (SCM-off).
"""
import argparse, hashlib, json, runpy
from pathlib import Path

SCM_SCRIPT=Path("scripts/452_scm_ufs_ice_audit.py")
H0_SCRIPT=Path("scripts/452_h0_sm5714_conservative.py")
ICE="drivers/scsi/ufs/ufs-qcom-ice.c"
SMMU="drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c"
SCM="drivers/firmware/qcom_scm.c"

def digest(data):return hashlib.sha256(data).hexdigest()

def main():
    arg=argparse.ArgumentParser()
    arg.add_argument("--root",type=Path,required=True)
    arg.add_argument("--mode",choices=["a","b","c","d"],required=True)
    arg.add_argument("--pre-ice",type=Path)
    arg.add_argument("--report",type=Path,required=True)
    a=arg.parse_args()
    root=a.root.resolve()
    scm=runpy.run_path(str(SCM_SCRIPT))
    frozen=scm["INCIDENTAL_SCM_CALLERS"]
    source_paths=[ICE,SMMU,SCM,*frozen,scm.get("SRC","")]
    source_paths=list(dict.fromkeys(x for x in source_paths if x))
    original={f:(root/f).read_bytes() for f in source_paths}
    if a.mode in ("a","b"):
        scm["patch"](root)
        retained=({ICE} if a.mode=="a" else {SMMU,*frozen})
        for f,raw in original.items():
            if f not in retained:
                (root/f).write_bytes(raw)
    elif a.mode=="c":
        h0=runpy.run_path(str(H0_SCRIPT))
        path=root/h0["SRC"]
        path.write_text(h0["patch"](path.read_text()))
        h0["check"](root)
    else:
        if not a.pre_ice or not a.pre_ice.is_file():
            raise RuntimeError("D requires pre-P452 original UFS ICE source")
        # The complete 452 G chain already applied H0, SCM SMMU and freeze.
        # Remove only P452's ICE modification; preserve P97 early bypass.
        old=a.pre_ice.read_bytes()
        if b"#define A52_UFS_ICE_SAFE_BRINGUP" not in old:
            raise RuntimeError("D baseline ICE bypass contract missing")
        (root/ICE).write_bytes(old)
        h0=runpy.run_path(str(H0_SCRIPT))
        h0["check"](root)
        for f in [SMMU,*frozen]:
            if scm["SCM_FREEZE_TAG"].encode() not in (root/f).read_bytes() and f in frozen:
                raise RuntimeError("D missing incidental freeze "+f)
        if scm["TAG"].encode() not in (root/SMMU).read_bytes():
            raise RuntimeError("D missing qcom SMMU guard")
    changed=[]
    for f,was in original.items():
        now=(root/f).read_bytes()
        if now!=was:
            changed.append({"file":f,"before_sha256":digest(was),"after_sha256":digest(now)})
    if a.mode=="a" and [x["file"] for x in changed]!=[ICE]:
        raise RuntimeError("A changed files besides ICE: "+str(changed))
    if a.mode=="b" and set(x["file"] for x in changed)!={SMMU,*frozen}:
        raise RuntimeError("B has unexpected caller delta")
    if a.mode=="c" and changed:
        raise RuntimeError("C modified SCM/SMMU/ICE")
    ice=(root/ICE).read_text()
    if not ("#define A52_UFS_ICE_SAFE_BRINGUP" in ice and
            "hba->caps &= ~UFSHCD_CAP_CRYPTO;" in ice and
            "A52 diagnostic: bypassing ICE for storage bring-up" in ice):
        raise RuntimeError("P97 fail-closed ICE bypass missing")
    if a.mode in ("b","c","d") and "P452 ICE EARLY_FAIL_CLOSED" in ice:
        raise RuntimeError("P452 ICE guard unexpectedly included")
    if a.mode=="a" and "P452 ICE EARLY_FAIL_CLOSED" not in ice:
        raise RuntimeError("A ICE guard absent")
    if a.mode in ("a","c") and "A52_PHASE452_SCM_AVAILABILITY_FROZEN_V1" in (root/frozen[0]).read_text():
        raise RuntimeError("scope isolation failed: SCM caller modified")
    report={"mode":a.mode,"label":{
       "a":"451 + P452 ICE guard only",
       "b":"451 + SMMU guard and nine SCM-caller freezes only",
       "c":"451 + H0 only, conservative v2",
       "d":"452 G phase chain with ICE restored from original 451, P451 SCM-off DTB"
       }[a.mode],
       "base_commit":"b80b04b64c0c7dfcf48a3aac0174f7b834f8b142",
       "phase452_reference":"f4d761f945f742e143b537412dca7bf4da7b43f8",
       "compilation_tested":False,"hardware_validated":False,
       "scm_firmware_node_expected":False,"adsp_start":"manual",
       "charger_h0":"not present" if a.mode in ("a","b") else "compiled but disabled until explicit manual opt-in",
       "ice_phase97_bypass_already_in_451":True,
       "changed_scm_ice_smmu_files":changed}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print("P456 SCOPE PASS",a.mode,report["label"],"files=",len(changed))

if __name__=="__main__":main()
