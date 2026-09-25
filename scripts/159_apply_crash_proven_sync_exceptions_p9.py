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
marker = "A52 P159: crash-proven synchronous provider exceptions"
if marker in s:
    print("A52 P159 sync exception policy already applied")
    raise SystemExit(0)

anchor = """bool driver_allows_async_probing(struct device_driver *drv)
{
	switch (drv->probe_type) {
"""
if s.count(anchor) != 1:
    raise SystemExit(f"driver_allows_async_probing anchor count={s.count(anchor)}")

helper = r'''/*
 * A52 P159: crash-proven synchronous provider exceptions.
 *
 * P158 proved the direct QSMMUv500 TBU NULL-drvdata race can be fixed
 * without disabling global async probing. Its hardware capture then exposed
 * two remaining classes of unsafe ordering:
 *
 *  1. arm-smmu itself faulted while probing from async_run_entry_fn.
 *  2. downstream camera consumers entered before shared camera providers
 *     (camera SMMU / CPAS / resource manager) were ready, causing Oopses in
 *     cam_smmu_get_handle() and cam_res_mgr_led_trigger_register().
 *
 * Keep those provider-heavy chains synchronous and leave every other
 * ordinary driver under P153's async-default policy.
 */
static bool a52_p159_force_sync(const struct device_driver *drv)
{
	static const char * const sync_drivers[] = {
		/* Main IOMMU provider. P158 keeps its TBU child race-safe. */
		"arm-smmu",

		/* Camera framework/provider chain. */
		"cam_req_mgr",
		"cam_sync",
		"cam_res_mgr",
		"cam_cpas",
		"cam-cpas",
		"msm_cam_smmu",
		"msm_cam_cdm_intf",
		"msm_cam_cdm",
		"cam-cci-driver",
		"cam-csiphy-driver",
		"qcom,actuator",
		"qcom,camera",
		"qcom,eeprom",
		"qcom,ois",
		"CAM-FLASH-DRIVER",
		"cam-a5",
		"cam-ipe",
		"cam-bps",
		"cam_icp",
		"cam-jpeg-enc",
		"cam-jpeg-dma",
		"cam_jpeg",
		"cam_fd_hw",
		"cam_fd",
		"cam_lrme_hw",
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
	if (a52_p159_force_sync(drv))
		return false;

	switch (drv->probe_type) {
'''
s = s.replace(anchor, helper, 1)

for token in (
    marker,
    "A52 P153: default all ordinary initial driver probing asynchronous",
    "if (a52_p159_force_sync(drv))",
    '"arm-smmu"',
    '"msm_cam_smmu"',
    '"cam_res_mgr"',
    '"cam_cpas"',
    '"msm_cam_cdm"',
    '"CAM-FLASH-DRIVER"',
    '"cam_icp"',
):
    if token not in s:
        raise SystemExit(f"P159 audit missing: {token}")

dd.write_text(s)

print("A52 P159 crash-proven sync exceptions applied")
print("  sync: arm-smmu")
print("  sync: downstream camera provider/consumer chain")
print("  qsmmuv500-tbu remains async and protected by P158 readiness guard")
print("  all unrelated ordinary drivers remain P153 async-default")
