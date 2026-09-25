#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"
if not dd.is_file():
    raise SystemExit(f"missing required source: {dd}")

s = dd.read_text()
marker = "A52 P157: census-derived synchronous exception set"
if marker in s:
    print("A52 P157 sync exception policy already applied")
    raise SystemExit(0)

include_anchor = "#include <linux/pinctrl/devinfo.h>\n"
if include_anchor not in s:
    raise SystemExit("dd.c include anchor missing")
if "#include <linux/string.h>\n" not in s:
    s = s.replace(include_anchor, include_anchor + "#include <linux/string.h>\n", 1)

func_anchor = """bool driver_allows_async_probing(struct device_driver *drv)
{
	switch (drv->probe_type) {
"""
if s.count(func_anchor) != 1:
    raise SystemExit(f"driver_allows_async_probing anchor count={s.count(func_anchor)}")

helper = r'''/*
 * A52 P157: census-derived synchronous exception set.
 *
 * P156 proved that global async probing is viable, but also exposed a small
 * set of downstream drivers whose probe ordering is an implicit ABI.  Keep
 * only those drivers synchronous; all other ordinary drivers retain P153's
 * async-default policy.
 *
 * Sources:
 *  - P156 8s census persistent set: subsys PIL, KGSL, ADC-TM, SM5714,
 *    FTS touch, DSI display and camera CDM; msm-dwc3 returned -EINVAL.
 *  - Earlier global-async runs also exposed SMMU/UFS/SDHCI ordering risk.
 *  - P156 hard Oops traces: cam_hw_cdm_probe -> cam_smmu_get_handle and
 *    cam_icp_probe -> cam_hfi_mgr_init while camera providers were incomplete.
 *  - P153 rescue test: PMIC power-key input was not guaranteed ready early.
 */
static bool a52_probe_force_sync(const struct device_driver *drv)
{
	static const char * const sync_drivers[] = {
		/* Critical boot/resource providers. */
		"arm-smmu",
		"subsys-pil-tz",
		"ufshcd-qcom",
		"kgsl-3d",
		"sdhci-msm",
		"qcom,adc-tm",
		"sm5714",
		"fts_touch",
		"msm-dsi-display",
		"msm-dwc3",

		/* Camera ordering chain exposed by P156 Oopses/census. */
		"msm_cam_smmu",
		"cam_cpas",
		"cam-cpas",
		"msm_cam_cdm",
		"msm_cam_cdm_intf",
		"cam_icp",
		"qcom,camera",
		"cam-cci-driver",

		/* Keep the early recovery-chord input path deterministic. */
		"qpnp-power-on",
		"qpnp_pon",
	};
	unsigned int i;

	if (!drv || !drv->name)
		return false;

	for (i = 0; i < sizeof(sync_drivers) / sizeof(sync_drivers[0]); i++)
		if (!strcmp(drv->name, sync_drivers[i]))
			return true;

	return false;
}

bool driver_allows_async_probing(struct device_driver *drv)
{
	if (a52_probe_force_sync(drv))
		return false;

	switch (drv->probe_type) {
'''
s = s.replace(func_anchor, helper, 1)

# P153 must still be present below our exception gate.
for token in (
    marker,
    "A52 P153: default all ordinary initial driver probing asynchronous",
    "case PROBE_PREFER_ASYNCHRONOUS:",
    "case PROBE_FORCE_SYNCHRONOUS:",
    "if (a52_probe_force_sync(drv))",
    '"arm-smmu"',
    '"ufshcd-qcom"',
    '"kgsl-3d"',
    '"sm5714"',
    '"fts_touch"',
    '"msm-dsi-display"',
    '"msm-dwc3"',
    '"msm_cam_smmu"',
    '"msm_cam_cdm"',
    '"cam_icp"',
    '"qpnp-power-on"',
):
    if token not in s:
        raise SystemExit(f"P157 audit missing: {token}")

dd.write_text(s)

print("A52 P157 census-derived synchronous exception set applied")
print("  critical sync: SMMU/UFS/KGSL/SDHCI/ADC-TM/SM5714/FTS/display/USB/subsys PIL")
print("  camera sync: SMMU/CPAS/CDM/ICP/CCI/sensor ordering chain")
print("  rescue sync: QPNP power-key naming variants")
print("  every other ordinary driver remains P153 async-default")
