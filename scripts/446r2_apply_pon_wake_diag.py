#!/usr/bin/env python3
"""446R2: restore missing PM6350 PON KEY_POWER IRQ observer/wake source.

TG lagoon PM6350 DT: platform child qcom,qpnp-power-on@800 with
interrupt-names kpdpwr,resin; 5.10 GKI CONFIG lacks downstream QPNP PON
driver, so no input events or wake-up IRQ. A minimal diagnostic input
driver binds that existing DT node and never writes PMIC reset settings.

Do not enable if another active driver already owns qcom,qpnp-power-on.
"""
from pathlib import Path
import argparse

MARK="A52_PHASE446R2_PON_WAKE_DIAG_V1"
SRC=Path("drivers/a52_secure/a52_pon_wake_diag.c")
MAKE=Path("drivers/a52_secure/Makefile")

CODE=r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE446R2_PON_WAKE_DIAG_V1
 * Qualcomm PM6350 PON KEY_POWER edge and wake diagnostics.
 * TG DTS power-on@800 has reg=0x800, interrupts named "kpdpwr" and "resin".
 * This minimal driver does not modify PON power/reset registers.
 */
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/input.h>
#include <linux/interrupt.h>
#include <linux/irq.h>
#include <linux/irqdesc.h>
#include <linux/regmap.h>
#include <linux/pm_wakeup.h>
#include <linux/suspend.h>
#include <linux/notifier.h>
#include <linux/slab.h>

struct a52_pon_diag {
    struct device *dev;
    struct input_dev *input;
    struct regmap *regmap;
    struct notifier_block pm_nb;
    u32 base;
    int key_irq;
    bool wake_enabled;
    atomic_t edges;
};
static struct a52_pon_diag *a52_pon_primary;

/* PM6350 PON RT status: KPDPWR_N active-low bit 0 (TouchGrass). */
static irqreturn_t a52_pon_key_irq(int irq, void *data)
{
    struct a52_pon_diag *p=data;
    unsigned int val=0;
    int ret=regmap_read(p->regmap,p->base+0x10U,&val);
    int edge=atomic_inc_return(&p->edges);
    if (edge<=48 || !(edge%100))
        pr_warn("P446R2 PON_KEY_IRQ edge=%d irq=%d read_rc=%d rt_sts=%02x pressed=%u wake=%u\n",
            edge,irq,ret,val,ret?0U:!(val&1U),p->wake_enabled);
    if (!ret) {
        input_report_key(p->input,KEY_POWER,!(val&1U));
        input_sync(p->input);
        pm_wakeup_event(p->dev,1000);
    }
    return IRQ_HANDLED;
}

static int a52_pon_pm_notifier(struct notifier_block *nb,
                              unsigned long event, void *unused)
{
    struct a52_pon_diag *p=container_of(nb,struct a52_pon_diag,pm_nb);
    if (event==PM_SUSPEND_PREPARE) {
        char wakes[192]={0};
        pm_get_active_wakeup_sources(wakes,sizeof(wakes));
        pr_warn("P446R2 SUSPEND_PREPARE mem_sleep=%d key_irq=%d edges=%d wake=%u success=%u fail=%u active_wakes=%s\n",
            mem_sleep_current,p->key_irq,atomic_read(&p->edges),
            p->wake_enabled,suspend_stats.success,suspend_stats.fail,wakes);
    }
    if (event==PM_POST_SUSPEND)
        pr_warn("P446R2 POST_SUSPEND mem_sleep=%d key_irq=%d edges=%d success=%u fail=%u\n",
            mem_sleep_current,p->key_irq,atomic_read(&p->edges),
            suspend_stats.success,suspend_stats.fail);
    return NOTIFY_OK;
}
static void a52_pon_irq_chain(int irq)
{
    struct irq_data *data=irq_get_irq_data(irq);
    int depth=0;
    while(data && depth<5) {
        pr_warn("P446R2 PON_IRQ_MAP depth=%d virq=%u hwirq=%lu chip=%s\n",
            depth,data->irq,(unsigned long)data->hwirq,
            data->chip && data->chip->name?data->chip->name:"?");
        data=data->parent_data;
        depth++;
    }
    /* Parent data reveals PMIC->GIC/PDC chain, if supplied by the DT. */
}
static int a52_pon_probe(struct platform_device *pdev)
{
    struct a52_pon_diag *p;
    struct input_dev *in;
    int ret;
    int irq;
    u32 base;
    if (a52_pon_primary)
        return -EBUSY;
    if (!of_device_is_compatible(pdev->dev.of_node,"qcom,qpnp-power-on"))
        return -ENODEV;
    ret=of_property_read_u32_index(pdev->dev.of_node,"reg",0,&base);
    if (ret)return ret;
    irq=platform_get_irq_byname(pdev,"kpdpwr");
    if (irq<0) {
        dev_warn(&pdev->dev,"P446R2 PON no kpdpwr IRQ rc=%d\n",irq);
        return irq;
    }
    p=devm_kzalloc(&pdev->dev,sizeof(*p),GFP_KERNEL);
    if (!p)return -ENOMEM;
    p->dev=&pdev->dev;p->base=base;p->key_irq=irq;
    atomic_set(&p->edges,0);
    p->regmap=dev_get_regmap(pdev->dev.parent,NULL);
    if (!p->regmap) {
        dev_warn(&pdev->dev,"P446R2 PON no parent SPMI regmap\n");
        return -EPROBE_DEFER;
    }
    in=devm_input_allocate_device(&pdev->dev);
    if (!in)return -ENOMEM;
    in->name="a52-qpnp-pon-pwrkey";
    in->phys="pm6350-pon/input0";
    in->id.bustype=BUS_HOST;
    input_set_capability(in,EV_KEY,KEY_POWER);
    p->input=in;
    ret=devm_request_threaded_irq(&pdev->dev,irq,NULL,a52_pon_key_irq,
                                   IRQF_ONESHOT,"a52-pon-key",p);
    if(ret)return ret;
    ret=input_register_device(in);
    if(ret)return ret;
    device_init_wakeup(&pdev->dev,true);
    ret=enable_irq_wake(irq);
    if (ret)
        dev_warn(&pdev->dev,"P446R2 PON enable_irq_wake failed rc=%d\n",ret);
    else
        p->wake_enabled=true;
    p->pm_nb.notifier_call=a52_pon_pm_notifier;
    ret=register_pm_notifier(&p->pm_nb);
    if (ret)
        dev_warn(&pdev->dev,"P446R2 PM notifier rc=%d\n",ret);
    platform_set_drvdata(pdev,p);
    a52_pon_primary=p;
    pr_warn("P446R2 PON_BOUND reg=%x irq=%d wake=%u pm_nb_rc=%d\n",
        base,irq,p->wake_enabled,ret);
    a52_pon_irq_chain(irq);
    return 0;
}
static int a52_pon_remove(struct platform_device *pdev)
{
    struct a52_pon_diag *p=platform_get_drvdata(pdev);
    if (a52_pon_primary==p)a52_pon_primary=NULL;
    if (p->wake_enabled)disable_irq_wake(p->key_irq);
    unregister_pm_notifier(&p->pm_nb);
    return 0;
}
static const struct of_device_id a52_pon_of_match[]={
    {.compatible="qcom,qpnp-power-on"},
    {},
};
MODULE_DEVICE_TABLE(of,a52_pon_of_match);
static struct platform_driver a52_pon_driver={
    .driver={
        .name="a52-pon-wake-diag",
        .of_match_table=a52_pon_of_match,
    },
    .probe=a52_pon_probe,
    .remove=a52_pon_remove,
};
static int __init a52_pon_init(void)
{
    return platform_driver_register(&a52_pon_driver);
}
device_initcall(a52_pon_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("A52 PM6350 PON KEY_POWER input and wake diagnostics");
'''

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    c=a.root/SRC;m=a.root/MAKE
    if not a.check_only:
        if c.exists() and MARK not in c.read_text():
            raise RuntimeError("R2 refuses to overwrite PON driver")
        c.write_text(CODE)
        s=m.read_text()
        if "a52_pon_wake_diag.o" not in s:
            m.write_text(s+"\n# "+MARK+"\nobj-y += a52_pon_wake_diag.o\n")
    for p,need in [(c,MARK),(c,"enable_irq_wake(irq)"),
                   (c,"P446R2 PON_KEY_IRQ"),(c,"KEY_POWER"),
                   (m,"a52_pon_wake_diag.o")]:
        if not p.is_file() or need not in p.read_text():
            raise RuntimeError("R2 PON expected "+need)
    print("P446R2 PON source audit PASS: IRQ edge logging, wake enable, PM notifier")
if __name__=="__main__":main()
