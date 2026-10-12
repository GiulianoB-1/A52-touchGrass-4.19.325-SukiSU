#!/usr/bin/env python3
"""Phase446L paired trigger-time DSI observation, for TG and GKI.

Extends 446K after 446i/446j. One first cold-ON command per boot.
All samples are passive; no writes except the pre-existing DSI debug-bus
selector sweep, which is restored. Records wall time spent sampling.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE446L_FIRST_COLD_ON_TRIGGER_V1"

def one(s, old, new, why):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"446L {why}: anchor count={n}")
    return s.replace(old, new, 1)

IMPL = r'''
/* A52_PHASE446L_FIRST_COLD_ON_TRIGGER_V1 */
#define P446L_WORDS 256U
#define P446L_DISPCC_WORDS 128U
struct p446l_meta {
    u64 iova, phys;
    u32 len, flags, msg_flags, domain_found, prefix_len;
    u8 prefix[16];
} __packed;
struct p446l_sample {
    u64 ns_begin, ns_end;
    u32 tag, hw_flags, elapsed_us, status, lane, clk, int_ctrl;
    u32 ctrl[P446L_WORDS];
    u32 dispcc[P446L_DISPCC_WORDS]; /* offsets 0x1000..0x11fc */
    u32 gdsc;  /* unavailable unless separately wired; 0xffffffff */
    struct p446i_bus_pair bus[ARRAY_SIZE(p446i_sel)];
} __packed;
static struct p446l_meta p446l_cmd;
static struct p446l_sample p446l_t3,p446l_t4;
static atomic_t p446l_state=ATOMIC_INIT(0); /* 0 idle; 1 armed; 2 taken */

void a52_p446l_arm(struct dsi_ctrl_hw *ctrl)
{
    if (!ctrl || ctrl->index!=0 || !ctrl->base)
        return;
    (void)atomic_cmpxchg(&p446l_state,0,1);
}
EXPORT_SYMBOL_GPL(a52_p446l_arm);

void a52_p446l_note(struct dsi_ctrl_hw *ctrl, u64 iova, u64 phys,
                    u32 len, u32 flags, u32 msg_flags,
                    u32 domain_found, const u8 *prefix)
{
    if (!ctrl || ctrl->index!=0 || atomic_read(&p446l_state)!=1)
        return;
    p446l_cmd.iova=iova;
    p446l_cmd.phys=phys;
    p446l_cmd.len=len;
    p446l_cmd.flags=flags;
    p446l_cmd.msg_flags=msg_flags;
    p446l_cmd.domain_found=domain_found;
    p446l_cmd.prefix_len=min(len,16U);
    memset(p446l_cmd.prefix,0,sizeof(p446l_cmd.prefix));
    if (prefix && p446l_cmd.prefix_len)
        memcpy(p446l_cmd.prefix,prefix,p446l_cmd.prefix_len);
}
EXPORT_SYMBOL_GPL(a52_p446l_note);

static void p446l_take(struct dsi_ctrl_hw *hw,
                        struct p446l_sample *s,u32 tag,u32 flags)
{
    u32 i;
    s->ns_begin=ktime_get_ns();
    s->tag=tag;
    s->hw_flags=flags;
    s->status=DSI_R32(hw,DSI_STATUS);
    s->lane=DSI_R32(hw,DSI_LANE_STATUS);
    s->clk=DSI_R32(hw,DSI_CLK_STATUS);
    s->int_ctrl=DSI_R32(hw,DSI_INT_CTRL);
    for(i=0;i<P446L_WORDS;i++)
        s->ctrl[i]=DSI_R32(hw,i*4U);
    for(i=0;i<P446L_DISPCC_WORDS;i++)
        s->dispcc[i]=p446i_r(p446i_dispcc,0x1000U+i*4U);
    s->gdsc=~0U;
    p446i_sweep(s->bus,hw);
    s->ns_end=ktime_get_ns();
    s->elapsed_us=(u32)((s->ns_end-s->ns_begin)/1000ULL);
}

void a52_p446l_after_trigger(struct dsi_ctrl_hw *hw,u32 hw_flags)
{
    u64 trigger_ns;
    u32 c3,c4;
    if (!hw || hw->index!=0 || !hw->base ||
        atomic_cmpxchg(&p446l_state,1,2)!=1)
        return;
    trigger_ns=ktime_get_ns();
    p446l_take(hw,&p446l_t3,3U,hw_flags);
    /* Approx. +200 us from trigger. If t3 itself costs >200 us,
     * retain the measured timestamp instead of pretending timing matched.
     */
    while(ktime_get_ns()-trigger_ns<200000ULL)
        cpu_relax();
    p446l_take(hw,&p446l_t4,4U,hw_flags);
    c3=p446k_hash(p446l_t3.ctrl,P446L_WORDS);
    c4=p446k_hash(p446l_t4.ctrl,P446L_WORDS);
    __STORE__(0x446c0030U,16U,"L_CMD_META",p446l_cmd.flags,
        p446l_cmd.domain_found,&p446l_cmd,sizeof(p446l_cmd));
    __STORE__(0x446c0031U,16U,"L_T3_CTRL",3U,c3,
        p446l_t3.ctrl,sizeof(p446l_t3.ctrl));
    __STORE__(0x446c0032U,16U,"L_T3_CLK",3U,p446l_t3.elapsed_us,
        p446l_t3.dispcc,sizeof(p446l_t3.dispcc));
    __STORE__(0x446c0033U,16U,"L_T3_BUS",3U,ARRAY_SIZE(p446i_sel),
        p446l_t3.bus,sizeof(p446l_t3.bus));
    __STORE__(0x446c0041U,16U,"L_T4_CTRL",4U,c4,
        p446l_t4.ctrl,sizeof(p446l_t4.ctrl));
    __STORE__(0x446c0042U,16U,"L_T4_CLK",4U,p446l_t4.elapsed_us,
        p446l_t4.dispcc,sizeof(p446l_t4.dispcc));
    __STORE__(0x446c0043U,16U,"L_T4_BUS",4U,ARRAY_SIZE(p446i_sel),
        p446l_t4.bus,sizeof(p446l_t4.bus));
    pr_warn("P446L post-SW_TRIGGER +%lluus t3=%llu..%llu t4=%llu..%llu st=%08x/%08x clk=%08x/%08x lane=%08x/%08x int=%08x/%08x CTRL=%08x/%08x IOVA=%llx PHYS=%llx valid=%u len=%u flags=%08x\n",
        (unsigned long long)((p446l_t4.ns_begin-trigger_ns)/1000ULL),
        (unsigned long long)p446l_t3.ns_begin,
        (unsigned long long)p446l_t3.ns_end,
        (unsigned long long)p446l_t4.ns_begin,
        (unsigned long long)p446l_t4.ns_end,
        p446l_t3.status,p446l_t4.status,
        p446l_t3.clk,p446l_t4.clk,
        p446l_t3.lane,p446l_t4.lane,
        p446l_t3.int_ctrl,p446l_t4.int_ctrl,c3,c4,
        (unsigned long long)p446l_cmd.iova,
        (unsigned long long)p446l_cmd.phys,
        p446l_cmd.domain_found,p446l_cmd.len,p446l_cmd.flags);
}
EXPORT_SYMBOL_GPL(a52_p446l_after_trigger);
'''

def patch(root, kind):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    central=base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")
    panel=base/"dsi/dsi_panel.c"
    ctrl=base/"dsi/dsi_ctrl.c"
    hw=base/"dsi/dsi_ctrl_hw_cmn.c"
    s=central.read_text()
    if MARK not in s:
        store="a52_p445_store_section" if kind=="gki" else "a52_p444_store_section"
        s=one(s,"EXPORT_SYMBOL_GPL(a52_p446k_capture);\n",
              "EXPORT_SYMBOL_GPL(a52_p446k_capture);\n"+IMPL.replace("__STORE__",store),
              "central post-446K")
        central.write_text(s)
    s=panel.read_text()
    if "A52_PHASE446L_ARM_ON" not in s:
        anchor="a52_p446k_capture(&display->ctrl[0].ctrl->hw,2U);"
        addition=("\n        /* A52_PHASE446L_ARM_ON: first actual cold ON packet only. */\n"
            "        if (type==DSI_CMD_SET_ON && !display->is_cont_splash_enabled &&\n"
            "            display->ctrl[0].ctrl)\n"
            "            a52_p446l_arm(&display->ctrl[0].ctrl->hw);")
        s=one(s,anchor,anchor+addition,"panel ON arm")
        sig="extern void a52_p446k_capture(struct dsi_ctrl_hw *,u32);\n"
        s=one(s,sig,sig+"extern void a52_p446l_arm(struct dsi_ctrl_hw *);\n",
              "panel prototype")
        panel.write_text(s)
    s=ctrl.read_text()
    if "A52_PHASE446L_IOVA_CONTEXT" not in s:
        # The actual display GEM memory domain is accessed before triggering,
        # never from the MMIO snapshot's time-critical section.
        helper=r'''/* A52_PHASE446L_IOVA_CONTEXT */
extern void a52_p446l_note(struct dsi_ctrl_hw *,u64,u64,u32,u32,u32,u32,const u8 *);
static void p446l_note_cmd(struct dsi_ctrl *c,
    const struct dsi_ctrl_cmd_dma_info *cmd,
    const struct mipi_dsi_msg *msg,u32 flags)
{
    struct msm_gem_address_space *as;
    struct iommu_domain *domain=NULL;
    phys_addr_t phys=0;
    u32 found=0;
    if (!c || !cmd || !msg)
        return;
    as=dsi_ctrl_get_aspace(c,MSM_SMMU_DOMAIN_UNSECURE);
    if (as && as->mmu && as->mmu->dev)
        domain=iommu_get_domain_for_dev(as->mmu->dev);
    if (domain) {
        found=1;
        phys=iommu_iova_to_phys(domain,cmd->offset);
    }
    a52_p446l_note(&c->hw,cmd->offset,(u64)phys,
        cmd->length,flags,(u32)msg->flags,found,c->vaddr);
}
'''
        s=one(s,"#include <linux/of_device.h>\n",
              "#include <linux/of_device.h>\n#include <linux/iommu.h>\n#include \"msm_gem.h\"\n",
              "iommu and gem includes")
        s=one(s,"static void dsi_kickoff_msg_tx(",
              helper+"\nstatic void dsi_kickoff_msg_tx(","note helper")
        s=one(s,"a52_p446i_hw_pre(&dsi_ctrl->hw);",
              "p446l_note_cmd(dsi_ctrl,cmd_mem,msg,flags);\n\t\t\t\ta52_p446i_hw_pre(&dsi_ctrl->hw);",
              "pre-kickoff memory context")
        ctrl.write_text(s)
    s=hw.read_text()
    if "A52_PHASE446L_HW_TRIGGER" not in s:
        decl="/* A52_PHASE446L_HW_TRIGGER */\nextern void a52_p446l_after_trigger(struct dsi_ctrl_hw *,u32);\n"
        s=one(s,"void dsi_ctrl_hw_cmn_kickoff_command(",
              decl+"void dsi_ctrl_hw_cmn_kickoff_command(",
              "hardware declaration")
        # Three physical trigger points: memory, FIFO, deferred.
        token="DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);"
        occurrences=s.count(token)
        if occurrences!=3:
            raise RuntimeError(f"446L hardware trigger count {occurrences}, expected 3")
        s=s.replace(token,token+"\n\t\ta52_p446l_after_trigger(ctrl,flags);",2)
        # Deferred trigger function has no flags parameter.
        s=one(s,"void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
              "void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_p446l_after_trigger(ctrl,0x80000000U);",
              "deferred trigger")
        hw.write_text(s)

def check(root,kind):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    c=(base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")).read_text()
    p=(base/"dsi/dsi_panel.c").read_text()
    d=(base/"dsi/dsi_ctrl.c").read_text()
    h=(base/"dsi/dsi_ctrl_hw_cmn.c").read_text()
    for tok in (MARK,"L_CMD_META","L_T3_CTRL","L_T4_CTRL","L_T3_BUS","L_T4_BUS","P446L post-SW_TRIGGER"):
        if tok not in c: raise RuntimeError("446L central missing "+tok)
    if p.count("A52_PHASE446L_ARM_ON")!=1: raise RuntimeError("446L ON arm not unique")
    if d.count("A52_PHASE446L_IOVA_CONTEXT")!=1: raise RuntimeError("446L IOVA helper not unique")
    if h.count("a52_p446l_after_trigger(ctrl,")!=3:
        raise RuntimeError("446L expected 3 real HW trigger hooks")
    print(f"Phase446L {kind} PASS: first cold ON trigger twins, timestamps, CTRL+DBG+DISPCC+IOVA")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--kind",choices=("gki","tg"),required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only: patch(a.root,a.kind)
    check(a.root,a.kind)
