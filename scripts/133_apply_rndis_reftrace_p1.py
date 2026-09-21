#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 133_apply_rndis_reftrace_p1.py <kernel-dir> <artifact-dir>")

kernel = Path(sys.argv[1])
art = Path(sys.argv[2])
art.mkdir(parents=True, exist_ok=True)

hdr = kernel / "include/linux/netdevice.h"
devc = kernel / "net/core/dev.c"

h = hdr.read_text()
d = devc.read_text()

marker = "/* A52 RNDIS REFTRACE P1 */"
if marker in h:
    raise SystemExit("A52 RNDIS REFTRACE P1 already applied")

old = """static inline void dev_put(struct net_device *dev)
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

new = """/* A52 RNDIS REFTRACE P1 */
static inline bool a52_rndis_reftrace_match(const struct net_device *dev)
{
\treturn dev &&
\t\tdev->name[0] == 'r' && dev->name[1] == 'n' &&
\t\tdev->name[2] == 'd' && dev->name[3] == 'i' &&
\t\tdev->name[4] == 's' && dev->name[5] == '0' &&
\t\tdev->name[6] == '\\0';
}

static inline void dev_put(struct net_device *dev)
{
\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\tpr_info("A52_RNDIS_REF PUT pre=%d caller=%pS\\n",
\t\t\tnetdev_refcnt_read(dev), __builtin_return_address(0));

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

\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\tpr_info("A52_RNDIS_REF HOLD post=%d caller=%pS\\n",
\t\t\tnetdev_refcnt_read(dev), __builtin_return_address(0));
}
"""

if h.count(old) != 1:
    raise SystemExit(f"expected one dev_hold/dev_put block, found {h.count(old)}")
h = h.replace(old, new, 1)

old_name = """\tdev = dev_get_by_name_rcu(net, name);
\tif (dev)
\t\tdev_hold(dev);
\trcu_read_unlock();
\treturn dev;
"""
new_name = """\tdev = dev_get_by_name_rcu(net, name);
\tif (dev) {
\t\tdev_hold(dev);
\t\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\t\tpr_info("A52_RNDIS_GET NAME caller=%pS\\n",
\t\t\t\t__builtin_return_address(0));
\t}
\trcu_read_unlock();
\treturn dev;
"""
if d.count(old_name) != 1:
    raise SystemExit(f"expected one dev_get_by_name body, found {d.count(old_name)}")
d = d.replace(old_name, new_name, 1)

old_idx = """\tdev = dev_get_by_index_rcu(net, ifindex);
\tif (dev)
\t\tdev_hold(dev);
\trcu_read_unlock();
\treturn dev;
"""
new_idx = """\tdev = dev_get_by_index_rcu(net, ifindex);
\tif (dev) {
\t\tdev_hold(dev);
\t\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\t\tpr_info("A52_RNDIS_GET INDEX caller=%pS\\n",
\t\t\t\t__builtin_return_address(0));
\t}
\trcu_read_unlock();
\treturn dev;
"""
if d.count(old_idx) != 1:
    raise SystemExit(f"expected one dev_get_by_index body, found {d.count(old_idx)}")
d = d.replace(old_idx, new_idx, 1)

old_first = """\tfor_each_netdev_rcu(net, dev)
\t\tif (dev->type == type) {
\t\t\tdev_hold(dev);
\t\t\tret = dev;
\t\t\tbreak;
\t\t}
"""
new_first = """\tfor_each_netdev_rcu(net, dev)
\t\tif (dev->type == type) {
\t\t\tdev_hold(dev);
\t\t\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\t\t\tpr_info("A52_RNDIS_GET HWTYPE caller=%pS\\n",
\t\t\t\t\t__builtin_return_address(0));
\t\t\tret = dev;
\t\t\tbreak;
\t\t}
"""
if d.count(old_first) != 1:
    raise SystemExit(f"expected one dev_getfirstbyhwtype body, found {d.count(old_first)}")
d = d.replace(old_first, new_first, 1)

hdr.write_text(h)
devc.write_text(d)

report = """A52 RNDIS REFTRACE P1
=====================
scope=refcount-observability-only
behavior_change=none
target_netdev=rndis0
logs=A52_RNDIS_REF HOLD/PUT + wrapper GET callsites
files=include/linux/netdevice.h,net/core/dev.c
"""
(art / "report.txt").write_text(report)
print(report)
