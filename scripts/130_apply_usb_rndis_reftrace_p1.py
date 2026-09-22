#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 130_apply_usb_rndis_reftrace_p1.py <kernel_tree> <report_dir>")

kernel = Path(sys.argv[1]).resolve()
report_dir = Path(sys.argv[2]).resolve()
report_dir.mkdir(parents=True, exist_ok=True)

hdr = kernel / "include/linux/netdevice.h"
devc = kernel / "net/core/dev.c"
rndis = kernel / "drivers/platform/msm/ipa/ipa_clients/rndis_ipa.c"

for p in (hdr, devc, rndis):
    if not p.is_file():
        raise SystemExit(f"missing expected kernel file: {p}")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# 1. Hook dev_hold()/dev_put() only for RNDIS interfaces.
s = hdr.read_text()
decl_anchor = """/* Called by rtnetlink.c:rtnl_unlock() */
void netdev_run_todo(void);
"""
decl_new = """/* Called by rtnetlink.c:rtnl_unlock() */
void netdev_run_todo(void);

/*
 * A52 USB RNDIS reftrace P1
 *
 * Diagnostic-only reference tracker used to identify the owner of a leaked
 * rndis net_device reference during USB gadget teardown.
 */
void a52_rndis_reftrace(struct net_device *dev, bool hold);
void a52_rndis_reftrace_reset(struct net_device *dev);
void a52_rndis_reftrace_mark(struct net_device *dev, char op);
"""
s = replace_once(s, decl_anchor, decl_new, "netdevice declarations")

put_anchor = """static inline void dev_put(struct net_device *dev)
{
	this_cpu_dec(*dev->pcpu_refcnt);
}
"""
put_new = """static inline void dev_put(struct net_device *dev)
{
	this_cpu_dec(*dev->pcpu_refcnt);
	if (unlikely(dev->name[0] == 'r' && dev->name[1] == 'n' &&
		     dev->name[2] == 'd' && dev->name[3] == 'i' &&
		     dev->name[4] == 's'))
		a52_rndis_reftrace(dev, false);
}
"""
s = replace_once(s, put_anchor, put_new, "dev_put hook")

hold_anchor = """static inline void dev_hold(struct net_device *dev)
{
	this_cpu_inc(*dev->pcpu_refcnt);
}
"""
hold_new = """static inline void dev_hold(struct net_device *dev)
{
	this_cpu_inc(*dev->pcpu_refcnt);
	if (unlikely(dev->name[0] == 'r' && dev->name[1] == 'n' &&
		     dev->name[2] == 'd' && dev->name[3] == 'i' &&
		     dev->name[4] == 's'))
		a52_rndis_reftrace(dev, true);
}
"""
s = replace_once(s, hold_anchor, hold_new, "dev_hold hook")
hdr.write_text(s)

# 2. Add the lockless ring buffer and freeze-on-stall logic to net/core/dev.c.
s = devc.read_text()
if "#include <linux/seq_file.h>" not in s:
    s = replace_once(
        s,
        "#include <linux/module.h>\n",
        "#include <linux/module.h>\n#include <linux/proc_fs.h>\n#include <linux/seq_file.h>\n",
        "dev.c diagnostic includes",
    )

refcnt_anchor = """int netdev_refcnt_read(const struct net_device *dev)
{
	int i, refcnt = 0;

	for_each_possible_cpu(i)
		refcnt += *per_cpu_ptr(dev->pcpu_refcnt, i);
	return refcnt;
}
EXPORT_SYMBOL(netdev_refcnt_read);
"""

trace_code = r'''
int netdev_refcnt_read(const struct net_device *dev)
{
	int i, refcnt = 0;

	for_each_possible_cpu(i)
		refcnt += *per_cpu_ptr(dev->pcpu_refcnt, i);
	return refcnt;
}
EXPORT_SYMBOL(netdev_refcnt_read);

/*
 * A52 USB RNDIS reftrace P1
 *
 * The normal netdev refcount is per-CPU, so once unregister_netdev() stalls
 * with Usage count = 1 there is no ownership information left.  Record every
 * RNDIS dev_hold/dev_put caller in a fixed ring, then freeze the ring if
 * netdev_wait_allrefs() is still waiting after two seconds.
 *
 * No printk is performed on the hot hold/put path.  A compact tail is emitted
 * only when a real unregister stall is detected, while the full trace remains
 * available at /proc/a52_rndis_reftrace.
 */
#define A52_RNDIS_REFTRACE_ORDER	12
#define A52_RNDIS_REFTRACE_SIZE		(1U << A52_RNDIS_REFTRACE_ORDER)
#define A52_RNDIS_REFTRACE_MASK		(A52_RNDIS_REFTRACE_SIZE - 1)
#define A52_RNDIS_REFTRACE_DUMP_TAIL	128

struct a52_rndis_reftrace_entry {
	u64 seq;
	unsigned long stamp;
	unsigned long caller;
	struct net_device *dev;
	pid_t pid;
	int cpu;
	char op;
	char comm[TASK_COMM_LEN];
};

static struct a52_rndis_reftrace_entry
	a52_rndis_reftrace_ring[A52_RNDIS_REFTRACE_SIZE];
static atomic64_t a52_rndis_reftrace_head = ATOMIC64_INIT(0);
static atomic_t a52_rndis_reftrace_frozen = ATOMIC_INIT(0);
static struct net_device *a52_rndis_reftrace_dev;
static int a52_rndis_reftrace_final_refcnt;

static __always_inline bool a52_is_rndis_dev(const struct net_device *dev)
{
	return dev && dev->name[0] == 'r' && dev->name[1] == 'n' &&
	       dev->name[2] == 'd' && dev->name[3] == 'i' &&
	       dev->name[4] == 's';
}

static void a52_rndis_reftrace_record(struct net_device *dev, char op,
				     unsigned long caller)
{
	struct a52_rndis_reftrace_entry *entry;
	u64 seq;

	if (!dev || atomic_read(&a52_rndis_reftrace_frozen))
		return;
	if (READ_ONCE(a52_rndis_reftrace_dev) != dev)
		return;

	seq = atomic64_inc_return(&a52_rndis_reftrace_head);
	entry = &a52_rndis_reftrace_ring[seq & A52_RNDIS_REFTRACE_MASK];

	WRITE_ONCE(entry->seq, 0);
	smp_wmb();

	entry->stamp = jiffies;
	entry->caller = caller;
	entry->dev = dev;
	entry->pid = current->pid;
	entry->cpu = raw_smp_processor_id();
	entry->op = op;
	memcpy(entry->comm, current->comm, TASK_COMM_LEN);

	smp_wmb();
	WRITE_ONCE(entry->seq, seq);
}

noinline void a52_rndis_reftrace(struct net_device *dev, bool hold)
{
	a52_rndis_reftrace_record(dev, hold ? 'H' : 'P',
		(unsigned long)__builtin_return_address(0));
}
EXPORT_SYMBOL(a52_rndis_reftrace);

noinline void a52_rndis_reftrace_reset(struct net_device *dev)
{
	unsigned int i;

	atomic_set(&a52_rndis_reftrace_frozen, 1);
	for (i = 0; i < A52_RNDIS_REFTRACE_SIZE; i++)
		WRITE_ONCE(a52_rndis_reftrace_ring[i].seq, 0);

	WRITE_ONCE(a52_rndis_reftrace_dev, dev);
	WRITE_ONCE(a52_rndis_reftrace_final_refcnt, 0);
	atomic64_set(&a52_rndis_reftrace_head, 0);
	smp_wmb();
	atomic_set(&a52_rndis_reftrace_frozen, 0);

	a52_rndis_reftrace_record(dev, 'I',
		(unsigned long)__builtin_return_address(0));
	pr_info("A52_RNDIS_REFTRACE reset dev=%s\n", dev->name);
}
EXPORT_SYMBOL(a52_rndis_reftrace_reset);

noinline void a52_rndis_reftrace_mark(struct net_device *dev, char op)
{
	a52_rndis_reftrace_record(dev, op,
		(unsigned long)__builtin_return_address(0));
}
EXPORT_SYMBOL(a52_rndis_reftrace_mark);

static void a52_rndis_reftrace_dump_tail(struct net_device *dev)
{
	struct a52_rndis_reftrace_entry *entry;
	u64 head, first, seq, seen;

	head = atomic64_read(&a52_rndis_reftrace_head);
	first = head > A52_RNDIS_REFTRACE_DUMP_TAIL ?
		head - A52_RNDIS_REFTRACE_DUMP_TAIL + 1 : 1;

	pr_emerg("A52_RNDIS_REFTRACE FROZEN dev=%s refcnt=%d head=%llu\n",
		 dev->name, READ_ONCE(a52_rndis_reftrace_final_refcnt),
		 (unsigned long long)head);

	for (seq = first; seq <= head; seq++) {
		entry = &a52_rndis_reftrace_ring[
			seq & A52_RNDIS_REFTRACE_MASK];
		seen = READ_ONCE(entry->seq);
		if (seen != seq)
			continue;
		smp_rmb();
		if (entry->dev != dev)
			continue;

		pr_emerg("A52_RNDIS_REFTRACE seq=%llu j=%lu op=%c pid=%d cpu=%d comm=%.16s caller=%pS\n",
			 (unsigned long long)seq, entry->stamp, entry->op,
			 entry->pid, entry->cpu, entry->comm,
			 (void *)entry->caller);
	}
}

static void a52_rndis_reftrace_freeze(struct net_device *dev, int refcnt)
{
	if (!a52_is_rndis_dev(dev))
		return;
	if (atomic_cmpxchg(&a52_rndis_reftrace_frozen, 0, 1))
		return;

	WRITE_ONCE(a52_rndis_reftrace_final_refcnt, refcnt);
	smp_wmb();
	a52_rndis_reftrace_dump_tail(dev);
}

static int a52_rndis_reftrace_proc_show(struct seq_file *m, void *v)
{
	struct net_device *dev = READ_ONCE(a52_rndis_reftrace_dev);
	struct a52_rndis_reftrace_entry *entry;
	u64 head, first, seq, seen;

	head = atomic64_read(&a52_rndis_reftrace_head);
	first = head > A52_RNDIS_REFTRACE_SIZE ?
		head - A52_RNDIS_REFTRACE_SIZE + 1 : 1;

	seq_printf(m,
		"A52 USB RNDIS reftrace P1\nfrozen=%d final_refcnt=%d head=%llu capacity=%u\n",
		atomic_read(&a52_rndis_reftrace_frozen),
		READ_ONCE(a52_rndis_reftrace_final_refcnt),
		(unsigned long long)head, A52_RNDIS_REFTRACE_SIZE);

	for (seq = first; seq <= head; seq++) {
		entry = &a52_rndis_reftrace_ring[
			seq & A52_RNDIS_REFTRACE_MASK];
		seen = READ_ONCE(entry->seq);
		if (seen != seq)
			continue;
		smp_rmb();
		if (dev && entry->dev != dev)
			continue;

		seq_printf(m,
			"%llu j=%lu op=%c pid=%d cpu=%d comm=%.16s caller=%pS\n",
			(unsigned long long)seq, entry->stamp, entry->op,
			entry->pid, entry->cpu, entry->comm,
			(void *)entry->caller);
	}

	return 0;
}

static int a52_rndis_reftrace_proc_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_rndis_reftrace_proc_show, NULL);
}

static const struct file_operations a52_rndis_reftrace_proc_fops = {
	.owner	 = THIS_MODULE,
	.open	 = a52_rndis_reftrace_proc_open,
	.read	 = seq_read,
	.llseek	 = seq_lseek,
	.release = single_release,
};

static int __init a52_rndis_reftrace_proc_init(void)
{
	if (!proc_create("a52_rndis_reftrace", 0444, NULL,
			 &a52_rndis_reftrace_proc_fops))
		pr_warn("A52_RNDIS_REFTRACE failed to create proc entry\n");
	else
		pr_info("A52_RNDIS_REFTRACE ready: /proc/a52_rndis_reftrace\n");
	return 0;
}
late_initcall(a52_rndis_reftrace_proc_init);
'''
s = replace_once(s, refcnt_anchor, trace_code.strip("\n") + "\n", "dev.c tracer body")

wait_locals = """static void netdev_wait_allrefs(struct net_device *dev)
{
	unsigned long rebroadcast_time, warning_time;
	int refcnt;

	linkwatch_forget_dev(dev);

	rebroadcast_time = warning_time = jiffies;
	refcnt = netdev_refcnt_read(dev);
"""
wait_locals_new = """static void netdev_wait_allrefs(struct net_device *dev)
{
	unsigned long rebroadcast_time, warning_time;
	unsigned long a52_freeze_time;
	bool a52_rndis;
	bool a52_frozen = false;
	int refcnt;

	linkwatch_forget_dev(dev);

	rebroadcast_time = warning_time = jiffies;
	refcnt = netdev_refcnt_read(dev);
	a52_rndis = a52_is_rndis_dev(dev);
	a52_freeze_time = jiffies + 2 * HZ;
	if (a52_rndis && refcnt)
		a52_rndis_reftrace_mark(dev, 'W');
"""
s = replace_once(s, wait_locals, wait_locals_new, "netdev_wait_allrefs locals")

wait_ref = """		refcnt = netdev_refcnt_read(dev);

		if (refcnt && time_after(jiffies, warning_time + 10 * HZ)) {
"""
wait_ref_new = """		refcnt = netdev_refcnt_read(dev);

		if (a52_rndis && refcnt && !a52_frozen &&
		    time_after_eq(jiffies, a52_freeze_time)) {
			a52_rndis_reftrace_freeze(dev, refcnt);
			a52_frozen = true;
		}

		if (refcnt && time_after(jiffies, warning_time + 10 * HZ)) {
"""
s = replace_once(s, wait_ref, wait_ref_new, "netdev_wait_allrefs freeze")
devc.write_text(s)

# 3. Mark the RNDIS lifecycle around register/unregister.
s = rndis.read_text()
reg_anchor = """	result = register_netdev(net);
	if (result) {
"""
reg_new = """	a52_rndis_reftrace_reset(net);
	result = register_netdev(net);
	if (result) {
"""
s = replace_once(s, reg_anchor, reg_new, "RNDIS reset before register")

reg_ok_anchor = """	RNDIS_IPA_DEBUG
		("netdev:%s registration succeeded, index=%d\\n",
		net->name, net->ifindex);
"""
reg_ok_new = """	a52_rndis_reftrace_mark(net, 'R');
	RNDIS_IPA_DEBUG
		("netdev:%s registration succeeded, index=%d\\n",
		net->name, net->ifindex);
"""
s = replace_once(s, reg_ok_anchor, reg_ok_new, "RNDIS registered marker")

unreg_anchor = """	RNDIS_IPA_DEBUG("RNDIS_IPA netdev unregistered started\\n");
	unregister_netdev(rndis_ipa_ctx->net);
"""
unreg_new = """	RNDIS_IPA_DEBUG("RNDIS_IPA netdev unregistered started\\n");
	a52_rndis_reftrace_mark(rndis_ipa_ctx->net, 'U');
	unregister_netdev(rndis_ipa_ctx->net);
"""
s = replace_once(s, unreg_anchor, unreg_new, "RNDIS unregister marker")
rndis.write_text(s)

report = report_dir / "report.txt"
report.write_text(
    "A52 USB RNDIS reftrace P1\n"
    "baseline=GitHub Actions run 35633887441 / project commit "
    "95278a740d6c4ccca32489247f373a0f6ab96982\n"
    "trace_target=rndis* net_device dev_hold/dev_put lifecycle\n"
    "ring_entries=4096\n"
    "stall_freeze=2s after netdev_wait_allrefs begins with refs outstanding\n"
    "proc=/proc/a52_rndis_reftrace\n"
    "markers=I(init/reset),R(register complete),U(unregister requested),"
    "W(wait-allrefs entered),H(dev_hold),P(dev_put)\n"
)

print(report.read_text(), end="")
