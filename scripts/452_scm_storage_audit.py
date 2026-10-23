#!/usr/bin/env python3
"""P452 SCM availability impact audit: hard-preserve P451 UFS ICE/SMMU paths.

Source comparison uses the *actual* successful 451 final-patched source bundle,
not an upstream approximation. Any divergence requires fresh analysis before CI.
This is a source gate, not a runtime safety certification.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

BASELINE_SHA256 = {
 "drivers/scsi/ufs/ufs-qcom-ice.c":"54f6ec3105812be5e9e954e9aaada4e3403f9d616f86f669f6e91bae74c6f167",
 "drivers/scsi/ufs/ufs-qcom.c":"2fa51b276b07bb8a9395bd6d85ccd07e07e5ae4b07b6a170c950ac9904978fcd",
 "drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c":"e671bac9aaac9e75ec8a4e0ddd0c7b858dd63e7bb6ecfb32ade4d6c51a9f5e24",
 "drivers/firmware/qcom_scm.c":"ac216893ba4841a1745fdd92059baf1c33ab1dbe2b603575b76fb3d32e838fba",
}
# All qcom_scm_* caller source paths present in the P451 final source.
# Even disabled paths are enumerated so an unexpected new caller is a blocker.
KNOWN_CALLERS = {
 "drivers/remoteproc/qcom_wcnss.c":"disabled",
 "drivers/remoteproc/qcom_q6v5_mss.c":"disabled",
 "drivers/remoteproc/qcom_q6v5_pas.c":"PAS manual start",
 "drivers/pinctrl/qcom/pinctrl-msm.c":"active existing SCM calls: inspect runtime",
 "drivers/mmc/host/sdhci-msm.c":"disabled",
 "drivers/gpu/drm/msm/adreno/a5xx_gpu.c":"disabled",
 "drivers/gpu/drm/msm/adreno/adreno_gpu.c":"disabled",
 "drivers/gpu/drm/msm/hdmi/hdmi_hdcp.c":"disabled",
 "drivers/irqchip/qcom-pdc.c":"active existing SCM calls: inspect runtime",
 "drivers/cpuidle/cpuidle-qcom-spm.c":"must stay uncompiled",
 "drivers/media/platform/qcom/venus/firmware.c":"disabled",
 "drivers/soc/qcom/rmtfs_mem.c":"disabled",
 "drivers/soc/qcom/mdt_loader.c":"PAS firmware",
 "drivers/soc/qcom/ocmem.c":"disabled",
 "drivers/net/wireless/ath/ath10k/qmi.c":"disabled",
 "drivers/net/ipa/ipa_main.c":"disabled",
 "drivers/scsi/ufs/ufs-qcom-ice.c":"active but compile-time ICE bypass",
 "drivers/iommu/arm/arm-smmu/qcom_iommu.c":"disabled",
 "drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c":"active, source hash and handoff unchanged",
}
EXCLUDED_INTERNAL = ("drivers/firmware/qcom_scm.c",
                     "drivers/firmware/qcom_scm-legacy.c",
                     "drivers/firmware/qcom_scm-smc.c",
                     "drivers/firmware/qcom_scm.h")
NEEDED = ("QCOM_SCM","SCSI_UFS_QCOM","SCSI_UFS_CRYPTO",
          "BLK_INLINE_ENCRYPTION_FALLBACK","ARM_SMMU","QCOM_AOSS_QMP")
MUST_OFF = ("QCOM_SCM_DOWNLOAD_MODE_DEFAULT","QCOM_IOMMU",
            "VIDEO_QCOM_VENUS","DRM_MSM","MMC_SDHCI_MSM",
            "QCOM_OCMEM","QCOM_RMTFS_MEM","QCOM_Q6V5_MSS",
            "QCOM_WCNSS_PIL","QCOM_WCNSS_CTRL","A52_MSS_DEFAULT",
            "A52_IPA_DEFAULT")
def config_values(path):
    cfg=path.read_text()
    vals={}
    for line in cfg.splitlines():
        m=re.fullmatch(r"CONFIG_(\w+)=(.+)",line)
        n=re.fullmatch(r"# CONFIG_(\w+) is not set",line)
        if m:vals[m[1]]=m[2]
        if n:vals[n[1]]="n"
    return vals
def fail(msg):raise RuntimeError("P452 SCM STORAGE BLOCKED: "+msg)
def audit(root, config, manual_pas, report):
    vals=config_values(config)
    for k in NEEDED:
        if vals.get(k)!="y":fail("expected "+k+"=y, got "+str(vals.get(k)))
    for k in MUST_OFF:
        if vals.get(k,"n")!="n":fail("unexpected "+k+"="+vals[k])
    if manual_pas and vals.get("QCOM_Q6V5_PAS")!="y":
        fail("manual SM6350 PAS must be built-in")
    if vals.get("QCOM_SCM_DOWNLOAD_MODE_DEFAULT","n")!="n":
        fail("SCM download-mode default ON")
    tested={}
    for path,want in BASELINE_SHA256.items():
        actual=root/path
        if not actual.is_file():fail("missing source "+path)
        got=hashlib.sha256(actual.read_bytes()).hexdigest()
        if got!=want:fail("source drift in "+path+": "+got+" expected "+want)
        tested[path]=got
    ice=(root/"drivers/scsi/ufs/ufs-qcom-ice.c").read_text()
    init=ice[ice.index("int ufs_qcom_ice_init("):]
    markers=("#define A52_UFS_ICE_SAFE_BRINGUP", "#if A52_UFS_ICE_SAFE_BRINGUP",
             "hba->caps &= ~UFSHCD_CAP_CRYPTO;", "qcom_scm_ice_available()")
    if any(x not in ice for x in markers):fail("ICE safe bypass markers missing")
    if not re.search(r"#define A52_UFS_ICE_SAFE_BRINGUP\s+1\b",ice):
        fail("ICE bypass macro not enabled")
    # Verify active preprocessor branch clears cap and RETURNS BEFORE SCM/ICE.
    before_scm=init.split("qcom_scm_ice_available()",1)[0]
    if not re.search(r"#if A52_UFS_ICE_SAFE_BRINGUP\b[\s\S]*?"
                     r"hba->caps &= ~UFSHCD_CAP_CRYPTO;\s*return 0;\s*#endif",
                     before_scm):
        fail("ICE bypass no longer returns before SCM/ICE MMIO")
    if "devm_ioremap_resource" in before_scm:
        fail("ICE register mapping moved before guarded early return")
    smmu=(root/"drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c").read_text()
    if "if (!scm && !downstream_handoff)" not in smmu:
        fail("Lagoon qcom,skip-init handoff gate changed")
    if not all(x in smmu for x in ('"qcom,skip-init"','"qcom,qsmmu-v500"')):
        fail("SMMU downstream compatibility lost")
    fw=(root/"drivers/firmware/qcom_scm.c").read_text()
    if not all(x in fw for x in ("return !!__scm;",
               'if (download_mode)', 'CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT',
               'P452 SCM PROBE rc=0 dload_default=%d')):
        fail("SCM availability/download-mode default code changed")
    callers={}
    for path,label in KNOWN_CALLERS.items():
        src=root/path
        if not src.is_file():fail("known SCM caller vanished: "+path)
        content=src.read_text(errors="replace")
        matched=sorted(set(re.findall(r"\b(qcom_scm_[a-zA-Z0-9_]+)\s*\(",content)))
        callers[path]={"classification":label,"calls":matched}
        if not matched:fail("SCM caller unexpectedly changed: "+path)
    # Scan all relevant source paths so new SCM consumers cannot escape review.
    unexpected=[]
    for directory in ("drivers","arch/arm64"):
        parent=root/directory
        if not parent.exists():fail("missing "+directory)
        for p in parent.rglob("*.c"):
            rel=str(p.relative_to(root))
            if rel in KNOWN_CALLERS or rel in EXCLUDED_INTERNAL:continue
            if p.stat().st_size>1000000:continue
            if re.search(r"\bqcom_scm_[a-zA-Z0-9_]+\s*\(",p.read_text(errors="replace")):
                unexpected.append(rel)
    if unexpected:fail("new SCM source call sites must be reviewed "+repr(unexpected[:30]))
    result={"status":"P452 SCM STORAGE SOURCE AUDIT PASS",
            "reference":"P451 DAILY-431 actual final-patched source tar",
            "no_hardware_or_runtime_validation":True,
            "ufs_ice_bypass_before_scm":True,
            "scm_download_mode_default":vals.get("QCOM_SCM_DOWNLOAD_MODE_DEFAULT","n"),
            "adsp_pas_manual_variant":manual_pas,
            "baseline_file_hashes":tested,"scm_callers":callers,
            "excluded_internal_implementations":list(EXCLUDED_INTERNAL)}
    if report:
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print("P452 SCM STORAGE SOURCE AUDIT PASS: UFS ICE bypass exact, SMMU handoff unchanged, download_mode=n, callers="+str(len(callers)))
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--config",type=Path,required=True)
    p.add_argument("--report",type=Path)
    p.add_argument("--manual-pas",action="store_true")
    a=p.parse_args(); audit(a.root,a.config,a.manual_pas,a.report)
if __name__=="__main__":main()
