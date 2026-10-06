#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE446B_GATE_CB_REFRESH_V1"


def die(msg: str) -> None:
    raise SystemExit("Phase446b: " + msg)


def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return s.replace(old, new, 1)


def replace_c_function(s: str, needle: str, replacement: str) -> str:
    start = s.find(needle)
    if start < 0:
        die(f"function not found: {needle}")
    brace = s.find("{", start)
    if brace < 0:
        die(f"opening brace missing: {needle}")
    depth = 0
    i = brace
    while i < len(s):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[:start] + replacement.rstrip() + "\n" + s[i + 1:]
        i += 1
    die(f"unterminated function: {needle}")


def patch_central(s: str) -> str:
    if MARK in s:
        return s

    old_gate = """    gcc_ahb=p446_r(p446_gcc,0x1700c); disp_ahb=mdp_clk=gdsc=byte0=pclk0=~0U;
    r.clk[0]=gcc_ahb; r.clk[1]=p446_r(p446_gcc,0x1701c);
    if (!(gcc_ahb & BIT(31))) { disp_ahb=p446_r(p446_dispcc,0x104c); mdp_clk=p446_r(p446_dispcc,0x1010); gdsc=p446_r(p446_dispcc,0x1004); byte0=p446_r(p446_dispcc,0x102c); pclk0=p446_r(p446_dispcc,0x100c); r.clk[7]=p446_r(p446_dispcc,0x1034); } else r.clk[7]=~0U;
    r.clk[2]=disp_ahb; r.clk[3]=mdp_clk; r.clk[4]=byte0; r.clk[5]=pclk0; r.clk[6]=gdsc;
    r.refgen=p446_r(p446_refgen,0x80); r.pll_lock=~0U;
    safe_mdp=!(gcc_ahb & BIT(31)) && !(disp_ahb & BIT(31)) && !(mdp_clk & BIT(31)) && !!(gdsc & BIT(31));
    safe_dsi=safe_mdp && !(byte0 & BIT(31)) && !(pclk0 & BIT(31));
"""
    new_gate = """    /* Phase446b: gcc_disp_ahb_clk is critical + HW-gated. Its CLK_OFF bit is
     * not a valid safety gate in HWCG mode. Read DISPCC unconditionally.
     */
    gcc_ahb=p446_r(p446_gcc,0x1700c);
    disp_ahb=p446_r(p446_dispcc,0x104c);
    mdp_clk=p446_r(p446_dispcc,0x1010);
    gdsc=p446_r(p446_dispcc,0x1004);
    byte0=p446_r(p446_dispcc,0x102c);
    pclk0=p446_r(p446_dispcc,0x100c);
    r.clk[0]=gcc_ahb; r.clk[1]=p446_r(p446_gcc,0x1701c);
    r.clk[2]=disp_ahb; r.clk[3]=mdp_clk; r.clk[4]=byte0; r.clk[5]=pclk0; r.clk[6]=gdsc;
    r.clk[7]=p446_r(p446_dispcc,0x1034);
    r.refgen=p446_r(p446_refgen,0x80); r.pll_lock=~0U;
    safe_mdp=!!(gdsc & BIT(31)) && !(disp_ahb & BIT(31)) && !(mdp_clk & BIT(31));
    safe_dsi=safe_mdp && !(byte0 & BIT(31)) && !(pclk0 & BIT(31));
"""
    s = one(s, old_gate, new_gate, "clock/power gate")

    decl_anchor = "void a52_p446_mark(u32 event,u32 aux0,u32 aux1)\n"
    decl = (
        "extern int a52_p446_read_cb2(u32 *smr,u32 *s2cr,u32 *cb,u32 *sctlr,"
        "u64 *ttbr0,u32 *tcr,u32 *fsr);\n"
        "/* A52_PHASE446B_LIVE_CB_READ */\n"
    )
    if decl not in s:
        s = one(s, decl_anchor, decl + decl_anchor, "live CB read declaration")

    old_smmu = """    spin_lock_irqsave(&p446_meta_lock,f); sc=p446_smmu; spin_unlock_irqrestore(&p446_meta_lock,f);
    if(sc.valid){ r.flags|=P446_F_SMMU_VALID; r.smr=sc.smr; r.s2cr=sc.s2cr; r.cb=sc.cb; r.sctlr=sc.sctlr; r.ttbr0=sc.ttbr0; r.tcr=sc.tcr; r.fsr=sc.fsr; r.smmu_aux=sc.aux; }
"""
    new_smmu = """    spin_lock_irqsave(&p446_meta_lock,f); sc=p446_smmu; spin_unlock_irqrestore(&p446_meta_lock,f);
    {
        u32 l_smr=~0U,l_s2cr=~0U,l_cb=~0U,l_sctlr=~0U,l_tcr=~0U,l_fsr=~0U;
        u64 l_ttbr0=~0ULL;
        int lrc=-1;
        bool smmu_internal=(event==1U || event==8U || event==9U ||
                            event==0x70U || event==0x71U ||
                            event==0x74U || event==0x75U);
        if(!smmu_internal)
            lrc=a52_p446_read_cb2(&l_smr,&l_s2cr,&l_cb,&l_sctlr,&l_ttbr0,&l_tcr,&l_fsr);
        if(!lrc){
            r.flags|=P446_F_SMMU_VALID; r.smr=l_smr; r.s2cr=l_s2cr; r.cb=l_cb;
            r.sctlr=l_sctlr; r.ttbr0=l_ttbr0; r.tcr=l_tcr; r.fsr=l_fsr;
            r.smmu_aux=0x446b0001U;
        } else if(sc.valid){
            r.flags|=P446_F_SMMU_VALID; r.smr=sc.smr; r.s2cr=sc.s2cr; r.cb=sc.cb;
            r.sctlr=sc.sctlr; r.ttbr0=sc.ttbr0; r.tcr=sc.tcr; r.fsr=sc.fsr; r.smmu_aux=sc.aux;
        }
    }
"""
    s = one(s, old_smmu, new_smmu, "per-record SMMU refresh")

    old_dbg = """    {
        u32 ai=0, aa=0, nz=0;
        for (ai=0; ai<4; ai++) if (r.sspp[ai][2]) { if (!nz) aa=r.sspp[ai][2]; nz |= BIT(ai); }
        a52_ackfr_record("P446 q=%u e=%x f=%u fr=%x i2=%x sa=%x sm=%x cb=%u s2=%x b0=%llu/%llu cr=%08x",
            r.seq,r.event,r.flags,r.frame,r.top0c,aa,nz,r.actual_cb,r.s2cr,
            (unsigned long long)r.bus[0].ab,(unsigned long long)r.bus[0].ib,r.crc32);
    }
"""
    s = one(
        s, old_dbg,
        "    /* A52_PHASE446B_DEBUG_MIRROR_DROPPED: the Phase414 partition transport did not persist P446. */\n",
        "drop ineffective debug-partition mirror",
    )

    marker_anchor = 'static const char p446_marker[] __used = "A52_PHASE446_EARLY_SPLASH_HEARTBEAT_V1";'
    if marker_anchor in s:
        s = s.replace(
            marker_anchor,
            marker_anchor + '\nstatic const char p446b_marker[] __used = "' + MARK + '";',
            1,
        )
    else:
        s += '\nstatic const char p446b_marker[] __used = "' + MARK + '";\n'
    return s


def patch_arm_gki(s: str) -> str:
    if "A52_PHASE446B_LIVE_CB_API" in s:
        return s

    replacement = r'''static struct arm_smmu_device *a52_p446b_sample_smmu;
static int a52_p446b_sample_sme = -1;

/* A52_PHASE446B_LIVE_CB_API */
int a52_p446_read_cb2(u32 *smr,u32 *s2cr,u32 *cb,u32 *sctlr,
                      u64 *ttbr0,u32 *tcr,u32 *fsr)
{
    struct arm_smmu_device *smmu=READ_ONCE(a52_p446b_sample_smmu);
    int sme=READ_ONCE(a52_p446b_sample_sme);
    u32 sr=~0U,s2=~0U,c=~0U;
    int ret;

    if(!smr || !s2cr || !cb || !sctlr || !ttbr0 || !tcr || !fsr)
        return -EINVAL;
    if(!smmu || sme<0 || sme>=smmu->num_mapping_groups)
        return -ENODEV;

    ret=arm_smmu_rpm_get(smmu);
    if(ret<0)
        return ret;

    if(smmu->smrs)
        sr=arm_smmu_gr0_read(smmu,ARM_SMMU_GR0_SMR(sme));
    s2=arm_smmu_gr0_read(smmu,ARM_SMMU_GR0_S2CR(sme));
    c=FIELD_GET(ARM_SMMU_S2CR_CBNDX,s2);
    if(c>=smmu->num_context_banks){
        arm_smmu_rpm_put(smmu);
        return -ERANGE;
    }

    *smr=sr; *s2cr=s2; *cb=c;
    *sctlr=arm_smmu_cb_read(smmu,c,ARM_SMMU_CB_SCTLR);
    *ttbr0=arm_smmu_cb_readq(smmu,c,ARM_SMMU_CB_TTBR0);
    *tcr=arm_smmu_cb_read(smmu,c,ARM_SMMU_CB_TCR);
    *fsr=arm_smmu_cb_read(smmu,c,ARM_SMMU_CB_FSR);
    arm_smmu_rpm_put(smmu);
    return 0;
}
EXPORT_SYMBOL_GPL(a52_p446_read_cb2);

static void a52_p446_emit_smmu(struct arm_smmu_device *smmu,u32 event,int sme,u32 cb,u32 aux)
{
    u32 smr=~0U,s2cr=~0U,sctlr=~0U,tcr=~0U,fsr=~0U; u64 ttbr=~0ULL; int ret;
    if(!smmu || sme<0 || sme>=smmu->num_mapping_groups || cb>=smmu->num_context_banks) return;
    ret=arm_smmu_rpm_get(smmu); if(ret<0)return;
    if(smmu->smrs) smr=arm_smmu_gr0_read(smmu,ARM_SMMU_GR0_SMR(sme));
    s2cr=arm_smmu_gr0_read(smmu,ARM_SMMU_GR0_S2CR(sme));
    if(smmu->smrs && FIELD_GET(ARM_SMMU_SMR_ID,smr)==0x800){
        WRITE_ONCE(a52_p446b_sample_smmu,smmu);
        WRITE_ONCE(a52_p446b_sample_sme,sme);
    }
    sctlr=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_SCTLR); tcr=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_TCR); fsr=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_FSR); ttbr=arm_smmu_cb_readq(smmu,cb,ARM_SMMU_CB_TTBR0);
    arm_smmu_rpm_put(smmu); a52_p446_set_cb2_state(a52_p446_cb2_reuse ? 1U:0U,cb); a52_p446_note_smmu(event,smr,s2cr,cb,sctlr,ttbr,tcr,fsr,aux);
}'''
    return replace_c_function(s, "static void a52_p446_emit_smmu(", replacement)


def patch_arm_tg(s: str) -> str:
    if "A52_PHASE446B_LIVE_CB_API" in s:
        return s

    replacement = r'''static struct arm_smmu_device *a52_p446b_sample_smmu;
static int a52_p446b_sample_sme = -1;

/* A52_PHASE446B_LIVE_CB_API */
int a52_p446_read_cb2(u32 *smr,u32 *s2cr,u32 *cb,u32 *sctlr,
                      u64 *ttbr0,u32 *tcr,u32 *fsr)
{
    struct arm_smmu_device *smmu=READ_ONCE(a52_p446b_sample_smmu);
    void __iomem *cb_base;
    int sme=READ_ONCE(a52_p446b_sample_sme);
    u32 sr=~0U,s2=~0U,c=~0U;
    int ret;

    if(!smr || !s2cr || !cb || !sctlr || !ttbr0 || !tcr || !fsr)
        return -EINVAL;
    if(!smmu || sme<0 || sme>=smmu->num_mapping_groups)
        return -ENODEV;

    ret=arm_smmu_power_on(smmu->pwr);
    if(ret)
        return ret;

    if(smmu->smrs)
        sr=readl_relaxed(ARM_SMMU_GR0(smmu)+ARM_SMMU_GR0_SMR(sme));
    s2=readl_relaxed(ARM_SMMU_GR0(smmu)+ARM_SMMU_GR0_S2CR(sme));
    c=(s2 >> S2CR_CBNDX_SHIFT) & S2CR_CBNDX_MASK;
    if(c>=smmu->num_context_banks){
        arm_smmu_power_off(smmu->pwr);
        return -ERANGE;
    }

    cb_base=ARM_SMMU_CB(smmu,c);
    *smr=sr; *s2cr=s2; *cb=c;
    *sctlr=readl_relaxed(cb_base+ARM_SMMU_CB_SCTLR);
    *ttbr0=(u64)readl_relaxed(cb_base+ARM_SMMU_CB_TTBR0);
    *ttbr0|=(u64)readl_relaxed(cb_base+ARM_SMMU_CB_TTBR0+4)<<32;
    *tcr=readl_relaxed(cb_base+ARM_SMMU_CB_TCR);
    *fsr=readl_relaxed(cb_base+ARM_SMMU_CB_FSR);
    arm_smmu_power_off(smmu->pwr);
    return 0;
}
EXPORT_SYMBOL_GPL(a52_p446_read_cb2);

static void a52_p446g_emit_smmu(struct arm_smmu_device *smmu, u32 event,
                               int sme, u32 cb, u32 aux)
{
    void __iomem *cb_base;
    u32 smr = ~0U, s2cr = ~0U, sctlr = ~0U, tcr = ~0U, fsr = ~0U;
    u64 ttbr0 = ~0ULL;

    if (!smmu || sme < 0 || sme >= smmu->num_mapping_groups ||
        cb >= smmu->num_context_banks)
        return;

    if (smmu->smrs)
        smr = readl_relaxed(ARM_SMMU_GR0(smmu) + ARM_SMMU_GR0_SMR(sme));
    s2cr = readl_relaxed(ARM_SMMU_GR0(smmu) + ARM_SMMU_GR0_S2CR(sme));
    cb_base = ARM_SMMU_CB(smmu, cb);
    sctlr = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
    tcr = readl_relaxed(cb_base + ARM_SMMU_CB_TCR);
    fsr = readl_relaxed(cb_base + ARM_SMMU_CB_FSR);
    ttbr0 = (u64)readl_relaxed(cb_base + ARM_SMMU_CB_TTBR0);
    ttbr0 |= (u64)readl_relaxed(cb_base + ARM_SMMU_CB_TTBR0 + 4) << 32;
    a52_p446_note_smmu(event, smr, s2cr, cb, sctlr, ttbr0, tcr, fsr, aux);
}'''
    s = replace_c_function(s, "static void a52_p446g_emit_smmu(", replacement)

    old = """		if (smr.id == 0x800)
			a52_p446g_emit_smmu(smmu,1U,i,s2cr.cbndx,0U);"""
    new = """		if (smr.id == 0x800) {
			WRITE_ONCE(a52_p446b_sample_smmu,smmu);
			WRITE_ONCE(a52_p446b_sample_sme,(int)i);
			a52_p446g_emit_smmu(smmu,1U,i,s2cr.cbndx,0U);
		}"""
    s = one(s, old, new, "TG SID 0x800 live-cache")

    # Phase446G had duplicate 12/13 marks inside arm_smmu_enable_s1_translations.
    # Phase446b keeps only the shared KMS EARLY_MAP pre/post markers, so those
    # external checkpoints can safely invoke the live CB read without re-entering
    # SMMU runtime-PM from inside the SMMU driver.
    inner = """\treg = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
\tif (cfg->cbndx == 2)
\t\ta52_p446_mark(12U,cfg->cbndx,reg);
\treg |= SCTLR_M;

\twritel_relaxed(reg, cb_base + ARM_SMMU_CB_SCTLR);
\tif (cfg->cbndx == 2) {
\t\tu32 now = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
\t\ta52_p446_mark(13U,cfg->cbndx,now);
\t}"""
    original = """\treg = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
\treg |= SCTLR_M;

\twritel_relaxed(reg, cb_base + ARM_SMMU_CB_SCTLR);"""
    if inner in s:
        s = s.replace(inner, original, 1)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--kind", choices=("gki","tg"), required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    if a.kind == "gki":
        central = a.root / "drivers/a52_display/msm/a52_phase445.c"
        arm = a.root / "drivers/iommu/arm/arm-smmu/arm-smmu.c"
    else:
        central = a.root / "techpack/display/msm/a52_phase446g.c"
        arm = a.root / "drivers/iommu/arm-smmu.c"

    for p in (central, arm):
        if not p.exists():
            die(f"missing {p}")

    if not a.check_only:
        cs = central.read_text(errors="replace")
        ars = arm.read_text(errors="replace")
        central.write_text(patch_central(cs))
        arm.write_text(patch_arm_gki(ars) if a.kind == "gki" else patch_arm_tg(ars))

    cs = central.read_text(errors="replace")
    ars = arm.read_text(errors="replace")
    required_c = (
        MARK,
        "safe_mdp=!!(gdsc & BIT(31)) && !(disp_ahb & BIT(31)) && !(mdp_clk & BIT(31))",
        "A52_PHASE446B_LIVE_CB_READ",
        "a52_p446_read_cb2(",
        "A52_PHASE446B_DEBUG_MIRROR_DROPPED",
    )
    required_a = (
        "A52_PHASE446B_LIVE_CB_API",
        "a52_p446_read_cb2(",
        "EXPORT_SYMBOL_GPL(a52_p446_read_cb2)",
    )
    missing = [x for x in required_c if x not in cs] + [x for x in required_a if x not in ars]
    if missing:
        die("contract missing: " + ", ".join(missing))

    forbidden = (
        "safe_mdp=!(gcc_ahb & BIT(31))",
        'a52_ackfr_record("P446 q=%u',
    )
    bad = [x for x in forbidden if x in cs]
    if bad:
        die("old Phase446 behavior still present: " + ", ".join(bad))

    print(f"Phase446b {a.kind}: gate fix + per-record live CB read + debug mirror drop PASS")


if __name__ == "__main__":
    main()
