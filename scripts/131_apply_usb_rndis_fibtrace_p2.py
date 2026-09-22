#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 131_apply_usb_rndis_fibtrace_p2.py <kernel_tree> <report_dir>")

kernel = Path(sys.argv[1]).resolve()
report_dir = Path(sys.argv[2]).resolve()
report_dir.mkdir(parents=True, exist_ok=True)

hdr = kernel / "include/linux/netdevice.h"
devc = kernel / "net/core/dev.c"
rndis = kernel / "drivers/platform/msm/ipa/ipa_clients/rndis_ipa.c"
fib4 = kernel / "net/ipv4/fib_semantics.c"
route6 = kernel / "net/ipv6/route.c"
fib6 = kernel / "net/ipv6/ip6_fib.c"

for p in (hdr, devc, rndis, fib4, route6, fib6):
    if not p.is_file():
        raise SystemExit(f"missing expected kernel file: {p}")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# 1. Public diagnostic hooks.
s = hdr.read_text()
decl_anchor = """void a52_rndis_reftrace(struct net_device *dev, bool hold);
void a52_rndis_reftrace_reset(struct net_device *dev);
void a52_rndis_reftrace_mark(struct net_device *dev, char op);
"""
decl_new = """void a52_rndis_reftrace(struct net_device *dev, bool hold);
void a52_rndis_reftrace_reset(struct net_device *dev);
void a52_rndis_reftrace_mark(struct net_device *dev, char op);

/*
 * A52 USB RNDIS FIB trace P2
 *
 * Records route/FIB ownership around the single leaked rndis netdev ref found
 * by P1.  'caller' is explicitly supplied so dev_get_by_index() can record its
 * parent call site rather than merely identifying itself.
 */
void a52_rndis_fibtrace(struct net_device *dev, char op,
			const void *object, unsigned long caller,
			unsigned long aux);
void a52_rndis_fibtrace_reset(struct net_device *dev);
"""
s = replace_once(s, decl_anchor, decl_new, "P2 netdevice declarations")
hdr.write_text(s)

# 2. Add a dedicated route/FIB ring to net/core/dev.c.
s = devc.read_text()
proc_anchor = """late_initcall(a52_rndis_reftrace_proc_init);
"""
fibtrace_code = r'''
late_initcall(a52_rndis_reftrace_proc_init);

/*
 * A52 USB RNDIS FIB trace P2
 *
 * P1 proved that rndis0 reaches unregister with exactly one extra reference.
 * This second ring traces only ownership paths capable of explaining the
 * routing/FIB imbalance, without adding stack capture to every dev_hold().
 */
#define A52_RNDIS_FIBTRACE_ORDER	9
#define A52_RNDIS_FIBTRACE_SIZE		(1U << A52_RNDIS_FIBTRACE_ORDER)
#define A52_RNDIS_FIBTRACE_MASK		(A52_RNDIS_FIBTRACE_SIZE - 1)

struct a52_rndis_fibtrace_entry {
	u64 seq;
	unsigned long stamp;
	unsigned long caller;
	unsigned long aux;
	const void *object;
	struct net_device *dev;
	pid_t pid;
	int cpu;
	char op;
	char comm[TASK_COMM_LEN];
};

static struct a52_rndis_fibtrace_entry
	a52_rndis_fibtrace_ring[A52_RNDIS_FIBTRACE_SIZE];
static atomic64_t a52_rndis_fibtrace_head = ATOMIC64_INIT(0);

static void a52_rndis_fibtrace_record(struct net_device *dev, char op,
				      const void *object,
				      unsigned long caller,
				      unsigned long aux)
{
	struct a52_rndis_fibtrace_entry *entry;
	u64 seq;

	if (!a52_is_rndis_dev(dev))
		return;
	if (READ_ONCE(a52_rndis_reftrace_dev) != dev)
		return;
	if (atomic_read(&a52_rndis_reftrace_frozen))
		return;

	seq = atomic64_inc_return(&a52_rndis_fibtrace_head);
	entry = &a52_rndis_fibtrace_ring[seq & A52_RNDIS_FIBTRACE_MASK];

	WRITE_ONCE(entry->seq, 0);
	smp_wmb();

	entry->stamp = jiffies;
	entry->caller = caller;
	entry->aux = aux;
	entry->object = object;
	entry->dev = dev;
	entry->pid = current->pid;
	entry->cpu = raw_smp_processor_id();
	entry->op = op;
	memcpy(entry->comm, current->comm, TASK_COMM_LEN);

	smp_wmb();
	WRITE_ONCE(entry->seq, seq);
}

noinline void a52_rndis_fibtrace(struct net_device *dev, char op,
				 const void *object, unsigned long caller,
				 unsigned long aux)
{
	a52_rndis_fibtrace_record(dev, op, object, caller, aux);
}
EXPORT_SYMBOL(a52_rndis_fibtrace);

noinline void a52_rndis_fibtrace_reset(struct net_device *dev)
{
	unsigned int i;

	for (i = 0; i < A52_RNDIS_FIBTRACE_SIZE; i++)
		WRITE_ONCE(a52_rndis_fibtrace_ring[i].seq, 0);
	atomic64_set(&a52_rndis_fibtrace_head, 0);
	smp_wmb();

	a52_rndis_fibtrace_record(dev, 'I', NULL,
		(unsigned long)__builtin_return_address(0), 0);
	pr_info("A52_RNDIS_FIBTRACE reset dev=%s\n", dev->name);
}
EXPORT_SYMBOL(a52_rndis_fibtrace_reset);

static int a52_rndis_fibtrace_proc_show(struct seq_file *m, void *v)
{
	struct net_device *dev = READ_ONCE(a52_rndis_reftrace_dev);
	struct a52_rndis_fibtrace_entry *entry;
	u64 head, first, seq, seen;

	head = atomic64_read(&a52_rndis_fibtrace_head);
	first = head > A52_RNDIS_FIBTRACE_SIZE ?
		head - A52_RNDIS_FIBTRACE_SIZE + 1 : 1;

	seq_printf(m,
		"A52 USB RNDIS FIB trace P2\n"
		"p1_frozen=%d p1_final_refcnt=%d head=%llu capacity=%u\n",
		atomic_read(&a52_rndis_reftrace_frozen),
		READ_ONCE(a52_rndis_reftrace_final_refcnt),
		(unsigned long long)head, A52_RNDIS_FIBTRACE_SIZE);

	for (seq = first; seq <= head; seq++) {
		entry = &a52_rndis_fibtrace_ring[
			seq & A52_RNDIS_FIBTRACE_MASK];
		seen = READ_ONCE(entry->seq);
		if (seen != seq)
			continue;
		smp_rmb();
		if (dev && entry->dev != dev)
			continue;

		seq_printf(m,
			"%llu j=%lu op=%c obj=%px aux=0x%lx pid=%d cpu=%d comm=%.16s caller=%pS\n",
			(unsigned long long)seq, entry->stamp, entry->op,
			entry->object, entry->aux, entry->pid, entry->cpu,
			entry->comm, (void *)entry->caller);
	}

	return 0;
}

static int a52_rndis_fibtrace_proc_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_rndis_fibtrace_proc_show, NULL);
}

static const struct file_operations a52_rndis_fibtrace_proc_fops = {
	.owner	 = THIS_MODULE,
	.open	 = a52_rndis_fibtrace_proc_open,
	.read	 = seq_read,
	.llseek	 = seq_lseek,
	.release = single_release,
};

static int __init a52_rndis_fibtrace_proc_init(void)
{
	if (!proc_create("a52_rndis_fibtrace", 0444, NULL,
			 &a52_rndis_fibtrace_proc_fops))
		pr_warn("A52_RNDIS_FIBTRACE failed to create proc entry\n");
	else
		pr_info("A52_RNDIS_FIBTRACE ready: /proc/a52_rndis_fibtrace\n");
	return 0;
}
late_initcall(a52_rndis_fibtrace_proc_init);
'''
s = replace_once(s, proc_anchor, fibtrace_code.strip("\n") + "\n", "P2 fibtrace ring")

get_anchor = """struct net_device *dev_get_by_index(struct net *net, int ifindex)
{
	struct net_device *dev;

	rcu_read_lock();
	dev = dev_get_by_index_rcu(net, ifindex);
	if (dev)
		dev_hold(dev);
	rcu_read_unlock();
	return dev;
}
"""
get_new = """struct net_device *dev_get_by_index(struct net *net, int ifindex)
{
	struct net_device *dev;

	rcu_read_lock();
	dev = dev_get_by_index_rcu(net, ifindex);
	if (dev) {
		dev_hold(dev);
		a52_rndis_fibtrace(dev, 'G', NULL,
			(unsigned long)__builtin_return_address(0),
			(unsigned long)ifindex);
	}
	rcu_read_unlock();
	return dev;
}
"""
s = replace_once(s, get_anchor, get_new, "dev_get_by_index parent caller")
devc.write_text(s)

# 3. Reset P2 alongside P1 on each RNDIS instance.
s = rndis.read_text()
reset_anchor = """	a52_rndis_reftrace_reset(net);
	result = register_netdev(net);
"""
reset_new = """	a52_rndis_reftrace_reset(net);
	a52_rndis_fibtrace_reset(net);
	result = register_netdev(net);
"""
s = replace_once(s, reset_anchor, reset_new, "RNDIS P2 reset")
rndis.write_text(s)

# 4. IPv4 fib_info object lifetime.
s = fib4.read_text()
free4_anchor = """	change_nexthops(fi) {
		if (nexthop_nh->nh_dev)
			dev_put(nexthop_nh->nh_dev);
		lwtstate_put(nexthop_nh->nh_lwtstate);
"""
free4_new = """	change_nexthops(fi) {
		if (nexthop_nh->nh_dev) {
			a52_rndis_fibtrace(nexthop_nh->nh_dev, 'q', fi,
				(unsigned long)__builtin_return_address(0),
				(unsigned long)nexthop_nh->nh_oif);
			dev_put(nexthop_nh->nh_dev);
		}
		lwtstate_put(nexthop_nh->nh_lwtstate);
"""
s = replace_once(s, free4_anchor, free4_new, "IPv4 fib destroy marker")

create4_anchor = """link_it:
	ofi = fib_find_info(fi);
"""
create4_new = """link_it:
	change_nexthops(fi) {
		if (nexthop_nh->nh_dev)
			a52_rndis_fibtrace(nexthop_nh->nh_dev, '4', fi,
				(unsigned long)__builtin_return_address(0),
				(unsigned long)nexthop_nh->nh_oif);
	} endfor_nexthops(fi)

	ofi = fib_find_info(fi);
"""
s = replace_once(s, create4_anchor, create4_new, "IPv4 fib create marker")
fib4.write_text(s)

# 5. IPv6 route creation paths.
s = route6.read_text()
addrconf_anchor = """	f6i->fib6_dst.addr = *addr;
	f6i->fib6_dst.plen = 128;
	tb_id = l3mdev_fib_table(idev->dev) ? : RT6_TABLE_LOCAL;
	f6i->fib6_table = fib6_get_table(net, tb_id);

	return f6i;
}
"""
addrconf_new = """	f6i->fib6_dst.addr = *addr;
	f6i->fib6_dst.plen = 128;
	tb_id = l3mdev_fib_table(idev->dev) ? : RT6_TABLE_LOCAL;
	f6i->fib6_table = fib6_get_table(net, tb_id);
	a52_rndis_fibtrace(dev, 'A', f6i,
		(unsigned long)__builtin_return_address(0),
		(unsigned long)tb_id);

	return f6i;
}
"""
s = replace_once(s, addrconf_anchor, addrconf_new, "IPv6 addrconf fib6 create marker")

route6_anchor = """	rt->fib6_nh.nh_flags |= (cfg->fc_flags & RTNH_F_ONLINK);
	rt->fib6_nh.nh_dev = dev;
	rt->fib6_table = table;

	cfg->fc_nlinfo.nl_net = dev_net(dev);
"""
route6_new = """	rt->fib6_nh.nh_flags |= (cfg->fc_flags & RTNH_F_ONLINK);
	rt->fib6_nh.nh_dev = dev;
	rt->fib6_table = table;
	a52_rndis_fibtrace(dev, '6', rt,
		(unsigned long)__builtin_return_address(0),
		(unsigned long)cfg->fc_table);

	cfg->fc_nlinfo.nl_net = dev_net(dev);
"""
s = replace_once(s, route6_anchor, route6_new, "IPv6 route fib6 create marker")
route6.write_text(s)

# 6. IPv6 fib6_info destruction.
s = fib6.read_text()
free6_anchor = """	if (f6i->fib6_nh.nh_dev)
		dev_put(f6i->fib6_nh.nh_dev);

	m = f6i->fib6_metrics;
"""
free6_new = """	if (f6i->fib6_nh.nh_dev) {
		a52_rndis_fibtrace(f6i->fib6_nh.nh_dev, 'v', f6i,
			(unsigned long)__builtin_return_address(0),
			f6i->fib6_table ? (unsigned long)f6i->fib6_table->tb6_id : 0);
		dev_put(f6i->fib6_nh.nh_dev);
	}

	m = f6i->fib6_metrics;
"""
s = replace_once(s, free6_anchor, free6_new, "IPv6 fib6 destroy marker")
fib6.write_text(s)

report = report_dir / "report.txt"
report.write_text(
    "A52 USB RNDIS FIB trace P2\n"
    "baseline=GitHub Actions run 35633887441 / project commit "
    "95278a740d6c4ccca32489247f373a0f6ab96982\n"
    "prerequisite=P1 dev_hold/dev_put tracer\n"
    "finding_driving_P2=P1 captured 93 holds vs 92 puts; routing/FIB group 21 vs 20\n"
    "proc=/proc/a52_rndis_fibtrace\n"
    "ring_entries=512\n"
    "markers=I(reset),G(dev_get_by_index parent),4(IPv4 fib create),"
    "q(IPv4 fib destroy),6(IPv6 route create),A(IPv6 addrconf create),"
    "v(IPv6 fib6 destroy)\n"
    "object_identity=raw kernel pointer printed as obj=%px for create/destroy pairing\n"
    "freeze=P2 stops recording automatically when P1 freezes on the 2s unregister stall\n"
)

print(report.read_text(), end="")
