#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
defconfig = kernel / "arch/arm64/configs/a52xq_defconfig"
dd = kernel / "drivers/base/dd.c"

for path in (defconfig, dd):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

cfg = defconfig.read_text()
old_cfg = "CONFIG_PANIC_ON_OOPS=y\nCONFIG_PANIC_ON_OOPS_VALUE=1\n"
new_cfg = "# CONFIG_PANIC_ON_OOPS is not set\nCONFIG_PANIC_ON_OOPS_VALUE=0\n"
if new_cfg not in cfg:
    if cfg.count(old_cfg) != 1:
        raise SystemExit(f"panic-on-oops config anchor count={cfg.count(old_cfg)}")
    cfg = cfg.replace(old_cfg, new_cfg, 1)
    defconfig.write_text(cfg)

s = dd.read_text()
marker = "A52 P155 PROBE CENSUS"
if marker in s:
    print("A52 P155 probe census already applied")
    raise SystemExit(0)

include_anchor = "#include <linux/pinctrl/devinfo.h>\n"
include_new = (
    include_anchor
    + "#include <linux/reboot.h>\n"
    + "#include <linux/kmsg_dump.h>\n"
    + "#include <linux/spinlock.h>\n"
    + "#include <linux/jiffies.h>\n"
    + "#include <linux/string.h>\n"
)
if s.count(include_anchor) != 1:
    raise SystemExit("dd.c include anchor missing")
s = s.replace(include_anchor, include_new, 1)

probe_count_anchor = "static atomic_t probe_count = ATOMIC_INIT(0);\nstatic DECLARE_WAIT_QUEUE_HEAD(probe_waitqueue);\n"
probe_census = r'''static atomic_t probe_count = ATOMIC_INIT(0);
static DECLARE_WAIT_QUEUE_HEAD(probe_waitqueue);

/*
 * A52 P155 PROBE CENSUS
 * Diagnostic-only support for the P153 global async experiment.
 */
#define A52_PROBE_CENSUS_MAX 512
#define A52_PROBE_CENSUS_DELAY_MS 12000

enum a52_probe_census_state {
	A52_PC_EMPTY = 0,
	A52_PC_RUNNING,
	A52_PC_OK,
	A52_PC_DEFER,
	A52_PC_REJECT,
	A52_PC_FAIL,
};

struct a52_probe_census_rec {
	const struct device *dev;
	const struct device_driver *drv;
	unsigned long started;
	unsigned int attempts;
	int ret;
	u8 state;
	char driver[48];
	char device[64];
};

static DEFINE_SPINLOCK(a52_probe_census_lock);
static struct a52_probe_census_rec a52_probe_census[A52_PROBE_CENSUS_MAX];
static unsigned int a52_probe_census_used;
static unsigned int a52_probe_census_overflow;

static struct a52_probe_census_rec *
a52_probe_census_get_locked(struct device *dev, struct device_driver *drv)
{
	unsigned int i;

	for (i = 0; i < a52_probe_census_used; i++) {
		if (a52_probe_census[i].dev == dev &&
		    a52_probe_census[i].drv == drv)
			return &a52_probe_census[i];
	}

	if (a52_probe_census_used >= A52_PROBE_CENSUS_MAX) {
		a52_probe_census_overflow++;
		return NULL;
	}

	return &a52_probe_census[a52_probe_census_used++];
}

static void a52_probe_census_start(struct device *dev,
				   struct device_driver *drv)
{
	struct a52_probe_census_rec *rec;
	unsigned long flags;
	const char *dname = drv && drv->name ? drv->name : "?";
	const char *vname = dev_name(dev) ? dev_name(dev) : "?";

	spin_lock_irqsave(&a52_probe_census_lock, flags);
	rec = a52_probe_census_get_locked(dev, drv);
	if (rec) {
		rec->dev = dev;
		rec->drv = drv;
		rec->started = jiffies;
		rec->attempts++;
		rec->ret = 0;
		rec->state = A52_PC_RUNNING;
		strlcpy(rec->driver, dname, sizeof(rec->driver));
		strlcpy(rec->device, vname, sizeof(rec->device));
	}
	spin_unlock_irqrestore(&a52_probe_census_lock, flags);

	pr_notice("A52_PROBE_START drv=%s dev=%s pid=%d\n",
		  dname, vname, current->pid);
}

static void a52_probe_census_finish(struct device *dev,
				    struct device_driver *drv,
				    u8 state, int ret)
{
	struct a52_probe_census_rec *rec;
	unsigned long flags;

	spin_lock_irqsave(&a52_probe_census_lock, flags);
	rec = a52_probe_census_get_locked(dev, drv);
	if (rec) {
		rec->ret = ret;
		rec->state = state;
	}
	spin_unlock_irqrestore(&a52_probe_census_lock, flags);

	if (state == A52_PC_DEFER)
		pr_warn("A52_PROBE_DEFER drv=%s dev=%s ret=%d\n",
			drv->name, dev_name(dev), ret);
	else if (state == A52_PC_REJECT)
		pr_warn("A52_PROBE_REJECT drv=%s dev=%s ret=%d\n",
			drv->name, dev_name(dev), ret);
	else if (state == A52_PC_FAIL)
		pr_err("A52_PROBE_FAIL drv=%s dev=%s ret=%d\n",
		       drv->name, dev_name(dev), ret);
}

static const char *a52_probe_census_state_name(u8 state)
{
	switch (state) {
	case A52_PC_RUNNING: return "RUNNING";
	case A52_PC_OK: return "OK";
	case A52_PC_DEFER: return "DEFER";
	case A52_PC_REJECT: return "REJECT";
	case A52_PC_FAIL: return "FAIL";
	default: return "EMPTY";
	}
}

static int a52_probe_census_finalizer(void *unused)
{
	unsigned long flags;
	unsigned int i, running = 0, fail = 0, defer = 0, reject = 0, ok = 0;

	msleep(A52_PROBE_CENSUS_DELAY_MS);

	pr_emerg("A52_PROBE_CENSUS SUMMARY_BEGIN used=%u overflow=%u probe_count=%d\n",
		 a52_probe_census_used, a52_probe_census_overflow,
		 atomic_read(&probe_count));

	spin_lock_irqsave(&a52_probe_census_lock, flags);
	for (i = 0; i < a52_probe_census_used; i++) {
		struct a52_probe_census_rec *rec = &a52_probe_census[i];

		switch (rec->state) {
		case A52_PC_RUNNING: running++; break;
		case A52_PC_OK: ok++; break;
		case A52_PC_DEFER: defer++; break;
		case A52_PC_REJECT: reject++; break;
		case A52_PC_FAIL: fail++; break;
		default: break;
		}

		if (rec->state != A52_PC_OK)
			pr_emerg("A52_PROBE_CENSUS state=%s attempts=%u ret=%d drv=%s dev=%s age_ms=%u\n",
				 a52_probe_census_state_name(rec->state),
				 rec->attempts, rec->ret, rec->driver, rec->device,
				 jiffies_to_msecs(jiffies - rec->started));
	}
	spin_unlock_irqrestore(&a52_probe_census_lock, flags);

	pr_emerg("A52_PROBE_CENSUS SUMMARY_END ok=%u running=%u fail=%u defer=%u reject=%u overflow=%u probe_count=%d\n",
		 ok, running, fail, defer, reject, a52_probe_census_overflow,
		 atomic_read(&probe_count));
	pr_emerg("A52_PROBE_CENSUS snapshotting ramoops then rebooting directly to recovery\n");

	kmsg_dump(KMSG_DUMP_OOPS);
	machine_restart("recovery");

	pr_emerg("A52_PROBE_CENSUS machine_restart returned unexpectedly\n");
	return 0;
}

static int __init a52_probe_census_init(void)
{
	struct task_struct *task;

	task = kthread_run(a52_probe_census_finalizer, NULL, "a52_probe_census");
	if (IS_ERR(task))
		pr_err("A52_PROBE_CENSUS failed to start finalizer: %ld\n",
		       PTR_ERR(task));
	else
		pr_notice("A52_PROBE_CENSUS finalizer armed for %u ms\n",
			  A52_PROBE_CENSUS_DELAY_MS);

	return 0;
}
late_initcall(a52_probe_census_init);
'''
if s.count(probe_count_anchor) != 1:
    raise SystemExit("probe_count anchor missing")
s = s.replace(probe_count_anchor, probe_census, 1)

really_anchor = """static int really_probe(struct device *dev, struct device_driver *drv)
{
	int ret = -EPROBE_DEFER;
	int local_trigger_count = atomic_read(&deferred_trigger_count);
	bool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&
			   !drv->suppress_bind_attrs;

"""
if s.count(really_anchor) != 1:
    raise SystemExit("really_probe entry anchor missing")
s = s.replace(really_anchor, really_anchor + "\ta52_probe_census_start(dev, drv);\n\n", 1)

pairs = [
("""		dev_dbg(dev, "Driver %s force probe deferral\\n", drv->name);
		driver_deferred_probe_add(dev);
		return ret;
""",
 """		dev_dbg(dev, "Driver %s force probe deferral\\n", drv->name);
		driver_deferred_probe_add(dev);
		a52_probe_census_finish(dev, drv, A52_PC_DEFER, ret);
		return ret;
"""),
("""	if (ret == -EPROBE_DEFER)
		driver_deferred_probe_add_trigger(dev, local_trigger_count);
	if (ret)
		return ret;
""",
 """	if (ret == -EPROBE_DEFER)
		driver_deferred_probe_add_trigger(dev, local_trigger_count);
	if (ret) {
		a52_probe_census_finish(dev, drv,
			ret == -EPROBE_DEFER ? A52_PC_DEFER : A52_PC_FAIL,
			ret);
		return ret;
	}
"""),
("""	if (!list_empty(&dev->devres_head)) {
		dev_crit(dev, "Resources present before probing\\n");
		ret = -EBUSY;
		goto done;
	}
""",
 """	if (!list_empty(&dev->devres_head)) {
		dev_crit(dev, "Resources present before probing\\n");
		ret = -EBUSY;
		a52_probe_census_finish(dev, drv, A52_PC_FAIL, ret);
		goto done;
	}
"""),
("""	driver_bound(dev);
	ret = 1;
	pr_debug("bus: '%s': %s: bound device %s to driver %s\\n",
		 drv->bus->name, __func__, dev_name(dev), drv->name);
	goto done;
""",
 """	driver_bound(dev);
	ret = 1;
	a52_probe_census_finish(dev, drv, A52_PC_OK, 0);
	pr_debug("bus: '%s': %s: bound device %s to driver %s\\n",
		 drv->bus->name, __func__, dev_name(dev), drv->name);
	goto done;
""")]
for old, new in pairs:
    if s.count(old) != 1:
        raise SystemExit(f"probe census anchor missing count={s.count(old)}: {old[:50]!r}")
    s = s.replace(old, new, 1)

old_switch = """	switch (ret) {
	case -EPROBE_DEFER:
		/* Driver requested deferred probing */
		dev_dbg(dev, "Driver %s requests probe deferral\\n", drv->name);
		driver_deferred_probe_add_trigger(dev, local_trigger_count);
		break;
	case -ENODEV:
	case -ENXIO:
		pr_debug("%s: probe of %s rejects match %d\\n",
			 drv->name, dev_name(dev), ret);
		break;
	default:
		/* driver matched but the probe failed */
		printk(KERN_WARNING
		       "%s: probe of %s failed with error %d\\n",
		       drv->name, dev_name(dev), ret);
	}
"""
new_switch = """	switch (ret) {
	case -EPROBE_DEFER:
		/* Driver requested deferred probing */
		dev_dbg(dev, "Driver %s requests probe deferral\\n", drv->name);
		driver_deferred_probe_add_trigger(dev, local_trigger_count);
		a52_probe_census_finish(dev, drv, A52_PC_DEFER, ret);
		break;
	case -ENODEV:
	case -ENXIO:
		pr_debug("%s: probe of %s rejects match %d\\n",
			 drv->name, dev_name(dev), ret);
		a52_probe_census_finish(dev, drv, A52_PC_REJECT, ret);
		break;
	default:
		/* driver matched but the probe failed */
		printk(KERN_WARNING
		       "%s: probe of %s failed with error %d\\n",
		       drv->name, dev_name(dev), ret);
		a52_probe_census_finish(dev, drv, A52_PC_FAIL, ret);
	}
"""
if s.count(old_switch) != 1:
    raise SystemExit(f"probe failure switch anchor count={s.count(old_switch)}")
s = s.replace(old_switch, new_switch, 1)

dd.write_text(s)

cfg = defconfig.read_text()
ddt = dd.read_text()
for token in ("# CONFIG_PANIC_ON_OOPS is not set", "CONFIG_PANIC_ON_OOPS_VALUE=0"):
    if token not in cfg:
        raise SystemExit(f"P155 config audit missing: {token}")
for token in (
    "A52 P155 PROBE CENSUS",
    "A52_PROBE_START drv=%s dev=%s pid=%d",
    "A52_PROBE_CENSUS SUMMARY_BEGIN",
    "A52_PROBE_CENSUS SUMMARY_END",
    'machine_restart("recovery")',
    "kmsg_dump(KMSG_DUMP_OOPS)",
    "late_initcall(a52_probe_census_init)",
):
    if token not in ddt:
        raise SystemExit(f"P155 source audit missing: {token}")

print("A52 P155 probe census applied")
print("  panic_on_oops=0 for diagnostic continuation")
print("  fixed 512-entry driver/device state table")
print("  records RUNNING/OK/DEFER/REJECT/FAIL")
print("  12-second independent census kthread")
print("  final kmsg_dump(OOPS) + direct recovery reboot")
