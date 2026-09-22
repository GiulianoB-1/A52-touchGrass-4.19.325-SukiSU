#!/usr/bin/env python3
from pathlib import Path
import sys

BASELINE_RUN = "35633887441"
BASELINE_COMMIT = "95278a740d6c4ccca32489247f373a0f6ab96982"
MARKER = "A52 USB RNDIS reftrace P1"

if len(sys.argv) != 3:
    raise SystemExit("usage: 130_apply_usb_rndis_reftrace_p1.py <kernel-tree> <artifact-dir>")

kernel = Path(sys.argv[1])
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)

hdr = kernel / "include/linux/netdevice.h"
devc = kernel / "net/core/dev.c"

for p in (hdr, devc):
    if not p.is_file():
        raise SystemExit(f"missing kernel source file: {p}")

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)

# Instrument the central dev_hold()/dev_put() primitives, but only emit trace
# events for interfaces whose name starts with "rndis".  This keeps the
# diagnostic focused and avoids changing refcount semantics.
h = hdr.read_text()

old_ref_block = """/**
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

new_ref_block = """/*
 * A52 USB RNDIS reftrace P1.
 *
 * Diagnostic only.  The reference count is never changed by the tracer.
 * dev_hold()/dev_put() retain their original increment/decrement semantics.
 */
void a52_rndis_reftrace_event(struct net_device *dev, int delta);

static inline bool a52_rndis_reftrace_match(const struct net_device *dev)
{
\treturn unlikely(dev &&
\t\tdev->name[0] == 'r' && dev->name[1] == 'n' &&
\t\tdev->name[2] == 'd' && dev->name[3] == 'i' &&
\t\tdev->name[4] == 's');
}

/**
 *\tdev_put - release reference to device
 *\t@dev: network device
 *
 * Release reference to device to allow it to be freed.
 */
static inline void dev_put(struct net_device *dev)
{
\tif (a52_rndis_reftrace_match(dev))
\t\ta52_rndis_reftrace_event(dev, -1);
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
\tif (a52_rndis_reftrace_match(dev))
\t\ta52_rndis_reftrace_event(dev, 1);
}
"""

h = replace_once(h, old_ref_block, new_ref_block, "netdevice ref primitive")
hdr.write_text(h)

c = devc.read_text()

if "#include <linux/stacktrace.h>" not in c:
    c = replace_once(
        c,
        "#include <linux/netdevice.h>\n",
        "#include <linux/netdevice.h>\n#include <linux/stacktrace.h>\n",
        "stacktrace include",
    )

anchor = """int netdev_refcnt_read(const struct net_device *dev)
{
\tint i, refcnt = 0;

\tfor_each_possible_cpu(i)
\t\trefcnt += *per_cpu_ptr(dev->pcpu_refcnt, i);
\treturn refcnt;
}
EXPORT_SYMBOL(netdev_refcnt_read);
"""

instrumentation = r'''
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
 * Keep a bounded in-kernel history of rndis* dev_hold()/dev_put() activity.
 * The tracer is observational only: it does not take or drop netdev refs.
 *
 * We record a short stack for each event.  When rndis0 enters
 * netdev_wait_allrefs() with a non-zero reference count, the most recent
 * history is dumped automatically to dmesg before notifier rebroadcasts can
 * obscure the original failure window.
 */
#define A52_RNDIS_REFTRACE_SLOTS	4096
#define A52_RNDIS_REFTRACE_MASK		(A52_RNDIS_REFTRACE_SLOTS - 1)
#define A52_RNDIS_REFTRACE_DEPTH	6
#define A52_RNDIS_REFTRACE_DUMP_EVENTS	192

struct a52_rndis_reftrace_entry {
	u64 seq;
	unsigned long caller;
	unsigned long stack[A52_RNDIS_REFTRACE_DEPTH];
	int nr_entries;
	int delta;
	int ref_before;
	int ref_after;
	int cpu;
	pid_t pid;
	char comm[TASK_COMM_LEN];
};

static struct a52_rndis_reftrace_entry
	a52_rndis_reftrace_ring[A52_RNDIS_REFTRACE_SLOTS];
static atomic64_t a52_rndis_reftrace_seq = ATOMIC64_INIT(0);

void noinline a52_rndis_reftrace_event(struct net_device *dev, int delta)
{
	struct a52_rndis_reftrace_entry *e;
	u64 seq;
	int observed;

#ifdef CONFIG_STACKTRACE
	struct stack_trace trace;
#endif

	/*
	 * For HOLD the inline wrapper has already incremented the reference.
	 * For PUT the inline wrapper calls us immediately before decrementing it.
	 */
	observed = netdev_refcnt_read(dev);
	seq = (u64)atomic64_inc_return(&a52_rndis_reftrace_seq);
	e = &a52_rndis_reftrace_ring[(seq - 1) & A52_RNDIS_REFTRACE_MASK];

	WRITE_ONCE(e->seq, 0);
	e->caller = (unsigned long)_RET_IP_;
	e->delta = delta;
	if (delta > 0) {
		e->ref_before = observed - 1;
		e->ref_after = observed;
	} else {
		e->ref_before = observed;
		e->ref_after = observed - 1;
	}
	e->cpu = raw_smp_processor_id();
	e->pid = current->pid;
	strscpy(e->comm, current->comm, sizeof(e->comm));
	e->nr_entries = 0;
	memset(e->stack, 0, sizeof(e->stack));

#ifdef CONFIG_STACKTRACE
	trace.nr_entries = 0;
	trace.max_entries = A52_RNDIS_REFTRACE_DEPTH;
	trace.entries = e->stack;
	trace.skip = 1;
	save_stack_trace(&trace);
	e->nr_entries = trace.nr_entries;
#endif

	/* Publish seq last so a concurrent dump never treats a partial entry
	 * as complete.
	 */
	smp_wmb();
	WRITE_ONCE(e->seq, seq);
}
EXPORT_SYMBOL(a52_rndis_reftrace_event);

static void a52_rndis_reftrace_dump(struct net_device *dev, int refcnt)
{
	struct a52_rndis_reftrace_entry *e;
	u64 end, start, seq;
	int cpu;
	int pcpu;

	end = (u64)atomic64_read(&a52_rndis_reftrace_seq);
	start = end > A52_RNDIS_REFTRACE_DUMP_EVENTS ?
		end - A52_RNDIS_REFTRACE_DUMP_EVENTS + 1 : 1;

	pr_emerg("A52_RNDIS_REFTRACE_BEGIN dev=%s ifindex=%d refcnt=%d seq=%llu..%llu\n",
		 dev->name, dev->ifindex, refcnt,
		 (unsigned long long)start, (unsigned long long)end);

	for_each_possible_cpu(cpu) {
		pcpu = *per_cpu_ptr(dev->pcpu_refcnt, cpu);
		if (pcpu)
			pr_emerg("A52_RNDIS_REFTRACE_CPU cpu=%d count=%d\n",
				 cpu, pcpu);
	}

	for (seq = start; seq <= end; seq++) {
		e = &a52_rndis_reftrace_ring[(seq - 1) &
					     A52_RNDIS_REFTRACE_MASK];
		if (READ_ONCE(e->seq) != seq)
			continue;
		smp_rmb();
		pr_emerg("A52_RNDIS_REFTRACE_EVT seq=%llu op=%s ref=%d->%d cpu=%d pid=%d comm=%s caller=%pS s0=%pS s1=%pS s2=%pS s3=%pS\n",
			 (unsigned long long)seq,
			 e->delta > 0 ? "HOLD" : "PUT",
			 e->ref_before, e->ref_after,
			 e->cpu, e->pid, e->comm,
			 (void *)e->caller,
			 (void *)e->stack[0], (void *)e->stack[1],
			 (void *)e->stack[2], (void *)e->stack[3]);
	}

	pr_emerg("A52_RNDIS_REFTRACE_END dev=%s refcnt_now=%d\n",
		 dev->name, netdev_refcnt_read(dev));
}
'''

c = replace_once(c, anchor, instrumentation.strip("\n") + "\n", "netdev_refcnt_read instrumentation anchor")

old_wait = """static void netdev_wait_allrefs(struct net_device *dev)
{
\tunsigned long rebroadcast_time, warning_time;
\tint refcnt;

\tlinkwatch_forget_dev(dev);

\trebroadcast_time = warning_time = jiffies;
\trefcnt = netdev_refcnt_read(dev);

\twhile (refcnt != 0) {
"""

new_wait = """static void netdev_wait_allrefs(struct net_device *dev)
{
\tunsigned long rebroadcast_time, warning_time;
\tint refcnt;
\tbool a52_rndis_reftrace_dumped = false;

\tlinkwatch_forget_dev(dev);

\trebroadcast_time = warning_time = jiffies;
\trefcnt = netdev_refcnt_read(dev);

\t/*
\t * Dump immediately, before the once-per-second NETDEV_UNREGISTER
\t * rebroadcast can add noise to the trace history.
\t */
\tif (refcnt && a52_rndis_reftrace_match(dev)) {
\t\ta52_rndis_reftrace_dump(dev, refcnt);
\t\ta52_rndis_reftrace_dumped = true;
\t}

\twhile (refcnt != 0) {
"""

c = replace_once(c, old_wait, new_wait, "netdev_wait_allrefs entry")

old_warning = """\t\tif (refcnt && time_after(jiffies, warning_time + 10 * HZ)) {
\t\t\tpr_emerg("unregister_netdevice: waiting for %s to become free. Usage count = %d\\n",
\t\t\t\t dev->name, refcnt);
\t\t\twarning_time = jiffies;
\t\t}
"""

new_warning = """\t\tif (refcnt && !a52_rndis_reftrace_dumped &&
\t\t    a52_rndis_reftrace_match(dev)) {
\t\t\ta52_rndis_reftrace_dump(dev, refcnt);
\t\t\ta52_rndis_reftrace_dumped = true;
\t\t}

\t\tif (refcnt && time_after(jiffies, warning_time + 10 * HZ)) {
\t\t\tpr_emerg("unregister_netdevice: waiting for %s to become free. Usage count = %d\\n",
\t\t\t\t dev->name, refcnt);
\t\t\twarning_time = jiffies;
\t\t}
"""

c = replace_once(c, old_warning, new_warning, "netdev_wait_allrefs warning")
devc.write_text(c)

report = f"""baseline_run={BASELINE_RUN}
baseline_commit={BASELINE_COMMIT}
marker={MARKER}
scope=include/linux/netdevice.h,net/core/dev.c
trace_target=rndis*
ring_slots=4096
stack_depth=6
automatic_dump_events=192
semantic_change=none
force_dev_put=no
refcount_override=no
purpose=find owner of final leaked rndis0 netdev reference
"""
(out / "report.txt").write_text(report)

(out / "markers.txt").write_text(
    "A52 USB RNDIS reftrace P1\n"
    "A52_RNDIS_REFTRACE_BEGIN\n"
    "A52_RNDIS_REFTRACE_EVT\n"
    "A52_RNDIS_REFTRACE_END\n"
)

print(report, end="")
