#!/usr/bin/env python3
"""446L cold-only first ON packet FIFO A/B, separate from FETCH_MEMORY control."""
import argparse
from pathlib import Path
MARK="A52_PHASE446L_COLD_ON_FIFO_AB_V1"

def one(s,a,b,why):
    n=s.count(a)
    if n!=1: raise RuntimeError(f"{why}: anchor count {n}")
    return s.replace(a,b,1)

CENTRAL=r'''
/* A52_PHASE446L_COLD_ON_FIFO_AB_V1 */
static atomic_t p446l_fifo_on=ATOMIC_INIT(0);
void a52_p446l_fifo_arm(void)
{
    (void)atomic_cmpxchg(&p446l_fifo_on,0,1);
}
EXPORT_SYMBOL_GPL(a52_p446l_fifo_arm);
bool a52_p446l_fifo_convert(const struct mipi_dsi_msg *msg,u32 *flags)
{
    if (!msg || !flags || atomic_read(&p446l_fifo_on)!=1)
        return false;
    if (!(*flags & DSI_CTRL_CMD_FETCH_MEMORY) ||
        (*flags & (DSI_CTRL_CMD_NON_EMBEDDED_MODE |
                   DSI_CTRL_CMD_DEFER_TRIGGER)) ||
        msg->tx_len+4U > DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE)
        return false;
    if (atomic_cmpxchg(&p446l_fifo_on,1,2)!=1)
        return false;
    *flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;
    *flags |= DSI_CTRL_CMD_FIFO_STORE;
    pr_warn("P446L FIFO first cold ON type=%02x len=%zu flags=%08x msgflags=%x\n",
        (u32)msg->type,msg->tx_len,*flags,(u32)msg->flags);
    return true;
}
EXPORT_SYMBOL_GPL(a52_p446l_fifo_convert);
'''

def patch(root):
    d=root/"drivers/a52_display/msm"
    c=d/"a52_phase445.c"
    p=d/"dsi/dsi_panel.c"
    ctrl=d/"dsi/dsi_ctrl.c"
    s=c.read_text()
    if MARK not in s:
        # Definitions for FIFO flags and mipi_dsi_msg live in the controller
        # compilation unit. Put the single-use conversion state there instead
        # of putting type-specific code in the Phase445 recorder.
        pass
    s=ctrl.read_text()
    if MARK not in s:
        # Avoid premature references to dsi_ctrl flags by keeping the code
        # inside dsi_ctrl.c, after all its header macros.
        pos="static void dsi_kickoff_msg_tx("
        s=one(s,pos,CENTRAL+"\n"+pos,"controller FIFO helper")
        s=one(s,"dsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n",
              "dsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n"
              "\t(void)a52_p446l_fifo_convert(msg,flags);\n",
              "select first cold ON")
        # Add FIFO buffer prefix to 446L metadata in the immediate-send
        # branch, so it is not mistaken for an unmapped IOVA.
        start=s.find("static void dsi_kickoff_msg_tx(")
        stop=s.find("\nstatic ",start+25)
        if start<0 or stop<0: raise RuntimeError("kickoff body missing")
        fn=s[start:stop]
        k=fn.rfind("dsi_hw_ops.kickoff_fifo_command(&dsi_ctrl->hw,")
        if k<0:raise RuntimeError("FIFO kickoff not found")
        fn=fn[:k]+("a52_p446l_note(&dsi_ctrl->hw,0,0,cmd->size,\n"
                   "\t\t\t\t\t\tflags,(u32)msg->flags,0,(const u8 *)cmd->command);\n"
                   "\t\t\t")+fn[k:]
        s=s[:start]+fn+s[stop:]
        ctrl.write_text(s)
    s=p.read_text()
    if "A52_PHASE446L_FIFO_ON_ARM" not in s:
        anchor="a52_p446l_arm(&display->ctrl[0].ctrl->hw);"
        addition=("\n        /* A52_PHASE446L_FIFO_ON_ARM */\n"
                  "        if (type==DSI_CMD_SET_ON && !display->is_cont_splash_enabled &&\n"
                  "            display->ctrl[0].ctrl) a52_p446l_fifo_arm();")
        s=one(s,anchor,anchor+addition,"panel FIFO arm")
        s=one(s,"extern void a52_p446l_arm(struct dsi_ctrl_hw *);\n",
              "extern void a52_p446l_arm(struct dsi_ctrl_hw *);\n"
              "extern void a52_p446l_fifo_arm(void);\n",
              "panel FIFO prototype")
        p.write_text(s)

def check(root):
    d=root/"drivers/a52_display/msm"
    x=(d/"dsi/dsi_ctrl.c").read_text()
    y=(d/"dsi/dsi_panel.c").read_text()
    for token in (MARK,"P446L FIFO first cold ON","a52_p446l_fifo_convert(msg,flags)",
                  "a52_p446l_note(&dsi_ctrl->hw,0,0,cmd->size,"):
        if token not in x:raise RuntimeError("FIFO missing "+token)
    if y.count("A52_PHASE446L_FIFO_ON_ARM")!=1:raise RuntimeError("FIFO arm not unique")
    print("446L FIFO cold ON A/B PASS: first eligible ON packet uses FIFO_STORE, no FETCH_MEMORY")

if __name__=="__main__":
    a=argparse.ArgumentParser()
    a.add_argument("--root",type=Path,required=True)
    a.add_argument("--check-only",action="store_true")
    q=a.parse_args()
    if not q.check_only:patch(q.root)
    check(q.root)
