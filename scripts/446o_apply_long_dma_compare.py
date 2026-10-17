#!/usr/bin/env python3
"""446O: TG/GKI first splash F0 and cold first-long DMA matched snapshots.

GKI diag: read-only + Phase446N rearm; GKI split: additionally force
LASTCOMMAND per ON-set packet (one SW_TRIGGER per packet).
Never synthesize a DSI command. Capture is bounded: 2 hardware triggers
per boot, one before cold ON and one after. Sample buffers are persistent
in the Phase444/445 binary section stream.
"""
import argparse
from pathlib import Path

MARK="A52_PHASE446O_LONG_DMA_TWIN_V1"

def one(s,a,b,why):
    n=s.count(a)
    if n!=1:raise RuntimeError("446O "+why+": expected 1 anchor, got "+str(n))
    return s.replace(a,b,1)

IMPL=r'''
/* A52_PHASE446O_LONG_DMA_TWIN_V1
 * Two read-only, first-long DMA twins: splash F0, cold ON.
 * Before/after control 0x000..0x3fc + DSI debug bus + 0..200us
 * STATUS/INT/LANE/CLK/error status samples.
 */
#define P446O_N 2U
#define P446O_BYTES 4096U
struct p446o_cmd {
    u64 iova; u32 len, copied, ctrl_flags, msg_flags, type;
    u8 prefix[16];
};
struct p446o_fast {
    u64 ns;
    u32 us,status,intr,lane,clk,err,ack,timeout,fifo;
};
static struct p446o_cmd p446o_meta[P446O_N];
static u8 p446o_bytes[P446O_N][P446O_BYTES];
static struct p446l_sample p446o_pre[P446O_N],p446o_post[P446O_N];
static struct p446o_fast p446o_samples[P446O_N][9];
static const u32 p446o_times[9]={0,1,2,5,10,20,50,100,200};
static atomic_t p446o_cold=ATOMIC_INIT(0);
static atomic_t p446o_splash_ready=ATOMIC_INIT(0);
static atomic_t p446o_slot_done[P446O_N]={
    ATOMIC_INIT(0),ATOMIC_INIT(0)};
static int p446o_inflight=-1;

void a52_p446o_arm_cold(void)
{
    atomic_set(&p446o_cold,1);
}
EXPORT_SYMBOL_GPL(a52_p446o_arm_cold);

void a52_p446o_note(struct dsi_ctrl_hw *hw,u64 iova,u32 len,
                    u32 flags,u32 msgflags,u32 type,
                    const u8 *tx,u32 txlen,const u8 *dma)
{
    struct p446o_cmd *m;
    unsigned int slot,copy;
    if (!hw || hw->index!=0 || !dma || !len)
        return;
    slot=atomic_read(&p446o_cold)?1U:0U;
    if (!slot) {
        /* Splash F0, 5A 5A (long write). Ignore unrelated early DMA. */
        if (!tx || txlen<3U || tx[0]!=0xf0 ||
            tx[1]!=0x5a || tx[2]!=0x5a)
            return;
        atomic_set(&p446o_splash_ready,1);
    } else if (!(msgflags & BIT(3) /* LASTCOMMAND */)) {
        /* The batch's final packet carries LASTCOMMAND. */
        return;
    }
    if (atomic_read(&p446o_slot_done[slot]))
        return;
    copy=min_t(u32,len,P446O_BYTES);
    m=&p446o_meta[slot];
    m->iova=iova; m->len=len; m->copied=copy;
    m->ctrl_flags=flags; m->msg_flags=msgflags; m->type=type;
    memcpy(p446o_bytes[slot],dma,copy);
    memcpy(m->prefix,dma,min_t(u32,copy,sizeof(m->prefix)));
}
EXPORT_SYMBOL_GPL(a52_p446o_note);

static void p446o_fast_read(struct dsi_ctrl_hw *hw,struct p446o_fast *s,u32 us)
{
    s->ns=ktime_get_ns(); s->us=us;
    s->status=DSI_R32(hw,DSI_STATUS);
    s->intr=DSI_R32(hw,DSI_INT_CTRL);
    s->lane=DSI_R32(hw,DSI_LANE_STATUS);
    s->clk=DSI_R32(hw,DSI_CLK_STATUS);
    s->err=DSI_R32(hw,DSI_DLN0_PHY_ERR);
    s->ack=DSI_R32(hw,DSI_ACK_ERR_STATUS);
    s->timeout=DSI_R32(hw,DSI_TIMEOUT_STATUS);
    s->fifo=DSI_R32(hw,DSI_FIFO_STATUS);
}
void a52_p446o_hw_before(struct dsi_ctrl_hw *hw,u32 flags)
{
    u32 slot,len;
    if (!hw || hw->index!=0 || !hw->base)
        return;
    slot=atomic_read(&p446o_cold)?1U:0U;
    len=DSI_R32(hw,DSI_DMA_CMD_LENGTH);
    if (len<=4U || (slot==0U &&
        !atomic_read(&p446o_splash_ready)) ||
        atomic_cmpxchg(&p446o_slot_done[slot],0,1)!=0)
        return;
    p446o_inflight=(int)slot;
    p446l_take(hw,&p446o_pre[slot],5U,flags);
    pr_warn("P446O %s PRE lenreg=%u captured=%u iova=%llx flags=%08x\n",
        slot?"COLD":"SPLASH",len,p446o_meta[slot].copied,
        (unsigned long long)p446o_meta[slot].iova,flags);
}
EXPORT_SYMBOL_GPL(a52_p446o_hw_before);

void a52_p446o_hw_after(struct dsi_ctrl_hw *hw,u32 flags)
{
    int slot=p446o_inflight;
    u64 start;
    unsigned int i,j;
    if (!hw || hw->index!=0 || slot<0 || slot>=P446O_N)
        return;
    p446o_inflight=-1;
    start=ktime_get_ns();
    for (i=0;i<ARRAY_SIZE(p446o_times);i++) {
        while(ktime_get_ns()-start<(u64)p446o_times[i]*1000ULL)
            cpu_relax();
        p446o_fast_read(hw,&p446o_samples[slot][i],p446o_times[i]);
    }
    p446l_take(hw,&p446o_post[slot],6U,flags);
    __STORE__(0x446c0060U+slot*16U,16U,"O_CMD_META",
        slot,p446o_meta[slot].len,&p446o_meta[slot],sizeof(p446o_meta[slot]));
    __STORE__(0x446c0061U+slot*16U,16U,"O_FAST_0_200",
        slot,200U,p446o_samples[slot],sizeof(p446o_samples[slot]));
    __STORE__(0x446c0062U+slot*16U,16U,"O_PRE_CTRL",
        slot,0U,p446o_pre[slot].ctrl,sizeof(p446o_pre[slot].ctrl));
    __STORE__(0x446c0063U+slot*16U,16U,"O_POST_CTRL",
        slot,0U,p446o_post[slot].ctrl,sizeof(p446o_post[slot].ctrl));
    __STORE__(0x446c0064U+slot*16U,16U,"O_PRE_BUS",
        slot,0U,p446o_pre[slot].bus,sizeof(p446o_pre[slot].bus));
    __STORE__(0x446c0065U+slot*16U,16U,"O_POST_BUS",
        slot,0U,p446o_post[slot].bus,sizeof(p446o_post[slot].bus));
    __STORE__(0x446c0066U+slot*16U,16U,"O_PRE_REG",
        slot,0U,&p446o_pre[slot],offsetof(struct p446l_sample,ctrl));
    __STORE__(0x446c0067U+slot*16U,16U,"O_POST_REG",
        slot,0U,&p446o_post[slot],offsetof(struct p446l_sample,ctrl));
    for(j=0;j<4;j++)
        __STORE__(0x446c0068U+slot*16U+j,16U,"O_DMA_BUF",
            slot,j,p446o_bytes[slot]+1024U*j,1024U);
    pr_warn("P446O %s POST st=%08x int=%08x lane=%08x clk=%08x size=%u first_int=%08x last_int=%08x\n",
        slot?"COLD":"SPLASH",
        p446o_samples[slot][8].status,p446o_samples[slot][8].intr,
        p446o_samples[slot][8].lane,p446o_samples[slot][8].clk,
        p446o_meta[slot].copied,p446o_samples[slot][0].intr,
        p446o_samples[slot][8].intr);
}
EXPORT_SYMBOL_GPL(a52_p446o_hw_after);
'''

NOTE=r'''
/* A52_PHASE446O_DMA_BUFFER_NOTE */
extern void a52_p446o_note(struct dsi_ctrl_hw *,u64,u32,u32,u32,u32,
                           const u8 *,u32,const u8 *);
static void p446o_note_dma(struct dsi_ctrl *c,
    const struct dsi_ctrl_cmd_dma_info *cmd,
    const struct mipi_dsi_msg *msg,u32 flags)
{
    if (!c || !cmd || !msg || !c->vaddr ||
        !(flags & DSI_CTRL_CMD_FETCH_MEMORY))
        return;
    a52_p446o_note(&c->hw,cmd->offset,cmd->length,flags,
        msg->flags,msg->type,msg->tx_buf,msg->tx_len,c->vaddr);
}
'''

def patch(root,kind,variant):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    central=base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")
    panel=base/"dsi/dsi_panel.c"
    ctrl=base/"dsi/dsi_ctrl.c"
    hw=base/"dsi/dsi_ctrl_hw_cmn.c"
    assert "A52_PHASE446L_FIRST_COLD_ON_TRIGGER_V1" in central.read_text()
    s=central.read_text()
    if MARK not in s:
        store="a52_p445_store_section" if kind=="gki" else "a52_p444_store_section"
        s=one(s,"EXPORT_SYMBOL_GPL(a52_p446l_after_trigger);\n",
              "EXPORT_SYMBOL_GPL(a52_p446l_after_trigger);\n"+
              IMPL.replace("__STORE__",store),"central 446L append")
        central.write_text(s)
    s=panel.read_text()
    if "A52_PHASE446O_PANEL_ARM" not in s:
        a="a52_p446l_arm(&display->ctrl[0].ctrl->hw);"
        b=(a+"\n        /* A52_PHASE446O_PANEL_ARM */\n"+
          "        if (type==DSI_CMD_SET_ON && !display->is_cont_splash_enabled)\n"+
          "            a52_p446o_arm_cold();")
        s=one(s,a,b,"panel cold arm")
        s=one(s,"extern void a52_p446l_arm(struct dsi_ctrl_hw *);\n",
              "extern void a52_p446l_arm(struct dsi_ctrl_hw *);\n"+
              "extern void a52_p446o_arm_cold(void);\n","panel extern")
        panel.write_text(s)
    s=ctrl.read_text()
    if "A52_PHASE446O_DMA_BUFFER_NOTE" not in s:
        s=one(s,"static void dsi_kickoff_msg_tx(",NOTE+
              "\nstatic void dsi_kickoff_msg_tx(","dma note helper")
        s=one(s,"p446l_note_cmd(dsi_ctrl,cmd_mem,msg,flags);",
              "p446l_note_cmd(dsi_ctrl,cmd_mem,msg,flags);\n"+
              "\t\t\t\tp446o_note_dma(dsi_ctrl,cmd_mem,msg,flags);",
              "capture complete DMA buffer")
        ctrl.write_text(s)
    s=hw.read_text()
    if "A52_PHASE446O_HW_TRIGGER_HOOK" not in s:
        a="/* A52_PHASE446L_HW_TRIGGER */\n"
        s=one(s,a,
              "/* A52_PHASE446O_HW_TRIGGER_HOOK */\n"+
              "extern void a52_p446o_hw_before(struct dsi_ctrl_hw *,u32);\n"+
              "extern void a52_p446o_hw_after(struct dsi_ctrl_hw *,u32);\n"+
              a,"hardware externs")
        # Match each of three 446L trigger sites; select actual MMIO writes only.
        scan=0
        for flags in ("flags","flags","0x80000000U"):
            marker="a52_p446l_after_trigger(ctrl,"+flags+");"
            pos=s.find(marker,scan)
            if pos<0:raise RuntimeError("446O HW missing "+marker)
            wr="DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);"
            word=s.rfind(wr,0,pos)
            if word<0 or pos-word>250:
                raise RuntimeError("446O trigger not immediately preceding "+marker)
            s=s[:word]+"a52_p446o_hw_before(ctrl,"+flags+");\n\t\t"+s[word:]
            pos=s.find(marker,word)
            s=s[:pos]+"a52_p446o_hw_after(ctrl,"+flags+");\n\t\t"+s[pos:]
            scan=s.find(marker,pos)+len(marker)
        if s.count("a52_p446o_hw_after(ctrl,")!=3:
            raise RuntimeError("446O expected 3 HW trigger hooks")
        hw.write_text(s)
    if kind=="gki" and variant=="split":
        s=panel.read_text()
        needle="if(vdd->not_support_single_tx) /* Some DDI does not support single tx */\n\t\t\tcmds->last_command = true;"
        replacement=needle+'''
        /* A52_PHASE446O_SPLIT_COLD_ON: force one trigger per packet,
         * only in the normal panel ON sequence after cold restart.
         * This is an intentionally behavior-changing diagnostic.
         */
        if (type==DSI_CMD_SET_ON && !display->is_cont_splash_enabled)
            cmds->last_command = true;
'''
        s=one(s,needle,replacement,"split per-packet command")
        panel.write_text(s)

def check(root,kind,variant):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    central=(base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")).read_text()
    panel=(base/"dsi/dsi_panel.c").read_text()
    ctrl=(base/"dsi/dsi_ctrl.c").read_text()
    hw=(base/"dsi/dsi_ctrl_hw_cmn.c").read_text()
    for needle in (MARK,"O_PRE_CTRL","O_POST_CTRL","O_DMA_BUF","O_FAST_0_200","O_PRE_BUS","O_POST_BUS"):
        assert needle in central,needle
    assert "A52_PHASE446O_DMA_BUFFER_NOTE" in ctrl
    assert "A52_PHASE446O_PANEL_ARM" in panel
    assert hw.count("a52_p446o_hw_after(ctrl,")==3
    assert ("A52_PHASE446O_SPLIT_COLD_ON" in panel)==(kind=="gki" and variant=="split")
    print("Phase446O",kind,variant,"source audit PASS")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--kind",choices=("gki","tg"),required=True)
    ap.add_argument("--variant",choices=("diag","split"),default="diag")
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only:patch(a.root,a.kind,a.variant)
    check(a.root,a.kind,a.variant)

if __name__=="__main__":main()
