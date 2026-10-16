#!/usr/bin/env python3
"""Phase446M controlled MDSS GDSC -> SDE power -> IRQ diagnosis and recovery A/B.

diag: retain Phase415 -EBUSY behavior and log the GDSC/IRQ chain.
recovery: only on MDSS, clear HW_CONTROL on enable and resume the
          existing driver path (SW_COLLAPSE clear + PWR_ON poll).
No forced IRQ enable, no DSI timing modifications, no changes to UFS.
"""
import argparse
from pathlib import Path

TAG = "A52_PHASE446M_GDSC_IRQ_AB_V1"

def getfn(src, name):
    start = src.find(name)
    if start < 0 or src.find(name, start+1) != -1:
        raise RuntimeError("446M function anchor missing/duplicated: " + name)
    end = src.find("\n}\n", start)
    if end < 0:
        raise RuntimeError("446M function closing brace missing: " + name)
    return start, end+3, src[start:end+3]

def one(src, old, new, context):
    n=src.count(old)
    if n!=1:
        raise RuntimeError("446M %s: expected 1 anchor, found %d" % (context,n))
    return src.replace(old,new,1)

def before_final_return(fn, trace, why):
    marker="    return ret;\n"
    pos=fn.rfind(marker)
    if pos<0 or fn[pos+len(marker):].strip() != "}":
        raise RuntimeError("446M missing final return: "+why)
    return fn[:pos]+trace+fn[pos:]

def rework_fn(source, name, callback):
    a,b,f=getfn(source,name)
    f=callback(f)
    return source[:a]+f+source[b:]

def include(src, anchor, header, label):
    line="#include <"+header+">"
    if line in src:
        return src
    return one(src,anchor,anchor+line+"\n",label)

def apply_gdsc(src, variant):
    if TAG in src:
        return src
    if "A52_PHASE415_MDSS_GDSC_HWCTRL_PARITY_V1" not in src:
        raise RuntimeError("446M requires Phase415 MDSS GDSC HW_CONTROL parity")
    src=include(src,"#include <linux/io.h>\n","linux/printk.h","GDSC include")
    en="static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)"
    dis="static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)"
    mode="static int a52_legacy_gdsc_set_mode(struct regulator_dev *rdev,"
    def patch_enable(f):
        f=one(f,"    before = readl_relaxed(gdsc->gdscr);\n",
                "    before = readl_relaxed(gdsc->gdscr);\n"
                "    pr_warn(\"P446M GDSC enable enter name=%s caller=%pS use=%d before=%08x mode=%s\\n\",\n"
                "        gdsc->desc.name, __builtin_return_address(0), rdev->use_count,\n"
                "        before, (before & A52_GDSC_HW_CONTROL) ? \"HW\" : \"SW\");\n",
                "GDSC enable entry")
        blocked='        return -EBUSY;\n'
        if variant=="recovery":
            fix=(
                "        /* 446M experimental: re-take MDSS control only on enable. */\n"
                "        val = readl_relaxed(gdsc->gdscr);\n"
                "        val &= ~A52_GDSC_HW_CONTROL;\n"
                "        writel_relaxed(val, gdsc->gdscr);\n"
                "        mb();\n"
                "        udelay(1);\n"
                "        pr_warn(\"P446M GDSC reclaim name=%s use=%d old=%08x new=%08x\\n\",\n"
                "            gdsc->desc.name, rdev->use_count, before,\n"
                "            readl_relaxed(gdsc->gdscr));\n")
            f=one(f,blocked,fix,"MDSS-only -EBUSY treatment")
        else:
            f=one(f,blocked,
                '        pr_warn("P446M GDSC blocked name=%s use=%d before=%08x rc=%d\\n",\n'
                '            gdsc->desc.name, rdev->use_count, before, -EBUSY);\n'
                +blocked,"MDSS-only -EBUSY diagnostic")
        if "return ret;" not in f:
            raise RuntimeError("446M GDSC enable tail missing")
        f=before_final_return(f,
            '    pr_warn("P446M GDSC enable exit name=%s use=%d rc=%d after=%08x\\n",\n'
            '        gdsc->desc.name, rdev->use_count, ret,\n'
            '        readl_relaxed(gdsc->gdscr));\n'
            "GDSC enable return")
        return f
    def patch_disable(f):
        f=one(f,"    before = readl_relaxed(gdsc->gdscr);\n",
            '    before = readl_relaxed(gdsc->gdscr);\n'
            '    pr_warn("P446M GDSC disable enter name=%s caller=%pS use=%d before=%08x\\n",\n'
            '        gdsc->desc.name, __builtin_return_address(0),\n'
            '        rdev->use_count, before);\n',"GDSC disable entry")
        f=before_final_return(f,
            '    pr_warn("P446M GDSC disable exit name=%s use=%d rc=%d after=%08x\\n",\n'
            '        gdsc->desc.name, rdev->use_count, ret,\n'
            '        readl_relaxed(gdsc->gdscr));\n'
            "GDSC disable return")
        return f
    def patch_mode(f):
        f=one(f,"    before = readl_relaxed(gdsc->gdscr);\n",
            '    before = readl_relaxed(gdsc->gdscr);\n'
            '    pr_warn("P446M GDSC mode enter name=%s caller=%pS use=%d mode=%u before=%08x\\n",\n'
            '        gdsc->desc.name, __builtin_return_address(0),\n'
            '        rdev->use_count, mode, before);\n',"GDSC set_mode entry")
        f=before_final_return(f,
            '    pr_warn("P446M GDSC mode exit name=%s use=%d mode=%u rc=%d after=%08x\\n",\n'
            '        gdsc->desc.name, rdev->use_count, mode, ret,\n'
            '        readl_relaxed(gdsc->gdscr));\n'
            "GDSC set_mode return")
        return f
    src=rework_fn(src,en,patch_enable)
    src=rework_fn(src,dis,patch_disable)
    src=rework_fn(src,mode,patch_mode)
    return src+"\n/* "+TAG+" "+variant+" */\n"

def apply_power(src):
    if TAG in src: return src
    sig="int sde_power_resource_enable(struct sde_power_handle *phandle, bool enable)"
    def rewrite(f):
        f=one(f,'\tpr_debug("enable:%d\\n", enable);\n',
            '\tpr_warn("P446M POWER enter enable=%u caller=%pS\\n",\n'
            '\t\tenable, __builtin_return_address(0));\n'
            '\tpr_debug("enable:%d\\n", enable);\n',"POWER entry")
        for msg,stage in (
            ('failed to set data bus vote id=%d rc=%d','dbus'),
            ('failed to enable vregs rc=%d','vreg'),
            ('failed to set reg bus vote rc=%d','regbus'),
            ('failed to update rsc','rsc'),
            ('clock enable failed rc:%d','clocks')):
            marker='pr_err("'+msg
            if marker not in f:
                raise RuntimeError("446M POWER stage absent: "+stage)
            # Print a stage marker just before the relevant existing pr_err.
            prefix=f.rfind("\n",0,f.index(marker))+1
            f=f[:prefix]+'\t\t\tpr_warn("P446M POWER failure stage='+stage+' rc=%d\\n", rc);\n'+f[prefix:]
        f=one(f,
              "\t\tsde_power_event_trigger_locked(phandle,\n"
              "\t\t\t\tSDE_POWER_EVENT_POST_ENABLE);",
              '\t\tpr_warn("P446M POWER POST_ENABLE rc=%d\\n", rc);\n'
              "\t\tsde_power_event_trigger_locked(phandle,\n"
              "\t\t\t\tSDE_POWER_EVENT_POST_ENABLE);",
              "POWER success event")
        f=one(f,"vreg_err:\n",
              'vreg_err:\n'
              '\tpr_warn("P446M POWER aborted without POST_ENABLE rc=%d\\n", rc);\n',
              "POWER error cleanup")
        return f
    return rework_fn(src,sig,rewrite)+"\n/* "+TAG+" */\n"

IRQ_HELPER=r"""
/* A52_PHASE446M_GDSC_IRQ_AB_V1: first timeout IRQ state witness. */
static struct sde_kms *a52_p446m_kms;
static atomic_t a52_p446m_timeout_once = ATOMIC_INIT(0);

static void a52_p446m_desc_state(unsigned int irq, const char *who)
{
    struct irq_desc *desc = irq_to_desc(irq);
    pr_warn("P446M IRQ %s irq=%u depth=%d disabled=%u kstat=%u\n",
            who, irq, desc ? desc->depth : -1,
            desc ? irqd_irq_disabled(&desc->irq_data) : 0xffffffffU,
            kstat_irqs(irq));
}

void a52_p446m_snapshot_irq_timeout(void)
{
    struct sde_kms *kms;
    struct device_node *node;
    void __iomem *gic, *gdsc;
    u32 intr = 0xffffffffU, ctrl = 0xffffffffU;
    u32 gic_en = 0xffffffffU, gic_pending = 0xffffffffU;
    if (atomic_cmpxchg(&a52_p446m_timeout_once, 0, 1))
        return;
    kms = READ_ONCE(a52_p446m_kms);
    gdsc = ioremap(0x0af01004, 4);
    if (gdsc) {
        ctrl = readl_relaxed(gdsc);
        iounmap(gdsc);
    }
    node = of_find_compatible_node(NULL, NULL, "arm,gic-v3");
    if (node) {
        gic = of_iomap(node, 0);
        of_node_put(node);
        if (gic) {
            /* GICD ISENABLER3/ISPENDR3: INTID 115 (115 / 32 = 3). */
            gic_en = readl_relaxed(gic + 0x100 + 3 * 4);
            gic_pending = readl_relaxed(gic + 0x200 + 3 * 4);
            iounmap(gic);
        }
    }
    if (kms && kms->hw_intr && (ctrl & BIT(31)))
        kms->hw_intr->ops.get_interrupt_sources(kms->hw_intr, &intr);
    pr_warn("P446M FIRST_DMA_TIMEOUT GDSCR=%08x MDSS_HW_INTR=%08x GICD115_EN=%08x GICD115_PEND=%08x mdss_irq_enabled=%d\n",
            ctrl, intr, gic_en, gic_pending,
            kms ? READ_ONCE(kms->irq_enabled) : -1);
    if (kms && kms->irq_num > 0)
        a52_p446m_desc_state(kms->irq_num, "msm_drm");
    a52_p446m_desc_state(271, "dsi_virq271");
}
EXPORT_SYMBOL_GPL(a52_p446m_snapshot_irq_timeout);

"""

def apply_irq(src):
    if TAG in src: return src
    src=include(src,"#include <linux/irq.h>\n","linux/irqdesc.h","irqdesc include")
    src=include(src,"#include <linux/irqdesc.h>\n","linux/kernel_stat.h","kstat include")
    src=include(src,"#include <linux/kernel_stat.h>\n","linux/of_address.h","OF address include")
    src=include(src,"#include <linux/of_address.h>\n","linux/io.h","MMIO include")
    src=include(src,"#include <linux/io.h>\n","linux/atomic.h","atomic include")
    src=one(src,"uint32_t g_sde_irq_status;\n",
        "uint32_t g_sde_irq_status;\n"+IRQ_HELPER,"IRQ witness declarations")
    def rework(f):
        f=one(f,"\tsde_kms->irq_enabled = enable;\n",
            "\tWRITE_ONCE(a52_p446m_kms, sde_kms);\n"
            '\tpr_warn("P446M IRQ update enter mdss=%d enable=%u caller=%pS\\n",\n'
            '\t\tsde_kms->irq_num, enable, __builtin_return_address(0));\n'
            '\ta52_p446m_desc_state(sde_kms->irq_num, "before_update");\n'
            "\tsde_kms->irq_enabled = enable;\n", "IRQ pre")
        f=one(f,"\t\tdisable_irq(sde_kms->irq_num);\n",
            '\t\tdisable_irq(sde_kms->irq_num);\n'
            '\ta52_p446m_desc_state(sde_kms->irq_num, "after_update");\n',
            "IRQ post")
        return f
    return rework_fn(src,"void sde_irq_update(struct msm_kms *msm_kms, bool enable)",rework)

def apply_dsi(src):
    if TAG in src: return src
    # The worker timeout is reached only after failing completion waiting,
    # whereas plain dsi_ctrl_clear_slave_dma_status is a different path.
    func="static void dsi_ctrl_dma_cmd_wait_for_done("
    a,b,f=getfn(src,func)
    anchor="\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n"
    if f.count(anchor)!=1:
        raise RuntimeError("446M DMA timeout anchor missing or ambiguous")
    f=f.replace(anchor,
        anchor+"\t\ta52_p446m_snapshot_irq_timeout();\n",1)
    src=src[:a]+f+src[b:]
    include_at="\n/* "+TAG+" */\nextern void a52_p446m_snapshot_irq_timeout(void);\n"
    return include_at+src

def check(root,variant):
    g=(root/"drivers/regulator/a52-legacy-gdsc-regulator.c").read_text()
    p=(root/"drivers/a52_display/msm/sde_power_handle.c").read_text()
    i=(root/"drivers/a52_display/msm/sde/sde_irq.c").read_text()
    d=(root/"drivers/a52_display/msm/dsi/dsi_ctrl.c").read_text()
    for source,label in ((g,"GDSC"),(p,"POWER"),(i,"IRQ"),(d,"DSI")):
        if TAG not in source: raise RuntimeError("446M %s not patched"%label)
    if variant=="diag":
        if "return -EBUSY;" not in getfn(g,
            "static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)")[2]:
            raise RuntimeError("446M diagnostic no longer retains -EBUSY")
    else:
        if "446M experimental: re-take MDSS control" not in g:
            raise RuntimeError("446M recovery missing")
    if "P446M FIRST_DMA_TIMEOUT" not in i:
        raise RuntimeError("446M IRQ snapshot missing")
    print("Phase446M",variant,"GDSC+POWER+IRQ+DSI hooks PASS")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--variant",choices=["diag","recovery"],required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    root=a.root
    if not a.check_only:
        files=(
            ("drivers/regulator/a52-legacy-gdsc-regulator.c",lambda s:apply_gdsc(s,a.variant)),
            ("drivers/a52_display/msm/sde_power_handle.c",apply_power),
            ("drivers/a52_display/msm/sde/sde_irq.c",apply_irq),
            ("drivers/a52_display/msm/dsi/dsi_ctrl.c",apply_dsi))
        # Transform in memory first: no partial patch if an anchor fails.
        updates=[(root/rel,fn((root/rel).read_text())) for rel,fn in files]
        for path,data in updates:path.write_text(data)
    check(root,a.variant)

if __name__=="__main__":
    main()
