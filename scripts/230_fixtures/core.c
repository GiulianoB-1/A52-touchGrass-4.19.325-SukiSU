/* fixture gap */

/* Device links support. */
static LIST_HEAD(deferred_sync);
static unsigned int defer_sync_state_count = 1;
static DEFINE_MUTEX(fwnode_link_lock);
static struct workqueue_struct *device_link_wq;
static bool fw_devlink_is_permissive(void);

static bool a52_smmu_unsec_trace_dev(const struct device *dev)
{
	return dev && dev->of_node &&
		of_device_is_compatible(dev->of_node, "qcom,smmu_sde_unsec");
}


/**
 * fwnode_link_add - Create a link between two fwnode_handles.
 * @con: Consumer end of the link.
 * @sup: Supplier end of the link.
 *
 * Create a fwnode link between fwnode handles @con and @sup. The fwnode link
 * represents the detail that the firmware lists @sup fwnode as supplying a
 * resource to @con.
/* fixture gap */
 * mark the link as "consumer probe in progress" to make the supplier removal
 * wait for us to complete (or bad things may happen).
 *
 * Links without the DL_FLAG_MANAGED flag set are ignored.
 */
int device_links_check_suppliers(struct device *dev)
{
	struct device_link *link;
	int ret = 0;

	if (a52_smmu_unsec_trace_dev(dev))
		a52_ackfr_record("DLINK check enter dev=%s status=%d permissive=%d",
			dev_name(dev), dev->links.status, fw_devlink_is_permissive());

	/*
	 * Device waiting for supplier to become available is not allowed to
	 * probe.
	 */
	mutex_lock(&fwnode_link_lock);
	if (dev->fwnode && !list_empty(&dev->fwnode->suppliers) &&
	    !fw_devlink_is_permissive()) {
		dev_dbg(dev, "probe deferral - wait for supplier %pfwP\n",
			list_first_entry(&dev->fwnode->suppliers,
			struct fwnode_link,
			c_hook)->supplier);
		if (a52_smmu_unsec_trace_dev(dev))
			a52_ackfr_record("DLINK fwnode wait supplier=%pfwP",
				list_first_entry(&dev->fwnode->suppliers,
				struct fwnode_link, c_hook)->supplier);
		mutex_unlock(&fwnode_link_lock);
		return -EPROBE_DEFER;
	}
	mutex_unlock(&fwnode_link_lock);

	device_links_write_lock();

	list_for_each_entry(link, &dev->links.suppliers, c_node) {
		if (a52_smmu_unsec_trace_dev(dev))
			a52_ackfr_record("DLINK supplier=%s st=%d flags=%x supst=%d",
				dev_name(link->supplier), link->status, link->flags,
				link->supplier->links.status);
		if (!(link->flags & DL_FLAG_MANAGED))
			continue;

		if (link->status != DL_STATE_AVAILABLE &&
		    !(link->flags & DL_FLAG_SYNC_STATE_ONLY)) {
			device_links_missing_supplier(dev);
			dev_dbg(dev, "probe deferral - supplier %s not ready\n",
				dev_name(link->supplier));
			ret = -EPROBE_DEFER;
			break;
		}
		WRITE_ONCE(link->status, DL_STATE_CONSUMER_PROBE);
	}
	dev->links.status = DL_DEV_PROBING;

	device_links_write_unlock();
	if (a52_smmu_unsec_trace_dev(dev))
		a52_ackfr_record("DLINK check exit rc=%d status=%d", ret,
			dev->links.status);
	return ret;
}

/**
 * __device_links_queue_sync_state - Queue a device for sync_state() callback
 * @dev: Device to call sync_state() on
