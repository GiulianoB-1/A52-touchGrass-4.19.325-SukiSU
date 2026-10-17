#!/usr/bin/env python3
"""Phase446S scoped GPU SMMU runtime PM for the Phase250 GDSC vote.

Apply AFTER Phase250 and all late SMMU patches. Does NOT modify the DSI,
GDSC, GPU/GMU polling timeout, or unrelated SMMUs.
"""
import argparse
from pathlib import Path

TAG="A52_KGSL_SMMU_RPM_V1"
F=Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
H=Path("drivers/iommu/arm/arm-smmu/arm-smmu.h")
PHASE250="A52_PHASE250_GPU_SMMU_POWER_CONTRACT_V1"
TARGET=r'''
/* A52_KGSL_SMMU_RPM_V1: A52 GPU SMMU only. No global PM change. */
static bool a52_kgsl_smmu_has_phase250_vdd(struct arm_smmu_device *smmu)
{
	struct device_node *np = smmu->dev->of_node;

	return smmu->a52_num_gdscs > 0 && np &&
		np->full_name && strstr(np->full_name, "smmu-kgsl@3d40000") &&
		of_property_read_bool(np, "vdd-supply") &&
		of_property_match_string(np, "qcom,regulator-names", "vdd") >= 0;
}

static void a52_kgsl_smmu_pm_diag(struct device *dev, const char *stage)
{
	struct arm_smmu_device *smmu = dev_get_drvdata(dev);

	pr_warn("P446S KGSL_SMMU_RPM stage=%s dev=%s domain=%px pm_enabled=%u usage=%d status=%d phase250_vdd=%u\n",
		stage, dev_name(dev), dev->pm_domain, pm_runtime_enabled(dev),
		atomic_read(&dev->power.usage_count), dev->power.runtime_status,
		smmu && a52_kgsl_smmu_has_phase250_vdd(smmu));
}
'''

def once(s,old,new,where):
    n=s.count(old)
    if n!=1:
        raise RuntimeError("446S KGSL SMMU %s anchor count=%d"%(where,n))
    return s.replace(old,new,1)

def replace_block(src,sig,transform,where):
    first=src.find(sig)
    if first<0 or src.find(sig,first+1)>=0:
        raise RuntimeError("446S SMMU %s function not unique"%where)
    begin=src.find("{",first+len(sig))
    depth=0
    for i in range(begin,len(src)):
        if src[i]=="{":depth+=1
        elif src[i]=="}":
            depth-=1
            if depth==0:break
    else:raise RuntimeError("446S SMMU %s body unterminated"%where)
    original=src[begin:i+1]
    rewritten=transform(original)
    return src[:begin]+rewritten+src[i+1:]

def patch_probe(body):
    if "dev->pm_domain" not in body or PHASE250 not in body:
        # Phase250 marker appears in helper, not necessarily in this function.
        if "a52_arm_smmu_enable_gdscs(smmu)" not in body:
            raise RuntimeError("446S missing Phase250 regulator enable")
    # Validate rollback scopes before inserting code.
    required=(
      "err = arm_smmu_device_cfg_probe(smmu);",
      "err = iommu_device_sysfs_add(&smmu->iommu",
      "err = iommu_device_register(&smmu->iommu);",
      "platform_set_drvdata(pdev, smmu);",
      "if (dev->pm_domain) {",
      "return arm_smmu_bus_init(&arm_smmu_ops);",
    )
    for anchor in required:
        if anchor not in body:
            raise RuntimeError("446S SMMU probe anchor missing "+anchor)
    # Correct Phase250's error lifetime on the KGSL SMMU without changing
    # preexisting error returns on unrelated SMMU nodes.
    body=once(body,"int num_irqs, i, err;",
              "int num_irqs, i, err;\n\tbool a52_pm_owned = false;\n\tbool a52_pm_ref = false;\n\tbool a52_sysfs_added = false;\n\tbool a52_iommu_registered = false;",
              "probe locals")
    body=once(body,
        "err = arm_smmu_device_cfg_probe(smmu);\n\tif (err)\n\t\treturn err;",
        "err = arm_smmu_device_cfg_probe(smmu);\n\tif (err) {\n\t\tif (a52_kgsl_smmu_has_phase250_vdd(smmu))\n\t\t\tgoto a52_probe_error;\n\t\treturn err;\n\t}",
        "cfg probe failure")
    # For the remaining errors, never convert return values for unrelated SMMUs.
    body=once(body,
        'dev_err(dev,\n\t\t\t      "found only %d context irq(s) but %d required\\n",\n\t\t\t\tsmmu->num_context_irqs, smmu->num_context_banks);\n\t\t\treturn -ENODEV;',
        'dev_err(dev,\n\t\t\t      "found only %d context irq(s) but %d required\\n",\n\t\t\t\tsmmu->num_context_irqs, smmu->num_context_banks);\n\t\t\tif (a52_kgsl_smmu_has_phase250_vdd(smmu)) {\n\t\t\t\terr = -ENODEV;\n\t\t\t\tgoto a52_probe_error;\n\t\t\t}\n\t\t\treturn -ENODEV;',
        "context IRQ mismatch")
    body=once(body,
        'dev_err(dev, "failed to request global IRQ %d (%u)\\n",\n\t\t\t\ti, smmu->irqs[i]);\n\t\t\treturn err;',
        'dev_err(dev, "failed to request global IRQ %d (%u)\\n",\n\t\t\t\ti, smmu->irqs[i]);\n\t\t\tif (a52_kgsl_smmu_has_phase250_vdd(smmu))\n\t\t\t\tgoto a52_probe_error;\n\t\t\treturn err;',
        "global IRQ failure")
    body=once(body,
        'dev_err(dev, "Failed to register iommu in sysfs\\n");\n\t\treturn err;',
        'dev_err(dev, "Failed to register iommu in sysfs\\n");\n\t\tif (a52_kgsl_smmu_has_phase250_vdd(smmu))\n\t\t\tgoto a52_probe_error;\n\t\treturn err;',
        "iommu sysfs error")
    body=once(body,
        'iommu_device_set_ops(&smmu->iommu, &arm_smmu_ops);',
        'a52_sysfs_added = true;\n\tiommu_device_set_ops(&smmu->iommu, &arm_smmu_ops);',
        "sysfs flag")
    body=once(body,
        'dev_err(dev, "Failed to register iommu\\n");\n\t\treturn err;',
        'dev_err(dev, "Failed to register iommu\\n");\n\t\tif (a52_kgsl_smmu_has_phase250_vdd(smmu))\n\t\t\tgoto a52_probe_error;\n\t\treturn err;',
        "iommu register error")
    body=once(body,
        'platform_set_drvdata(pdev, smmu);',
        'a52_iommu_registered = true;\n\tplatform_set_drvdata(pdev, smmu);',
        "iommu registered flag")
    old=r'''	if (dev->pm_domain) {
		pm_runtime_set_active(dev);
		pm_runtime_enable(dev);
	}

	/*
	 * For ACPI and generic DT bindings, an SMMU will be probed before
	 * any device which might need it, so we want the bus ops in place
	 * ready to handle default domain setup as soon as any SMMU exists.
	 */
	if (!using_legacy_binding)
		return arm_smmu_bus_init(&arm_smmu_ops);

	return 0;'''
    new=r'''	/* Only SMMU with the Phase250 GPU vdd supply, not all SMMUs.
	 * Probe's regulator and clocks are currently ON. Hold a runtime PM
	 * usage reference through bus registration and release it with a
	 * 20 ms autosuspend once probe is finished.
	 */
	if (dev->pm_domain || a52_kgsl_smmu_has_phase250_vdd(smmu)) {
		if (a52_kgsl_smmu_has_phase250_vdd(smmu)) {
			pm_runtime_set_autosuspend_delay(dev, 20);
			pm_runtime_use_autosuspend(dev);
			a52_kgsl_smmu_pm_diag(dev, "probe_before_pm");
		}
		err = pm_runtime_set_active(dev);
		if (err) {
			if (a52_kgsl_smmu_has_phase250_vdd(smmu))
				goto a52_probe_error;
			return err;
		}
		if (a52_kgsl_smmu_has_phase250_vdd(smmu)) {
			pm_runtime_get_noresume(dev);
			a52_pm_ref = true;
		}
		pm_runtime_enable(dev);
		a52_pm_owned = a52_kgsl_smmu_has_phase250_vdd(smmu);
	}

	if (!using_legacy_binding) {
		err = arm_smmu_bus_init(&arm_smmu_ops);
		if (err) {
			if (a52_kgsl_smmu_has_phase250_vdd(smmu))
				goto a52_probe_error;
			return err;
		}
	}
	if (a52_pm_ref) {
		a52_kgsl_smmu_pm_diag(dev, "probe_before_autosuspend");
		pm_runtime_mark_last_busy(dev);
		pm_runtime_put_autosuspend(dev);
		a52_kgsl_smmu_pm_diag(dev, "probe_after_autosuspend");
	}
	return 0;

a52_probe_error:
	/* Correct Phase250's post-regulator probe-failure leak. No forced
	 * regulator-off during normal SMMU usage, only unwind of a failed
	 * probe after this instance's successful regulator enable.
	 */
	pr_err("P446S KGSL_SMMU_RPM probe_error dev=%s rc=%d pm_owned=%u\n",
		dev_name(dev), err, a52_pm_owned);
	if (a52_pm_owned)
		pm_runtime_disable(dev);
	if (a52_pm_ref)
		pm_runtime_put_noidle(dev);
	if (a52_pm_owned || pm_runtime_use_autosuspend)
		pm_runtime_dont_use_autosuspend(dev);
	if (a52_iommu_registered)
		iommu_device_unregister(&smmu->iommu);
	if (a52_sysfs_added)
		iommu_device_sysfs_remove(&smmu->iommu);
	clk_bulk_disable(smmu->num_clks, smmu->clks);
	a52_arm_smmu_disable_gdscs(smmu);
	clk_bulk_unprepare(smmu->num_clks, smmu->clks);
	return err;'''
    # Eliminate a function pointer expression by using scope-safe DT test.
    new=new.replace("if (a52_pm_owned || pm_runtime_use_autosuspend)",
                    "if (a52_kgsl_smmu_has_phase250_vdd(smmu))")
    return once(body,old,new,"probe PM policy")

def patch_remove(body):
    old=r'''	if (pm_runtime_enabled(smmu->dev))
		pm_runtime_force_suspend(smmu->dev);
	else {
		clk_bulk_disable(smmu->num_clks, smmu->clks);
		a52_arm_smmu_disable_gdscs(smmu);
	}

	clk_bulk_unprepare(smmu->num_clks, smmu->clks);'''
    new=r'''	if (a52_kgsl_smmu_has_phase250_vdd(smmu))
		a52_kgsl_smmu_pm_diag(smmu->dev, "remove_begin");
	if (pm_runtime_enabled(smmu->dev))
		pm_runtime_force_suspend(smmu->dev);
	else {
		clk_bulk_disable(smmu->num_clks, smmu->clks);
		a52_arm_smmu_disable_gdscs(smmu);
	}

	if (a52_kgsl_smmu_has_phase250_vdd(smmu)) {
		pm_runtime_dont_use_autosuspend(smmu->dev);
		a52_kgsl_smmu_pm_diag(smmu->dev, "remove_done");
	}
	clk_bulk_unprepare(smmu->num_clks, smmu->clks);'''
    return once(body,old,new,"remove lifetime")

def patch_runtime_resume(body):
    anchor=r'''	ret = a52_arm_smmu_enable_gdscs(smmu);
	if (ret)
		return ret;'''
    added=r'''	if (a52_kgsl_smmu_has_phase250_vdd(smmu))
		a52_kgsl_smmu_pm_diag(dev, "runtime_resume_begin");
	ret = a52_arm_smmu_enable_gdscs(smmu);
	if (ret)
		return ret;
	if (a52_kgsl_smmu_has_phase250_vdd(smmu))
		a52_kgsl_smmu_pm_diag(dev, "runtime_resume_vdd_on");'''
    return once(body,anchor,added,"runtime resume")

def patch_runtime_suspend(body):
    anchor=r'''	clk_bulk_disable(smmu->num_clks, smmu->clks);
	a52_arm_smmu_disable_gdscs(smmu);

	return 0;'''
    added=r'''	if (a52_kgsl_smmu_has_phase250_vdd(smmu))
		a52_kgsl_smmu_pm_diag(dev, "runtime_suspend_begin");
	clk_bulk_disable(smmu->num_clks, smmu->clks);
	a52_arm_smmu_disable_gdscs(smmu);
	if (a52_kgsl_smmu_has_phase250_vdd(smmu))
		a52_kgsl_smmu_pm_diag(dev, "runtime_suspend_vdd_off");

	return 0;'''
    return once(body,anchor,added,"runtime suspend")

def patch(s):
    if TAG in s:
        return s
    for anchor in (PHASE250,"a52_arm_smmu_enable_gdscs",
                   "a52_arm_smmu_disable_gdscs",
                   "pm_runtime_set_active", "arm_smmu_rpm_get",
                   "arm_smmu_rpm_put", "if (dev->pm_domain)"):
        if anchor not in s:
            raise RuntimeError("446S SMMU missing 5.10/Phase250 "+anchor)
    include="#include <linux/pm_runtime.h>"
    if include not in s:
        raise RuntimeError("446S SMMU missing pm_runtime include")
    s=once(s,"static bool using_legacy_binding, using_generic_binding;",
           "static bool using_legacy_binding, using_generic_binding;\n"+TARGET,
           "helper placement")
    # 5.10 fault IRQ handlers touch registers without arm_smmu_rpm_get.
    # Log this risk if a fault arrives while A52 runtime PM is suspended.
    # Do not call the sleeping pm_runtime resume path in IRQ context.
    def fault_context(body):
        anchor='int idx = smmu_domain->cfg.cbndx;'
        addition=anchor+r'''

	if (unlikely(a52_kgsl_smmu_has_phase250_vdd(smmu) &&
		     pm_runtime_suspended(smmu->dev)))
		dev_warn_ratelimited(smmu->dev,
			"P446S KGSL_SMMU_RPM FAULT_IRQ context while suspended irq=%d\n",
			irq);'''
        return once(body,anchor,addition,"context IRQ audit")
    def fault_global(body):
        anchor='static DEFINE_RATELIMIT_STATE(rs, DEFAULT_RATELIMIT_INTERVAL,'
        if anchor not in body:
            raise RuntimeError("446S SMMU fault global ratelimit anchor absent")
        insert_before=r'''
	if (unlikely(a52_kgsl_smmu_has_phase250_vdd(smmu) &&
		     pm_runtime_suspended(smmu->dev)))
		dev_warn_ratelimited(smmu->dev,
			"P446S KGSL_SMMU_RPM FAULT_IRQ global while suspended irq=%d\n",
			irq);

'''
        # Insert before the first MMIO read, without changing existing reads.
        return once(body,"\tgfsr = arm_smmu_gr0_read(smmu,",
                    insert_before+"\tgfsr = arm_smmu_gr0_read(smmu,",
                    "global IRQ audit")
    for sig,fn in (
        ("static irqreturn_t arm_smmu_context_fault(int irq, void *dev)",fault_context),
        ("static irqreturn_t arm_smmu_global_fault(int irq, void *dev)",fault_global),
    ):
        s=replace_block(s,sig,fn,sig)
    for sig,fn in (
        ("static int arm_smmu_device_probe(struct platform_device *pdev)",patch_probe),
        ("static int arm_smmu_device_remove(struct platform_device *pdev)",patch_remove),
        ("static int __maybe_unused arm_smmu_runtime_resume(struct device *dev)",patch_runtime_resume),
        ("static int __maybe_unused arm_smmu_runtime_suspend(struct device *dev)",patch_runtime_suspend),
    ):
        s=replace_block(s,sig,fn,sig)
    return s

def audit(root):
    s=(root/F).read_text()
    for t in (TAG,"smmu-kgsl@3d40000","vdd-supply","phase250_vdd",
              "pm_runtime_get_noresume(dev)",
              "pm_runtime_put_autosuspend(dev)",
              "pm_runtime_set_autosuspend_delay(dev, 20)",
              "a52_probe_error:",
              "runtime_suspend_vdd_off","runtime_resume_vdd_on",
              "FAULT_IRQ context while suspended",
              "FAULT_IRQ global while suspended"):
        if t not in s:
            raise RuntimeError("446S SMMU audit missing "+t)
    if s.count("if (dev->pm_domain || a52_kgsl_smmu_has_phase250_vdd(smmu))")!=1:
        raise RuntimeError("446S SMMU global PM scope changed")
    if "gmu_disable_gdsc" in s:
        raise RuntimeError("446S SMMU must not modify GMU")
    print("P446S SMMU RPM source audit PASS: GPU-only vdd runtime PM, 20ms idle, probe/remove/PM logs")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    opt=p.parse_args()
    path=opt.root/F
    if not opt.check_only:
        path.write_text(patch(path.read_text()))
    audit(opt.root)
if __name__=="__main__":
    main()
