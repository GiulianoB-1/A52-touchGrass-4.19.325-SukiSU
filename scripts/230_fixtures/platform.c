/* fixture gap */
#include <linux/acpi.h>
#include <linux/clk/clk-conf.h>
#include <linux/limits.h>
#include <linux/property.h>
#include <linux/kmemleak.h>
#include <linux/types.h>
#include <linux/a52_ack_secure_flight_recorder.h>

#include "base.h"
#include "power/power.h"

/* For automatically allocated device IDs */
static DEFINE_IDA(platform_devid_ida);

/* fixture gap */
			pdev->name);
	return 0;
}

static const struct platform_device_id *platform_match_id(
			const struct platform_device_id *id,
			struct platform_device *pdev)
{
	while (id->name[0]) {
		if (strcmp(pdev->name, id->name) == 0) {
			pdev->id_entry = id;
			return id;
		}
		id++;
	}
	return NULL;
}

/**
 * platform_match - bind platform device to platform driver.
 * @dev: device.
 * @drv: driver.
 *
 * Platform device IDs are assumed to be encoded like this:
 * "<name><instance>", where <name> is a short description of the type of
 * device, like "pci" or "floppy", and <instance> is the enumerated
 * instance of the device, like '0' or '42'.  Driver IDs are simply
 * "<name>".  So, extract the <name> from the platform_device structure,
 * and compare it against the name of the driver. Return whether they match
 * or not.
 */
static int platform_match(struct device *dev, struct device_driver *drv)
{
	struct platform_device *pdev = to_platform_device(dev);
	struct platform_driver *pdrv = to_platform_driver(drv);
	int ret;

	/* When driver_override is set, only bind to the matching driver */
	if (pdev->driver_override)
		ret = !strcmp(pdev->driver_override, drv->name);
	else if (of_driver_match_device(dev, drv))
		ret = 1;
	else if (acpi_driver_match_device(dev, drv))
		ret = 1;
	else if (pdrv->id_table)
		ret = platform_match_id(pdrv->id_table, pdev) != NULL;
	else
		ret = strcmp(pdev->name, drv->name) == 0;

	if (dev->of_node &&
	    of_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec") &&
	    !strcmp(drv->name, "msmdrm_smmu"))
		a52_ackfr_record("DCORE platform-match drv=%s rc=%d",
			drv->name, ret);
	return ret;
}

#ifdef CONFIG_PM_SLEEP

static int platform_legacy_suspend(struct device *dev, pm_message_t mesg)
{
	struct platform_driver *pdrv = to_platform_driver(dev->driver);
	struct platform_device *pdev = to_platform_device(dev);
	int ret = 0;

	if (dev->driver && pdrv->suspend)
