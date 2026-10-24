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


# On Lagoon only PAS is opted into native SCM; all other consumers must
# retain P451 no-provider behavior rather than silently changing hardware.
# Source-wide audit includes disabled CONFIG_QCOM_RMTFS_MEM/OCMEM/IPA code,
# ensuring future config changes cannot silently enable SCM-sensitive paths.
INCIDENTAL_SCM_CALLERS=[
 "drivers/remoteproc/qcom_wcnss.c",
 "drivers/remoteproc/qcom_q6v5_mss.c",
 "drivers/mmc/host/sdhci-msm.c",
 "drivers/gpu/drm/msm/adreno/adreno_gpu.c",
 "drivers/cpuidle/cpuidle-qcom-spm.c",
 "drivers/media/platform/qcom/venus/firmware.c",
 "drivers/soc/qcom/rmtfs_mem.c",
 "drivers/soc/qcom/ocmem.c",
 "drivers/net/ipa/ipa_main.c",
]
SCM_FREEZE_TAG="A52_PHASE452_SCM_AVAILABILITY_FROZEN_V1"
def freeze_incidental_scm(root):
    for rel in INCIDENTAL_SCM_CALLERS:
        p=root/rel
        if not p.is_file():raise RuntimeError("P452 missing SCM caller "+rel)
        s=p.read_text()
        if SCM_FREEZE_TAG in s:continue
        n=s.count("qcom_scm_is_available()")+s.count("qcom_scm_ice_available()")
        if not n:raise RuntimeError("P452 SCM availability caller drift "+rel)
        if "#include <linux/of.h>" not in s:
            pos=s.find("#include ")
            if pos<0:raise RuntimeError("P452 no include anchor "+rel)
            s=s[:pos]+"#include <linux/of.h>\n"+s[pos:]
        s=s.replace("qcom_scm_is_available()",
          '(!of_machine_is_compatible("qcom,lagoon") && qcom_scm_is_available())')
        s=s.replace("qcom_scm_ice_available()",
          '(!of_machine_is_compatible("qcom,lagoon") && qcom_scm_ice_available())')
        s=s.replace("#include <linux/of.h>",
          "#include <linux/of.h>\n/* "+SCM_FREEZE_TAG+" */",1)
        p.write_text(s)
def check_incidental_scm(root):
    for rel in INCIDENTAL_SCM_CALLERS:
        s=(root/rel).read_text()
        if s.count(SCM_FREEZE_TAG)!=1:raise RuntimeError("P452 SCM missing marker "+rel)
        if '(!of_machine_is_compatible("qcom,lagoon") && qcom_scm_' not in s:
            raise RuntimeError("P452 SCM missing Lagoon freeze "+rel)

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
    # Execute before ICE MMIO/resource work, not merely inside the
    # qcom_scm_ice_available() fallback. Preserve P451 no-SCM storage mode.
    ice=once(ice,'res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice");',
       '''/* P452 ICE EARLY_FAIL_CLOSED: Lagoon must never initialize ICE
        * when native SCM becomes available for the ADSP port.
        * The shared disable path clears UFSHCD_CAP_CRYPTO.
        */
       if (of_machine_is_compatible("qcom,lagoon")) {
           dev_warn(dev, "P452 SCM UFS_ICE_FROZEN off baseline_451=1\\n");
           goto disable;
       }
       res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ice");''',
       "ICE early disable")
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
    # The P451 qcom-smmu parent MUST probe. The old P452 patch inserted
    # a Lagoon -EPROBE_DEFER before devm_kzalloc; B and D fail to boot.
    # Preserve P451 probe/error paths byte-for-byte except the SCM boolean,
    # which remains false on Lagoon even when PAS later enables native SCM.
    smmu=once(smmu,'bool scm = qcom_scm_is_available();',
       '''/* A52_PHASE452_SCM_ICE_SMMU_FREEZE_V1:
         * Do NOT defer the APPS/KGSL SMMUs on Lagoon. The existing P451
         * downstream_handoff path must run even if PAS exposes SCM.
         */
        bool scm = !of_machine_is_compatible("qcom,lagoon") &&
                   qcom_scm_is_available();''',
       "SMMU availability only; no new return or probe defer")
    ice_file.write_text(ice)
    smmu_file.write_text(smmu)
    scm=once(scm,"\t__get_convention();",
        r'''    __get_convention();
    dev_info(&pdev->dev,
        "P452 SCM PROBED native_5_10=1 dload_default=%d\n",
        IS_ENABLED(CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT));''',
        "SCM probe marker")
    scm_file.write_text(scm)
    freeze_incidental_scm(root)
    check(root)

def check_p97_ice_bypass(ice):
    """Hard fail unless the old P97 ICE short-circuit still disables crypto.

    The Phase97 branch returns before the later P452 Lagoon diagnostic, so
    the P452 marker may be optimized out of Image even in a safe build.
    """
    if not re.search(r"(?m)^#define[ \t]+A52_UFS_ICE_SAFE_BRINGUP[ \t]+1[ \t]*$", ice):
        raise RuntimeError("P452 P97 ICE safe bypass must be defined as 1")
    tag="#if A52_UFS_ICE_SAFE_BRINGUP"
    if ice.count(tag)!=1:
        raise RuntimeError("P452 P97 ICE bypass conditional drift")
    start=ice.index(tag)
    end=ice.find("#endif",start)
    if end<0:
        raise RuntimeError("P452 P97 ICE bypass missing endif")
    body=ice[start:end]
    for marker in ("A52 diagnostic: bypassing ICE for storage bring-up",
                   "hba->caps &= ~UFSHCD_CAP_CRYPTO;", "return 0;"):
        if marker not in body:
            raise RuntimeError("P452 P97 ICE bypass missing "+marker)
    if body.index("hba->caps &= ~UFSHCD_CAP_CRYPTO;") >= body.rindex("return 0;"):
        raise RuntimeError("P452 P97 ICE bypass must clear capability before return")
    if any(x in body for x in ("res = platform_get_resourcebyname(",
                              "qcom_scm_ice_available()", "qcom_ice_readl(")):
        raise RuntimeError("P452 P97 bypass attempts SCM/ICE access")
    if "res = platform_get_resource_byname" not in ice[end:]:
        raise RuntimeError("P452 P97 bypass ordering drift")
    print("P452 ICE P97 HARD_BYPASS PASS macro=1 cap_clear_before_return=1")

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
        ('/* P452 ICE EARLY_FAIL_CLOSED',ice),
        ('goto disable;',ice),
        ('bool scm = !of_machine_is_compatible("qcom,lagoon") &&',smmu),
        ("qcom_scm_is_available();",smmu),
        ("qsmmu = devm_kzalloc(smmu->dev",smmu)]:
        if required not in src:
            raise RuntimeError("P452 source safety guard missing "+required)
    check_p97_ice_bypass(ice)
    check_incidental_scm(root)
    if "P452 SCM QCOM_SMMU_FROZEN defer" in smmu:
        raise RuntimeError("P457 SMMU fatal forced Lagoon defer was not removed")
    if 'if (of_machine_is_compatible("qcom,lagoon")) {\\n' in smmu:
        raise RuntimeError("P457 unexpected Lagoon gated probe branch")
    print("P457 SCM SOURCE PASS: Lagoon ICE bypass, qcom SMMU probe retained with SCM=false on Lagoon, nine caller freezes, manual PAS only")

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
    scoped=set(INCIDENTAL_SCM_CALLERS)
    bad=[r for r in conds if r["file"] not in expected and
         not (r["file"] in scoped and
              '!of_machine_is_compatible("qcom,lagoon")' in r["text"])]
    # Exact manifest of frozen conditional SCM sites, never silently exempt.
    if {r["file"] for r in conds if r["file"] in scoped}!=scoped:
        raise RuntimeError("P452 incidental availability source inventory drift")
    obj={"tag":TAG,"total_callsite_lines":len(records),
         "unique_symbols":sorted(names),
         "conditional_availability_sites":conds,
         "unexpected_conditional_callers":bad,
         "callsites":records}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(obj,indent=2)+"\n")
    if bad:
        raise RuntimeError("P452 SCM: additional availability-branching callers require review "+repr(bad))
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
