#!/usr/bin/env python3
"""P460b opt-in TG-style apps-SMMU system PM experiment.

No effect while sysfs=0. Scope: Lagoon 15000000.apps-smmu, skip_init,
unmanaged TBUs, runtime PM disabled. Never changes KGSL or qcom impl.
CAUTION: checksum mismatch detection is POST-hoc, not a power retention proof.
"""
import argparse
from pathlib import Path

PATH = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")
MARK = "A52_P460B_APPS_SMMU_TG_OPTIN_V1"

def one(s, a, b, name):
    n=s.count(a)
    if n != 1:
        raise RuntimeError(f"P460B {name}: expected one anchor, got {n}")
    return s.replace(a,b,1)

HELPER = r"""
/* A52_P460B_APPS_SMMU_TG_OPTIN_V1
 * EXPERIMENTAL. TG 4.19 skip-init resumes with global FSR/TLB and sCR0.
 * No SME or context bank writes. No power/domain/clock votes are altered.
 * This does NOT prove TCU/TBU state survives system sleep.
 */
static bool a52_p460b_apps_smmu_tg;
static bool a52_p460b_suspend_armed;
static u64 a52_p460b_pre_sme_sum;
static u32 a52_p460b_pre_scr0;
static u64 a52_p460b_cycle;
static struct kobject *a52_p460b_kobj;

static bool a52_p460b_scope(struct device *dev)
{
	struct arm_smmu_device *smmu = dev_get_drvdata(dev);

	return smmu && smmu->skip_init &&
		of_machine_is_compatible("qcom,lagoon") &&
		!strcmp(dev_name(dev), "15000000.apps-smmu") &&
		a52_apps_smmu_has_unmanaged_tbus(dev) &&
		!pm_runtime_enabled(dev);
}

static u64 a52_p460b_sme_checksum(struct arm_smmu_device *smmu)
{
	u64 sum = 1469598103934665603ULL;
	unsigned int i;
	u32 value;

	/* MMIO read only. Do not mask or rewrite firmware-owned SMRs. */
	for (i = 0; i < smmu->num_mapping_groups; i++) {
		value = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(i));
		sum = (sum ^ value) * 1099511628211ULL;
		value = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_S2CR(i));
		sum = (sum ^ value) * 1099511628211ULL;
	}
	return sum ^ smmu->num_mapping_groups;
}

static ssize_t apps_smmu_s2idle_tg_show(struct kobject *kobj,
				       struct kobj_attribute *attr, char *buf)
{
	return scnprintf(buf, PAGE_SIZE, "%u\n",
			 READ_ONCE(a52_p460b_apps_smmu_tg));
}

static ssize_t apps_smmu_s2idle_tg_store(struct kobject *kobj,
					struct kobj_attribute *attr,
					const char *buf, size_t size)
{
	bool on;

	if (!capable(CAP_SYS_RAWIO))
		return -EPERM;
	if (kstrtobool(buf, &on))
		return -EINVAL;
	WRITE_ONCE(a52_p460b_apps_smmu_tg, on);
	pr_warn("P460B SMMU KNOB enable=%u (experimental, no TBU rail guarantee)\n",
		on);
	return size;
}

static struct kobj_attribute a52_p460b_tg_attr =
	__ATTR(apps_smmu_s2idle_tg, 0600,
	       apps_smmu_s2idle_tg_show, apps_smmu_s2idle_tg_store);

static int __init a52_p460b_sysfs_init(void)
{
	int ret;

	a52_p460b_kobj = kobject_create_and_add("a52_pm", kernel_kobj);
	if (!a52_p460b_kobj)
		return -ENOMEM;
	ret = sysfs_create_file(a52_p460b_kobj, &a52_p460b_tg_attr.attr);
	if (ret) {
		kobject_put(a52_p460b_kobj);
		a52_p460b_kobj = NULL;
	}
	return ret;
}
late_initcall(a52_p460b_sysfs_init);

static int a52_p460b_tg_suspend(struct device *dev)
{
	struct arm_smmu_device *smmu = dev_get_drvdata(dev);

	a52_p460b_pre_scr0 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sCR0);
	a52_p460b_pre_sme_sum = a52_p460b_sme_checksum(smmu);
	a52_p460b_cycle++;
	WRITE_ONCE(a52_p460b_suspend_armed, true);
	dev_warn(dev,
		 "P460B SMMU SUSPEND cycle=%llu scr0=%08x sum=%016llx mappings=%u\n",
		 (unsigned long long)a52_p460b_cycle,
		 a52_p460b_pre_scr0,
		 (unsigned long long)a52_p460b_pre_sme_sum,
		 smmu->num_mapping_groups);
	/* TG has no suspend PM callback. No register/clock/power writes. */
	return 0;
}

static int a52_p460b_tg_resume(struct device *dev)
{
	struct arm_smmu_device *smmu = dev_get_drvdata(dev);
	u32 gfsr, scr0_before, scr0_after;
	u64 after;
	bool match;

	scr0_before = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sCR0);
	gfsr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSR);
	arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_sGFSR, gfsr);
	arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_TLBIALLH, 0);
	arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_TLBIALLNSNH, 0);
	arm_smmu_tlb_sync_global(smmu);
	arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_sCR0, a52_p460b_pre_scr0);

	scr0_after = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sCR0);
	after = a52_p460b_sme_checksum(smmu);
	match = (after == a52_p460b_pre_sme_sum &&
		 scr0_after == a52_p460b_pre_scr0);
	WRITE_ONCE(a52_p460b_suspend_armed, false);
	dev_warn(dev,
		 "P460B SMMU RESUME cycle=%llu scr0_before=%08x scr0_after=%08x expected=%08x sum_before=%016llx sum_after=%016llx match=%u\n",
		 (unsigned long long)a52_p460b_cycle,
		 scr0_before, scr0_after, a52_p460b_pre_scr0,
		 (unsigned long long)a52_p460b_pre_sme_sum,
		 (unsigned long long)after, match);
	if (!match) {
		WRITE_ONCE(a52_p460b_apps_smmu_tg, false);
		dev_crit(dev,
			 "P460B SMMU CORRUPTION: mismatch; tg knob forced 0. Mapping state is NOT repaired!\n");
		return -EIO;
	}
	return 0;
}
"""

def patch(s):
    if MARK in s:
        return s
    for token in ('a52_apps_smmu_has_unmanaged_tbus',
                  'smmu->skip_init', 'arm_smmu_tlb_sync_global',
                  'ARM_SMMU_GR0_SMR(', 'ARM_SMMU_GR0_S2CR('):
        if token not in s:
            raise RuntimeError("P460B missing 5.10 skip-init/probe marker "+token)
    # The Phase206 veto must still be present when knob is 0.
    head = 'static int __maybe_unused arm_smmu_pm_resume(struct device *dev)\n{'
    s = one(s, head, HELPER + '\n' + head, "helper placement")
    s = one(s, head, head + r"""
	if (a52_p460b_scope(dev) &&
	    READ_ONCE(a52_p460b_suspend_armed))
		return a52_p460b_tg_resume(dev);
""", "resume dispatch")
    sig = 'static int __maybe_unused arm_smmu_pm_suspend(struct device *dev)\n{'
    p = s.index(sig)
    k = s.index('if (a52_apps_smmu_has_unmanaged_tbus(dev)) {',p)
    s = s[:k] + s[k:].replace('if (a52_apps_smmu_has_unmanaged_tbus(dev)) {',
        '''if (a52_apps_smmu_has_unmanaged_tbus(dev)) {
		if (READ_ONCE(a52_p460b_apps_smmu_tg) &&
		    a52_p460b_scope(dev))
			return a52_p460b_tg_suspend(dev);
''',1)
    if '#include <linux/kobject.h>' not in s:
        anchor = '#include <linux/pm_runtime.h>'
        s = one(s,anchor,anchor+'\n#include <linux/kobject.h>\n#include <linux/sysfs.h>\n#include <linux/capability.h>',"includes")
    return s

def audit(s):
    for t in (MARK, 'apps_smmu_s2idle_tg, 0600',
              'of_machine_is_compatible("qcom,lagoon")',
              '15000000.apps-smmu', 'smmu->skip_init',
              'system suspend blocked until qsmmuv500 TBU support is ported',
              'P460B SMMU SUSPEND cycle=', 'P460B SMMU RESUME cycle=',
              'arm_smmu_gr0_write(smmu, ARM_SMMU_GR0_TLBIALLH, 0)',
              'arm_smmu_tlb_sync_global(smmu)',
              'P460B SMMU CORRUPTION:', 'return -EBUSY;',
              'if (READ_ONCE(a52_p460b_apps_smmu_tg) &&'):
        if t not in s:
            raise RuntimeError("P460B source gate missing "+t)
    begin = s.index('/* '+MARK)
    end = s.index('static int __maybe_unused arm_smmu_pm_resume',begin)
    chunk = s[begin:end]
    for forbidden in ("arm_smmu_device_reset(", "arm_smmu_write_sme(",
                      "arm_smmu_write_context_bank(", "clk_bulk_disable(",
                      "clk_bulk_enable(", "regulator_disable("):
        if forbidden in chunk:
            raise RuntimeError("P460B forbidden experiment operation "+forbidden)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--check-only',action='store_true')
    o=parser.parse_args()
    path=o.root/PATH
    original=path.read_text()
    if o.check_only:
        if MARK not in original:
            raise RuntimeError("P460B patch absent")
        audit(original)
        print("P460B source audit PASS (NO guarantee of safe power-state retention)")
        return
    updated=patch(original)
    audit(updated)
    if updated != original:
        path.write_text(updated)
    print("P460B opt-in source gate applied: default=0 preserves static veto")

if __name__ == '__main__':
    main()
