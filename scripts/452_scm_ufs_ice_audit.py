#!/usr/bin/env python3
"""P452 SCM availability transition - preserve Lagoon UFS/ICE/SMMU behaviour.

P451 starts with qcom_scm_is_available()==false (no /firmware/scm).
P452 makes upstream SCM available to PAS, but shall NEVER silently turn
on UFS ICE inline crypto or a previously deferred qcom SMMU implementation.
This patch is applied to the compiled tree BEFORE config and strict audit.
"""
import argparse,json,re
from pathlib import Path
TAG="A52_PHASE452_SCM_ICE_SMMU_FREEZE_V1"
ICE=Path("drivers/scsi/ufs/ufs-qcom-ice.c")
SMMU=Path("drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c")
SCM=Path("drivers/firmware/qcom_scm.c")

def once(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P452 {name}: anchor count {n}")
    return s.replace(a,b,1)

def patch(root):
    ice_file=root/ICE
    smmu_file=root/SMMU
    scm_file=root/SCM
    ice=ice_file.read_text()
    smmu=smmu_file.read_text()
    scm=scm_file.read_text()
    if TAG in ice or TAG in smmu:
        check(root)
        return
    ice=once(ice,"#include <linux/qcom_scm.h>",
             "#include <linux/qcom_scm.h>\n#include <linux/of.h>","ICE OF include")
    ice=once(ice,"if (!qcom_scm_ice_available()) {",
        """/* A52_PHASE452_SCM_ICE_SMMU_FREEZE_V1: 451 has no SCM device,
         * therefore ICE is disabled on Lagoon. Keep the same storage
         * encryption mode even when P452 PAS enables the SCM provider.
         * Do not change UFS controller crypto flags on other devices.
         */
        if (of_machine_is_compatible("qcom,lagoon") ||
            !qcom_scm_ice_available()) {
            if (of_machine_is_compatible("qcom,lagoon"))
                dev_info_once(dev,
                     "P452 SCM UFS_ICE_FROZEN off baseline_451=1\\n");""",
        "ICE availability")
    smmu=once(smmu,"#include <linux/of_device.h>",
              "#include <linux/of_device.h>\n#include <linux/of.h>","SMMU OF include")
    smmu=once(smmu,'if (!qcom_scm_is_available())\n\t\treturn ERR_PTR(-EPROBE_DEFER);',
       r'''/* A52_PHASE452_SCM_ICE_SMMU_FREEZE_V1:
         * 451 qcom-smmu-qcom was deferred for lack of upstream SCM.
         * SCM is now present for ADSP only. Keep QCOM SMMU probe behaviour
         * on Lagoon identical to the 451 kernel; vendor SMMU stays owner.
         */
        if (of_machine_is_compatible("qcom,lagoon")) {
            dev_info_once(smmu->dev,
                 "P452 SCM QCOM_SMMU_FROZEN defer baseline_451=1\\n");
            return ERR_PTR(-EPROBE_DEFER);
        }
        if (!qcom_scm_is_available())
            return ERR_PTR(-EPROBE_DEFER);''',
       "SMMU SCM availability")
    ice_file.write_text(ice)
    smmu_file.write_text(smmu)
    scm=once(scm,"\t__get_convention();",
        r'''    __get_convention();
    dev_info(&pdev->dev,
        "P452 SCM PROBED native_5_10=1 dload_default=%d\n",
        IS_ENABLED(CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT));''',
        "SCM probe marker")
    scm_file.write_text(scm)
    check(root)

def check(root):
    ice=(root/ICE).read_text()
    smmu=(root/SMMU).read_text()
    scm=(root/SCM).read_text()
    if "P452 SCM PROBED native_5_10=1" not in scm:
        raise RuntimeError("P452 SCM probe marker missing")
    if ice.count(TAG)!=1 or smmu.count(TAG)!=1:
        raise RuntimeError("P452 freeze missing or duplicated")
    for required,src in [
        ('if (of_machine_is_compatible("qcom,lagoon") ||',ice),
        ("!qcom_scm_ice_available())",ice),
        ("P452 SCM UFS_ICE_FROZEN",ice),
        ('if (of_machine_is_compatible("qcom,lagoon")) {',smmu),
        ("P452 SCM QCOM_SMMU_FROZEN",smmu),
        ("if (!qcom_scm_is_available())",smmu)]:
        if required not in src:
            raise RuntimeError("P452 source safety guard missing "+required)
    print("P452 SCM SOURCE PASS: Lagoon UFS ICE remains OFF; Qualcomm SMMU stays deferred; PAS opt-in only")

def audit_calls(root,out):
    """Enumerate all direct compiled-tree SCM callsites for human review.

    This is source-wide, including ported Samsung drivers. qcom_scm.c itself
    contains the definitions; mark definitions separately by path. A manifest
    of callsites is produced rather than silently assuming only PAS uses SCM.
    """
    records=[]
    names=set()
    for prefix in ("drivers","arch/arm64","fs","mm","kernel"):
        base=root/prefix
        if not base.exists():continue
        for path in base.rglob("*.[ch]"):
            if not path.is_file():continue
            try:s=path.read_text(errors="replace")
            except OSError:continue
            for idx,line in enumerate(s.splitlines(),1):
                found=re.findall(r"\bqcom_scm_[a-zA-Z0-9_]+\s*\(",line)
                if not found:continue
                funcs=sorted(set(x.split("(")[0].strip() for x in found))
                names.update(funcs)
                records.append({"file":str(path.relative_to(root)),
                                "line":idx,"calls":funcs,
                                "text":line.strip()[:210]})
    conds=[r for r in records if any(n in r["calls"] for n in
           ("qcom_scm_is_available","qcom_scm_ice_available"))]
    expected={
      "drivers/remoteproc/qcom_q6v5_pas.c",
      str(ICE),str(SMMU),"drivers/firmware/qcom_scm.c",
    }
    bad=[r for r in conds if r["file"] not in expected]
    obj={"tag":TAG,"total_callsite_lines":len(records),
         "unique_symbols":sorted(names),
         "conditional_availability_sites":conds,
         "unexpected_conditional_callers":bad,
         "callsites":records}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(obj,indent=2)+"\n")
    if bad:
        raise RuntimeError("P452 SCM: additional availability-branching callers require review "+repr(bad[:6]))
    check(root)
    print(f"P452 SCM CALLER AUDIT PASS source_sites={len(records)} symbols={len(names)} conditional_sites={len(conds)}")
    print("P452 SCM KNOWN RISKS: downstream SCM coexistence, Venus/ZAP/PAS secure calls, no normal charging changes")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    p.add_argument("--audit-output",type=Path)
    p.add_argument("--config",type=Path)
    args=p.parse_args()
    if not args.check_only:patch(args.root)
    else:check(args.root)
    if args.config:
        cfg=args.config.read_text()
        if "# CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT is not set" not in cfg:
            raise RuntimeError("P452 unsafe DOWNLOAD_MODE_DEFAULT (must be n)")
        if "CONFIG_QCOM_SCM=y" not in cfg:
            raise RuntimeError("P452 SCM must be built in")
    if args.audit_output:audit_calls(args.root,args.audit_output)
if __name__=="__main__":main()
