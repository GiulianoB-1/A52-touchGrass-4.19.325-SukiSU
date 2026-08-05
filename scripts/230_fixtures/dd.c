/* fixture helper */
static bool a52_smmu_unsec_trace_dev(const struct device *dev)
{
	return dev && dev->of_node &&
		of_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec");
}

/* fixture deferred retry */
		dev = private->device;
		list_del_init(&private->deferred_probe);

/* fixture deferred add */
	mutex_lock(&deferred_probe_mutex);
	if (list_empty(&dev->p->deferred_probe)) {

/* fixture deferred del */
void driver_deferred_probe_del(struct device *dev)
{
	mutex_lock(&deferred_probe_mutex);

/* fixture really probe */
static int really_probe(struct device *dev, struct device_driver *drv)
{
	int ret = -EPROBE_DEFER;
	int local_trigger_count = atomic_read(&deferred_trigger_count);
	bool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&
			   !drv->suppress_bind_attrs;

	ret = device_links_check_suppliers(dev);

	if (ret == -EPROBE_DEFER)
		driver_deferred_probe_add_trigger(dev, local_trigger_count);
	if (ret)
		return ret;

	ret = pinctrl_bind_pins(dev);

		ret = dev->bus->dma_configure(dev);

	ret = driver_sysfs_add(dev);
	if (a52_rscc_probe_device(dev))

		ret = dev->pm_domain->activate(dev);

		ret = dev->bus->probe(dev);

		ret = drv->probe(dev);

/* fixture driver probe */
int driver_probe_device(struct device_driver *drv, struct device *dev)
{
	int ret = 0;

	if (a52_rscc_probe_device(dev))
		a52_ackfr_record("RSCCCORE driver-probe exit dev=%s rc=%d bound=%s",
			dev_name(dev), ret, dev->driver && dev->driver->name ?
			dev->driver->name : "-");
	return ret;
}

static inline bool cmdline_requested_async_probing

/* fixture device attach */
	ret = driver_match_device(drv, dev);
	if (a52_smmu_unsec_trace_dev(dev) &&

	if (a52_smmu_unsec_trace_dev(dev))
		a52_ackfr_record("DCORE match probe drv=%s async=%d",
			drv->name, async_allowed);
	return driver_probe_device(drv, dev);
}

/* fixture driver attach */
	ret = driver_match_device(drv, dev);
	if (a52_rscc_probe_device(dev))

	device_driver_attach(drv, dev);

	return 0;
}

int driver_attach(struct device_driver *drv)
{
	return bus_for_each_dev(drv->bus, NULL, drv, __driver_attach);
}
