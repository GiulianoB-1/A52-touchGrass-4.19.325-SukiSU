#!/usr/bin/env python3
"""446L read-only CB2 live FSR/FAR/FSYNR0/SCTLR/TTBR0 on TG/GKI.
Only reads if the SMMU device is already runtime-active. Never powers it up.
"""
import argparse
from pathlib import Path
MARK="A52_PHASE446L_CB2_LIVE_V1"

def one(s,a,b,w):
    if s.count(a)!=1: raise RuntimeError(f"446L {w}: anchor count {s.count(a)}")
    return s.replace(a,b,1)

GKI=r'''/* A52_PHASE446L_CB2_LIVE_V1: record CB2 and raw route SMR3/S2CR3.
 * Caller must not issue any SMMU power transition in the hot path.
 */
void a52_p446l_cb2_live(u32 *route,u32 *fsr,u64 *far,
                        u32 *fsynr0,u32 *sctlr,u64 *ttbr0)
{
    struct arm_smmu_device *s=READ_ONCE(a52_p426_apps_smmu);
    if (!s || !s->dev || !pm_runtime_active(s->dev) ||
        s->num_context_banks <= 2U)
        return;
    *route=arm_smmu_gr0_read(s,ARM_SMMU_GR0_S2CR(3));
    *fsr=arm_smmu_cb_read(s,2,ARM_SMMU_CB_FSR);
    *far=arm_smmu_cb_readq(s,2,ARM_SMMU_CB_FAR);
    *fsynr0=arm_smmu_cb_read(s,2,ARM_SMMU_CB_FSYNR0);
    *sctlr=arm_smmu_cb_read(s,2,ARM_SMMU_CB_SCTLR);
    *ttbr0=arm_smmu_cb_readq(s,2,ARM_SMMU_CB_TTBR0);
}
EXPORT_SYMBOL_GPL(a52_p446l_cb2_live);

'''

TG=r'''/* A52_PHASE446L_CB2_LIVE_V1 */
static struct arm_smmu_device *a52_p446l_apps_smmu;
void a52_p446l_cb2_live(u32 *route,u32 *fsr,u64 *far,
                        u32 *fsynr0,u32 *sctlr,u64 *ttbr0)
{
    struct arm_smmu_device *s=READ_ONCE(a52_p446l_apps_smmu);
    void __iomem *cb;
    if (!s || !s->dev || !pm_runtime_active(s->dev) ||
        s->num_context_banks <= 2U)
        return;
    cb=ARM_SMMU_CB(s,2);
    *route=readl_relaxed(ARM_SMMU_GR0(s)+ARM_SMMU_GR0_S2CR(3));
    *fsr=readl_relaxed(cb+ARM_SMMU_CB_FSR);
    *far=readq_relaxed(cb+ARM_SMMU_CB_FAR);
    *fsynr0=readl_relaxed(cb+ARM_SMMU_CB_FSYNR0);
    *sctlr=readl_relaxed(cb+ARM_SMMU_CB_SCTLR);
    *ttbr0=readq_relaxed(cb+ARM_SMMU_CB_TTBR0);
}
EXPORT_SYMBOL_GPL(a52_p446l_cb2_live);

'''

def patch(root,kind):
    path=(root/"drivers/iommu/arm/arm-smmu/arm-smmu.c" if kind=="gki"
          else root/"drivers/iommu/arm-smmu.c")
    s=path.read_text()
    if MARK in s:return
    if kind=="gki":
        if "static struct arm_smmu_device *a52_p426_apps_smmu;" not in s:
            raise RuntimeError("GKI 426 retained SMMU pointer missing")
        s=one(s,"static void a52_p426_cache_boot_routes(struct arm_smmu_device *smmu)\n",
              GKI+"static void a52_p426_cache_boot_routes(struct arm_smmu_device *smmu)\n",
              "GKI SMMU api")
    else:
        anchor="/* A52_PHASE446_TG_ARM_SMMU"
        s=one(s,anchor,TG+anchor,"TG SMMU api")
        start=s.find("static void a52_p446g_emit_smmu(struct arm_smmu_device *smmu, u32 event,")
        # The first occurrence is a prototype; find the definition with '{'.
        begin=s.find("{",start)
        if begin<0:raise RuntimeError("TG emit helper missing")
        end=s.find("\n}",begin)
        if end<0:raise RuntimeError("TG emit helper end missing")
        block=s[start:end]
        # insert after validation, within the actual helper body
        old="    cb_base = ARM_SMMU_CB(smmu, cb);"
        if old not in block:raise RuntimeError("TG SMMU helper missing CB read")
        block=block.replace(old,"    WRITE_ONCE(a52_p446l_apps_smmu,smmu);\n"+old,1)
        s=s[:start]+block+s[end:]
    path.write_text(s)

def check(root,kind):
    path=(root/"drivers/iommu/arm/arm-smmu/arm-smmu.c" if kind=="gki"
          else root/"drivers/iommu/arm-smmu.c")
    s=path.read_text()
    for tok in (MARK,"a52_p446l_cb2_live","ARM_SMMU_CB_FSYNR0","ARM_SMMU_CB_FAR","ARM_SMMU_CB_S2CR"):
        if tok=="ARM_SMMU_CB_S2CR":continue
        if tok not in s:raise RuntimeError("missing "+tok)
    print(f"446L {kind} CB2 live reader PASS")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--kind",choices=["gki","tg"],required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:patch(a.root,a.kind)
    check(a.root,a.kind)
