/* SPDX-License-Identifier: GPL-2.0-only */
/* P458 SCM-on-demand: DT /firmware/scm stays disabled at boot.
 * The platform_device is manually created here, once, only after a
 * root-only sysfs write. Never patch OF status to "okay": dynamic OF
 * notifier auto-population could create a second SCM device.
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/errno.h>
#include <linux/kobject.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/qcom_scm.h>
#include <linux/string.h>
#include <linux/sysfs.h>

#define A52_P458_SCM_RUNTIME_ONLY_V1 1
static DEFINE_MUTEX(a52_scm_knob_lock);
static bool a52_scm_attempted;
static struct kobject *a52_dsp_kobj;
/* This is the ONLY path to a new SCM platform_device in P458. */
static int a52_p458_create_scm_once(void)
{
	struct device_node *np;
	struct platform_device *pdev;
	int ret;

	if (!of_machine_is_compatible("qcom,lagoon"))
		return -EPERM;
	if (qcom_scm_is_available())
		return -EALREADY;
	np = of_find_node_by_path("/firmware/scm");
	if (!np)
		return -ENODEV;
	if (of_device_is_available(np) ||
	    !of_device_is_compatible(np, "qcom,scm")) {
		of_node_put(np);
		return -EINVAL;
	}
	/* of_platform_device_create*() intentionally rejects disabled
	 * OF nodes. Allocate a platform device explicitly, with the
	 * otherwise inert SCM node as its OF match information.
	 * Never change the live OF node's disabled status.
	 */
	pdev = platform_device_alloc("firmware:scm", PLATFORM_DEVID_NONE);
	if (!pdev) {
		of_node_put(np);
		return -ENOMEM;
	}
	pdev->dev.of_node = of_node_get(np);
	ret = platform_device_add(pdev);
	if (ret) {
		platform_device_put(pdev);
		of_node_put(np);
		return ret;
	}
	/* One-shot permanent device. Keep its OF node reference for the
	 * lifetime of the machine. No unload/remove on this built-in path.
	 */
	of_node_put(np);
	return 0;
}

static ssize_t scm_enable_store(struct kobject *kobj,
				struct kobj_attribute *attr,
				const char *buf, size_t count)
{
	int ret;
	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	mutex_lock(&a52_scm_knob_lock);
	if (a52_scm_attempted) {
		mutex_unlock(&a52_scm_knob_lock);
		return -EALREADY;
	}
	a52_scm_attempted = true; /* fail-closed: never retry after failure */
	ret = a52_p458_create_scm_once();
	pr_warn("P458 SCM RUNTIME_ENABLE rc=%d available=%d once=1\n",
		ret, qcom_scm_is_available());
	mutex_unlock(&a52_scm_knob_lock);
	return ret ? ret : count;
}
static struct kobj_attribute a52_scm_enable_attr =
	__ATTR(scm_enable, 0200, NULL, scm_enable_store);

static int __init a52_p458_scm_knob_init(void)
{
	int ret;
	if (!of_machine_is_compatible("qcom,lagoon"))
		return 0;
	/* Boot must look exactly like P457. No device creation here. */
	if (qcom_scm_is_available()) {
		pr_err("P458 SCM BOOT_UNSAFE provider present before manual opt-in\n");
		return -EBUSY;
	}
	a52_dsp_kobj = kobject_create_and_add("a52_dsp", kernel_kobj);
	if (!a52_dsp_kobj)
		return -ENOMEM;
	ret = sysfs_create_file(a52_dsp_kobj, &a52_scm_enable_attr.attr);
	if (ret) {
		kobject_put(a52_dsp_kobj);
		a52_dsp_kobj = NULL;
		return ret;
	}
	pr_info("P458 SCM DORMANT status=disabled manual=1 sysfs=/sys/kernel/a52_dsp/scm_enable\n");
	return 0;
}
late_initcall(a52_p458_scm_knob_init);
