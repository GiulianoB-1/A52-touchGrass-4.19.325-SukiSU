#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"
vidc = kernel / "techpack/video/msm/vidc/msm_v4l2_vidc.c"

for path in (dd, vidc):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

dd_s = dd.read_text()
vidc_s = vidc.read_text()

display_marker = "A52 P163: keep critical display bring-up synchronous"
census_marker = "A52 P163: extend full async census to 12 seconds"
vidc_marker = "A52 P163 VIDC forensic stage recorder"

# 1) Display safety: keep the two top-level display drivers out of P153 async-default.
if display_marker not in dd_s:
    anchor = '''\t\t/* Main IOMMU provider. P158 keeps its TBU child race-safe. */
\t\t"arm-smmu",

\t\t/* Camera framework/provider chain. */
'''
    repl = '''\t\t/* Main IOMMU provider. P158 keeps its TBU child race-safe. */
\t\t"arm-smmu",

\t\t/*
\t\t * A52 P163: keep critical display bring-up synchronous.
\t\t *
\t\t * P162 was the first 12-second diagnostic boot and exposed a large
\t\t * visible panel artifact. Earlier 8-second runs could reboot before
\t\t * the corruption was obvious. The surviving ramoops window begins
\t\t * around 5 seconds, after initial DSI/SDE bring-up, so retain stock
\t\t * ordering for the display component and DRM master while verifying.
\t\t */
\t\t"msm-dsi-display",
\t\t"msm_drm",

\t\t/* Camera framework/provider chain. */
'''
    if dd_s.count(anchor) != 1:
        raise SystemExit(f"P163 display exception anchor count={dd_s.count(anchor)}")
    dd_s = dd_s.replace(anchor, repl, 1)

# 2) Keep the longer P162 observation window, but starting from the clean P161 base.
if census_marker not in dd_s:
    old = "#define A52_PROBE_CENSUS_FINAL_MS 8000"
    new = """/*
 * A52 P163: extend full async census to 12 seconds.
 *
 * P162 proved kona-asoc-snd naturally completes after the old 8-second
 * cutoff. Keep 2-second checkpoints through 12 seconds while investigating
 * VIDC and the visible display artifact.
 */
#define A52_PROBE_CENSUS_FINAL_MS 12000"""
    if dd_s.count(old) != 1:
        raise SystemExit(f"P163 final-census anchor count={dd_s.count(old)}")
    dd_s = dd_s.replace(old, new, 1)

# 3) VIDC forensic: retain the failed step and rc until a 10-second delayed print.
if vidc_marker not in vidc_s:
    include_anchor = '#include <linux/io.h>\n'
    if vidc_s.count(include_anchor) != 1:
        raise SystemExit(f"P163 VIDC include anchor count={vidc_s.count(include_anchor)}")
    vidc_s = vidc_s.replace(include_anchor,
        include_anchor + '#include <linux/workqueue.h>\n', 1)

    global_anchor = '''#define BASE_DEVICE_NUMBER 32

struct msm_vidc_drv *vidc_driver;


'''
    global_repl = r'''#define BASE_DEVICE_NUMBER 32

struct msm_vidc_drv *vidc_driver;

/*
 * A52 P163 VIDC forensic stage recorder.
 *
 * The standard ramoops console writer is intentionally released only after
 * five seconds by the boot-rescue logic, so the original VIDC probe error at
 * ~0.8s is not retained. Persist the last major probe stage in normal kernel
 * memory and print it at 10s, inside the surviving console window.
 */
static int a52_vidc_probe_stage;
static int a52_vidc_probe_rc;

static void a52_vidc_diag_workfn(struct work_struct *work)
{
	pr_emerg("A52_P163_VIDC_DIAG stage=%d rc=%d\n",
		 a52_vidc_probe_stage, a52_vidc_probe_rc);
}

static DECLARE_DELAYED_WORK(a52_vidc_diag_work, a52_vidc_diag_workfn);


'''
    if vidc_s.count(global_anchor) != 1:
        raise SystemExit(f"P163 VIDC global anchor count={vidc_s.count(global_anchor)}")
    vidc_s = vidc_s.replace(global_anchor, global_repl, 1)

    # Stage 1: entry and platform resources.
    old = '''static int msm_vidc_probe_vidc_device(struct platform_device *pdev)
{
\tint rc = 0;
\tstruct msm_vidc_core *core;
\tstruct device *dev = NULL;
\tint nr = BASE_DEVICE_NUMBER;

\tif (!vidc_driver) {
'''
    new = '''static int msm_vidc_probe_vidc_device(struct platform_device *pdev)
{
\tint rc = 0;
\tstruct msm_vidc_core *core;
\tstruct device *dev = NULL;
\tint nr = BASE_DEVICE_NUMBER;

\ta52_vidc_probe_stage = 1;
\ta52_vidc_probe_rc = 0;

\tif (!vidc_driver) {
\t\ta52_vidc_probe_rc = -EINVAL;
'''
    if vidc_s.count(old) != 1:
        raise SystemExit(f"P163 VIDC entry anchor count={vidc_s.count(old)}")
    vidc_s = vidc_s.replace(old, new, 1)

    replacements = [
        ('''\tcore->platform_data = vidc_get_drv_data(&pdev->dev);
\tdev_set_drvdata(&pdev->dev, core);
\trc = msm_vidc_initialize_core(pdev, core);
''',
         '''\tcore->platform_data = vidc_get_drv_data(&pdev->dev);
\tdev_set_drvdata(&pdev->dev, core);
\ta52_vidc_probe_stage = 2; /* platform resources / core init */
\trc = msm_vidc_initialize_core(pdev, core);
'''),
        ('''\trc = sysfs_create_group(&pdev->dev.kobj, &msm_vidc_core_attr_group);
''',
         '''\ta52_vidc_probe_stage = 3; /* sysfs */
\trc = sysfs_create_group(&pdev->dev.kobj, &msm_vidc_core_attr_group);
'''),
        ('''\trc = v4l2_device_register(&pdev->dev, &core->v4l2_dev);
''',
         '''\ta52_vidc_probe_stage = 4; /* v4l2 core */
\trc = v4l2_device_register(&pdev->dev, &core->v4l2_dev);
'''),
        ('''\trc = msm_vidc_register_video_device(MSM_VIDC_DECODER,
\t\t\tnr, core, dev);
''',
         '''\ta52_vidc_probe_stage = 5; /* decoder video node */
\trc = msm_vidc_register_video_device(MSM_VIDC_DECODER,
\t\t\tnr, core, dev);
'''),
        ('''\trc = msm_vidc_register_video_device(MSM_VIDC_ENCODER,
\t\t\tnr + 1, core, dev);
''',
         '''\ta52_vidc_probe_stage = 6; /* encoder video node */
\trc = msm_vidc_register_video_device(MSM_VIDC_ENCODER,
\t\t\tnr + 1, core, dev);
'''),
        ('''\tif (core->resources.cvp_internal) {
\t\trc = msm_vidc_register_video_device(MSM_VIDC_CVP,
''',
         '''\tif (core->resources.cvp_internal) {
\t\ta52_vidc_probe_stage = 7; /* CVP video node */
\t\trc = msm_vidc_register_video_device(MSM_VIDC_CVP,
'''),
        ('''\t/* finish setting up the 'core' */
\tmutex_lock(&vidc_driver->lock);
''',
         '''\t/* finish setting up the 'core' */
\ta52_vidc_probe_stage = 8; /* core-count admission */
\tmutex_lock(&vidc_driver->lock);
'''),
        ('''\tcore->device = vidc_hfi_initialize(core->hfi_type, core->id,
''',
         '''\ta52_vidc_probe_stage = 9; /* HFI creation */
\tcore->device = vidc_hfi_initialize(core->hfi_type, core->id,
'''),
        ('''\tcore->vidc_core_workq = create_singlethread_workqueue(
''',
         '''\ta52_vidc_probe_stage = 10; /* core workqueue */
\tcore->vidc_core_workq = create_singlethread_workqueue(
'''),
        ('''\trc = of_platform_populate(pdev->dev.of_node, msm_vidc_dt_match, NULL,
''',
         '''\ta52_vidc_probe_stage = 11; /* context-bank/bus children */
\trc = of_platform_populate(pdev->dev.of_node, msm_vidc_dt_match, NULL,
'''),
        ('''\treturn rc;

err_fail_sub_device_probe:
''',
         '''\ta52_vidc_probe_stage = 100; /* success */
\ta52_vidc_probe_rc = 0;
\treturn rc;

err_fail_sub_device_probe:
'''),
        ('''\tdev_set_drvdata(&pdev->dev, NULL);
\tkfree(core);
\treturn rc;
}
''',
         '''\tdev_set_drvdata(&pdev->dev, NULL);
\tkfree(core);
\ta52_vidc_probe_rc = rc;
\treturn rc;
}
'''),
    ]
    for old, new in replacements:
        if vidc_s.count(old) != 1:
            raise SystemExit(f"P163 VIDC stage anchor count={vidc_s.count(old)} for {old[:60]!r}")
        vidc_s = vidc_s.replace(old, new, 1)

    # Schedule the persistent diagnostic after platform driver registration.
    init_anchor = '''\trc = platform_driver_register(&msm_vidc_driver);
\tif (rc) {
'''
    init_repl = '''\trc = platform_driver_register(&msm_vidc_driver);
\tif (!rc)
\t\tschedule_delayed_work(&a52_vidc_diag_work,
\t\t\tmsecs_to_jiffies(10000));

\tif (rc) {
'''
    if vidc_s.count(init_anchor) != 1:
        raise SystemExit(f"P163 VIDC init anchor count={vidc_s.count(init_anchor)}")
    vidc_s = vidc_s.replace(init_anchor, init_repl, 1)

    exit_anchor = '''static void __exit msm_vidc_exit(void)
{
\tplatform_driver_unregister(&msm_vidc_driver);
'''
    exit_repl = '''static void __exit msm_vidc_exit(void)
{
\tcancel_delayed_work_sync(&a52_vidc_diag_work);
\tplatform_driver_unregister(&msm_vidc_driver);
'''
    if vidc_s.count(exit_anchor) != 1:
        raise SystemExit(f"P163 VIDC exit anchor count={vidc_s.count(exit_anchor)}")
    vidc_s = vidc_s.replace(exit_anchor, exit_repl, 1)

for token in (
    display_marker,
    '"msm-dsi-display"',
    '"msm_drm"',
    census_marker,
    "#define A52_PROBE_CENSUS_FINAL_MS 12000",
):
    if token not in dd_s:
        raise SystemExit(f"P163 dd.c audit missing: {token}")

for token in (
    vidc_marker,
    "A52_P163_VIDC_DIAG stage=%d rc=%d",
    "a52_vidc_probe_stage = 2;",
    "a52_vidc_probe_stage = 9;",
    "a52_vidc_probe_stage = 11;",
    "a52_vidc_probe_stage = 100;",
    "schedule_delayed_work(&a52_vidc_diag_work",
):
    if token not in vidc_s:
        raise SystemExit(f"P163 VIDC audit missing: {token}")

if "A52 P162: restore synchronous VIDC probe ordering" in vidc_s:
    raise SystemExit("P163 must start from P161, not retain P162 VIDC sync")

dd.write_text(dd_s)
vidc.write_text(vidc_s)

print("A52 P163 display safety + VIDC forensic applied")
print("  sync display: msm-dsi-display, msm_drm")
print("  VIDC remains under P153 global async-default")
print("  VIDC stage/rc reprinted at 10 seconds")
print("  census: 2048 entries, 2/4/6/8/10/12 seconds")
