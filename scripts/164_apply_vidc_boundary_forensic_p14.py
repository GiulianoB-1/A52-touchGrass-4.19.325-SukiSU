#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"
platform = kernel / "drivers/base/platform.c"

for path in (dd, platform):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

dd_s = dd.read_text()
pl_s = platform.read_text()

dd_marker = "A52 P164: VIDC driver-core boundary forensic"
pl_marker = "A52 P164: VIDC platform wrapper forensic"

if dd_marker not in dd_s:
    globals_anchor = """static unsigned int a52_probe_census_used;
static unsigned int a52_probe_census_overflow;
"""
    globals_repl = r'''static unsigned int a52_probe_census_used;
static unsigned int a52_probe_census_overflow;

/*
 * A52 P164: VIDC driver-core boundary forensic.
 *
 * P163's in-driver recorder reached stage=100 rc=0 while the P161/P163
 * driver-core census still ended with msm_vidc_v4l2 FAIL ret=-EINVAL.
 * Capture the return value at each generic driver-core boundary so the next
 * boot can identify exactly where the successful callback becomes a failure.
 */
static unsigned int a52_p164_vidc_really_calls;
static unsigned int a52_p164_vidc_test_remove;
static int a52_p164_vidc_stage;
static int a52_p164_vidc_ret;

static bool a52_p164_is_vidc(struct device *dev, struct device_driver *drv)
{
	const char *vname = dev ? dev_name(dev) : NULL;

	return drv && drv->name && !strcmp(drv->name, "msm_vidc_v4l2") &&
	       vname && !strcmp(vname, "aa00000.qcom,vidc0");
}
'''
    if dd_s.count(globals_anchor) != 1:
        raise SystemExit(f"P164 dd globals anchor count={dd_s.count(globals_anchor)}")
    dd_s = dd_s.replace(globals_anchor, globals_repl, 1)

    entry_anchor = """	bool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&
			   !drv->suppress_bind_attrs;

	a52_probe_census_start(dev, drv);

"""
    entry_repl = """	bool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&
			   !drv->suppress_bind_attrs;
	bool a52_p164_vidc = a52_p164_is_vidc(dev, drv);

	if (a52_p164_vidc) {
		a52_p164_vidc_really_calls++;
		a52_p164_vidc_test_remove = test_remove ? 1 : 0;
		a52_p164_vidc_stage = 1; /* really_probe entry */
		a52_p164_vidc_ret = 0;
	}

	a52_probe_census_start(dev, drv);

"""
    if dd_s.count(entry_anchor) != 1:
        raise SystemExit(f"P164 dd entry anchor count={dd_s.count(entry_anchor)}")
    dd_s = dd_s.replace(entry_anchor, entry_repl, 1)

    suppliers_anchor = """	ret = device_links_check_suppliers(dev);
	if (ret == -EPROBE_DEFER)
"""
    suppliers_repl = """	ret = device_links_check_suppliers(dev);
	if (a52_p164_vidc) {
		a52_p164_vidc_stage = 2; /* suppliers checked */
		a52_p164_vidc_ret = ret;
	}
	if (ret == -EPROBE_DEFER)
"""
    if dd_s.count(suppliers_anchor) != 1:
        raise SystemExit(f"P164 suppliers anchor count={dd_s.count(suppliers_anchor)}")
    dd_s = dd_s.replace(suppliers_anchor, suppliers_repl, 1)

    pin_anchor = """	ret = pinctrl_bind_pins(dev);
	if (ret)
"""
    pin_repl = """	ret = pinctrl_bind_pins(dev);
	if (a52_p164_vidc) {
		a52_p164_vidc_stage = 3; /* pinctrl */
		a52_p164_vidc_ret = ret;
	}
	if (ret)
"""
    if dd_s.count(pin_anchor) != 1:
        raise SystemExit(f"P164 pinctrl anchor count={dd_s.count(pin_anchor)}")
    dd_s = dd_s.replace(pin_anchor, pin_repl, 1)

    dma_anchor = """	ret = dma_configure(dev);
	if (ret)
"""
    dma_repl = """	ret = dma_configure(dev);
	if (a52_p164_vidc) {
		a52_p164_vidc_stage = 4; /* dma_configure */
		a52_p164_vidc_ret = ret;
	}
	if (ret)
"""
    if dd_s.count(dma_anchor) != 1:
        raise SystemExit(f"P164 dma anchor count={dd_s.count(dma_anchor)}")
    dd_s = dd_s.replace(dma_anchor, dma_repl, 1)

    sysfs_anchor = r"""	if (driver_sysfs_add(dev)) {
		printk(KERN_ERR "%s: driver_sysfs_add(%s) failed\n",
"""
    sysfs_repl = r"""	if (driver_sysfs_add(dev)) {
		if (a52_p164_vidc)
			a52_p164_vidc_stage = 5; /* driver sysfs failed */
		printk(KERN_ERR "%s: driver_sysfs_add(%s) failed\n",
"""
    if dd_s.count(sysfs_anchor) != 1:
        raise SystemExit(f"P164 sysfs anchor count={dd_s.count(sysfs_anchor)}")
    dd_s = dd_s.replace(sysfs_anchor, sysfs_repl, 1)

    pm_anchor = """	if (dev->pm_domain && dev->pm_domain->activate) {
		ret = dev->pm_domain->activate(dev);
		if (ret)
"""
    pm_repl = """	if (dev->pm_domain && dev->pm_domain->activate) {
		ret = dev->pm_domain->activate(dev);
		if (a52_p164_vidc) {
			a52_p164_vidc_stage = 6; /* generic PM-domain activate */
			a52_p164_vidc_ret = ret;
		}
		if (ret)
"""
    if dd_s.count(pm_anchor) != 1:
        raise SystemExit(f"P164 pm anchor count={dd_s.count(pm_anchor)}")
    dd_s = dd_s.replace(pm_anchor, pm_repl, 1)

    bus_anchor = """	if (dev->bus->probe) {
		ret = dev->bus->probe(dev);
		if (ret)
			goto probe_failed;
"""
    bus_repl = """	if (dev->bus->probe) {
		if (a52_p164_vidc)
			a52_p164_vidc_stage = 7; /* entering platform-bus wrapper */
		ret = dev->bus->probe(dev);
		if (a52_p164_vidc) {
			a52_p164_vidc_stage = 8; /* platform-bus wrapper returned */
			a52_p164_vidc_ret = ret;
		}
		if (ret)
			goto probe_failed;
"""
    if dd_s.count(bus_anchor) != 1:
        raise SystemExit(f"P164 bus anchor count={dd_s.count(bus_anchor)}")
    dd_s = dd_s.replace(bus_anchor, bus_repl, 1)

    bound_anchor = """	driver_bound(dev);
	ret = 1;
	a52_probe_census_finish(dev, drv, A52_PC_OK, 0);
"""
    bound_repl = """	driver_bound(dev);
	ret = 1;
	if (a52_p164_vidc) {
		a52_p164_vidc_stage = 100; /* generic driver core bound it */
		a52_p164_vidc_ret = 0;
	}
	a52_probe_census_finish(dev, drv, A52_PC_OK, 0);
"""
    if dd_s.count(bound_anchor) != 1:
        raise SystemExit(f"P164 bound anchor count={dd_s.count(bound_anchor)}")
    dd_s = dd_s.replace(bound_anchor, bound_repl, 1)

    failed_anchor = """probe_failed:
	if (dev->bus)
"""
    failed_repl = """probe_failed:
	if (a52_p164_vidc) {
		a52_p164_vidc_stage = 90; /* generic probe_failed path */
		a52_p164_vidc_ret = ret;
	}
	if (dev->bus)
"""
    if dd_s.count(failed_anchor) != 1:
        raise SystemExit(f"P164 failed anchor count={dd_s.count(failed_anchor)}")
    dd_s = dd_s.replace(failed_anchor, failed_repl, 1)

    checkpoint_anchor = r'''	pr_emerg("A52_PROBE_CENSUS CHECKPOINT_END t_ms=%u ok=%u running=%u fail=%u defer=%u reject=%u overflow=%u probe_count=%d\\n",
		 elapsed_ms, ok, running, fail, defer, reject, overflow,
		 atomic_read(&probe_count));
'''
    checkpoint_repl = checkpoint_anchor + r'''
	if (elapsed_ms >= 10000)
		pr_emerg("A52_P164_VIDC_CORE t_ms=%u really_calls=%u test_remove=%u stage=%d ret=%d\n",
			 elapsed_ms, a52_p164_vidc_really_calls,
			 a52_p164_vidc_test_remove, a52_p164_vidc_stage,
			 a52_p164_vidc_ret);
'''
    if dd_s.count(checkpoint_anchor) != 1:
        raise SystemExit(f"P164 checkpoint anchor count={dd_s.count(checkpoint_anchor)}")
    dd_s = dd_s.replace(checkpoint_anchor, checkpoint_repl, 1)

if pl_marker not in pl_s:
    include_anchor = "#include <linux/platform_device.h>\n"
    if pl_s.count(include_anchor) != 1:
        raise SystemExit(f"P164 platform include anchor count={pl_s.count(include_anchor)}")
    pl_s = pl_s.replace(include_anchor, include_anchor + "#include <linux/workqueue.h>\n", 1)

    probe_anchor = """static int platform_drv_probe(struct device *_dev)
{
	struct platform_driver *drv = to_platform_driver(_dev->driver);
	struct platform_device *dev = to_platform_device(_dev);
	int ret;

"""
    probe_repl = r'''/*
 * A52 P164: VIDC platform wrapper forensic.
 * Persist each stage of platform_drv_probe() and print it at 10 seconds.
 */
static unsigned int a52_p164_plat_vidc_calls;
static int a52_p164_plat_vidc_clk_ret = 0x7fffffff;
static int a52_p164_plat_vidc_pm_ret = 0x7fffffff;
static int a52_p164_plat_vidc_inner_ret = 0x7fffffff;
static int a52_p164_plat_vidc_final_ret = 0x7fffffff;

static void a52_p164_plat_vidc_diag_workfn(struct work_struct *work)
{
	pr_emerg("A52_P164_PLATFORM_VIDC calls=%u clk=%d pm=%d inner=%d final=%d debug_test_remove=%d\n",
		 a52_p164_plat_vidc_calls, a52_p164_plat_vidc_clk_ret,
		 a52_p164_plat_vidc_pm_ret, a52_p164_plat_vidc_inner_ret,
		 a52_p164_plat_vidc_final_ret,
		 IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) ? 1 : 0);
}

static DECLARE_DELAYED_WORK(a52_p164_plat_vidc_diag_work,
			    a52_p164_plat_vidc_diag_workfn);

static bool a52_p164_platform_is_vidc(struct device *dev,
				      struct platform_driver *drv)
{
	const char *vname = dev ? dev_name(dev) : NULL;

	return drv && drv->driver.name &&
	       !strcmp(drv->driver.name, "msm_vidc_v4l2") &&
	       vname && !strcmp(vname, "aa00000.qcom,vidc0");
}

static int platform_drv_probe(struct device *_dev)
{
	struct platform_driver *drv = to_platform_driver(_dev->driver);
	struct platform_device *dev = to_platform_device(_dev);
	bool a52_p164_vidc = a52_p164_platform_is_vidc(_dev, drv);
	int ret;

	if (a52_p164_vidc) {
		a52_p164_plat_vidc_calls++;
		a52_p164_plat_vidc_clk_ret = 0x7fffffff;
		a52_p164_plat_vidc_pm_ret = 0x7fffffff;
		a52_p164_plat_vidc_inner_ret = 0x7fffffff;
		a52_p164_plat_vidc_final_ret = 0x7fffffff;
		if (a52_p164_plat_vidc_calls == 1)
			schedule_delayed_work(&a52_p164_plat_vidc_diag_work,
					      msecs_to_jiffies(10000));
	}

'''
    if pl_s.count(probe_anchor) != 1:
        raise SystemExit(f"P164 platform probe anchor count={pl_s.count(probe_anchor)}")
    pl_s = pl_s.replace(probe_anchor, probe_repl, 1)

    clk_anchor = """	ret = of_clk_set_defaults(_dev->of_node, false);
	if (ret < 0)
		return ret;

"""
    clk_repl = """	ret = of_clk_set_defaults(_dev->of_node, false);
	if (a52_p164_vidc)
		a52_p164_plat_vidc_clk_ret = ret;
	if (ret < 0) {
		if (a52_p164_vidc)
			a52_p164_plat_vidc_final_ret = ret;
		return ret;
	}

"""
    if pl_s.count(clk_anchor) != 1:
        raise SystemExit(f"P164 platform clk anchor count={pl_s.count(clk_anchor)}")
    pl_s = pl_s.replace(clk_anchor, clk_repl, 1)

    pm_anchor = """	ret = dev_pm_domain_attach(_dev, true);
	if (ret)
		goto out;

"""
    pm_repl = """	ret = dev_pm_domain_attach(_dev, true);
	if (a52_p164_vidc)
		a52_p164_plat_vidc_pm_ret = ret;
	if (ret)
		goto out;

"""
    if pl_s.count(pm_anchor) != 1:
        raise SystemExit(f"P164 platform pm anchor count={pl_s.count(pm_anchor)}")
    pl_s = pl_s.replace(pm_anchor, pm_repl, 1)

    inner_anchor = """	if (drv->probe) {
		ret = drv->probe(dev);
		if (ret)
"""
    inner_repl = """	if (drv->probe) {
		ret = drv->probe(dev);
		if (a52_p164_vidc)
			a52_p164_plat_vidc_inner_ret = ret;
		if (ret)
"""
    if pl_s.count(inner_anchor) != 1:
        raise SystemExit(f"P164 platform inner anchor count={pl_s.count(inner_anchor)}")
    pl_s = pl_s.replace(inner_anchor, inner_repl, 1)

    out_anchor = """out:
	if (drv->prevent_deferred_probe && ret == -EPROBE_DEFER) {
"""
    out_repl = """out:
	if (drv->prevent_deferred_probe && ret == -EPROBE_DEFER) {
"""
    # No structural change needed here; final_ret is recorded immediately before return.
    if pl_s.count(out_anchor) != 1:
        raise SystemExit(f"P164 platform out anchor count={pl_s.count(out_anchor)}")

    return_anchor = """	}

	return ret;
}

static int platform_drv_probe_fail"""
    return_repl = """	}

	if (a52_p164_vidc)
		a52_p164_plat_vidc_final_ret = ret;
	return ret;
}

static int platform_drv_probe_fail"""
    if pl_s.count(return_anchor) != 1:
        raise SystemExit(f"P164 platform return anchor count={pl_s.count(return_anchor)}")
    pl_s = pl_s.replace(return_anchor, return_repl, 1)

for token in (
    dd_marker,
    "A52_P164_VIDC_CORE",
    "a52_p164_vidc_stage = 90",
    "a52_p164_vidc_stage = 100",
):
    if token not in dd_s:
        raise SystemExit(f"P164 dd audit missing: {token}")

for token in (
    pl_marker,
    "A52_P164_PLATFORM_VIDC",
    "a52_p164_plat_vidc_inner_ret",
    "debug_test_remove=%d",
):
    if token not in pl_s:
        raise SystemExit(f"P164 platform audit missing: {token}")

dd.write_text(dd_s)
platform.write_text(pl_s)

print("A52 P164 VIDC boundary forensic applied")
print("  driver-core stages retained through 10/12s census")
print("  platform wrapper records clk/PM/inner/final return codes")
print("  no functional probe-order changes beyond retained P163 display safety")
