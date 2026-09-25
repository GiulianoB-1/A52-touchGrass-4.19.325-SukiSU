#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
core = kernel / "drivers/usb/dwc3/core.c"
msm = kernel / "drivers/usb/dwc3/dwc3-msm.c"

for path in (core, msm):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

core_s = core.read_text()
msm_s = msm.read_text()

core_marker = "A52 P160: DWC3 child must complete synchronously for MSM glue"
msm_marker = "A52 P160: DWC3 child drvdata readiness guard"

if core_marker not in core_s:
    old = r'''	.driver		= {
		.name	= "dwc3",
		.of_match_table	= of_match_ptr(of_dwc3_match),
'''
    new = r'''	.driver		= {
		.name	= "dwc3",
		/*
		 * A52 P160: DWC3 child must complete synchronously for MSM glue.
		 *
		 * dwc3_msm_probe() calls of_platform_populate() and immediately
		 * consumes the child's drvdata.  P153's global async-default policy
		 * violates that downstream parent/child contract unless the core
		 * driver is explicitly synchronous.
		 */
		.probe_type = PROBE_FORCE_SYNCHRONOUS,
		.of_match_table	= of_match_ptr(of_dwc3_match),
'''
    if core_s.count(old) != 1:
        raise SystemExit(f"dwc3 core driver anchor count={core_s.count(old)}")
    core_s = core_s.replace(old, new, 1)

if msm_marker not in msm_s:
    old_find = r'''	mdwc->dwc3 = of_find_device_by_node(dwc3_node);
	of_node_put(dwc3_node);
	if (!mdwc->dwc3) {
		dev_err(&pdev->dev, "failed to get dwc3 platform device\n");
		goto put_dwc3;
	}
'''
    new_find = r'''	mdwc->dwc3 = of_find_device_by_node(dwc3_node);
	of_node_put(dwc3_node);
	if (!mdwc->dwc3) {
		/*
		 * A52 P160: DWC3 child drvdata readiness guard.
		 *
		 * Do not return a stale/zero status if the freshly populated child
		 * is not visible yet.  Make the parent retry through normal deferred
		 * probing instead.
		 */
		dev_dbg(&pdev->dev,
			"A52 P160: DWC3 child platform device not ready, defer parent\n");
		ret = -EPROBE_DEFER;
		goto put_dwc3;
	}
'''
    if msm_s.count(old_find) != 1:
        raise SystemExit(f"dwc3-msm child-device anchor count={msm_s.count(old_find)}")
    msm_s = msm_s.replace(old_find, new_find, 1)

    old_drvdata = r'''	dwc = platform_get_drvdata(mdwc->dwc3);
	if (!dwc) {
		dev_err(&pdev->dev, "Failed to get dwc3 device\n");
		goto put_dwc3;
	}
'''
    new_drvdata = r'''	dwc = platform_get_drvdata(mdwc->dwc3);
	if (!dwc) {
		/*
		 * A52 P160: DWC3 child drvdata readiness guard.
		 *
		 * With global asynchronous probing the child platform device can
		 * exist before dwc3_probe() has published drvdata.  Historically
		 * this path returned whatever value happened to remain in ret;
		 * on A52 P159 that was -EINVAL from the optional default-bus-vote
		 * DT read, producing the lone census FAIL for msm-dwc3.
		 *
		 * The core DWC3 driver is forced synchronous above.  If that child
		 * legitimately defers, propagate dependency deferral to the MSM
		 * parent instead of converting it into a permanent -EINVAL failure.
		 */
		dev_dbg(&pdev->dev,
			"A52 P160: DWC3 child drvdata not ready, defer parent\n");
		ret = -EPROBE_DEFER;
		goto put_dwc3;
	}
'''
    if msm_s.count(old_drvdata) != 1:
        raise SystemExit(f"dwc3-msm drvdata anchor count={msm_s.count(old_drvdata)}")
    msm_s = msm_s.replace(old_drvdata, new_drvdata, 1)

for token in (
    core_marker,
    ".probe_type = PROBE_FORCE_SYNCHRONOUS",
):
    if token not in core_s:
        raise SystemExit(f"P160 core audit missing: {token}")

for token in (
    msm_marker,
    "A52 P160: DWC3 child platform device not ready, defer parent",
    "A52 P160: DWC3 child drvdata not ready, defer parent",
    "ret = -EPROBE_DEFER;",
):
    if token not in msm_s:
        raise SystemExit(f"P160 msm audit missing: {token}")

core.write_text(core_s)
msm.write_text(msm_s)

print("A52 P160 DWC3 async parent/child contract fix applied")
print("  dwc3 core: PROBE_FORCE_SYNCHRONOUS")
print("  msm-dwc3 parent: remains under P153 async-default policy")
print("  missing child/device drvdata: propagate -EPROBE_DEFER")
print("  removes P159 stale-ret -EINVAL failure mode")
