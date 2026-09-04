// SPDX-License-Identifier: GPL-2.0-only
#include <linux/atomic.h>
#include <linux/device.h>
#include <linux/device/driver.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/jiffies.h>
#include <linux/of.h>
#include <linux/of_device.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/workqueue.h>
#include <linux/a52_ack_secure_flight_recorder.h>

/* Existing phase-177 helper. Phase 180 invokes it only on three display nodes. */
extern void a52_device_links_force_probe(struct device *dev,
					 unsigned int *kept,
					 unsigned int *dropped);

struct a52_bind_target {
	const char *tag;
	const char *compatible;
	const char *driver;
	bool retry;
};

static const struct a52_bind_target targets[] = {
	{ "sde", "qcom,sde-kms", "msm_drm", true },
	{ "dsi", "qcom,dsi-display", "msm-dsi-display", true },
	{ "ctrl", "qcom,dsi-ctrl-hw-v2.4", "drm_dsi_ctrl", true },
	{ "phy", "qcom,dsi-phy-v3.0", "dsi_phy", false },
};

/* Probe dependency order: controller, display aggregator, then SDE/DRM. */
static const unsigned int retry_order[] = { 2, 1, 0 };

static const char *bound_driver(const struct platform_device *pdev)
{
	return pdev && pdev->dev.driver && pdev->dev.driver->name ?
		pdev->dev.driver->name : "-";
}

struct a52_driver_match_ctx {
	const struct a52_bind_target *target;
	struct platform_device *pdev;
	unsigned int pass;
	int found;
	int match;
};

static int target_driver_match_cb(struct device_driver *driver, void *data)
{
	struct a52_driver_match_ctx *ctx = data;

	if (!driver || !driver->name ||
	    strcmp(driver->name, ctx->target->driver))
		return 0;

	ctx->found = 1;
	ctx->match = of_driver_match_device(&ctx->pdev->dev, driver);
	a52_ackfr_record("DISP CORE p=%u c=%s drv=%s found=1 match=%d bound=%s",
		ctx->pass, ctx->target->tag, driver->name, ctx->match,
		bound_driver(ctx->pdev));
	return 1;
}

static int target_driver_match(const struct a52_bind_target *target,
			       struct platform_device *pdev,
			       unsigned int pass)
{
	struct a52_driver_match_ctx ctx = {
		.target = target,
		.pdev = pdev,
		.pass = pass,
		.match = -ENOENT,
	};

	bus_for_each_drv(&platform_bus_type, NULL, &ctx,
		target_driver_match_cb);
	if (!ctx.found)
		a52_ackfr_record("DISP CORE p=%u c=%s drv=%s found=0 match=%d bound=%s",
			pass, target->tag, target->driver, ctx.match,
			bound_driver(pdev));

	return ctx.match;
}

static void audit_compat(const struct a52_bind_target *target,
			 unsigned int pass)
{
	struct device_node *node = NULL;
	unsigned int index = 0;

	for_each_compatible_node(node, NULL, target->compatible) {
		struct platform_device *pdev = of_find_device_by_node(node);

		a52_ackfr_record("DISP bind p=%u c=%s n=%u av=%u pdev=%u drv=%s",
			pass, target->tag, index, of_device_is_available(node),
			!!pdev, bound_driver(pdev));
		if (pdev) {
			target_driver_match(target, pdev, pass);
			put_device(&pdev->dev);
		}
		index++;
	}
	if (!index)
		a52_ackfr_record("DISP bind p=%u c=%s nodes=0", pass, target->tag);
}

static void audit_all(unsigned int pass)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(targets); i++)
		audit_compat(&targets[i], pass);
}

static void retry_compat(const struct a52_bind_target *target,
			 unsigned int pass, bool force_links)
{
	struct device_node *node = NULL;
	unsigned int index = 0;

	for_each_compatible_node(node, NULL, target->compatible) {
		struct platform_device *pdev = of_find_device_by_node(node);
		unsigned int kept = 0, dropped = 0;
		int match = -ENOENT;
		int rc = -ENODEV;

		if (!pdev) {
			a52_ackfr_record("DISP RETRY p=%u c=%s n=%u force=%u pdev=0 rc=%d",
				pass, target->tag, index, force_links, rc);
			index++;
			continue;
		}

		match = target_driver_match(target, pdev, pass);
		if (!pdev->dev.driver && match > 0) {
			if (force_links)
				a52_device_links_force_probe(&pdev->dev, &kept, &dropped);
			rc = device_attach(&pdev->dev);
		} else if (pdev->dev.driver) {
			rc = 1;
		} else {
			rc = 0;
		}

		a52_ackfr_record("DISP RETRY p=%u c=%s n=%u force=%u match=%d kept=%u drop=%u rc=%d drv=%s",
			pass, target->tag, index, force_links, match, kept, dropped,
			rc, bound_driver(pdev));
		put_device(&pdev->dev);
		index++;
	}
}

static void retry_all(unsigned int pass, bool force_links)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(retry_order); i++)
		retry_compat(&targets[retry_order[i]], pass, force_links);
}

static atomic_t pass_count = ATOMIC_INIT(0);
static void audit_workfn(struct work_struct *unused);
static DECLARE_DELAYED_WORK(audit_work, audit_workfn);

static void audit_workfn(struct work_struct *unused)
{
	unsigned int pass = (unsigned int)atomic_inc_return(&pass_count);

	audit_all(pass);
	/* First retry is normal. Second retry removes only unresolved managed links. */
	if (pass == 1)
		retry_all(pass, false);
	else if (pass == 2)
		retry_all(pass, true);
	audit_all(pass + 100);

	if (pass < 4)
		schedule_delayed_work(&audit_work,
			msecs_to_jiffies(pass == 1 ? 2000 : pass == 2 ? 8000 : 20000));
}

static int __init a52_display_bind_audit_init(void)
{
	a52_ackfr_record("DISP CORE phase=180 audit=start retry=normal,force");
	audit_all(0);
	schedule_delayed_work(&audit_work, msecs_to_jiffies(500));
	return 0;
}
late_initcall(a52_display_bind_audit_init);
