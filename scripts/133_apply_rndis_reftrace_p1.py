#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 133_apply_rndis_reftrace_p1.py <kernel-tree> <artifact-dir>")

root = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

hdr = root / "include/linux/netdevice.h"
devc = root / "net/core/dev.c"

hs = hdr.read_text()
ds = devc.read_text()

marker = "/* A52 RNDIS REFTRACE P1 */"
if marker in hs or marker in ds:
    raise SystemExit("A52 RNDIS REFTRACE P1 already applied")

old = """/**
 *\tdev_put - release reference to device
 *\t@dev: network device
 *
 * Release reference to device to allow it to be freed.
 */
static inline void dev_put(struct net_device *dev)
{
\tthis_cpu_dec(*dev->pcpu_refcnt);
}

/**
 *\tdev_hold - get reference to device
 *\t@dev: network device
 *
 * Hold reference to device to keep it from being freed.
 */
static inline void dev_hold(struct net_device *dev)
{
\tthis_cpu_inc(*dev->pcpu_refcnt);
}
"""

new = """/* A52 RNDIS REFTRACE P1
 *
 * Diagnostic-only conversion of dev_hold()/dev_put() from header inlines to
 * out-of-line functions.  This lets net/core/dev.c record the real caller
 * symbol for rndis0 reference acquisition/release without changing USB, IPA,
 * GSI, or netdevice lifetime policy.
 */
void dev_put(struct net_device *dev);
void dev_hold(struct net_device *dev);
"""

if hs.count(old) != 1:
    raise SystemExit(f"expected one inline dev_hold/dev_put block, found {hs.count(old)}")
hs = hs.replace(old, new, 1)

anchor = """int netdev_refcnt_read(const struct net_device *dev)
{
\tint i, refcnt = 0;

\tfor_each_possible_cpu(i)
\t\trefcnt += *per_cpu_ptr(dev->pcpu_refcnt, i);
\treturn refcnt;
}
EXPORT_SYMBOL(netdev_refcnt_read);
"""

impl = anchor + r'''

/* A52 RNDIS REFTRACE P1
 *
 * The USB/RNDIS failure leaves unregister_netdev(rndis0) permanently waiting
 * at Usage count = 1.  Log every reference transition for rndis0 together
 * with the actual dev_hold()/dev_put() caller.  No reference counts are
 * altered beyond the original increment/decrement.
 */
static __always_inline bool a52_rndis_reftrace_dev(const struct net_device *dev)
{
	return dev && !strncmp(dev->name, "rndis0", IFNAMSIZ);
}

void dev_hold(struct net_device *dev)
{
	bool trace = unlikely(a52_rndis_reftrace_dev(dev));
	int before = trace ? netdev_refcnt_read(dev) : 0;
	void *caller = __builtin_return_address(0);

	this_cpu_inc(*dev->pcpu_refcnt);

	if (trace)
		pr_info("A52_RNDIS_REF HOLD dev=%s ref=%d->%d caller=%pS\n",
			dev->name, before, netdev_refcnt_read(dev), caller);
}
EXPORT_SYMBOL(dev_hold);

void dev_put(struct net_device *dev)
{
	bool trace = unlikely(a52_rndis_reftrace_dev(dev));
	int before = trace ? netdev_refcnt_read(dev) : 0;
	void *caller = __builtin_return_address(0);

	this_cpu_dec(*dev->pcpu_refcnt);

	if (trace)
		pr_info("A52_RNDIS_REF PUT dev=%s ref=%d->%d caller=%pS\n",
			dev->name, before, netdev_refcnt_read(dev), caller);
}
EXPORT_SYMBOL(dev_put);
'''

if ds.count(anchor) != 1:
    raise SystemExit(f"expected one netdev_refcnt_read block, found {ds.count(anchor)}")
ds = ds.replace(anchor, impl, 1)

hdr.write_text(hs)
devc.write_text(ds)

checks = {
    "header_marker": marker in hs,
    "devc_marker": marker in ds,
    "inline_hold_removed": "static inline void dev_hold" not in hs,
    "inline_put_removed": "static inline void dev_put" not in hs,
    "hold_export": "EXPORT_SYMBOL(dev_hold);" in ds,
    "put_export": "EXPORT_SYMBOL(dev_put);" in ds,
    "hold_log": "A52_RNDIS_REF HOLD" in ds,
    "put_log": "A52_RNDIS_REF PUT" in ds,
    "rndis_filter": '"rndis0"' in ds,
}
bad = [k for k,v in checks.items() if not v]
if bad:
    raise SystemExit("REFTRACE audit failed: " + ", ".join(bad))

report = out / "report.txt"
report.write_text(
    "A52 RNDIS REFTRACE P1\n"
    "=====================\n"
    "scope=include/linux/netdevice.h,net/core/dev.c\n"
    "behavior_change=none_to_reference_accounting\n"
    "target=rndis0_only_for_logging\n"
    "logs=A52_RNDIS_REF HOLD/PUT caller=%pS ref=before->after\n"
    "purpose=identify_unmatched_netdev_reference_causing_unregister_hang\n"
)
print(report.read_text(), end="")
