#!/usr/bin/env python3
"""Phase446O: first and second cold-ON DMA trigger twin, plus GKI split/probe A/B.

Requires Phase446L. Retains Phase446N's rearm on GKI, changing ONLY the ON
message transfer boundary for split/probe variants. Stock TG remains passive.
Does not modify the low-level DMA SW_TRIGGER programming or timeout behavior.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE446O_SECOND_DMA_TWIN_V1"

def one(s,a,b,tag):
    n=s.count(a)
    if n!=1: raise RuntimeError("446O "+tag+" found "+str(n)+" anchors")
    return s.replace(a,b,1)

def apply_central(s,kind):
    if TAG in s:return s
    if "A52_PHASE446L_FIRST_COLD_ON_TRIGGER_V1" not in s:
        raise RuntimeError("446O requires Phase446L")
    s=one(s,"static struct p446l_fast p446l_fast[7];",
        "static struct p446l_fast p446l_fast[10];","fast array")
    s=one(s,
        "static const u32 p446l_deadline_us[7]={0U,1U,2U,5U,10U,20U,50U};",
        "static const u32 p446l_deadline_us[10]={0U,1U,2U,5U,10U,20U,50U,100U,150U,200U};",
        "fast sample times")
    s=one(s,'__STORE__(0x446c0035U,16U,"L_FAST_0_50",7U,50U,',
        '__STORE__(0x446c0035U,16U,(second ? "O_FAST_0_200" : "L_FAST_0_200"),10U,200U,',
        "fast raw storage")
    # The first capture stays compatible (same ID and data layout extended).
    s=one(s,"return atomic_read(&p446l_state)==1;",
          "return atomic_read(&p446l_state)==1 || atomic_read(&p446l_state)==2;",
          "keep second ON trigger armed")
    s=one(s,"if (!ctrl || ctrl->index!=0 || atomic_read(&p446l_state)!=1)",
          "if (!ctrl || ctrl->index!=0 ||\n        (atomic_read(&p446l_state)!=1 && atomic_read(&p446l_state)!=2))",
          "retain second command metadata")
    s=one(s,"static struct p446l_meta p446l_cmd;",
        """static struct p446l_meta p446l_cmd;
static u8 p446o_packet[4096];
static u32 p446o_packet_len;
static u32 p446o_pre_ctrl[256];
void a52_p446o_pre(struct dsi_ctrl_hw *hw)
{
    u32 i;
    if (!hw || !hw->base || hw->index!=0 || !a52_p446l_is_armed())
        return;
    for (i=0;i<ARRAY_SIZE(p446o_pre_ctrl);i++)
        p446o_pre_ctrl[i]=DSI_R32(hw,i*4U);
}
EXPORT_SYMBOL_GPL(a52_p446o_pre);
""","central pre-sample")
    # a52_p446l_is_armed() is defined below this point. Its prototype must
    # exist before calling it in a52_p446o_pre().
    s=one(s, "static u32 p446o_pre_ctrl[256];\nvoid a52_p446o_pre",
        "static u32 p446o_pre_ctrl[256];\nbool a52_p446l_is_armed(void);\nvoid a52_p446o_pre",
        "pre-sample declaration")
    marker="if (prefix && p446l_cmd.prefix_len)\n        memcpy(p446l_cmd.prefix,prefix,p446l_cmd.prefix_len);"
    new=marker+"""
    p446o_packet_len=min(len,(u32)sizeof(p446o_packet));
    if (prefix && p446o_packet_len)
        memcpy(p446o_packet,prefix,p446o_packet_len);
"""
    s=one(s,marker,new,"full DMA source bytes")
    header="""    u64 trigger_ns;
    u32 c3,c4;
    if (!hw || hw->index!=0 || !hw->base ||
        atomic_cmpxchg(&p446l_state,1,2)!=1)
        return;
    trigger_ns=ktime_get_ns();"""
    swapped="""    u64 trigger_ns;
    u32 c3,c4;
    bool second;
    if (!hw || hw->index!=0 || !hw->base)
        return;
    if (atomic_cmpxchg(&p446l_state,1,2)==1)
        second=false;
    else if (atomic_cmpxchg(&p446l_state,2,3)==2)
        second=true;
    else
        return;
    trigger_ns=ktime_get_ns();
    pr_warn("P446O %s SW_TRIGGER ns=%llu packet_bytes=%u cmd_iova=%llx command_len=%u hwflags=%x\\n",
        second ? "SECOND" : "FIRST",
        (unsigned long long)trigger_ns, p446o_packet_len,
        (unsigned long long)p446l_cmd.iova, p446l_cmd.len, hw_flags);"""
    s=one(s,header,swapped,"one-shot to two-shot capture")
    # Put second transfer into unique raw sections rather than overwriting
    # first-transfer records. Some GKI recorder section slots are already full;
    # text prints below are a fallback.
    names=["L_CMD_META","L_T3_CTRL","L_T3_CLK","L_T3_BUS",
           "L_T3_META","L_T3_PLATFORM","L_T4_CTRL","L_T4_CLK",
           "L_T4_BUS","L_T4_META","L_T4_PLATFORM"]
    for name in names:
        if s.count('"'+name+'"')!=1:
            raise RuntimeError("446O raw twin anchor "+name)
        s=s.replace('"'+name+'"',
                    '(second ? "'+name.replace("L_","O_",1)+'" : "'+name+'")',1)
    anchor='    __STORE__(0x446c0030U,16U,(second ? "O_CMD_META" : "L_CMD_META"),'
    s=one(s,anchor,
"""    __STORE__(0x446f0001U,16U,second ? "O_PRE_CTRL" : "L_PRE_CTRL",
        second ? 2U : 1U,p446k_hash(p446o_pre_ctrl,256U),
        p446o_pre_ctrl,sizeof(p446o_pre_ctrl));
    __STORE__(0x446f0002U,16U,second ? "O_CMD_FULL" : "L_CMD_FULL",
        p446o_packet_len,second ? 2U : 1U,p446o_packet,p446o_packet_len);
"""+anchor,"pre/bytes raw storage")
    # Fast sampling + full CTRL and debug-bus sweep are already performed by
    # Phase446L's p446l_take_fast() and p446l_take(). Print copies on second
    # trigger so data is available if GKI Phase445's metadata table is full.
    anchor='    pr_warn("P446L FAST 0us st=%08x irq=%08x lane=%08x clk=%08x;'
    insert="""    if (second) {
        pr_warn("P446O SECOND PRE_HASH=%08x POST_HASH=%08x INT0=%08x INT50=%08x INT200=%08x STATUS200=%08x LANE200=%08x CLK200=%08x full_len=%u\\n",
            p446k_hash(p446o_pre_ctrl,256U),c3,
            p446l_fast[0].irq,p446l_fast[6].irq,p446l_fast[9].irq,
            p446l_fast[9].status,p446l_fast[9].lane,
            p446l_fast[9].clk,p446o_packet_len);
        print_hex_dump(KERN_WARNING,"P446O PRE: ",DUMP_PREFIX_OFFSET,
            16,4,p446o_pre_ctrl,sizeof(p446o_pre_ctrl),false);
        print_hex_dump(KERN_WARNING,"P446O POST: ",DUMP_PREFIX_OFFSET,
            16,4,p446l_t3.ctrl,sizeof(p446l_t3.ctrl),false);
        if (p446o_packet_len)
            print_hex_dump(KERN_WARNING,"P446O CMD: ",DUMP_PREFIX_OFFSET,
                16,1,p446o_packet,p446o_packet_len,false);
    }
"""
    s=one(s,anchor,insert+anchor,"second-transfer text fallback")
    return s+"\n/* "+TAG+" */\n"

def apply_hw(s):
    if TAG in s:return s
    if "A52_PHASE446L_HW_TRIGGER" not in s:raise RuntimeError("446O requires 446L trigger hooks")
    a="extern void a52_p446l_after_trigger(struct dsi_ctrl_hw *,u32);\n"
    s=one(s,a,a+"extern void a52_p446o_pre(struct dsi_ctrl_hw *);\n","pre prototype")
    import re
    patt=r"(?m)^([ \t]*)DSI_W32\(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1\);"
    def transform(match):
        indent=match.group(1)
        return indent+"a52_p446o_pre(ctrl);\n"+match.group(0)
    s,n=re.subn(patt,transform,s)
    if n!=3: raise RuntimeError("446O expected exactly 3 SW_TRIGGER sites, got "+str(n))
    return s+"\n/* "+TAG+" */\n"

def apply_panel(s,variant):
    if TAG in s:return s
    if variant=="twin":
        return s+"\n/* "+TAG+" TG stock ON sequence */\n"
    anchor="\t\tlen = ops->transfer(panel->host, &cmds->msg);\n"
    if variant=="normal":
        replacement=anchor
    elif variant=="split":
        replacement="""        /* 446O A/B: send each previously queued idx1..10 immediately.
         * Copy descriptor to avoid modifying panel command-set data. */
        {
            struct mipi_dsi_msg p446o_msg = cmds->msg;
            if (type == DSI_CMD_SET_ON && i >= 1 && i <= 10 &&
                !display->is_cont_splash_enabled) {
                p446o_msg.flags |= MIPI_DSI_MSG_LASTCOMMAND;
                pr_warn("P446O SPLIT force-LAST idx=%d type=%x txlen=%zu\\n",
                    i,p446o_msg.type,p446o_msg.tx_len);
            }
            len = ops->transfer(panel->host, &p446o_msg);
        }
"""
    else:
        replacement="""        /* 446O A/B: harmless DCS NOP (short packet) immediately before
         * the first accumulated long-packet batch, then stock ON set. */
        if (type == DSI_CMD_SET_ON && i == 1 &&
            !display->is_cont_splash_enabled) {
            const u8 a52_nop = 0x00;
            struct mipi_dsi_msg nop = cmds->msg;
            ssize_t nrc;
            nop.type = 0x05; /* MIPI_DSI_DCS_SHORT_WRITE */
            nop.tx_buf = &a52_nop;
            nop.tx_len = 1;
            nop.rx_buf = NULL;
            nop.rx_len = 0;
            nop.flags |= MIPI_DSI_MSG_LASTCOMMAND;
            pr_warn("P446O PROBE second independent DCS NOP before idx1\\n");
            nrc = ops->transfer(panel->host, &nop);
            pr_warn("P446O PROBE DCS NOP rc=%zd\\n",nrc);
        }
""" + anchor
    if variant=="normal":
        # Keep the common marker in the panel source without touching transfer.
        return s+"\n/* "+TAG+" GKI normal batching */\n"
    s=one(s,anchor,replacement,"panel ON A/B")
    return s+"\n/* "+TAG+" GKI "+variant+" */\n"

def apply_ctrl(s,kind):
    if TAG in s:return s
    if kind=="tg":return s+"\n/* "+TAG+" TG passive */\n"
    # Read-only proof of whether TIMEOUT actually releases DMA IRQ refcounts;
    # do not add an extra decrement while the 4.19 timeout worker already has one.
    signature="void dsi_ctrl_disable_status_interrupt(struct dsi_ctrl *dsi_ctrl,"
    a=s.index(signature)
    b=s.index("\n}\n",a)
    fragment=s[a:b+3]
    old="\tspin_unlock_irqrestore(&dsi_ctrl->irq_info.irq_lock, flags);\n"
    new=old+"""    if (intr_idx == DSI_SINT_CMD_MODE_DMA_DONE) {
        static atomic_t p446o_rel_calls = ATOMIC_INIT(0);
        if (atomic_inc_return(&p446o_rel_calls) <= 16)
            pr_warn("P446O DMA_REF_RELEASE ref_now=%u mask=%08x irq=%d\\n",
                dsi_ctrl->irq_info.irq_stat_refcount[intr_idx],
                dsi_ctrl->irq_info.irq_stat_mask,
                dsi_ctrl->irq_info.irq_num);
    }
"""
    fragment=one(fragment,old,new,"IRQ release instrumentation")
    s=s[:a]+fragment+s[b+3:]
    return s+"\n/* "+TAG+" GKI IRQ release audit */\n"

def audit(root,kind,variant):
    base=root/("drivers/a52_display/msm" if kind=="gki" else "techpack/display/msm")
    central=base/("a52_phase445.c" if kind=="gki" else "a52_phase444.c")
    for p in [central,base/"dsi/dsi_ctrl_hw_cmn.c",base/"dsi/dsi_ctrl.c",base/"dsi/dsi_panel.c"]:
        if TAG not in p.read_text():raise RuntimeError("446O missing "+str(p))
    z=central.read_text()
    for needle in ("O_CMD_FULL","O_PRE_CTRL","O_FAST_0_200","P446O SECOND","P446O CMD:","100U,150U,200U"):
        if needle not in z:raise RuntimeError("446O missing "+needle)
    h=(base/"dsi/dsi_ctrl_hw_cmn.c").read_text()
    if h.count("a52_p446o_pre(ctrl);")!=3:raise RuntimeError("446O pretrigger sites mismatch")
    p=(base/"dsi/dsi_panel.c").read_text()
    if kind=="gki":
        if variant=="split" and "P446O SPLIT force-LAST" not in p:raise RuntimeError("split absent")
        if variant=="probe" and "P446O PROBE DCS NOP" not in p:raise RuntimeError("probe absent")
    print("Phase446O",kind,variant,"second trigger and full command/CTRL/debug-bus capture PASS")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--kind",choices=["gki","tg"],required=True)
    ap.add_argument("--variant",choices=["normal","split","probe","twin"],required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if (a.kind=="tg")!=(a.variant=="twin"):
        raise RuntimeError("446O kind and variant mismatch")
    base=a.root/("drivers/a52_display/msm" if a.kind=="gki" else "techpack/display/msm")
    cp=base/("a52_phase445.c" if a.kind=="gki" else "a52_phase444.c")
    hp=base/"dsi/dsi_ctrl_hw_cmn.c"
    pp=base/"dsi/dsi_panel.c"
    dp=base/"dsi/dsi_ctrl.c"
    if not a.check_only:
        updates=[(cp,apply_central(cp.read_text(),a.kind)),
                 (hp,apply_hw(hp.read_text())),
                 (pp,apply_panel(pp.read_text(),a.variant)),
                 (dp,apply_ctrl(dp.read_text(),a.kind))]
        for path,c in updates:path.write_text(c)
    audit(a.root,a.kind,a.variant)

if __name__=="__main__":main()
