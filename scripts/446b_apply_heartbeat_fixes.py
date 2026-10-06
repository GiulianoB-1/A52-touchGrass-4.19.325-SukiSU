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
    pos = 0
    start = -1
    brace = -1
    while True:
        cand = s.find(needle, pos)
        if cand < 0:
            break
        cand_brace = s.find("{", cand)
        cand_semi = s.find(";", cand)
        if cand_brace >= 0 and (cand_semi < 0 or cand_brace < cand_semi):
            start = cand
            brace = cand_brace
            break
        pos = cand + len(needle)
    if start < 0 or brace < 0:
        die(f"function definition not found: {needle}")
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
    *tcr=arm_smmu_cb_read(smmu,c,ARM_SMMU_CB_TTBCR);
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
    sctlr=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_SCTLR); tcr=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_TTBCR); fsr=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_FSR); ttbr=arm_smmu_cb_readq(smmu,cb,ARM_SMMU_CB_TTBR0);
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
    *tcr=readl_relaxed(cb_base+ARM_SMMU_CB_TTBCR);
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
    tcr = readl_relaxed(cb_base + ARM_SMMU_CB_TTBCR);
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



MICRO_MARK = "A52_PHASE446B_FIRST_COMMIT_MICROSCOPE_V1"

def _micro_decl(s: str, anchor: str, marker: str) -> str:
    if marker in s:
        return s
    if anchor not in s:
        die(f"{marker}: declaration anchor missing")
    return s.replace(anchor,
        f"extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* {marker} */\n" + anchor, 1)

def patch_central_micro(s: str) -> str:
    if MICRO_MARK in s:
        return s
    old_regs = """        r.pp[0]=p446_r(p446_mdp,0x71000+0x030); r.pp[1]=p446_r(p446_mdp,0x71000+0x02c); r.pp[2]=p446_r(p446_mdp,0x71000+0x014); r.pp[3]=p446_r(p446_mdp,0x71000+0x000);
        for(i=0;i<4;i++){ o=sspp_off[i]; r.sspp[i][0]=p446_r(p446_mdp,o+0x00); r.sspp[i][1]=p446_r(p446_mdp,o+0x08); r.sspp[i][2]=p446_r(p446_mdp,o+0x14); r.sspp[i][3]=p446_r(p446_mdp,o+0x30); r.sspp[i][4]=p446_r(p446_mdp,o+0x34); r.sspp[i][5]=p446_r(p446_mdp,o+0x38); }
        r.ctl[0]=p446_r(p446_mdp,0x2000+0x000); r.ctl[1]=p446_r(p446_mdp,0x2000+0x040); r.ctl[2]=p446_r(p446_mdp,0x2000+0x070); r.ctl[3]=p446_r(p446_mdp,0x2000+0x0a0); r.ctl[4]=p446_r(p446_mdp,0x2000+0x018); r.ctl[5]=p446_r(p446_mdp,0x2000+0x064); r.ctl[6]=p446_r(p446_mdp,0x2000+0x014);
"""
    new_regs = """        /* Phase446b first-commit microscope. Binary layout stays 508 bytes.
         * pp[]: AUTOREFRESH, LINE_COUNT, INT_COUNT, OUT_LINE_COUNT.
         * sspp[][0..5]: SRC_SIZE, SRC2_ADDR, SRC0_ADDR, SRC_FORMAT,
         *                 SRC_YSTRIDE0, SRC_OP_MODE.
         * ctl[]: LAYER, EXT, EXT2, EXT3, FLUSH, START, STATUS.
         */
        r.pp[0]=p446_r(p446_mdp,0x71000+0x030); r.pp[1]=p446_r(p446_mdp,0x71000+0x02c); r.pp[2]=p446_r(p446_mdp,0x71000+0x014); r.pp[3]=p446_r(p446_mdp,0x71000+0x028);
        for(i=0;i<4;i++){ o=sspp_off[i]; r.sspp[i][0]=p446_r(p446_mdp,o+0x00); r.sspp[i][1]=p446_r(p446_mdp,o+0x1c); r.sspp[i][2]=p446_r(p446_mdp,o+0x14); r.sspp[i][3]=p446_r(p446_mdp,o+0x30); r.sspp[i][4]=p446_r(p446_mdp,o+0x24); r.sspp[i][5]=p446_r(p446_mdp,o+0x38); }
        r.ctl[0]=p446_r(p446_mdp,0x2000+0x000); r.ctl[1]=p446_r(p446_mdp,0x2000+0x040); r.ctl[2]=p446_r(p446_mdp,0x2000+0x070); r.ctl[3]=p446_r(p446_mdp,0x2000+0x0a0); r.ctl[4]=p446_r(p446_mdp,0x2000+0x018); r.ctl[5]=p446_r(p446_mdp,0x2000+0x01c); r.ctl[6]=p446_r(p446_mdp,0x2000+0x064);
"""
    s=one(s,old_regs,new_regs,"first-commit register set")
    seq="static atomic_t p446_seq = ATOMIC_INIT(0);\nstatic atomic_t p446_drop = ATOMIC_INIT(0);\n"
    s=one(s,seq,seq+"static atomic_t p446b_first_safe = ATOMIC_INIT(0);\n","first-safe state")
    gate="""    safe_mdp=!!(gdsc & BIT(31)) && !(disp_ahb & BIT(31)) && !(mdp_clk & BIT(31));
    safe_dsi=safe_mdp && !(byte0 & BIT(31)) && !(pclk0 & BIT(31));
"""
    s=one(s,gate,gate+"""    /* H00: first periodic sample that passes the corrected HWCG-safe gate. */
    if(safe_mdp && event==P446_EVT_PERIODIC &&
       atomic_cmpxchg(&p446b_first_safe,0,1)==0){
        r.event=0x120U; r.aux0=P446_EVT_PERIODIC; r.aux1=0U;
    }
""","H00 first safe")
    marker='static const char p446b_marker[] __used = "'+MARK+'";'
    if marker in s:
        s=s.replace(marker,marker+'\nstatic const char p446b_micro_marker[] __used = "'+MICRO_MARK+'";',1)
    else:
        s+='\nstatic const char p446b_micro_marker[] __used = "'+MICRO_MARK+'";\n'
    return s

def patch_msm_smmu_micro(s: str) -> str:
    marker="A52_PHASE446B_H03_H04_ATTACH"
    if marker in s:return s
    anchor="static int msm_smmu_attach(struct msm_mmu *mmu, const char * const *names,\n"
    s=_micro_decl(s,anchor,marker)
    old="\trc = iommu_attach_device(client->domain, client->dev);\n"
    new=("\ta52_p446_mark(0x123U, client->secure ? 1U : 0U, 0U);\n"
         "\trc = iommu_attach_device(client->domain, client->dev);\n"
         "\ta52_p446_mark(0x124U, client->secure ? 1U : 0U, (u32)rc);\n")
    return one(s,old,new,"H03/H04 iommu attach")

def patch_kms_micro(s: str) -> str:
    marker="A52_PHASE446B_KMS_H01_H02_H05_H06"
    if marker in s:return s
    s=_micro_decl(s,"static int _sde_kms_mmu_init(struct sde_kms *sde_kms)\n",marker)
    needle="ret = sde_rm_cont_splash_res_init(priv, &sde_kms->rm,"
    p=s.find(needle)
    if p<0:die("cont_splash_res_init call missing")
    line=s.rfind("\n",0,p)+1
    end=s.find(");",p)
    if end<0:die("cont_splash_res_init end missing")
    end+=2
    call=s[line:end]; ind=call[:len(call)-len(call.lstrip())]
    s=s[:line]+ind+"a52_p446_mark(0x121U,(u32)display_count,0U);\n"+call+"\n"+ind+"a52_p446_mark(0x122U,(u32)display_count,(u32)ret);"+s[end:]
    old="\t\t\tret = _sde_kms_map_all_splash_regions(sde_kms);\n"
    s=one(s,old,old+"\t\t\ta52_p446_mark(0x125U,(u32)i,(u32)ret);\n","H05 splash map")
    old="""\t\tret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,
\t\t\t\t &early_map);
"""
    s=one(s,old,old+"\t\ta52_p446_mark(0x126U,(u32)i,(u32)ret);\n","H06 early-map")
    return s

def patch_msm_drv_micro(s: str) -> str:
    marker="A52_PHASE446B_H07_BIND_EXIT"
    if marker in s:return s
    s=_micro_decl(s,"static int msm_drm_bind(struct device *dev)\n",marker)
    old="\ta52_p446_mark(3U,1U,(u32)rc);\n"
    return one(s,old,old+"\ta52_p446_mark(0x127U,(u32)rc,0U);\n","H07 drm bind exit")

def patch_plane_micro(s: str) -> str:
    marker="A52_PHASE446B_PLANE_H08_H09"
    if marker in s:return s
    s=_micro_decl(s,"static inline void _sde_plane_set_scanout(struct drm_plane *plane,\n",marker)
    old="""\tpsde = to_sde_plane(plane);
\tstate = plane->state;
\tpstate = to_sde_plane_state(state);
"""
    new=old+"""\ta52_p446_mark(0x128U,(u32)plane->base.id,
\t\t(state->crtc ? BIT(0) : 0U) | (state->fb ? BIT(1) : 0U) |
\t\t((u32)psde->pipe << 8));
"""
    a=s.find("static void sde_plane_atomic_update("); z=s.find("void sde_plane_restore(",a)
    if a<0 or z<0:die("sde_plane_atomic_update boundaries missing")
    b=one(s[a:z],old,new,"H08 plane state"); s=s[:a]+b+s[z:]
    a=s.find("static inline void _sde_plane_set_scanout("); z=s.find("\nstatic int _sde_plane_setup_scaler3_lut",a)
    if a<0 or z<0:die("scanout helper boundaries missing")
    b=s[a:z]
    b=one(b,"\t\treturn;\n\t}\n\n\tpsde = to_sde_plane(plane);",
          "\t\ta52_p446_mark(0x129U,1U,0U);\n\t\treturn;\n\t}\n\n\tpsde = to_sde_plane(plane);","H09 bad args")
    b=one(b,"\t\tSDE_ERROR_PLANE(psde, \"invalid pipe_hw\\n\");\n\t\treturn;",
          "\t\tSDE_ERROR_PLANE(psde, \"invalid pipe_hw\\n\");\n\t\ta52_p446_mark(0x129U,2U,(u32)plane->base.id);\n\t\treturn;","H09 pipe_hw")
    b=one(b,"\t\tSDE_ERROR_PLANE(psde, \"Failed to get aspace %d\\n\", ret);\n\t\treturn;",
          "\t\tSDE_ERROR_PLANE(psde, \"Failed to get aspace %d\\n\", ret);\n\t\ta52_p446_mark(0x129U,3U,(u32)ret);\n\t\treturn;","H09 aspace")
    b=one(b,"\t\t\tSDE_ERROR_PLANE(psde,\n\t\t\t\t\"failed to prepare framebuffer %d\\n\", ret);\n\t\t\treturn;",
          "\t\t\tSDE_ERROR_PLANE(psde,\n\t\t\t\t\"failed to prepare framebuffer %d\\n\", ret);\n\t\t\ta52_p446_mark(0x129U,4U,(u32)ret);\n\t\t\treturn;","H09 defer prepare")
    b=one(b,"\tif (ret == -EAGAIN)\n\t\tSDE_DEBUG_PLANE(psde, \"not updating same src addrs\\n\");\n\telse if (ret) {\n",
          "\tif (ret == -EAGAIN) {\n\t\tSDE_DEBUG_PLANE(psde, \"not updating same src addrs\\n\");\n\t\ta52_p446_mark(0x129U,5U,(u32)plane->base.id);\n\t} else if (ret) {\n","H09 eagain")
    b=one(b,"\t\tpsde->is_error = true;\n\t} else if (psde->pipe_hw->ops.setup_sourceaddress) {",
          "\t\tpsde->is_error = true;\n\t\ta52_p446_mark(0x129U,6U,(u32)ret);\n\t} else if (psde->pipe_hw->ops.setup_sourceaddress) {","H09 layout error")
    old="""\t\tpsde->pipe_hw->ops.setup_sourceaddress(psde->pipe_hw, pipe_cfg,
\t\t\t\t\t\tpstate->multirect_index);
\t}
}
"""
    new="""\t\tpsde->pipe_hw->ops.setup_sourceaddress(psde->pipe_hw, pipe_cfg,
\t\t\t\t\t\tpstate->multirect_index);
\t\ta52_p446_mark(0x129U,7U,(u32)pipe_cfg->layout.plane_addr[0]);
\t} else {
\t\ta52_p446_mark(0x129U,8U,(u32)plane->base.id);
\t}
}
"""
    b=one(b,old,new,"H09 source programmed")
    return s[:a]+b+s[z:]

def patch_crtc_micro(s: str) -> str:
    marker="A52_PHASE446B_CRTC_H10_H11"
    if marker in s:return s
    anchor="static void _sde_crtc_blend_setup(struct drm_crtc *crtc,\n"
    if anchor not in s:anchor="static void _sde_crtc_blend_setup_mixer(struct drm_crtc *crtc,\n"
    s=_micro_decl(s,anchor,marker)
    old="""\t\tif (mixer[i].hw_ctl->ops.clear_all_blendstages)
\t\t\tmixer[i].hw_ctl->ops.clear_all_blendstages(
\t\t\t\t\tmixer[i].hw_ctl);
"""
    s=one(s,old,old+"\t\ta52_p446_mark(0x12aU,(u32)mixer[i].hw_ctl->idx,(u32)mixer[i].hw_lm->idx);\n","H10 clear blend")
    old="""\t\tctl->ops.setup_blendstage(ctl, mixer[i].hw_lm->idx,
\t\t\t&sde_crtc->stage_cfg);
"""
    return one(s,old,old+"\t\ta52_p446_mark(0x12bU,(u32)ctl->idx,cfg.pending_flush_mask);\n","H11 setup blend")

def patch_atomic_micro(s: str) -> str:
    marker="A52_PHASE446B_H12_BRIDGE_PRE_ENABLE"
    if marker in s:return s
    s=_micro_decl(s,"static void msm_atomic_helper_commit_modeset_enables(struct drm_device *dev,\n",marker)
    old="\t\tdrm_bridge_pre_enable(encoder->bridge);\n"
    return one(s,old,"\t\ta52_p446_mark(0x12cU,(u32)encoder->base.id,(u32)connector->base.id);\n"+old,"H12 bridge pre-enable")

def patch_dsi_micro(s: str) -> str:
    marker="A52_PHASE446B_H13_PANEL_ENABLE"
    if marker in s:return s
    anchor="int dsi_display_enable(struct dsi_display *display)\n"
    s=_micro_decl(s,anchor,marker)
    start=s.find(anchor); splash=s.find("if (display->is_cont_splash_enabled)",start); call=s.find("dsi_panel_enable(display->panel);",splash)
    if start<0 or splash<0 or call<0:die("continuous-splash panel-enable anchor missing")
    line=s.rfind("\n",0,call)+1
    prefix=s[line:call]
    ind=prefix[:len(prefix)-len(prefix.lstrip())]
    return s[:line]+ind+"a52_p446_mark(0x12dU,1U,(u32)display->ctrl_count);\n"+s[line:]

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--kind", choices=("gki","tg"), required=True)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    if a.kind == "gki":
        central = a.root / "drivers/a52_display/msm/a52_phase445.c"
        arm = a.root / "drivers/iommu/arm/arm-smmu/arm-smmu.c"
        roots = {
            "smmu": a.root / "drivers/a52_display/msm/msm_smmu.c",
            "kms": a.root / "drivers/a52_display/msm/sde/sde_kms.c",
            "drv": a.root / "drivers/a52_display/msm/msm_drv.c",
            "plane": a.root / "drivers/a52_display/msm/sde/sde_plane.c",
            "crtc": a.root / "drivers/a52_display/msm/sde/sde_crtc.c",
            "atomic": a.root / "drivers/a52_display/msm/msm_atomic.c",
            "dsi": a.root / "drivers/a52_display/msm/dsi/dsi_display.c",
        }
    else:
        central = a.root / "techpack/display/msm/a52_phase446g.c"
        arm = a.root / "drivers/iommu/arm-smmu.c"
        roots = {
            "smmu": a.root / "techpack/display/msm/msm_smmu.c",
            "kms": a.root / "techpack/display/msm/sde/sde_kms.c",
            "drv": a.root / "techpack/display/msm/msm_drv.c",
            "plane": a.root / "techpack/display/msm/sde/sde_plane.c",
            "crtc": a.root / "techpack/display/msm/sde/sde_crtc.c",
            "atomic": a.root / "techpack/display/msm/msm_atomic.c",
            "dsi": a.root / "techpack/display/msm/dsi/dsi_display.c",
        }

    for p in (central, arm, *roots.values()):
        if not p.exists():
            die(f"missing {p}")

    if not a.check_only:
        cs = patch_central(central.read_text(errors="replace"))
        central.write_text(patch_central_micro(cs))
        ars = arm.read_text(errors="replace")
        arm.write_text(patch_arm_gki(ars) if a.kind == "gki" else patch_arm_tg(ars))
        funcs = {
            "smmu": patch_msm_smmu_micro, "kms": patch_kms_micro,
            "drv": patch_msm_drv_micro, "plane": patch_plane_micro,
            "crtc": patch_crtc_micro, "atomic": patch_atomic_micro,
            "dsi": patch_dsi_micro,
        }
        for key, fn in funcs.items():
            roots[key].write_text(fn(roots[key].read_text(errors="replace")))

    cs = central.read_text(errors="replace")
    ars = arm.read_text(errors="replace")
    required_c = (
        MARK, MICRO_MARK,
        "safe_mdp=!!(gdsc & BIT(31)) && !(disp_ahb & BIT(31)) && !(mdp_clk & BIT(31))",
        "A52_PHASE446B_LIVE_CB_READ", "a52_p446_read_cb2(",
        "A52_PHASE446B_DEBUG_MIRROR_DROPPED",
        "r.sspp[i][1]=p446_r(p446_mdp,o+0x1c)",
        "r.sspp[i][2]=p446_r(p446_mdp,o+0x14)",
        "r.sspp[i][4]=p446_r(p446_mdp,o+0x24)",
        "r.ctl[5]=p446_r(p446_mdp,0x2000+0x01c)",
        "r.event=0x120U",
    )
    required_a = (
        "A52_PHASE446B_LIVE_CB_API",
        "a52_p446_read_cb2(",
        "EXPORT_SYMBOL_GPL(a52_p446_read_cb2)",
    )
    missing = [x for x in required_c if x not in cs] + [x for x in required_a if x not in ars]
    micro = {
        "smmu": ("A52_PHASE446B_H03_H04_ATTACH","a52_p446_mark(0x123U","a52_p446_mark(0x124U"),
        "kms": ("A52_PHASE446B_KMS_H01_H02_H05_H06","a52_p446_mark(0x121U","a52_p446_mark(0x122U","a52_p446_mark(0x125U","a52_p446_mark(0x126U"),
        "drv": ("A52_PHASE446B_H07_BIND_EXIT","a52_p446_mark(0x127U"),
        "plane": ("A52_PHASE446B_PLANE_H08_H09","a52_p446_mark(0x128U","a52_p446_mark(0x129U"),
        "crtc": ("A52_PHASE446B_CRTC_H10_H11","a52_p446_mark(0x12aU","a52_p446_mark(0x12bU"),
        "atomic": ("A52_PHASE446B_H12_BRIDGE_PRE_ENABLE","a52_p446_mark(0x12cU"),
        "dsi": ("A52_PHASE446B_H13_PANEL_ENABLE","a52_p446_mark(0x12dU"),
    }
    for key, toks in micro.items():
        body = roots[key].read_text(errors="replace")
        missing.extend(f"{key}:{tok}" for tok in toks if tok not in body)
    if "#define P446_MAX_REC 2048U" not in cs:
        missing.append("P446_MAX_REC")
    if missing:
        die("contract missing: " + ", ".join(missing))

    forbidden = (
        "safe_mdp=!(gcc_ahb & BIT(31))",
        'a52_ackfr_record("P446 q=%u',
    )
    bad = [x for x in forbidden if x in cs]
    if bad:
        die("old Phase446 behavior still present: " + ", ".join(bad))

    print(f"Phase446b {a.kind}: gate/live-CB fix + H00-H13 first-commit microscope PASS")


if __name__ == "__main__":
    main()
