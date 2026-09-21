#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 133_apply_rndis_netdev_reftrace_p1.py <kernel_tree> <artifact_dir>")

root = Path(sys.argv[1])
art = Path(sys.argv[2])
art.mkdir(parents=True, exist_ok=True)

hdr = root / "include/linux/netdevice.h"
devc = root / "net/core/dev.c"

hs = hdr.read_text()
ds = devc.read_text()

marker = "/* A52 RNDIS netdev reftrace P1 */"
if marker in hs or marker in ds:
    raise SystemExit("A52 RNDIS netdev reftrace P1 already applied")

old_decl = """/* Called by rtnetlink.c:rtnl_unlock() */
void netdev_run_todo(void);

/**
 *\tdev_put - release reference to device
"""
new_decl = """/* Called by rtnetlink.c:rtnl_unlock() */
void netdev_run_todo(void);

/* A52 RNDIS netdev reftrace P1 */
void a52_rndis_reftrace(struct net_device *dev, int delta,
\t\t\tunsigned long caller);

static __always_inline bool a52_rndis_reftrace_match(const struct net_device *dev)
{
\tconst char *n = dev->name;

\treturn n[0] == 'r' && n[1] == 'n' && n[2] == 'd' &&
\t       n[3] == 'i' && n[4] == 's' && n[5] == '0' &&
\t       n[6] == '\\0';
}

/**
 *\tdev_put - release reference to device
"""
if hs.count(old_decl) != 1:
    raise SystemExit(f"expected one netdev_run_todo/dev_put anchor, found {hs.count(old_decl)}")
hs = hs.replace(old_decl, new_decl, 1)

old_put = """static inline void dev_put(struct net_device *dev)
{
\tthis_cpu_dec(*dev->pcpu_refcnt);
}
"""
new_put = """static inline void dev_put(struct net_device *dev)
{
\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\ta52_rndis_reftrace(dev, -1,
\t\t\t(unsigned long)__builtin_return_address(0));
\tthis_cpu_dec(*dev->pcpu_refcnt);
}
"""
if hs.count(old_put) != 1:
    raise SystemExit(f"expected one dev_put body, found {hs.count(old_put)}")
hs = hs.replace(old_put, new_put, 1)

old_hold = """static inline void dev_hold(struct net_device *dev)
{
\tthis_cpu_inc(*dev->pcpu_refcnt);
}
"""
new_hold = """static inline void dev_hold(struct net_device *dev)
{
\tthis_cpu_inc(*dev->pcpu_refcnt);
\tif (unlikely(a52_rndis_reftrace_match(dev)))
\t\ta52_rndis_reftrace(dev, 1,
\t\t\t(unsigned long)__builtin_return_address(0));
}
"""
if hs.count(old_hold) != 1:
    raise SystemExit(f"expected one dev_hold body, found {hs.count(old_hold)}")
hs = hs.replace(old_hold, new_hold, 1)

anchor = """int netdev_refcnt_read(const struct net_device *dev)
{
\tint i, refcnt = 0;

\tfor_each_possible_cpu(i)
\t\trefcnt += *per_cpu_ptr(dev->pcpu_refcnt, i);
\treturn refcnt;
}
EXPORT_SYMBOL(netdev_refcnt_read);
"""
impl = anchor + """
/* A52 RNDIS netdev reftrace P1 */
void a52_rndis_reftrace(struct net_device *dev, int delta,
\t\t\tunsigned long caller)
{
\tint refcnt;

\tif (!dev)
\t\treturn;

\trefcnt = netdev_refcnt_read(dev);
\tif (delta > 0)
\t\tpr_info("A52_RNDIS_REF HOLD dev=%s ref=%d caller=%pS pid=%d comm=%s\\n",
\t\t\tdev->name, refcnt, (void *)caller,
\t\t\tcurrent->pid, current->comm);
\telse
\t\tpr_info("A52_RNDIS_REF PUT dev=%s ref=%d->%d caller=%pS pid=%d comm=%s\\n",
\t\t\tdev->name, refcnt, refcnt - 1, (void *)caller,
\t\t\tcurrent->pid, current->comm);
}
EXPORT_SYMBOL(a52_rndis_reftrace);
"""
if ds.count(anchor) != 1:
    raise SystemExit(f"expected one netdev_refcnt_read anchor, found {ds.count(anchor)}")
ds = ds.replace(anchor, impl, 1)

wait_old = """\t\tif (refcnt && time_after(jiffies, warning_time + 10 * HZ)) {
\t\t\tpr_emerg("unregister_netdevice: waiting for %s to become free. Usage count = %d\\n",
\t\t\t\t dev->name, refcnt);
\t\t\twarning_time = jiffies;
\t\t}
"""
wait_new = """\t\tif (refcnt && time_after(jiffies, warning_time + 10 * HZ)) {
\t\t\tpr_emerg("unregister_netdevice: waiting for %s to become free. Usage count = %d\\n",
\t\t\t\t dev->name, refcnt);
\t\t\tif (!strcmp(dev->name, "rndis0"))
\t\t\t\tpr_emerg("A52_RNDIS_REF STUCK dev=rndis0 ref=%d\\n",
\t\t\t\t\t refcnt);
\t\t\twarning_time = jiffies;
\t\t}
"""
if ds.count(wait_old) != 1:
    raise SystemExit(f"expected one netdev wait warning block, found {ds.count(wait_old)}")
ds = ds.replace(wait_old, wait_new, 1)

hdr.write_text(hs)
devc.write_text(ds)

report = """A52 RNDIS netdev reftrace P1
=============================
baseline=35633887441
behavior_change=no
target=netdevice refcount diagnostics for rndis0 only
dev_hold=logs HOLD after increment
dev_put=logs PUT before decrement
caller=kernel symbol via %pS
stuck_marker=A52_RNDIS_REF STUCK
files=include/linux/netdevice.h,net/core/dev.c
"""
(art / "report.txt").write_text(report)
print(report, end="")
