#!/usr/bin/env python3
from pathlib import Path
import sys

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/* Existing-tracepoint subscriber for TouchGrass definitive FDR. */
#include <linux/device.h>
#include <linux/init.h>
#include <linux/iommu.h>
#include <linux/kernel.h>
#include <linux/string.h>
#include <linux/tg_fdr.h>
#include <linux/tracepoint.h>

struct clk_core;
struct dma_fence;
struct binder_transaction;
struct binder_node;

static void tg_fdr_tp_clk(void *ignore, struct clk_core *core)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:STATE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

static void tg_fdr_tp_clk_enable(void *ignore, struct clk_core *core)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:ENABLE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

static void tg_fdr_tp_clk_enable_done(void *ignore, struct clk_core *core)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:ENABLE_DONE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

static void tg_fdr_tp_clk_disable(void *ignore, struct clk_core *core)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:DISABLE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

static void tg_fdr_tp_clk_prepare(void *ignore, struct clk_core *core)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:PREPARE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

static void tg_fdr_tp_clk_unprepare(void *ignore, struct clk_core *core)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:UNPREPARE", 0, obj,
		   (u64)(unsigned long)core, 0, 0, 0, 0);
}

static void tg_fdr_tp_clk_rate(void *ignore, struct clk_core *core,
			       unsigned long rate)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_CLOCK, core);
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "CLK:RATE", 0, obj,
		   (u64)(unsigned long)core, rate, 0, 0, 0);
}

static void tg_fdr_tp_regulator(void *ignore, const char *name)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:STATE", 0, 0,
		   tg_fdr_hash_tag(name), 0, 0, 0, 0);
}

static void tg_fdr_tp_reg_enable(void *ignore, const char *name)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:ENABLE", 0, 0,
		   tg_fdr_hash_tag(name), 0, 0, 0, 0);
}

static void tg_fdr_tp_reg_enable_done(void *ignore, const char *name)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:ENABLE_DONE", 0, 0,
		   tg_fdr_hash_tag(name), 0, 0, 0, 0);
}

static void tg_fdr_tp_reg_disable(void *ignore, const char *name)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:DISABLE", 0, 0,
		   tg_fdr_hash_tag(name), 0, 0, 0, 0);
}

static void tg_fdr_tp_reg_range(void *ignore, const char *name,
				int min, int max)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:VOLTAGE", 0, 0,
		   tg_fdr_hash_tag(name), (u64)(s64)min, (u64)(s64)max, 0, 0);
}

static void tg_fdr_tp_reg_value(void *ignore, const char *name,
				unsigned int value)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_POWER, "REG:VOLTAGE_DONE", 0, 0,
		   tg_fdr_hash_tag(name), value, 0, 0, 0);
}

static void tg_fdr_tp_rpm(void *ignore, struct device *dev, int flags)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "RPM:STATE", 0, obj,
		   flags, dev ? dev->power.runtime_status : 0,
		   dev ? atomic_read(&dev->power.usage_count) : 0,
		   dev ? dev->power.disable_depth : 0, 0);
}

static void tg_fdr_tp_rpm_suspend(void *ignore, struct device *dev, int flags)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "RPM:SUSPEND", 0, obj,
		   flags, dev ? dev->power.runtime_status : 0,
		   dev ? atomic_read(&dev->power.usage_count) : 0,
		   dev ? atomic_read(&dev->power.child_count) : 0, 0);
}

static void tg_fdr_tp_rpm_resume(void *ignore, struct device *dev, int flags)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "RPM:RESUME", 0, obj,
		   flags, dev ? dev->power.runtime_status : 0,
		   dev ? atomic_read(&dev->power.usage_count) : 0,
		   dev ? atomic_read(&dev->power.child_count) : 0, 0);
}

static void tg_fdr_tp_rpm_idle(void *ignore, struct device *dev, int flags)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "RPM:IDLE", 0, obj,
		   flags, dev ? dev->power.runtime_status : 0,
		   dev ? atomic_read(&dev->power.usage_count) : 0,
		   dev ? dev->power.request_pending : 0, 0);
}

static void tg_fdr_tp_rpm_return(void *ignore, struct device *dev,
				 unsigned long ip, int ret)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "RPM:RETURN", ret, obj,
		   ip, dev ? dev->power.runtime_status : 0,
		   dev ? atomic_read(&dev->power.usage_count) : 0,
		   dev ? dev->power.runtime_error : 0,
		   ret < 0 ? TG_FDR_FLAG_CRITICAL : 0);
}

static void tg_fdr_tp_dev_pm_start(void *ignore, struct device *dev,
				   const char *pm_ops, int event)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:CALLBACK_START", 0, obj,
		   event, tg_fdr_hash_tag(pm_ops),
		   dev && dev->parent ?
		     tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev->parent) : 0,
		   0, 0);
}

static void tg_fdr_tp_dev_pm_end(void *ignore, struct device *dev, int error)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:CALLBACK_END", error, obj,
		   dev ? dev->power.runtime_status : 0, 0, 0, 0,
		   error ? TG_FDR_FLAG_CRITICAL : 0);
}

static void tg_fdr_tp_suspend_resume(void *ignore, const char *action,
				     int val, bool start)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:SUSPEND_RESUME", 0, 0,
		   tg_fdr_hash_tag(action), (u64)(s64)val, start, 0,
		   TG_FDR_FLAG_CRITICAL);
}

static void tg_fdr_tp_wakeup(void *ignore, const char *name,
			     unsigned int state)
{
	TG_FDR_TAG(TG_FDR_SUBSYS_PM, "PM:WAKEUP_SOURCE", 0, 0,
		   tg_fdr_hash_tag(name), state, 0, 0, 0);
}

static void tg_fdr_tp_iommu_group(void *ignore, int gid, struct device *dev)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:GROUP", 0, obj,
		   (u64)(s64)gid, 0, 0, 0, 0);
}

static void tg_fdr_tp_iommu_attach(void *ignore, struct device *dev)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:ATTACH_TP", 0, obj,
		   (u64)(unsigned long)dev, 0, 0, 0, 0);
}

static void tg_fdr_tp_iommu_detach(void *ignore, struct device *dev)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:DETACH_TP", 0, obj,
		   (u64)(unsigned long)dev, 0, 0, 0, 0);
}

static void tg_fdr_tp_iommu_map(void *ignore, struct iommu_domain *domain,
				unsigned long iova, phys_addr_t paddr,
				size_t size, int prot)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_IOMMU_DOMAIN, domain);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:MAP", 0, obj,
		   iova, paddr, size, (u64)(u32)prot, 0);
}

static void tg_fdr_tp_iommu_unmap(void *ignore, struct iommu_domain *domain,
				  unsigned long iova, size_t size,
				  size_t unmapped)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_IOMMU_DOMAIN, domain);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:UNMAP", 0, obj,
		   iova, size, unmapped, 0, 0);
}

static void tg_fdr_tp_iommu_map_sg(void *ignore, struct iommu_domain *domain,
				   unsigned long iova, size_t size, int prot)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_IOMMU_DOMAIN, domain);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:MAP_SG", 0, obj,
		   iova, size, (u64)(u32)prot, 0, 0);
}

static void tg_fdr_tp_iommu_fault(void *ignore, struct device *dev,
				  unsigned long iova, int flags)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:FAULT", -EFAULT, obj,
		   iova, (u64)(u32)flags, 0, 0, TG_FDR_FLAG_CRITICAL);
}

static void tg_fdr_tp_iommu_tlbi(void *ignore, struct device *dev, u64 time)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:TLBI", 0, obj,
		   time, 0, 0, 0, 0);
}

static void tg_fdr_tp_iommu_tlbsync_timeout(void *ignore,
					    struct device *dev, u64 time)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_DEVICE, dev);
	TG_FDR_TAG(TG_FDR_SUBSYS_IOMMU, "IOMMU:TLBSYNC_TIMEOUT", -ETIMEDOUT,
		   obj, time, 0, 0, 0, TG_FDR_FLAG_CRITICAL);
}

static void tg_fdr_tp_fence(void *ignore, struct dma_fence *fence)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_FENCE, fence);
	TG_FDR_TAG(TG_FDR_SUBSYS_DISPLAY, "FENCE:STATE", 0, obj,
		   (u64)(unsigned long)fence, 0, 0, 0, 0);
}

static void tg_fdr_tp_fence_init(void *ignore, struct dma_fence *fence)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_FENCE, fence);
	TG_FDR_TAG(TG_FDR_SUBSYS_DISPLAY, "FENCE:INIT", 0, obj,
		   (u64)(unsigned long)fence, 0, 0, 0, 0);
}

static void tg_fdr_tp_fence_signaled(void *ignore, struct dma_fence *fence)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_FENCE, fence);
	TG_FDR_TAG(TG_FDR_SUBSYS_DISPLAY, "FENCE:SIGNALED", 0, obj,
		   (u64)(unsigned long)fence, 0, 0, 0, 0);
}

static void tg_fdr_tp_fence_wait_start(void *ignore, struct dma_fence *fence)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_FENCE, fence);
	TG_FDR_TAG(TG_FDR_SUBSYS_DISPLAY, "FENCE:WAIT_START", 0, obj,
		   (u64)(unsigned long)fence, 0, 0, 0, 0);
}

static void tg_fdr_tp_fence_wait_end(void *ignore, struct dma_fence *fence)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_FENCE, fence);
	TG_FDR_TAG(TG_FDR_SUBSYS_DISPLAY, "FENCE:WAIT_END", 0, obj,
		   (u64)(unsigned long)fence, 0, 0, 0, 0);
}

static void tg_fdr_tp_binder_transaction(void *ignore, bool reply,
					 struct binder_transaction *t,
					 struct binder_node *node)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_OTHER, t);
	TG_FDR_TAG(TG_FDR_SUBSYS_ANDROID, "BINDER:TRANSACTION", 0, obj,
		   reply, (u64)(unsigned long)t, (u64)(unsigned long)node, 0, 0);
}

static void tg_fdr_tp_binder_received(void *ignore,
				      struct binder_transaction *t)
{
	u32 obj = tg_fdr_object_id(TG_FDR_OBJ_OTHER, t);
	TG_FDR_TAG(TG_FDR_SUBSYS_ANDROID, "BINDER:RECEIVED", 0, obj,
		   (u64)(unsigned long)t, 0, 0, 0, 0);
}

struct tg_fdr_tp_spec {
	const char *name;
	void *probe;
	u16 flags;
};

static const struct tg_fdr_tp_spec tg_fdr_tp_specs[] = {
	{ "clk_enable", tg_fdr_tp_clk_enable, 0 },
	{ "clk_enable_complete", tg_fdr_tp_clk_enable_done, 0 },
	{ "clk_disable", tg_fdr_tp_clk_disable, 0 },
	{ "clk_prepare", tg_fdr_tp_clk_prepare, 0 },
	{ "clk_unprepare", tg_fdr_tp_clk_unprepare, 0 },
	{ "clk_set_rate", tg_fdr_tp_clk_rate, 0 },
	{ "clk_set_rate_complete", tg_fdr_tp_clk_rate, 0 },
	{ "regulator_enable", tg_fdr_tp_reg_enable, 0 },
	{ "regulator_enable_complete", tg_fdr_tp_reg_enable_done, 0 },
	{ "regulator_disable", tg_fdr_tp_reg_disable, 0 },
	{ "regulator_set_voltage", tg_fdr_tp_reg_range, 0 },
	{ "regulator_set_voltage_complete", tg_fdr_tp_reg_value, 0 },
	{ "rpm_suspend", tg_fdr_tp_rpm_suspend, 0 },
	{ "rpm_resume", tg_fdr_tp_rpm_resume, 0 },
	{ "rpm_idle", tg_fdr_tp_rpm_idle, 0 },
	{ "rpm_return_int", tg_fdr_tp_rpm_return, 0 },
	{ "device_pm_callback_start", tg_fdr_tp_dev_pm_start, 0 },
	{ "device_pm_callback_end", tg_fdr_tp_dev_pm_end, 0 },
	{ "suspend_resume", tg_fdr_tp_suspend_resume, 0 },
	{ "wakeup_source_activate", tg_fdr_tp_wakeup, 0 },
	{ "wakeup_source_deactivate", tg_fdr_tp_wakeup, 0 },
	{ "add_device_to_group", tg_fdr_tp_iommu_group, 0 },
	{ "remove_device_from_group", tg_fdr_tp_iommu_group, 0 },
	{ "attach_device_to_domain", tg_fdr_tp_iommu_attach, 0 },
	{ "detach_device_from_domain", tg_fdr_tp_iommu_detach, 0 },
	{ "map", tg_fdr_tp_iommu_map, 0 },
	{ "unmap", tg_fdr_tp_iommu_unmap, 0 },
	{ "map_sg", tg_fdr_tp_iommu_map_sg, 0 },
	{ "io_page_fault", tg_fdr_tp_iommu_fault, 0 },
	{ "tlbi_start", tg_fdr_tp_iommu_tlbi, 0 },
	{ "tlbi_end", tg_fdr_tp_iommu_tlbi, 0 },
	{ "tlbsync_timeout", tg_fdr_tp_iommu_tlbsync_timeout, 0 },
	{ "dma_fence_init", tg_fdr_tp_fence_init, 0 },
	{ "dma_fence_signaled", tg_fdr_tp_fence_signaled, 0 },
	{ "dma_fence_wait_start", tg_fdr_tp_fence_wait_start, 0 },
	{ "dma_fence_wait_end", tg_fdr_tp_fence_wait_end, 0 },
	{ "binder_transaction", tg_fdr_tp_binder_transaction, 0 },
	{ "binder_transaction_received", tg_fdr_tp_binder_received, 0 },
};

struct tg_fdr_tp_register_ctx {
	u32 found;
	u32 registered;
	u32 failed;
};

static void tg_fdr_register_one(struct tracepoint *tp, void *priv)
{
	struct tg_fdr_tp_register_ctx *ctx = priv;
	u32 i;

	for (i = 0; i < ARRAY_SIZE(tg_fdr_tp_specs); i++) {
		int ret;
		if (strcmp(tp->name, tg_fdr_tp_specs[i].name))
			continue;
		ctx->found++;
		ret = tracepoint_probe_register(tp, tg_fdr_tp_specs[i].probe, NULL);
		if (ret)
			ctx->failed++;
		else
			ctx->registered++;
		TG_FDR_TAG(TG_FDR_SUBSYS_META, "TP:REGISTER", ret, 0,
			   tg_fdr_hash_tag(tp->name), i, 0, 0,
			   ret ? TG_FDR_FLAG_CRITICAL : 0);
		break;
	}
}

static int __init tg_fdr_tracepoints_init(void)
{
	struct tg_fdr_tp_register_ctx ctx = { };

	for_each_kernel_tracepoint(tg_fdr_register_one, &ctx);
	TG_FDR_TAG(TG_FDR_SUBSYS_META, "TP:SUMMARY", ctx.failed ? -EINVAL : 0,
		   0, ctx.found, ctx.registered, ctx.failed,
		   ARRAY_SIZE(tg_fdr_tp_specs),
		   ctx.failed ? TG_FDR_FLAG_CRITICAL : 0);
	return 0;
}
early_initcall(tg_fdr_tracepoints_init);
'''


def main(root: Path):
    p = root / 'kernel/tg_fdr_tracepoints.c'
    p.write_text(SOURCE)
    mk = root / 'kernel/Makefile'
    text = mk.read_text()
    line = 'obj-y += tg_fdr_tracepoints.o\n'
    if line not in text:
        mk.write_text(text.rstrip() + '\n' + line)
    print('TouchGrass FDR tracepoint subscriber staged')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_definitive_fdr_tracepoints_overlay.py <kernel-root>')
    main(Path(sys.argv[1]).resolve())
