#!/usr/bin/env python3
from pathlib import Path
import sys

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/* Typed existing-tracepoint subscribers for TouchGrass definitive FDR. */
#include <linux/device.h>
#include <linux/dma-fence.h>
#include <linux/init.h>
#include <linux/iommu.h>
#include <linux/kernel.h>
#include <linux/tg_fdr.h>

#include <trace/events/clk.h>
#include <trace/events/regulator.h>
#include <trace/events/rpm.h>
#include <trace/events/power.h>
#include <trace/events/iommu.h>
#include <trace/events/dma_fence.h>
#include <trace/events/ufs.h>

static inline bool tg_fdr_runtime_trace_on(void)
{
	return tg_fdr_streaming_active();
}

static void tg_tp_clk_basic(void *ignore, struct clk_core *core)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:STATE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

#define TG_CLK_BASIC_CB(_fn, _tag) \
static void _fn(void *ignore, struct clk_core *core) \
{ \
	u32 obj; \
	if (!tg_fdr_runtime_trace_on()) return; \
	obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core); \
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, _tag, 0, obj, \
		   (u64)(unsigned long)core, 0, 0, 0, 0); \
}

TG_CLK_BASIC_CB(tg_tp_clk_enable, "CLK:ENABLE")
TG_CLK_BASIC_CB(tg_tp_clk_enable_done, "CLK:ENABLE_DONE")
TG_CLK_BASIC_CB(tg_tp_clk_disable, "CLK:DISABLE")
TG_CLK_BASIC_CB(tg_tp_clk_disable_done, "CLK:DISABLE_DONE")
TG_CLK_BASIC_CB(tg_tp_clk_prepare, "CLK:PREPARE")
TG_CLK_BASIC_CB(tg_tp_clk_prepare_done, "CLK:PREPARE_DONE")
TG_CLK_BASIC_CB(tg_tp_clk_unprepare, "CLK:UNPREPARE")
TG_CLK_BASIC_CB(tg_tp_clk_unprepare_done, "CLK:UNPREPARE_DONE")

static void tg_tp_clk_rate(void *ignore, struct clk_core *core,
			   unsigned long rate)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:RATE", 0, obj,
		   (u64)(unsigned long)core, rate, 0, 0, 0);
}

static void tg_tp_clk_rate_done(void *ignore, struct clk_core *core,
				unsigned long rate)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:RATE_DONE", 0, obj,
		   (u64)(unsigned long)core, rate, 0, 0, 0);
}

static void tg_tp_clk_parent(void *ignore, struct clk_core *core,
			     struct clk_core *parent)
{
	u32 obj, parent_obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	parent_obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, parent);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:PARENT", 0, obj,
		   parent_obj, (u64)(unsigned long)core,
		   (u64)(unsigned long)parent, 0, 0);
}

static void tg_tp_clk_parent_done(void *ignore, struct clk_core *core,
				  struct clk_core *parent)
{
	u32 obj, parent_obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	parent_obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, parent);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:PARENT_DONE", 0, obj,
		   parent_obj, (u64)(unsigned long)core,
		   (u64)(unsigned long)parent, 0, 0);
}

static void tg_tp_reg_basic(void *ignore, const char *name)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:STATE", 0, 0,
		   tg_fdr_hash_tag(name), 0, 0, 0, 0);
}

#define TG_REG_BASIC_CB(_fn, _tag) \
static void _fn(void *ignore, const char *name) \
{ \
	if (!tg_fdr_runtime_trace_on()) return; \
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, _tag, 0, 0, \
		   tg_fdr_hash_tag(name), 0, 0, 0, 0); \
}

TG_REG_BASIC_CB(tg_tp_reg_enable, "REG:ENABLE")
TG_REG_BASIC_CB(tg_tp_reg_enable_delay, "REG:ENABLE_DELAY")
TG_REG_BASIC_CB(tg_tp_reg_enable_done, "REG:ENABLE_DONE")
TG_REG_BASIC_CB(tg_tp_reg_disable, "REG:DISABLE")
TG_REG_BASIC_CB(tg_tp_reg_disable_done, "REG:DISABLE_DONE")

static void tg_tp_reg_voltage(void *ignore, const char *name, int min, int max)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:VOLTAGE", 0, 0,
		   tg_fdr_hash_tag(name), (u64)(s64)min, (u64)(s64)max, 0, 0);
}

static void tg_tp_reg_voltage_done(void *ignore, const char *name,
				   unsigned int value)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:VOLTAGE_DONE", 0, 0,
		   tg_fdr_hash_tag(name), value, 0, 0, 0);
}

#define TG_RPM_CB(_fn, _tag) \
static void _fn(void *ignore, struct device *dev, int flags) \
{ \
	u32 obj; \
	if (!tg_fdr_runtime_trace_on()) return; \
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev); \
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, _tag, 0, obj, flags, \
		   dev ? dev->power.runtime_status : 0, \
		   dev ? atomic_read(&dev->power.usage_count) : 0, \
		   dev ? atomic_read(&dev->power.child_count) : 0, 0); \
}

TG_RPM_CB(tg_tp_rpm_suspend, "RPM:SUSPEND")
TG_RPM_CB(tg_tp_rpm_resume, "RPM:RESUME")
TG_RPM_CB(tg_tp_rpm_idle, "RPM:IDLE")

static void tg_tp_rpm_return(void *ignore, struct device *dev,
			     unsigned long ip, int ret)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "RPM:RETURN", ret, obj,
		   ip, dev ? dev->power.runtime_status : 0,
		   dev ? atomic_read(&dev->power.usage_count) : 0,
		   dev ? dev->power.runtime_error : 0,
		   ret < 0 ? TG_FDR_FLAG_CRITICAL : 0);
}

static void tg_tp_dev_pm_start(void *ignore, struct device *dev,
			       const char *pm_ops, int event)
{
	u32 obj, parent_obj = 0;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	if (dev && dev->parent)
		parent_obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev->parent);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:CALLBACK_START", 0, obj,
		   event, tg_fdr_hash_tag(pm_ops), parent_obj, 0, 0);
}

static void tg_tp_dev_pm_end(void *ignore, struct device *dev, int error)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:CALLBACK_END", error, obj,
		   dev ? dev->power.runtime_status : 0, 0, 0, 0,
		   error ? TG_FDR_FLAG_CRITICAL : 0);
}

static void tg_tp_suspend_resume(void *ignore, const char *action,
				 int val, bool start)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:SUSPEND_RESUME", 0, 0,
		   tg_fdr_hash_tag(action), (u64)(s64)val, start, 0,
		   TG_FDR_FLAG_CRITICAL);
}

static void tg_tp_wakeup(void *ignore, const char *name, unsigned int state)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:WAKEUP_SOURCE", 0, 0,
		   tg_fdr_hash_tag(name), state, 0, 0, 0);
}

static void tg_tp_power_domain(void *ignore, const char *name,
			       unsigned int state, unsigned int cpu_id)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "GENPD:TARGET", 0, 0,
		   tg_fdr_hash_tag(name), state, cpu_id, 0, 0);
}

static void tg_tp_iommu_group(void *ignore, int gid, struct device *dev)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:GROUP_TP", 0, obj,
		   (u64)(s64)gid, 0, 0, 0, 0);
}

static void tg_tp_iommu_attach(void *ignore, struct device *dev)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:ATTACH_TP", 0, obj,
		   (u64)(unsigned long)dev, 0, 0, 0, 0);
}

static void tg_tp_iommu_detach(void *ignore, struct device *dev)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:DETACH_TP", 0, obj,
		   (u64)(unsigned long)dev, 0, 0, 0, 0);
}

static void tg_tp_iommu_map(void *ignore, struct iommu_domain *domain,
			    unsigned long iova, phys_addr_t paddr,
			    size_t size, int prot)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_IOMMU_DOMAIN, domain);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:MAP", 0, obj,
		   iova, paddr, size, (u64)(u32)prot, 0);
}

static void tg_tp_iommu_unmap(void *ignore, struct iommu_domain *domain,
			      unsigned long iova, size_t size, size_t unmapped)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_IOMMU_DOMAIN, domain);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:UNMAP", 0, obj,
		   iova, size, unmapped, 0, 0);
}

static void tg_tp_iommu_map_sg(void *ignore, struct iommu_domain *domain,
			       unsigned long iova, size_t size, int prot)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_IOMMU_DOMAIN, domain);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:MAP_SG", 0, obj,
		   iova, size, (u64)(u32)prot, 0, 0);
}

static void tg_tp_iommu_fault(void *ignore, struct device *dev,
			      unsigned long iova, int flags)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:FAULT", -EFAULT, obj,
		   iova, (u64)(u32)flags, 0, 0, TG_FDR_FLAG_CRITICAL);
}

static void tg_tp_iommu_tlbi(void *ignore, struct device *dev, u64 time)
{
	u32 obj;
	if (!tg_fdr_runtime_trace_on()) return;
	obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:TLBI", 0, obj,
		   time, 0, 0, 0, 0);
}

static void tg_tp_iommu_tlbsync_timeout(void *ignore, struct device *dev,
					u64 time)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:TLBSYNC_TIMEOUT", -ETIMEDOUT,
		   obj, time, 0, 0, 0, TG_FDR_FLAG_CRITICAL);
}

#define TG_FENCE_CB(_fn, _tag) \
static void _fn(void *ignore, struct dma_fence *fence) \
{ \
	u32 obj; \
	if (!tg_fdr_runtime_trace_on()) return; \
	obj = tg_fdr_object_id(TG_FDR_OBJ_FENCE, fence); \
	TG_FDR_TAG(TG_FDR_SUBSYS_DISPLAY, _tag, 0, obj, \
		   fence ? fence->context : 0, fence ? fence->seqno : 0, \
		   fence ? fence->flags : 0, (u64)(unsigned long)fence, 0); \
}

TG_FENCE_CB(tg_tp_fence_init, "FENCE:INIT")
TG_FENCE_CB(tg_tp_fence_emit, "FENCE:EMIT")
TG_FENCE_CB(tg_tp_fence_enable, "FENCE:ENABLE_SIGNAL")
TG_FENCE_CB(tg_tp_fence_signaled, "FENCE:SIGNALED")
TG_FENCE_CB(tg_tp_fence_wait_start, "FENCE:WAIT_START")
TG_FENCE_CB(tg_tp_fence_wait_end, "FENCE:WAIT_END")
TG_FENCE_CB(tg_tp_fence_destroy, "FENCE:DESTROY")

static void tg_tp_ufs_state(void *ignore, const char *dev_name, int state)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_STORAGE, "UFS:STATE", 0, 0,
		   tg_fdr_hash_tag(dev_name), (u64)(s64)state, 0, 0, 0);
}

static void tg_tp_ufs_clk_scaling(void *ignore, const char *dev_name,
				  const char *state, const char *clk,
				  u32 prev_state, u32 curr_state)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_STORAGE, "UFS:CLK_SCALING", 0, 0,
		   tg_fdr_hash_tag(dev_name), tg_fdr_hash_tag(state),
		   ((u64)prev_state << 32) | curr_state, tg_fdr_hash_tag(clk), 0);
}

static void tg_tp_ufs_profile(void *ignore, const char *dev_name,
			      const char *profile_info, s64 time_us, int err)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_STORAGE, "UFS:PROFILE", err, 0,
		   tg_fdr_hash_tag(dev_name), tg_fdr_hash_tag(profile_info),
		   (u64)time_us, 0, err ? TG_FDR_FLAG_CRITICAL : 0);
}

static void tg_tp_ufs_pm(void *ignore, const char *dev_name, int err,
			 s64 usecs, int dev_state, int link_state)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_STORAGE, "UFS:PM", err, 0,
		   tg_fdr_hash_tag(dev_name), (u64)usecs,
		   (u64)(u32)dev_state, (u64)(u32)link_state,
		   err ? TG_FDR_FLAG_CRITICAL : 0);
}

static void tg_tp_ufs_command(void *ignore, const char *dev_name,
			      const char *str, unsigned int tag, u32 doorbell,
			      int transfer_len, u32 intr, u64 lba, u8 opcode)
{
	if (!tg_fdr_runtime_trace_on()) return;
	TG_FDR_TAG(TG_FDR_SUBSYS_STORAGE, "UFS:COMMAND", 0, 0,
		   ((u64)opcode << 56) | ((u64)tag << 32) | doorbell,
		   lba, (u64)(u32)transfer_len,
		   ((u64)intr << 32) | tg_fdr_hash_tag(str), 0);
}

static void tg_fdr_tp_registration(const char *name, int ret)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_META, "TP:REGISTER", ret, 0,
		   tg_fdr_hash_tag(name), 0, 0, 0,
		   ret ? TG_FDR_FLAG_CRITICAL : 0);
}

#define TG_REG_TRACE(_name, _fn) do { \
	ret = register_trace_##_name(_fn, NULL); \
	tg_fdr_tp_registration(#_name, ret); \
	if (ret) failed++; else registered++; \
} while (0)

static int __init tg_fdr_typed_tracepoints_init(void)
{
	int ret;
	u32 registered = 0, failed = 0;

	TG_REG_TRACE(clk_enable, tg_tp_clk_enable);
	TG_REG_TRACE(clk_enable_complete, tg_tp_clk_enable_done);
	TG_REG_TRACE(clk_disable, tg_tp_clk_disable);
	TG_REG_TRACE(clk_disable_complete, tg_tp_clk_disable_done);
	TG_REG_TRACE(clk_prepare, tg_tp_clk_prepare);
	TG_REG_TRACE(clk_prepare_complete, tg_tp_clk_prepare_done);
	TG_REG_TRACE(clk_unprepare, tg_tp_clk_unprepare);
	TG_REG_TRACE(clk_unprepare_complete, tg_tp_clk_unprepare_done);
	TG_REG_TRACE(clk_set_rate, tg_tp_clk_rate);
	TG_REG_TRACE(clk_set_rate_complete, tg_tp_clk_rate_done);
	TG_REG_TRACE(clk_set_parent, tg_tp_clk_parent);
	TG_REG_TRACE(clk_set_parent_complete, tg_tp_clk_parent_done);

	TG_REG_TRACE(regulator_enable, tg_tp_reg_enable);
	TG_REG_TRACE(regulator_enable_delay, tg_tp_reg_enable_delay);
	TG_REG_TRACE(regulator_enable_complete, tg_tp_reg_enable_done);
	TG_REG_TRACE(regulator_disable, tg_tp_reg_disable);
	TG_REG_TRACE(regulator_disable_complete, tg_tp_reg_disable_done);
	TG_REG_TRACE(regulator_set_voltage, tg_tp_reg_voltage);
	TG_REG_TRACE(regulator_set_voltage_complete, tg_tp_reg_voltage_done);

	TG_REG_TRACE(rpm_suspend, tg_tp_rpm_suspend);
	TG_REG_TRACE(rpm_resume, tg_tp_rpm_resume);
	TG_REG_TRACE(rpm_idle, tg_tp_rpm_idle);
	TG_REG_TRACE(rpm_return_int, tg_tp_rpm_return);

	TG_REG_TRACE(device_pm_callback_start, tg_tp_dev_pm_start);
	TG_REG_TRACE(device_pm_callback_end, tg_tp_dev_pm_end);
	TG_REG_TRACE(suspend_resume, tg_tp_suspend_resume);
	TG_REG_TRACE(wakeup_source_activate, tg_tp_wakeup);
	TG_REG_TRACE(wakeup_source_deactivate, tg_tp_wakeup);
	TG_REG_TRACE(power_domain_target, tg_tp_power_domain);

	TG_REG_TRACE(add_device_to_group, tg_tp_iommu_group);
	TG_REG_TRACE(remove_device_from_group, tg_tp_iommu_group);
	TG_REG_TRACE(attach_device_to_domain, tg_tp_iommu_attach);
	TG_REG_TRACE(detach_device_from_domain, tg_tp_iommu_detach);
	TG_REG_TRACE(map, tg_tp_iommu_map);
	TG_REG_TRACE(unmap, tg_tp_iommu_unmap);
	TG_REG_TRACE(map_sg, tg_tp_iommu_map_sg);
	TG_REG_TRACE(io_page_fault, tg_tp_iommu_fault);
	TG_REG_TRACE(tlbi_start, tg_tp_iommu_tlbi);
	TG_REG_TRACE(tlbi_end, tg_tp_iommu_tlbi);
	TG_REG_TRACE(tlbsync_timeout, tg_tp_iommu_tlbsync_timeout);

	TG_REG_TRACE(dma_fence_init, tg_tp_fence_init);
	TG_REG_TRACE(dma_fence_emit, tg_tp_fence_emit);
	TG_REG_TRACE(dma_fence_enable_signal, tg_tp_fence_enable);
	TG_REG_TRACE(dma_fence_signaled, tg_tp_fence_signaled);
	TG_REG_TRACE(dma_fence_wait_start, tg_tp_fence_wait_start);
	TG_REG_TRACE(dma_fence_wait_end, tg_tp_fence_wait_end);
	TG_REG_TRACE(dma_fence_destroy, tg_tp_fence_destroy);

	TG_REG_TRACE(ufshcd_clk_gating, tg_tp_ufs_state);
	TG_REG_TRACE(ufshcd_hibern8_on_idle, tg_tp_ufs_state);
	TG_REG_TRACE(ufshcd_auto_bkops_state, tg_tp_ufs_state);
	TG_REG_TRACE(ufshcd_clk_scaling, tg_tp_ufs_clk_scaling);
	TG_REG_TRACE(ufshcd_profile_hibern8, tg_tp_ufs_profile);
	TG_REG_TRACE(ufshcd_profile_clk_gating, tg_tp_ufs_profile);
	TG_REG_TRACE(ufshcd_profile_clk_scaling, tg_tp_ufs_profile);
	TG_REG_TRACE(ufshcd_system_suspend, tg_tp_ufs_pm);
	TG_REG_TRACE(ufshcd_system_resume, tg_tp_ufs_pm);
	TG_REG_TRACE(ufshcd_runtime_suspend, tg_tp_ufs_pm);
	TG_REG_TRACE(ufshcd_runtime_resume, tg_tp_ufs_pm);
	TG_REG_TRACE(ufshcd_init, tg_tp_ufs_pm);
	TG_REG_TRACE(ufshcd_command, tg_tp_ufs_command);

	TG_FDR_TAG(TG_FDR_SUBSYS_META, "TP:SUMMARY", failed ? -EINVAL : 0,
		   0, registered, failed, 0, 0,
		   failed ? TG_FDR_FLAG_CRITICAL : 0);
	return 0;
}
late_initcall(tg_fdr_typed_tracepoints_init);
'''


def main(root: Path) -> None:
    p = root / 'kernel/tg_fdr_tracepoints.c'
    p.write_text(SOURCE)
    mk = root / 'kernel/Makefile'
    text = mk.read_text()
    line = 'obj-y += tg_fdr_tracepoints.o\n'
    if line not in text:
        mk.write_text(text.rstrip() + '\n' + line)
    if 'register_trace_map(tg_tp_iommu_map' not in p.read_text():
        raise SystemExit('typed IOMMU registration missing')
    if 'register_trace_ufshcd_command' not in p.read_text():
        raise SystemExit('typed UFS registration missing')
    print('TouchGrass definitive FDR typed runtime tracepoints staged')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_definitive_fdr_typed_tracepoints_overlay.py <kernel-root>')
    main(Path(sys.argv[1]).resolve())
