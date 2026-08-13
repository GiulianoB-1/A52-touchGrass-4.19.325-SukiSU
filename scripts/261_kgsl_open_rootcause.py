#!/usr/bin/env python3
"""Phase261: exact failed KGSL open tracing; diagnostic QCOM watchdog off."""
from __future__ import annotations
import re
from pathlib import Path

MARKER = "A52_PHASE261_KGSL_OPEN_ROOTCAUSE_V1"
REC = "A52_PHASE261_RECORDER_V1"
CHR = "A52_PHASE261_CHRDEV_OPEN_V1"
KGSL = "A52_PHASE261_KGSL_OPEN_V1"
MMU = "A52_PHASE261_KGSL_MMU_V1"
IOMMU = "A52_PHASE261_KGSL_IOMMU_V1"
ADRENO = "A52_PHASE261_ADRENO_OPEN_V1"
WDT = "A52_PHASE261_DIAGNOSTIC_QCOM_WDT_OFF_V1"


def one(s: str, old: str, new: str, label: str) -> str:
    if new in s:
        return s
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {n}")
    return s.replace(old, new, 1)


def one_after(s: str, marker: str, old: str, new: str, label: str) -> str:
    p = s.find(marker)
    if p < 0:
        raise RuntimeError(f"{label}: marker missing")
    q = s.find(old, p)
    if q < 0:
        raise RuntimeError(f"{label}: anchor missing after marker")
    return s[:q] + new + s[q + len(old):]


def inc(s: str, header: str) -> str:
    if header in s:
        return s
    hits = list(re.finditer(r'(?m)^#include [<\"][^>\"]+[>\"]\s*$', s))
    if not hits:
        raise RuntimeError("include block missing")
    p = hits[-1].end()
    return s[:p] + "\n" + header + s[p:]


def rw(p: Path, fn) -> None:
    s = p.read_text(encoding="utf-8")
    p.write_text(fn(s), encoding="utf-8")


def recorder(s: str) -> str:
    if REC in s:
        return s
    if "A52_PHASE260_RECORDER_V1" not in s:
        raise RuntimeError("Phase260 recorder missing")
    s = one(s, 'if (strncmp(fmt, "F260", 4) &&\n',
        f'/* {REC} */\nif (strncmp(fmt, "F261", 4) &&\n    strncmp(fmt, "F260", 4) &&\n', "recorder fmt")
    return one(s, 'return !strncmp(message, "F260 ", 5) ||\n',
        'return !strncmp(message, "F261 ", 5) ||\n       !strncmp(message, "F260 ", 5) ||\n', "recorder retention")


def chrdev(s: str) -> str:
    if CHR in s:
        return s
    for h in ("#include <linux/atomic.h>", "#include <linux/sched.h>",
              "#include <linux/a52_ack_secure_flight_recorder.h>"):
        s = inc(s, h)
    sig = "static int chrdev_open(struct inode *inode, struct file *filp)"
    p = s.find(sig)
    if p < 0:
        raise RuntimeError("chrdev_open missing")
    helper = f'''/* {CHR} */
static atomic_t a52_r261_chr_n = ATOMIC_INIT(0);
static bool a52_r261_chr(unsigned int *n)
{{
\tif (strncmp(current->comm, "surfaceflinger", 15)) return false;
\t*n = atomic_inc_return(&a52_r261_chr_n);
\treturn *n <= 48;
}}

'''
    s = s[:p] + helper + s[p:]
    s = one(s, "\tstruct cdev *new = NULL;\n\tint ret = 0;\n",
        "\tstruct cdev *new = NULL;\n\tint ret = 0;\n\tunsigned int a52n = 0;\n\tbool a52t = a52_r261_chr(&a52n);\n"
        "\tif (a52t) a52_ackfr_record(\"F261 c0 n=%u rdev=%lx ic=%px\", a52n, (unsigned long)inode->i_rdev, inode->i_cdev);\n", "chr locals")
    s = one(s, "\t\tstruct kobject *kobj;\n\t\tint idx;\n", "\t\tstruct kobject *kobj;\n\t\tint idx = 0;\n", "chr idx")
    s = one(s, "\t\tkobj = kobj_lookup(cdev_map, inode->i_rdev, &idx);\n",
        "\t\tkobj = kobj_lookup(cdev_map, inode->i_rdev, &idx);\n"
        "\t\tif (a52t) a52_ackfr_record(\"F261 c1 n=%u ko=%d idx=%d\", a52n, !!kobj, idx);\n", "chr lookup")
    s = one(s, "\tfops = fops_get(p->ops);\n",
        "\tfops = fops_get(p->ops);\n\tif (a52t) a52_ackfr_record(\"F261 c2 n=%u cd=%px ops=%px got=%px\", a52n, p, p ? p->ops : NULL, fops);\n", "chr fops")
    return one(s, "\tif (filp->f_op->open) {\n\t\tret = filp->f_op->open(inode, filp);\n",
        "\tif (filp->f_op->open) {\n"
        "\t\tif (a52t) a52_ackfr_record(\"F261 c3 n=%u op=%ps\", a52n, filp->f_op->open);\n"
        "\t\tret = filp->f_op->open(inode, filp);\n"
        "\t\tif (a52t) a52_ackfr_record(\"F261 c4 n=%u rc=%d op=%ps\", a52n, ret, filp->f_op->open);\n", "chr open")


def kgsl(s: str) -> str:
    if KGSL in s:
        return s
    for h in ("#include <linux/atomic.h>", "#include <linux/a52_ack_secure_flight_recorder.h>"):
        s = inc(s, h)
    sig = "static struct kgsl_process_private *kgsl_process_private_new("
    p = s.find(sig)
    if p < 0:
        raise RuntimeError("kgsl_process_private_new missing")
    state = f'''/* {KGSL} */
static atomic_t a52_r261_k_n = ATOMIC_INIT(0);
static atomic_t a52_r261_d_n = ATOMIC_INIT(0);
static atomic_t a52_r261_p_n = ATOMIC_INIT(0);
static bool a52_r261_lim(atomic_t *v, unsigned int *n) {{ *n = atomic_inc_return(v); return *n <= 32; }}

'''
    s = s[:p] + state + s[p:]
    s = one(s, "static int kgsl_open_device(struct kgsl_device *device)\n{\n\tint result = 0;\n",
        "static int kgsl_open_device(struct kgsl_device *device)\n{\n\tint result = 0;\n\tunsigned int a52n=0;\n\tbool a52t=a52_r261_lim(&a52_r261_d_n,&a52n);\n"
        "\tif (a52t) a52_ackfr_record(\"F261 d0 n=%u oc=%d init=%ps start=%ps\",a52n,device->open_count,device->ftbl->init,device->ftbl->start);\n", "kgsl device entry")
    s = one(s, "\t\tresult = device->ftbl->init(device);\n",
        "\t\tresult = device->ftbl->init(device);\n\t\tif (a52t) a52_ackfr_record(\"F261 di n=%u rc=%d\",a52n,result);\n", "kgsl init")
    s = one(s, "\t\tresult = device->ftbl->start(device, 0);\n",
        "\t\tresult = device->ftbl->start(device, 0);\n\t\tif (a52t) a52_ackfr_record(\"F261 ds n=%u rc=%d\",a52n,result);\n", "kgsl start")
    s = one(s, "static int kgsl_open(struct inode *inodep, struct file *filep)\n{\n\tint result;\n",
        "static int kgsl_open(struct inode *inodep, struct file *filep)\n{\n\tint result;\n\tunsigned int a52n=0;\n\tbool a52t=a52_r261_lim(&a52_r261_k_n,&a52n);\n", "kgsl open entry")
    s = one(s, "\tunsigned int minor = iminor(inodep);\n\n\tdevice = kgsl_get_minor(minor);\n",
        "\tunsigned int minor = iminor(inodep);\n\n\tif(a52t)a52_ackfr_record(\"F261 k0 n=%u mi=%u rdev=%lx p=%d c=%.15s\",a52n,minor,(unsigned long)inodep->i_rdev,current->pid,current->comm);\n"
        "\tdevice = kgsl_get_minor(minor);\n\tif(a52t)a52_ackfr_record(\"F261 k1 n=%u dev=%px\",a52n,device);\n", "kgsl minor")
    s = one(s, "\tresult = pm_runtime_get_sync(&device->pdev->dev);\n",
        "\tresult = pm_runtime_get_sync(&device->pdev->dev);\n\tif(a52t)a52_ackfr_record(\"F261 kr n=%u rc=%d\",a52n,result);\n", "kgsl rpm")
    s = one(s, "\tresult = kgsl_open_device(device);\n",
        "\tresult = kgsl_open_device(device);\n\tif(a52t)a52_ackfr_record(\"F261 ko n=%u rc=%d oc=%d\",a52n,result,device->open_count);\n", "kgsl device rc")
    s = one(s, "\tdev_priv->process_priv = kgsl_process_private_open(device);\n",
        "\tdev_priv->process_priv = kgsl_process_private_open(device);\n\tif(a52t)a52_ackfr_record(\"F261 kp n=%u err=%ld\",a52n,IS_ERR(dev_priv->process_priv)?PTR_ERR(dev_priv->process_priv):0L);\n", "kgsl proc")
    return one(s, "\tprivate->pagetable = kgsl_mmu_getpagetable(&device->mmu,\n\t\t\t\t\t\t\tpid_nr(cur_pid));\n",
        "\tprivate->pagetable = kgsl_mmu_getpagetable(&device->mmu,\n\t\t\t\t\t\t\tpid_nr(cur_pid));\n"
        "\t{unsigned int a52n=0;if(a52_r261_lim(&a52_r261_p_n,&a52n))a52_ackfr_record(\"F261 pt n=%u name=%d err=%ld\",a52n,pid_nr(cur_pid),IS_ERR(private->pagetable)?PTR_ERR(private->pagetable):0L);}\n", "kgsl pt")


def mmu(s: str) -> str:
    if MMU in s:
        return s
    for h in ("#include <linux/atomic.h>", "#include <linux/a52_ack_secure_flight_recorder.h>"):
        s = inc(s, h)
    a = "static void pagetable_remove_sysfs_objects(struct kgsl_pagetable *pagetable);\n"
    s = one(s, a, a + f"\n/* {MMU} */\nstatic atomic_t a52_r261_m_n=ATOMIC_INIT(0);\n", "mmu state")
    return one(s, "\tif (MMU_OP_VALID(mmu, mmu_init_pt)) {\n\t\tstatus = mmu->mmu_ops->mmu_init_pt(mmu, pagetable);\n",
        "\tif (MMU_OP_VALID(mmu, mmu_init_pt)) {\n\t\tunsigned int a52n=atomic_inc_return(&a52_r261_m_n);\n\t\tstatus = mmu->mmu_ops->mmu_init_pt(mmu, pagetable);\n"
        "\t\tif(a52n<=64)a52_ackfr_record(\"F261 m0 n=%u name=%u rc=%d fn=%ps\",a52n,name,status,mmu->mmu_ops->mmu_init_pt);\n", "mmu initpt")


def iommu(s: str) -> str:
    if IOMMU in s:
        return s
    for h in ("#include <linux/atomic.h>", "#include <linux/a52_ack_secure_flight_recorder.h>"):
        s = inc(s, h)
    a = "static struct kgsl_mmu_pt_ops iommu_pt_ops;\n"
    s = one(s, a, a + f"\n/* {IOMMU} */\nstatic atomic_t a52_r261_i_n=ATOMIC_INIT(0);\n", "iommu state")
    s = one(s, "\tret = iommu_attach_device(iommu_pt->domain, ctx->dev);\n",
        "\tret = iommu_attach_device(iommu_pt->domain, ctx->dev);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 it n=%u rc=%d dom=%px dev=%px\",a52n,ret,iommu_pt->domain,ctx->dev);}\n", "iommu attach")
    pp = "static int _init_per_process_pt"
    s = one_after(s, pp, "\tiommu_pt = _alloc_pt(ctx->dev, mmu, pt);\n",
        "\tiommu_pt = _alloc_pt(ctx->dev, mmu, pt);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 pa n=%u ptr=%px err=%ld\",a52n,iommu_pt,IS_ERR(iommu_pt)?PTR_ERR(iommu_pt):0L);}\n", "perproc alloc")
    s = one_after(s, pp, "\tret = iommu_domain_set_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_DYNAMIC, &dynamic);\n",
        "\tret = iommu_domain_set_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_DYNAMIC, &dynamic);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 pd n=%u rc=%d\",a52n,ret);}\n", "perproc dynamic")
    s = one_after(s, pp, "\tret = iommu_domain_set_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_CONTEXT_BANK, &cb_num);\n",
        "\tret = iommu_domain_set_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_CONTEXT_BANK, &cb_num);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 pc n=%u rc=%d cb=%u\",a52n,ret,cb_num);}\n", "perproc cb")
    s = one_after(s, pp, "\tret = iommu_domain_set_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_PROCID, &pt->name);\n",
        "\tret = iommu_domain_set_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_PROCID, &pt->name);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 pp n=%u rc=%d name=%u\",a52n,ret,pt->name);}\n", "perproc procid")
    s = one_after(s, pp, "\tret = iommu_domain_get_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_TTBR0, &iommu_pt->ttbr0);\n",
        "\tret = iommu_domain_get_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_TTBR0, &iommu_pt->ttbr0);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 p0 n=%u rc=%d tt=%llx\",a52n,ret,iommu_pt->ttbr0);}\n", "perproc ttbr0")
    s = one_after(s, pp, "\tret = iommu_domain_get_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_CONTEXTIDR, &iommu_pt->contextidr);\n",
        "\tret = iommu_domain_get_attr(iommu_pt->domain,\n\t\t\t\tDOMAIN_ATTR_CONTEXTIDR, &iommu_pt->contextidr);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 p1 n=%u rc=%d ci=%x\",a52n,ret,iommu_pt->contextidr);}\n", "perproc contextidr")
    s = one_after(s, pp, "\tret = kgsl_iommu_map_globals(pt);\n",
        "\tret = kgsl_iommu_map_globals(pt);\n\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 pg n=%u rc=%d\",a52n,ret);}\n", "perproc globals")
    s = one(s, "\tif (pt == NULL)\n\t\tpt = kgsl_mmu_createpagetableobject(mmu, name);\n\n\treturn pt;\n",
        "\tif (pt == NULL)\n\t\tpt = kgsl_mmu_createpagetableobject(mmu, name);\n\n"
        "\t{unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 ig n=%u name=%lu err=%ld\",a52n,name,IS_ERR(pt)?PTR_ERR(pt):0L);}\n\treturn pt;\n", "iommu getpt")
    s = one(s, "\tdefault:\n\t\treturn _init_per_process_pt(mmu, pt);\n",
        "\tdefault:{int r=_init_per_process_pt(mmu,pt);unsigned int a52n=atomic_inc_return(&a52_r261_i_n);if(a52n<=96)a52_ackfr_record(\"F261 ip n=%u name=%u rc=%d\",a52n,pt->name,r);return r;}\n", "iommu perproc")
    return s


def adreno(s: str) -> str:
    if ADRENO in s:
        return s
    s = inc(s, "#include <linux/a52_ack_secure_flight_recorder.h>")
    a = "static const struct kgsl_functable adreno_functable;\n"
    s = one(s, a, a + f"\n/* {ADRENO} */\n", "adreno marker")
    for old, new, lab in (
        ("\tret = gpudev->microcode_read(adreno_dev);\n", "\tret = gpudev->microcode_read(adreno_dev);\n\ta52_ackfr_record(\"F261 ai uc=%d\",ret);\n", "uc"),
        ("\tret = gmu_core_init(device);\n", "\tret = gmu_core_init(device);\n\ta52_ackfr_record(\"F261 ai gmu=%d\",ret);\n", "gmu"),
        ("\tstatus = kgsl_mmu_start(device);\n", "\tstatus = kgsl_mmu_start(device);\n\ta52_ackfr_record(\"F261 as mmu=%d\",status);\n", "mmu"),
        ("\tstatus = gmu_core_dev_hfi_start_msg(device);\n", "\tstatus = gmu_core_dev_hfi_start_msg(device);\n\ta52_ackfr_record(\"F261 as hfi=%d\",status);\n", "hfi"),
        ("\tstatus = adreno_ringbuffer_start(adreno_dev);\n", "\tstatus = adreno_ringbuffer_start(adreno_dev);\n\ta52_ackfr_record(\"F261 as rb=%d\",status);\n", "rb"),
        ("\tret = _adreno_start(adreno_dev);\n", "\tret = _adreno_start(adreno_dev);\n\ta52_ackfr_record(\"F261 ax rc=%d\",ret);\n", "start"),
    ):
        s = one(s, old, new, "adreno " + lab)
    return s


def watchdog(s: str) -> str:
    if WDT in s:
        return s
    old = "\tif (running) {\n\t\tqcom_wdt_start(&wdt->wdd);\n\t\tset_bit(WDOG_HW_RUNNING, &wdt->wdd.status);\n\t}\n\n\tret = devm_watchdog_register_device(dev, &wdt->wdd);\n"
    new = f"\t/* {WDT}: diagnostic boot only */\n\tif (running) qcom_wdt_stop(&wdt->wdd);\n\tdev_warn(dev, \"A52 Phase261: QCOM watchdog disabled for KGSL trace\\n\");\n\tret = 0; /* do not register/re-arm */\n"
    return one(s, old, new, "watchdog handoff")


def apply(root: Path) -> None:
    jobs = (
        ("drivers/a52_secure/a52_ack_secure_flight_recorder.c", recorder),
        ("fs/char_dev.c", chrdev), ("drivers/gpu/msm/kgsl.c", kgsl),
        ("drivers/gpu/msm/kgsl_mmu.c", mmu), ("drivers/gpu/msm/kgsl_iommu.c", iommu),
        ("drivers/gpu/msm/adreno.c", adreno), ("drivers/watchdog/qcom-wdt.c", watchdog),
    )
    for rel, fn in jobs:
        p = root / rel
        if not p.is_file(): raise RuntimeError(f"missing {p}")
        rw(p, fn)
    alltext = "\n".join((root / r).read_text(encoding="utf-8") for r, _ in jobs)
    for t in (REC, CHR, KGSL, MMU, IOMMU, ADRENO, WDT, "F261 c4", "F261 kr", "F261 di", "F261 ds", "F261 kp", "F261 pt", "F261 m0", "F261 it", "F261 pa", "F261 pd", "F261 pc", "F261 pp", "F261 p0", "F261 p1", "F261 pg", "F261 ip", "F261 ax"):
        if t not in alltext: raise RuntimeError(f"Phase261 verify missing {t}")
    kgsl_text = (root / "drivers/gpu/msm/kgsl.c").read_text(encoding="utf-8")
    helper = kgsl_text.find("static bool a52_r261_lim")
    first_use = kgsl_text.find("a52_r261_lim(&a52_r261_p_n")
    if helper < 0 or first_use < 0 or helper >= first_use:
        raise RuntimeError("Phase261 KGSL helper/state ordering invalid")
    print(f"{MARKER}: applied", flush=True)


def self_test() -> None:
    s = Path(__file__).read_text(encoding="utf-8")
    for t in (MARKER, REC, CHR, KGSL, MMU, IOMMU, ADRENO, WDT, "F261 c4", "F261 kp", "F261 pa", "F261 pd", "F261 p0", "F261 pg", "F261 ip", "qcom_wdt_stop"):
        if t not in s: raise AssertionError(t)
    print("Phase 261 KGSL open-rootcause self-test: PASS", flush=True)


if __name__ == "__main__": self_test()
