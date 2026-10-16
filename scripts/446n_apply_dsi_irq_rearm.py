#!/usr/bin/env python3
"""Phase446N -- matched first-cold-restart DSI virtual IRQ A/B.

Control retains all Phase446M diag behavior. Treatment resets software IRQ
subscription mask/refcounts ONLY after re-registering the DSI ISR a second time.
Bounded rate-limited MDSS top-IRQ source tracing avoids PuTTY flooding.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE446N_DSI_IRQ_REARM_V1"
D=Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
S=Path("drivers/a52_display/msm/sde/sde_irq.c")

def one(s,a,b,what):
    if s.count(a)!=1: raise RuntimeError("446N "+what+" anchors "+str(s.count(a)))
    return s.replace(a,b,1)

def fn(s,signature,callback):
    p=s.find(signature)
    if p<0 or s.find(signature,p+1)!=-1: raise RuntimeError("446N function "+signature)
    e=s.find("\n}\n",p)
    if e<0:raise RuntimeError("446N missing closing brace: "+signature)
    return s[:p]+callback(s[p:e+3])+s[e+3:]

DSI_HELPER=r'''
/* A52_PHASE446N_DSI_IRQ_REARM_V1: bounded read-only IRQ witness */
#include <linux/irqdesc.h>
#include <linux/kernel_stat.h>
static atomic_t a52_p446n_isr_registrations = ATOMIC_INIT(0);
static atomic_t a52_p446n_cold_trigger_logged = ATOMIC_INIT(0);
static atomic_t a52_p446n_cold_timeout_logged = ATOMIC_INIT(0);
static atomic_t a52_p446n_cold_en_logged = ATOMIC_INIT(0);
static atomic_t a52_p446n_cold_isr_logged = ATOMIC_INIT(0);
extern void a52_p446n_sde_snapshot(const char *stage);

static void a52_p446n_dsi_snapshot(struct dsi_ctrl *dc, const char *stage)
{
    struct irq_desc *desc;
    unsigned int i, bitmap = 0, sum = 0, virq;
    if (!dc) return;
    virq = dc->irq_info.irq_num;
    if (virq == (unsigned int)-1) return;
    desc = irq_to_desc(virq);
    for (i=0; i<DSI_STATUS_INTERRUPT_COUNT; ++i) {
        unsigned int ref = READ_ONCE(dc->irq_info.irq_stat_refcount[i]);
        sum += ref;
        if (ref) bitmap |= BIT(i);
    }
    pr_warn("P446N DSI %s virq=%u depth=%d disabled=%d pending=%d kstat=%u mask=%08x ref_dma=%u refs_total=%u refs_bitmap=%08x int_ctrl=%08x\n",
        stage, virq, desc ? (int)READ_ONCE(desc->depth) : -1,
        desc ? (int)irqd_irq_disabled(&desc->irq_data) : -1,
        /* Linux IRQ core IRQS_PENDING is bit 9 (0x200) */
        desc ? !!(READ_ONCE(desc->core_internal_state__do_not_mess_with_it) & 0x200) : -1,
        desc ? kstat_irqs(virq) : 0,
        READ_ONCE(dc->irq_info.irq_stat_mask),
        READ_ONCE(dc->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE]),
        sum, bitmap, DSI_R32(&dc->hw, DSI_INT_CTRL));
    a52_p446n_sde_snapshot(stage);
}
'''

SDE_HELPER=r'''
/* A52_PHASE446N_DSI_IRQ_REARM_V1: storm source sampler */
static atomic_t a52_p446n_mdss_count = ATOMIC_INIT(0);
void a52_p446n_sde_snapshot(const char *stage)
{
    struct sde_kms *kms = READ_ONCE(a52_p446m_kms);
    struct irq_desc *desc = irq_to_desc(271);
    u32 hw_src = 0xffffffffU;
    unsigned int mapping4 = 0;
    if (kms && kms->irq_controller.domain)
        mapping4 = irq_find_mapping(kms->irq_controller.domain, 4);
    if (kms && kms->hw_intr && kms->irq_enabled)
        kms->hw_intr->ops.get_interrupt_sources(kms->hw_intr, &hw_src);
    pr_warn("P446N MDSS %s mapping4=%u virq271_depth=%d disabled=%d pending=%d kstat=%u HW_INTR_STATUS=%08x last=%08x top_irq_count=%u\n",
        stage, mapping4, desc ? (int)READ_ONCE(desc->depth) : -1,
        desc ? (int)irqd_irq_disabled(&desc->irq_data) : -1,
        desc ? !!(READ_ONCE(desc->core_internal_state__do_not_mess_with_it) & 0x200) : -1,
        kstat_irqs(271), hw_src, READ_ONCE(g_sde_irq_status),
        (unsigned int)atomic_read(&a52_p446n_mdss_count));
}
EXPORT_SYMBOL_GPL(a52_p446n_sde_snapshot);
'''

def patch_dsi(s,variant):
    if TAG in s: return s
    if "A52_PHASE446M_GDSC_IRQ_AB_V1" not in s:
        raise RuntimeError("446N needs 446M DSI instrumentation")
    s=one(s, '#define DSI_CTRL_DEFAULT_LABEL "MDSS DSI CTRL"',
        DSI_HELPER+'\n#define DSI_CTRL_DEFAULT_LABEL "MDSS DSI CTRL"', "DSI helpers")
    def setup(f):
        f=one(f, '\tirq_num = platform_get_irq(dsi_ctrl->pdev, 0);',
            r'''    pr_warn("P446N VARIANT=@VAR@ setup oldirq=%d setups=%d old_mask=%08x old_dma_ref=%u\n",
        dsi_ctrl->irq_info.irq_num, atomic_read(&a52_p446n_isr_registrations),
        dsi_ctrl->irq_info.irq_stat_mask,
        dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE]);
'''.replace('@VAR@',variant)+'\tirq_num = platform_get_irq(dsi_ctrl->pdev, 0);', "setup variant")
        anchor='\t\t\tdsi_ctrl->irq_info.irq_num = irq_num;\n\t\t\tdisable_irq_nosync(irq_num);\n'
        rearm=r'''
                /* Experimental: the old ISR action was released and replaced.
                 * Its software subscribers are no longer valid for this new
                 * IRQ action. Only do this on the second successful setup. */
                spin_lock_irq(&dsi_ctrl->irq_info.irq_lock);
                dsi_ctrl->irq_info.irq_stat_mask = 0;
                memset(dsi_ctrl->irq_info.irq_stat_refcount, 0,
                    sizeof(dsi_ctrl->irq_info.irq_stat_refcount));
                spin_unlock_irq(&dsi_ctrl->irq_info.irq_lock);
                pr_warn("P446N REARM reset stale IRQ mask and refcounts\n");
                a52_p446n_dsi_snapshot(dsi_ctrl, "cold-after-rearm");
''' if variant=="rearm" else ""
        f=one(f,anchor,anchor+
            '\t\t\tif (atomic_inc_return(&a52_p446n_isr_registrations) >= 2) {\n'
            '\t\t\t\ta52_p446n_dsi_snapshot(dsi_ctrl, "cold-after-disable-before-rearm");\n'+
            rearm+'\t\t\t}\n', "second ISR registration")
        return f
    s=fn(s,'static int _dsi_ctrl_setup_isr(struct dsi_ctrl *dsi_ctrl)',setup)
    def enable(f):
        a='\tspin_unlock_irqrestore(&dsi_ctrl->irq_info.irq_lock, flags);\n'
        b=a+'''    if (atomic_read(&a52_p446n_isr_registrations) >= 2 &&
        intr_idx == DSI_SINT_CMD_MODE_DMA_DONE &&
        atomic_inc_return(&a52_p446n_cold_en_logged) <= 12)
        a52_p446n_dsi_snapshot(dsi_ctrl, "cold-dma-interrupt-enable");
'''
        return one(f,a,b,"enable_status_interrupt")
    s=fn(s,'void dsi_ctrl_enable_status_interrupt(struct dsi_ctrl *dsi_ctrl,',enable)
    def trigger(f):
        a='\tif (!(flags & DSI_CTRL_CMD_BROADCAST_MASTER))\n\t\tdsi_hw_ops.trigger_command_dma(&dsi_ctrl->hw);'
        b='''\tif (atomic_read(&a52_p446n_isr_registrations) >= 2 &&
\t    atomic_cmpxchg(&a52_p446n_cold_trigger_logged, 0, 1) == 0) {
\t\ta52_p446n_dsi_snapshot(dsi_ctrl, "cold-before-SW_TRIGGER");
\t}
'''+a
        return one(f,a,b,"cold SW_TRIGGER")
    s=fn(s,'int dsi_ctrl_cmd_tx_trigger(struct dsi_ctrl *dsi_ctrl, u32 flags)',trigger)
    def timeout(f):
        a='\t\ta52_p446m_snapshot_irq_timeout();\n'
        b=a+'''        if (atomic_read(&a52_p446n_isr_registrations) >= 2 &&
            atomic_cmpxchg(&a52_p446n_cold_timeout_logged, 0, 1) == 0)
            a52_p446n_dsi_snapshot(dsi_ctrl, "cold-first-DMA-timeout");
'''
        return one(f,a,b,"cold timeout")
    s=fn(s,'static void dsi_ctrl_dma_cmd_wait_for_done(struct work_struct *work)',timeout)
    def isr(f):
        a='\tdsi_ctrl = ptr;\n'
        b=a+'''    if (atomic_read(&a52_p446n_isr_registrations) >= 2 &&
        atomic_inc_return(&a52_p446n_cold_isr_logged) <= 4)
        pr_warn("P446N DSI_ISR virq=%d mask=%08x INT_CTRL=%08x\\n",
            irq, dsi_ctrl->irq_info.irq_stat_mask,
            DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
'''
        return one(f,a,b,"cold ISR entry")
    s=fn(s,'static irqreturn_t dsi_ctrl_isr(int irq, void *ptr)',isr)
    return s+'\n/* '+TAG+' variant='+variant+' */\n'

def patch_sde(s,variant):
    if TAG in s:return s
    if 'A52_PHASE446M_GDSC_IRQ_AB_V1' not in s:
        raise RuntimeError("446N needs 446M SDE instrumentation")
    a='EXPORT_SYMBOL_GPL(a52_p446m_snapshot_irq_timeout);\n'
    s=one(s,a,a+SDE_HELPER,"SDE snapshot after 446M")
    def isr(f):
        a='\tg_sde_irq_status = interrupts;\n'
        b=a+'''    {
        unsigned int n=(unsigned int)atomic_inc_return(&a52_p446n_mdss_count);
        /* Only powers of two: at most 24 messages up to 16M IRQs. */
        if (n && !(n & (n-1)))
            pr_warn("P446N MDSS_IRQ sample n=%u sources=%08x mapping4=%u\\n",
                n, interrupts,
                irq_find_mapping(sde_kms->irq_controller.domain, 4));
    }
'''
        return one(f,a,b,"MDSS storm witness")
    s=fn(s,'irqreturn_t sde_irq(struct msm_kms *kms)',isr)
    return s+'\n/* '+TAG+' variant='+variant+' */\n'

def validate(root,variant):
    d=(root/D).read_text()
    s=(root/S).read_text()
    assert TAG in d and TAG in s
    assert "variant="+variant in d and "variant="+variant in s
    assert ('P446N REARM reset' in d) == (variant=='rearm')
    for needle in ('cold-before-SW_TRIGGER','cold-first-DMA-timeout','cold-after-disable-before-rearm','P446N MDSS_IRQ sample','refs_bitmap','IRQS_PENDING'):
        assert needle in d+s,needle
    print("Phase446N "+variant+" patched and source-audited: PASS")

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--variant',choices=['diag','rearm'],required=True)
    p.add_argument('--check-only',action='store_true')
    a=p.parse_args()
    if not a.check_only:
        d,s=a.root/D,a.root/S
        converted=((d,patch_dsi(d.read_text(),a.variant)),
                   (s,patch_sde(s.read_text(),a.variant)))
        for path,data in converted:path.write_text(data)
    validate(a.root,a.variant)

if __name__=='__main__':main()
