#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 116_apply_a52_scheduler_recorder.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
core = root / "kernel/sched/core.c"
sched_h = root / "kernel/sched/sched.h"
cass = root / "kernel/sched/cass.c"
sugov = root / "kernel/sched/cpufreq_schedutil.c"

for p in (core, sched_h, cass, sugov):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")


def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Shared declarations.
# ---------------------------------------------------------------------------
sh = sched_h.read_text()
if "A52 scheduler efficiency recorder" in sh:
    raise SystemExit("A52 scheduler recorder already applied")

decl_anchor = "extern __read_mostly int scheduler_running;\n"
decl_block = r'''extern __read_mostly int scheduler_running;

/* A52 scheduler efficiency recorder: runtime disabled by default. */
extern int a52_sched_diag_level;

static __always_inline bool a52_sched_diag_enabled(void)
{
	return unlikely(READ_ONCE(a52_sched_diag_level));
}

void a52_sched_diag_cass(unsigned int best_cpu, unsigned int prev_cpu,
			 bool sync, unsigned long p_util,
			 unsigned long uc_min, unsigned long uc_max,
			 unsigned int candidates, unsigned int idle_candidates,
			 unsigned int under_ucmin, unsigned int under_task);
void a52_sched_diag_uclamp(unsigned long before, unsigned long min_util,
			   unsigned long max_util, unsigned long after,
			   bool active);
void a52_sched_diag_sugov_stale(unsigned int cpu, s64 age_ns);
void a52_sched_diag_sugov_freq(unsigned int cpu, unsigned long util,
			       unsigned long max, unsigned int old_freq,
			       unsigned int new_freq);
'''
sh = replace_once(sh, decl_anchor, decl_block, "scheduler recorder declarations")


# ---------------------------------------------------------------------------
# Uclamp helper telemetry. The static-key fast path stays first; when recorder
# level is 0 this adds no function calls at all.
# ---------------------------------------------------------------------------
inactive_old = r'''	if (likely(!uclamp_is_used()))
		return util;
'''
inactive_new = r'''	if (likely(!uclamp_is_used())) {
		if (unlikely(a52_sched_diag_enabled()))
			a52_sched_diag_uclamp(util, 0, SCHED_CAPACITY_SCALE,
					     util, false);
		return util;
	}
'''
sh = replace_once(sh, inactive_old, inactive_new, "uclamp inactive telemetry")

ret_old = r'''	if (unlikely(min_util >= max_util))
		return min_util;

	return clamp(util, min_util, max_util);
'''
ret_new = r'''	if (unlikely(min_util >= max_util)) {
		if (unlikely(a52_sched_diag_enabled()))
			a52_sched_diag_uclamp(util, min_util, max_util,
					     min_util, true);
		return min_util;
	}

	{
		unsigned long clamped = clamp(util, min_util, max_util);

		if (unlikely(a52_sched_diag_enabled()))
			a52_sched_diag_uclamp(util, min_util, max_util,
					     clamped, true);
		return clamped;
	}
'''
sh = replace_once(sh, ret_old, ret_new, "uclamp active telemetry")
sched_h.write_text(sh)


# ---------------------------------------------------------------------------
# Core recorder implementation and debugfs interface.
# Levels:
#   0 = off
#   1 = aggregate counters
#   2 = aggregates + bounded sampled per-CPU event ring
# ---------------------------------------------------------------------------
cc = core.read_text()
if "#include <linux/seq_file.h>" not in cc:
    cc = replace_once(
        cc,
        "#include <linux/nospec.h>\n",
        "#include <linux/nospec.h>\n#include <linux/seq_file.h>\n#include <linux/ktime.h>\n",
        "recorder core includes",
    )

impl_anchor = "DEFINE_PER_CPU_SHARED_ALIGNED(struct rq, runqueues);\n"
impl = r'''DEFINE_PER_CPU_SHARED_ALIGNED(struct rq, runqueues);

/* A52 scheduler efficiency recorder */
#define A52_SCHED_REC_RING_ORDER	7
#define A52_SCHED_REC_RING_SIZE		(1U << A52_SCHED_REC_RING_ORDER)
#define A52_SCHED_REC_RING_MASK		(A52_SCHED_REC_RING_SIZE - 1)

enum a52_sched_rec_type {
	A52_REC_CASS = 1,
	A52_REC_UCLAMP = 2,
	A52_REC_SUGOV_STALE = 3,
	A52_REC_SUGOV_FREQ = 4,
};

struct a52_sched_rec_event {
	u64 ts_ns;
	u32 a;
	u32 b;
	u32 c;
	u32 d;
	pid_t pid;
	u16 cpu;
	u8 type;
	u8 flags;
};

struct a52_sched_rec_cpu {
	u64 cass_calls;
	u64 cass_sync;
	u64 cass_uclamp;
	u64 cass_candidates;
	u64 cass_idle_candidates;
	u64 cass_under_ucmin;
	u64 cass_under_task;
	u64 cass_migrate;
	u64 cass_same;
	u64 cass_selected[NR_CPUS];

	u64 uclamp_calls;
	u64 uclamp_inactive;
	u64 uclamp_changed;
	u64 uclamp_min_raise;
	u64 uclamp_max_cap;

	u64 sugov_stale_drops;
	u64 sugov_freq_calls;
	u64 sugov_freq_up;
	u64 sugov_freq_down;
	u64 sugov_freq_same;

	u32 head;
	u32 count;
	u32 sample;
	struct a52_sched_rec_event event[A52_SCHED_REC_RING_SIZE];
};

int a52_sched_diag_level;
DEFINE_PER_CPU(struct a52_sched_rec_cpu, a52_sched_rec_cpu);

static void a52_sched_rec_event(u8 type, u8 flags,
				u32 a, u32 b, u32 c, u32 d)
{
	struct a52_sched_rec_cpu *rec;
	struct a52_sched_rec_event *event;
	u32 slot;

	if (unlikely(READ_ONCE(a52_sched_diag_level) < 2))
		return;

	preempt_disable();
	rec = this_cpu_ptr(&a52_sched_rec_cpu);

	/* Keep interesting events; sample one in 32 ordinary events. */
	if (!(flags & 0x80) && (rec->sample++ & 31)) {
		preempt_enable();
		return;
	}

	slot = rec->head++ & A52_SCHED_REC_RING_MASK;
	if (rec->count < A52_SCHED_REC_RING_SIZE)
		rec->count++;

	event = &rec->event[slot];
	event->ts_ns = ktime_get_ns();
	event->a = a;
	event->b = b;
	event->c = c;
	event->d = d;
	event->pid = current->pid;
	event->cpu = raw_smp_processor_id();
	event->type = type;
	event->flags = flags;
	preempt_enable();
}

void a52_sched_diag_cass(unsigned int best_cpu, unsigned int prev_cpu,
			 bool sync, unsigned long p_util,
			 unsigned long uc_min, unsigned long uc_max,
			 unsigned int candidates, unsigned int idle_candidates,
			 unsigned int under_ucmin, unsigned int under_task)
{
	struct a52_sched_rec_cpu *rec;
	u8 flags = 0;

	if (!a52_sched_diag_enabled())
		return;

	preempt_disable();
	rec = this_cpu_ptr(&a52_sched_rec_cpu);
	rec->cass_calls++;
	rec->cass_candidates += candidates;
	rec->cass_idle_candidates += idle_candidates;
	rec->cass_under_ucmin += under_ucmin;
	rec->cass_under_task += under_task;

	if (sync)
		rec->cass_sync++;
	if (uc_min)
		rec->cass_uclamp++;
	if (best_cpu == prev_cpu)
		rec->cass_same++;
	else
		rec->cass_migrate++;
	if (best_cpu < NR_CPUS)
		rec->cass_selected[best_cpu]++;
	preempt_enable();

	if (sync)
		flags |= 0x01;
	if (uc_min)
		flags |= 0x02;
	if (best_cpu != prev_cpu)
		flags |= 0x04;
	if (under_ucmin || under_task)
		flags |= 0x80;

	a52_sched_rec_event(A52_REC_CASS, flags, best_cpu, prev_cpu,
			    (u32)p_util, (u32)uc_min);
}

void a52_sched_diag_uclamp(unsigned long before, unsigned long min_util,
			   unsigned long max_util, unsigned long after,
			   bool active)
{
	struct a52_sched_rec_cpu *rec;
	u8 flags = 0;

	if (!a52_sched_diag_enabled())
		return;

	preempt_disable();
	rec = this_cpu_ptr(&a52_sched_rec_cpu);
	rec->uclamp_calls++;
	if (!active)
		rec->uclamp_inactive++;
	if (after != before)
		rec->uclamp_changed++;
	if (after > before)
		rec->uclamp_min_raise++;
	if (after < before)
		rec->uclamp_max_cap++;
	preempt_enable();

	if (active)
		flags |= 0x01;
	if (after != before)
		flags |= 0x80;

	a52_sched_rec_event(A52_REC_UCLAMP, flags, (u32)before,
			    (u32)min_util, (u32)max_util, (u32)after);
}

void a52_sched_diag_sugov_stale(unsigned int cpu, s64 age_ns)
{
	struct a52_sched_rec_cpu *rec;

	if (!a52_sched_diag_enabled())
		return;

	preempt_disable();
	rec = this_cpu_ptr(&a52_sched_rec_cpu);
	rec->sugov_stale_drops++;
	preempt_enable();

	a52_sched_rec_event(A52_REC_SUGOV_STALE, 0x80, cpu,
			    (u32)(age_ns / NSEC_PER_USEC), 0, 0);
}

void a52_sched_diag_sugov_freq(unsigned int cpu, unsigned long util,
			       unsigned long max, unsigned int old_freq,
			       unsigned int new_freq)
{
	struct a52_sched_rec_cpu *rec;
	u8 flags = 0;

	if (!a52_sched_diag_enabled())
		return;

	preempt_disable();
	rec = this_cpu_ptr(&a52_sched_rec_cpu);
	rec->sugov_freq_calls++;
	if (new_freq > old_freq) {
		rec->sugov_freq_up++;
		flags |= 0x01;
	} else if (new_freq < old_freq) {
		rec->sugov_freq_down++;
		flags |= 0x02;
	} else {
		rec->sugov_freq_same++;
	}
	preempt_enable();

	a52_sched_rec_event(A52_REC_SUGOV_FREQ, flags, cpu, (u32)util,
			    old_freq, new_freq);
}

static int a52_sched_diag_record_get(void *data, u64 *val)
{
	*val = READ_ONCE(a52_sched_diag_level);
	return 0;
}

static int a52_sched_diag_record_set(void *data, u64 val)
{
	if (val > 2)
		return -EINVAL;

	WRITE_ONCE(a52_sched_diag_level, (int)val);
	return 0;
}

DEFINE_SIMPLE_ATTRIBUTE(a52_sched_diag_record_fops,
			a52_sched_diag_record_get,
			a52_sched_diag_record_set, "%llu\n");

static void a52_sched_diag_reset_all(void)
{
	int cpu;

	for_each_possible_cpu(cpu)
		memset(per_cpu_ptr(&a52_sched_rec_cpu, cpu), 0,
		       sizeof(struct a52_sched_rec_cpu));
}

static int a52_sched_diag_reset_get(void *data, u64 *val)
{
	*val = 0;
	return 0;
}

static int a52_sched_diag_reset_set(void *data, u64 val)
{
	if (val != 1)
		return -EINVAL;

	a52_sched_diag_reset_all();
	return 0;
}

DEFINE_SIMPLE_ATTRIBUTE(a52_sched_diag_reset_fops,
			a52_sched_diag_reset_get,
			a52_sched_diag_reset_set, "%llu\n");

static int a52_sched_diag_stats_show(struct seq_file *m, void *v)
{
	u64 cass_calls = 0, cass_sync = 0, cass_uclamp = 0;
	u64 cass_candidates = 0, cass_idle = 0;
	u64 cass_under_ucmin = 0, cass_under_task = 0;
	u64 cass_migrate = 0, cass_same = 0;
	u64 uclamp_calls = 0, uclamp_inactive = 0, uclamp_changed = 0;
	u64 uclamp_min_raise = 0, uclamp_max_cap = 0;
	u64 sugov_stale = 0, sugov_freq = 0;
	u64 sugov_up = 0, sugov_down = 0, sugov_same = 0;
	u64 selected[NR_CPUS] = { 0 };
	int cpu, dst;

	for_each_possible_cpu(cpu) {
		struct a52_sched_rec_cpu *rec =
			per_cpu_ptr(&a52_sched_rec_cpu, cpu);

		cass_calls += rec->cass_calls;
		cass_sync += rec->cass_sync;
		cass_uclamp += rec->cass_uclamp;
		cass_candidates += rec->cass_candidates;
		cass_idle += rec->cass_idle_candidates;
		cass_under_ucmin += rec->cass_under_ucmin;
		cass_under_task += rec->cass_under_task;
		cass_migrate += rec->cass_migrate;
		cass_same += rec->cass_same;

		for (dst = 0; dst < NR_CPUS; dst++)
			selected[dst] += rec->cass_selected[dst];

		uclamp_calls += rec->uclamp_calls;
		uclamp_inactive += rec->uclamp_inactive;
		uclamp_changed += rec->uclamp_changed;
		uclamp_min_raise += rec->uclamp_min_raise;
		uclamp_max_cap += rec->uclamp_max_cap;

		sugov_stale += rec->sugov_stale_drops;
		sugov_freq += rec->sugov_freq_calls;
		sugov_up += rec->sugov_freq_up;
		sugov_down += rec->sugov_freq_down;
		sugov_same += rec->sugov_freq_same;
	}

	seq_printf(m,
		   "level=%d\n"
		   "cass_calls=%llu\ncass_sync=%llu\ncass_uclamp=%llu\n"
		   "cass_candidates=%llu\ncass_idle_candidates=%llu\n"
		   "cass_under_ucmin=%llu\ncass_under_task=%llu\n"
		   "cass_migrate=%llu\ncass_same=%llu\n"
		   "uclamp_calls=%llu\nuclamp_inactive_bypass=%llu\n"
		   "uclamp_changed=%llu\nuclamp_min_raise=%llu\n"
		   "uclamp_max_cap=%llu\n"
		   "sugov_stale_drops=%llu\nsugov_freq_calls=%llu\n"
		   "sugov_freq_up=%llu\nsugov_freq_down=%llu\n"
		   "sugov_freq_same=%llu\n",
		   READ_ONCE(a52_sched_diag_level),
		   cass_calls, cass_sync, cass_uclamp,
		   cass_candidates, cass_idle, cass_under_ucmin,
		   cass_under_task, cass_migrate, cass_same,
		   uclamp_calls, uclamp_inactive, uclamp_changed,
		   uclamp_min_raise, uclamp_max_cap,
		   sugov_stale, sugov_freq, sugov_up, sugov_down, sugov_same);

	for (dst = 0; dst < nr_cpu_ids; dst++)
		seq_printf(m, "cass_selected_cpu%d=%llu\n", dst, selected[dst]);

	return 0;
}

static int a52_sched_diag_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_sched_diag_stats_show, inode->i_private);
}

static const struct file_operations a52_sched_diag_stats_fops = {
	.owner = THIS_MODULE,
	.open = a52_sched_diag_stats_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int a52_sched_diag_events_show(struct seq_file *m, void *v)
{
	int cpu;

	seq_printf(m, "level=%d ring_per_cpu=%u sample=1/32\n",
		   READ_ONCE(a52_sched_diag_level), A52_SCHED_REC_RING_SIZE);
	seq_puts(m, "ts_ns cpu pid event flags a b c d\n");

	for_each_possible_cpu(cpu) {
		struct a52_sched_rec_cpu *rec =
			per_cpu_ptr(&a52_sched_rec_cpu, cpu);
		u32 count = min(rec->count, A52_SCHED_REC_RING_SIZE);
		u32 first = rec->head - count;
		u32 i;

		for (i = 0; i < count; i++) {
			struct a52_sched_rec_event *event =
				&rec->event[(first + i) & A52_SCHED_REC_RING_MASK];
			const char *name;

			if (!event->ts_ns)
				continue;

			switch (event->type) {
			case A52_REC_CASS:
				name = "cass";
				break;
			case A52_REC_UCLAMP:
				name = "uclamp";
				break;
			case A52_REC_SUGOV_STALE:
				name = "sugov_stale";
				break;
			case A52_REC_SUGOV_FREQ:
				name = "sugov_freq";
				break;
			default:
				name = "?";
				break;
			}

			seq_printf(m, "%llu %u %d %s 0x%x %u %u %u %u\n",
				   event->ts_ns, event->cpu, event->pid, name,
				   event->flags, event->a, event->b,
				   event->c, event->d);
		}
	}

	return 0;
}

static int a52_sched_diag_events_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_sched_diag_events_show, inode->i_private);
}

static const struct file_operations a52_sched_diag_events_fops = {
	.owner = THIS_MODULE,
	.open = a52_sched_diag_events_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init a52_sched_diag_init(void)
{
	struct dentry *dir;

	dir = debugfs_create_dir("a52_sched_diag", NULL);
	if (IS_ERR_OR_NULL(dir))
		return 0;

	debugfs_create_file("record", 0644, dir, NULL,
			    &a52_sched_diag_record_fops);
	debugfs_create_file("reset", 0644, dir, NULL,
			    &a52_sched_diag_reset_fops);
	debugfs_create_file("stats", 0444, dir, NULL,
			    &a52_sched_diag_stats_fops);
	debugfs_create_file("events", 0444, dir, NULL,
			    &a52_sched_diag_events_fops);

	return 0;
}
late_initcall(a52_sched_diag_init);

'''
cc = replace_once(cc, impl_anchor, impl, "scheduler recorder implementation")
core.write_text(cc)


# ---------------------------------------------------------------------------
# CASS: one aggregate record per placement call. Avoid per-candidate function
# calls; collect local work units and emit once after the winner is known.
# ---------------------------------------------------------------------------
ca = cass.read_text()
if "A52 CASS P3: uclamp-fit + sync-waker + no task double-count active" not in ca:
    raise SystemExit("CASS P3 marker missing before recorder")

locals_old = r'''    bool has_idle = false;
    int cidx = 0, cpu;
'''
locals_new = r'''    bool has_idle = false;
    bool a52_diag = a52_sched_diag_enabled();
    unsigned int a52_candidates = 0;
    unsigned int a52_idle_candidates = 0;
    unsigned int a52_under_ucmin = 0;
    unsigned int a52_under_task = 0;
    int cidx = 0, cpu;
'''
ca = replace_once(ca, locals_old, locals_new, "CASS recorder locals")

cap_old = r'''        curr->cpu = cpu;
        curr->cap_max = max_t(unsigned long, capacity_orig_of(cpu), 1UL);
'''
cap_new = r'''        curr->cpu = cpu;
        curr->cap_max = max_t(unsigned long, capacity_orig_of(cpu), 1UL);

        if (unlikely(a52_diag)) {
            a52_candidates++;
            if (curr->cap_max < uc_min)
                a52_under_ucmin++;
            if (curr->cap_max < p_util)
                a52_under_task++;
        }
'''
ca = replace_once(ca, cap_old, cap_new, "CASS candidate telemetry")

idle_old = r'''            curr->exit_lat = 1;
            idle_state = idle_get_state(rq);
'''
idle_new = r'''            curr->exit_lat = 1;
            if (unlikely(a52_diag))
                a52_idle_candidates++;
            idle_state = idle_get_state(rq);
'''
ca = replace_once(ca, idle_old, idle_new, "CASS idle telemetry")

ret_old = r'''    rcu_read_unlock();
    return best->cpu;
}
'''
ret_new = r'''    rcu_read_unlock();

    if (unlikely(a52_diag))
        a52_sched_diag_cass(best->cpu, prev_cpu, sync, p_util,
                            uc_min, uc_max, a52_candidates,
                            a52_idle_candidates, a52_under_ucmin,
                            a52_under_task);

    return best->cpu;
}
'''
ca = replace_once(ca, ret_old, ret_new, "CASS final telemetry")
cass.write_text(ca)


# ---------------------------------------------------------------------------
# WALT/schedutil: stale sibling drops and newly resolved frequency requests.
# ---------------------------------------------------------------------------
sg = sugov.read_text()
if "A52 WALT schedutil P2: expire stale sibling demand by wall time." not in sg:
    raise SystemExit("WALT schedutil P2 marker missing before recorder")

stale_old = r'''		if (delta_ns > stale_ns) {
			sugov_iowait_reset(j_sg_cpu, time, false);
			continue;
		}
'''
stale_new = r'''		if (delta_ns > stale_ns) {
			if (unlikely(a52_sched_diag_enabled()))
				a52_sched_diag_sugov_stale(j, delta_ns);
			sugov_iowait_reset(j_sg_cpu, time, false);
			continue;
		}
'''
sg = replace_once(sg, stale_old, stale_new, "schedutil stale telemetry")

freq_old = r'''	sg_policy->need_freq_update = false;
	sg_policy->prev_cached_raw_freq = sg_policy->cached_raw_freq;
	sg_policy->cached_raw_freq = freq;
	return cpufreq_driver_resolve_freq(policy, freq);
}
'''
freq_new = r'''	sg_policy->need_freq_update = false;
	sg_policy->prev_cached_raw_freq = sg_policy->cached_raw_freq;
	sg_policy->cached_raw_freq = freq;

	{
		unsigned int resolved = cpufreq_driver_resolve_freq(policy, freq);

		if (unlikely(a52_sched_diag_enabled()))
			a52_sched_diag_sugov_freq(policy->cpu, util, max,
						  sg_policy->next_freq, resolved);
		return resolved;
	}
}
'''
sg = replace_once(sg, freq_old, freq_new, "schedutil frequency telemetry")
sugov.write_text(sg)


checks = {
    sched_h: [
        "A52 scheduler efficiency recorder",
        "a52_sched_diag_uclamp(util, 0, SCHED_CAPACITY_SCALE",
        "a52_sched_diag_uclamp(util, min_util, max_util",
    ],
    core: [
        "A52 scheduler efficiency recorder",
        'debugfs_create_dir("a52_sched_diag", NULL)',
        'debugfs_create_file("record"',
        'debugfs_create_file("stats"',
        'debugfs_create_file("events"',
        "cass_under_ucmin=",
        "uclamp_inactive_bypass=",
        "sugov_stale_drops=",
    ],
    cass: [
        "bool a52_diag = a52_sched_diag_enabled();",
        "a52_sched_diag_cass(best->cpu",
    ],
    sugov: [
        "a52_sched_diag_sugov_stale(j, delta_ns);",
        "a52_sched_diag_sugov_freq(policy->cpu, util, max",
    ],
}
for p, needles in checks.items():
    text = p.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{p}: missing scheduler recorder element: {needle!r}")

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "a52-scheduler-recorder.txt").write_text(
    "default_level=0\n"
    "levels=0-off,1-aggregates,2-aggregates-plus-events\n"
    "root=/sys/kernel/debug/a52_sched_diag\n"
    "record=/sys/kernel/debug/a52_sched_diag/record\n"
    "reset=/sys/kernel/debug/a52_sched_diag/reset\n"
    "stats=/sys/kernel/debug/a52_sched_diag/stats\n"
    "events=/sys/kernel/debug/a52_sched_diag/events\n"
    "cass=placement,sync,uclamp,candidates,idle,underfit,migration,selected-cpu\n"
    "uclamp=inactive-bypass,changed,min-raise,max-cap\n"
    "walt_schedutil=stale-sibling-drops,freq-up-down-same\n"
    "event_sampling=1-in-32 ordinary; interesting always\n"
)

print("A52 scheduler efficiency recorder applied")
print("default_level=0")
print("record=/sys/kernel/debug/a52_sched_diag/record")
print("stats=/sys/kernel/debug/a52_sched_diag/stats")
print("events=/sys/kernel/debug/a52_sched_diag/events")
