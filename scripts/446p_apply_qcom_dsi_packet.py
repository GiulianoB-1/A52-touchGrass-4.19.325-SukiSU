#!/usr/bin/env python3
"""Phase446P: restore Qualcomm/TouchGrass embedded DSI command-DMA header.

TouchGrass drm_mipi_dsi.c places packet Data ID at header[2] (parameters/WC
at [0:2]). Android common 5.10 uses the standard header[0] Data ID,
which is incompatible with the ported Qualcomm dsi_ctrl_copy_and_pad_cmd()
that reads buf[2] as command type. Use a PRIVATE techpack helper only.
Also restore the precise timeout cleanup removed by Phase411, while keeping
FENCE1G no-panic and the original missed-IRQ DMA_DONE fallback.

Pinned TG: micr0softstore/samsung_android_kernel_a52xq@6bf351bd.
"""
import argparse
from pathlib import Path

MARK="A52_PHASE446P_QCOM_DSI_PACKET_V1"
TARGET=Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
UPSTREAM=Path("drivers/gpu/drm/drm_mipi_dsi.c")

QCOM=r'''
/* A52_PHASE446P_QCOM_DSI_PACKET_V1
 * Techpack-local reproduction of TouchGrass 4.19 drm_mipi_dsi.c:
 * Qualcomm embedded CMD DMA consumes [P0/WClo,P1/WChi,DataID,flags].
 * The Android 5.10 global DRM helper MUST retain its standard wire layout.
 */
static int a52_p446p_qcom_create_packet(struct mipi_dsi_packet *packet,
                                        const struct mipi_dsi_msg *msg)
{
    const u8 *tx;
    if (!packet || !msg)
        return -EINVAL;
    if (!mipi_dsi_packet_format_is_short(msg->type) &&
        !mipi_dsi_packet_format_is_long(msg->type))
        return -EINVAL;
    if (msg->channel > 3)
        return -EINVAL;

    memset(packet, 0, sizeof(*packet));
    packet->header[2] =
        ((msg->channel & 0x3) << 6) | (msg->type & 0x3f);
    if (mipi_dsi_packet_format_is_long(msg->type)) {
        packet->header[0] = (msg->tx_len >> 0) & 0xff;
        packet->header[1] = (msg->tx_len >> 8) & 0xff;
        packet->payload_length = msg->tx_len;
        packet->payload = msg->tx_buf;
    } else {
        tx = msg->tx_buf;
        packet->header[0] = msg->tx_len > 0 ? tx[0] : 0;
        packet->header[1] = msg->tx_len > 1 ? tx[1] : 0;
    }
    packet->size = sizeof(packet->header) + packet->payload_length;
    return 0;
}

static atomic_t a52_p446p_tx_messages = ATOMIC_INIT(0);
static atomic_t a52_p446p_f0_messages = ATOMIC_INIT(0);
static atomic_t a52_p446p_batches = ATOMIC_INIT(0);

static void a52_p446p_log_packet(const struct mipi_dsi_msg *msg,
                                 const u8 *data, u32 size, u32 flags)
{
    unsigned int n = (unsigned int)atomic_inc_return(&a52_p446p_tx_messages);
    const u8 *tx = msg->tx_buf;
    bool f0 = msg->tx_len >= 3 && tx &&
         tx[0] == 0xf0 && tx[1] == 0x5a && tx[2] == 0x5a;
    unsigned int f0n = f0 ?
         (unsigned int)atomic_inc_return(&a52_p446p_f0_messages) : 0;
    if (n <= 30 || (f0 && f0n <= 12))
        pr_info("P446P QCOM_TX n=%u type=%02x msgflags=%04x ctrlflags=%08x txlen=%zu padded=%u bytes=%*phN\n",
            n,msg->type,(unsigned int)msg->flags,flags,msg->tx_len,size,
            (int)min(size,16U),data);
}
'''

def fn(s,signature):
    p=s.find(signature)
    if p<0:raise RuntimeError("446P function not found: "+signature)
    e=s.find("\n}\n",p)
    if e<0:raise RuntimeError("446P closing brace absent: "+signature)
    return p,e+3,s[p:e+3]

def once(s,old,new,why):
    n=s.count(old)
    if n!=1:raise RuntimeError("446P "+why+" anchor count="+str(n))
    return s.replace(old,new,1)

def patch(s):
    if MARK in s:return s
    if "A52_PHASE446N_DSI_IRQ_REARM_V1" not in s or \
       "A52_FENCE1G_NO_TIMEOUT_PANIC_V1" not in s:
        raise RuntimeError("446P expects 446N/FENCE1G assembled sources")
    s=once(s,"static int dsi_ctrl_copy_and_pad_cmd(",
           QCOM+"\nstatic int dsi_ctrl_copy_and_pad_cmd(",
           "insert Qualcomm local packet helper")
    s=once(s,"\trc = mipi_dsi_create_packet(&packet, msg);",
           "\trc = a52_p446p_qcom_create_packet(&packet, msg);",
           "replace tx encoding (also used by dsi_message_rx)")
    # The log is AFTER Qualcomm's LASTCOMMAND/long-packet flags were set.
    start,end,f=fn(s,"static int dsi_message_tx(struct dsi_ctrl *dsi_ctrl,")
    old="\tif ((msg->flags & MIPI_DSI_MSG_LASTCOMMAND))\n\t\tbuffer[3] |= BIT(7);//set the last cmd bit in header."
    new=(old+"\n\t/* P446P: actual padded bytes submitted to the DMA buffer. */\n"
         "\ta52_p446p_log_packet(msg,buffer,length,*flags);")
    if f.count(old)!=1:raise RuntimeError("446P header logger anchor absent")
    s=s[:start]+f.replace(old,new,1)+s[end:]
    # Capture complete batched memory before cmd_len is reset to zero.
    start,end,f=fn(s,"static int dsi_message_tx(struct dsi_ctrl *dsi_ctrl,")
    old="\t\t\tcmd_mem.length = dsi_ctrl->cmd_len;\n\t\t\tdsi_ctrl->cmd_len = 0;"
    new='''            cmd_mem.length = dsi_ctrl->cmd_len;
            {
                unsigned int n=(unsigned int)atomic_inc_return(&a52_p446p_batches);
                if (n <= 12)
                    pr_info("P446P DMA_BUFFER n=%u iova=%llx total=%u bytes=%*phN\\n",
                        n,(unsigned long long)cmd_mem.offset,cmd_mem.length,
                        (int)min(cmd_mem.length,16U),dsi_ctrl->vaddr);
            }
            dsi_ctrl->cmd_len = 0;'''
    if f.count(old)!=1:raise RuntimeError("446P batching logger anchor absent")
    s=s[:start]+f.replace(old,new,1)+s[end:]

    # Phase411 replaced the stock if {...status fallback...disable interrupt}
    # with if (timed_out) a52_p411_timeout_panic(). FENCE1G made this
    # diagnostic nonfatal, but a refcount leak remains on every timeout.
    # Restore the stock fallback/one decrement, keep the existing FENCE1G
    # diagnostic ONLY when hardware did not raise DMA_DONE.
    start,end,f=fn(s,"static void dsi_ctrl_dma_cmd_wait_for_done(struct work_struct *work)")
    old='''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig))
\t\ta52_p411_timeout_panic(dsi_ctrl);'''
    new='''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {
        u32 status = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);
        if (status & DSI_CMD_MODE_DMA_DONE) {
            status |= (DSI_CMD_MODE_DMA_DONE | DSI_BTA_DONE);
            dsi_hw_ops.clear_interrupt_status(&dsi_ctrl->hw, status);
            DSI_CTRL_WARN(dsi_ctrl,
                "dma_tx done but irq not triggered (P446P fallback)\\n");
        } else {
            /* P411/FENCE1G diagnostic, still NO PANIC. */
            a52_p411_timeout_panic(dsi_ctrl);
        }
        /* Restore EXACTLY one release of the enable performed for DMA. */
        dsi_ctrl_disable_status_interrupt(dsi_ctrl,
            DSI_SINT_CMD_MODE_DMA_DONE);
    }'''
    if f.count(old)!=1:raise RuntimeError("446P Phase411 timeout branch unexpected")
    f=f.replace(old,new,1)
    # The separate P445 cold-R1 recovery retries by calling enable_status
    # a second time. On exit it currently releases only one subscription;
    # balance the extra reference whether the retry times out or completes.
    f=once(f,"\tstruct dsi_ctrl_hw_ops dsi_hw_ops;\n",
           "\tstruct dsi_ctrl_hw_ops dsi_hw_ops;\n"+
           "\tbool a52_p446p_r1_rearmed = false;\n","R1 scope guard")
    arm=("\t\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\n"+
         "\t\t\tdsi_ctrl_enable_status_interrupt(dsi_ctrl,\n"+
         "\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE, NULL);")
    f=once(f,arm,arm+
           "\n\t\t\ta52_p446p_r1_rearmed = true;","mark R1 subscription")
    cleanup=("\t\tif (!atomic_read(&dsi_ctrl->dma_irq_trig))\n"+
             "\t\t\tdsi_ctrl_disable_status_interrupt(dsi_ctrl, DSI_SINT_CMD_MODE_DMA_DONE);\n"+
             "\t\tgoto done;")
    repair='''        if (!ret && !atomic_read(&dsi_ctrl->dma_irq_trig)) {
            u32 status = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);
            if (status & DSI_CMD_MODE_DMA_DONE) {
                status |= DSI_CMD_MODE_DMA_DONE | DSI_BTA_DONE;
                dsi_hw_ops.clear_interrupt_status(&dsi_ctrl->hw, status);
                DSI_CTRL_WARN(dsi_ctrl,
                    "dma_tx done but irq not triggered (P446P cold R1)\\n");
            }
        }
        if (!atomic_read(&dsi_ctrl->dma_irq_trig))
            dsi_ctrl_disable_status_interrupt(dsi_ctrl,
                DSI_SINT_CMD_MODE_DMA_DONE);
        /* R1 added a second status-interrupt subscription. The second ISR
         * can only release its own; explicitly release the original one.
         * If both timed out, the lines above release both subscriptions.
         */
        if (a52_p446p_r1_rearmed)
            dsi_ctrl_disable_status_interrupt(dsi_ctrl,
                DSI_SINT_CMD_MODE_DMA_DONE);
        goto done;'''
    f=once(f,cleanup,repair,"cold R1 balanced cleanup")
    s=s[:start]+f+s[end:]
    return s+"\n/* "+MARK+": local Qualcomm-format helper, no global DRM changes. */\n"

def audit(root):
    s=(root/TARGET).read_text()
    if MARK not in s: raise RuntimeError("446P patch missing")
    if s.count("a52_p446p_qcom_create_packet(&packet, msg);")!=1:
        raise RuntimeError("446P call-site not unique")
    if "rc = mipi_dsi_create_packet(&packet, msg);" in s:
        raise RuntimeError("446P original global caller remains")
    if s.count("packet->header[2] =")!=1:
        raise RuntimeError("446P DataID not at header[2]")
    if "dma_tx done but irq not triggered (P446P fallback)" not in s:
        raise RuntimeError("446P missed-IRQ fallback absent")
    start,end,worker=fn(s,"static void dsi_ctrl_dma_cmd_wait_for_done(struct work_struct *work)")
    if worker.count("dsi_ctrl_disable_status_interrupt(dsi_ctrl,")!=3:
        raise RuntimeError("446P expected 1 normal timeout + 2 cold R1 reference releases")
    if "a52_p446p_r1_rearmed = true" not in worker or "P446P cold R1" not in worker:
        raise RuntimeError("446P cold R1 balancing or fallback absent")
    if worker.count("a52_p411_timeout_panic(dsi_ctrl);")!=1:
        raise RuntimeError("446P FENCE1G timeout witness missing")
    if "A52_PHASE446N_DSI_IRQ_REARM_V1" not in s:
        raise RuntimeError("446P lost the known-good IRQ rearm")
    upstream=(root/UPSTREAM).read_text()
    if "packet->header[0] = ((msg->channel & 0x3) << 6)" not in upstream:
        raise RuntimeError("446P expected original upstream 5.10 helper order")
    # Check every techpack caller in resulting tree; only our local builder
    # is allowed for embedded Qualcomm hardware transactions.
    users=[]
    base=root/"drivers/a52_display"
    for file in base.rglob("*.c"):
        body=file.read_text(errors="replace")
        if "mipi_dsi_create_packet(" in body:
            users.append(str(file.relative_to(root)))
    if users:
        raise RuntimeError("446P unported techpack global helper callers: "+str(users))
    print("Phase446P PASS: TG header[2] restored, global 5.10 helper unchanged, "
          "single read/write encoding path, original missed-IRQ fallback and "
          "one DMA refcount decrement on timeout")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    target=a.root/TARGET
    if not a.check_only:
        target.write_text(patch(target.read_text()))
    audit(a.root)

if __name__=="__main__":
    main()
