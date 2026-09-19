#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 112_apply_mglru_power_recorder_p2.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"
if not vmscan.is_file():
    raise SystemExit(f"missing required file: {vmscan}")

s = vmscan.read_text()

for marker in (
    "/* A52 MGLRU power diagnostics P1 */",
    "A52 MGLRU Modern P3: upstream c28ac3c7 skip special VMAs",
    "__ATTR(diag, 0644, mglru_diag_show, mglru_diag_store)",
):
    if marker not in s:
        raise SystemExit(f"required P1/P3 diagnostic marker missing: {marker}")

marker = "/* A52 MGLRU power recorder P2 */"
if marker in s:
    raise SystemExit("MGLRU power recorder P2 already applied")

def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)

def function_bounds(text, name):
    m = re.search(rf"(?m)^[ \t]*(?:static[ \t]+)?(?:inline[ \t]+)?[^\n;]*\b{re.escape(name)}[ \t]*\([^;]*?\)\s*\{{", text, re.S)
    if not m:
        raise SystemExit(f"{name}: definition not found")
    brace = text.find("{", m.start(), m.end())
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return m.start(), brace, i + 1
        i += 1
    raise SystemExit(f"{name}: unbalanced braces")

if "#include <linux/ktime.h>" not in s:
    s = replace_once(
        s,
        "#include <linux/debugfs.h>\n",
        "#include <linux/debugfs.h>\n#include <linux/ktime.h>\n",
        "ktime include",
    )

decl_anchor = "DEFINE_PER_CPU(unsigned long, mglru_diag_evict_pages);\n\n"
recorder_decl = r'''DEFINE_PER_CPU(unsigned long, mglru_diag_evict_pages);

/* A52 MGLRU power recorder P2 */
#define MGLRU_REC_RING_ORDER 8
#define MGLRU_REC_RING_SIZE (1U << MGLRU_REC_RING_ORDER)
#define MGLRU_REC_RING_MASK (MGLRU_REC_RING_SIZE - 1)
#define MGLRU_REC_SLOW_NS 500000UL

enum mglru_rec_type {
	MGLRU_REC_LOOK = 1,
	MGLRU_REC_SCAN = 2,
	MGLRU_REC_EVICT = 3,
};

struct mglru_rec_event {
	unsigned long ts_ns;
	unsigned long dur_ns;
	unsigned long a;
	unsigned long b;
	unsigned long c;
	pid_t pid;
	unsigned short cpu;
	unsigned char type;
	unsigned char flags;
};

struct mglru_rec_ring {
	unsigned int head;
	unsigned int count;
	unsigned int sample;
	struct mglru_rec_event event[MGLRU_REC_RING_SIZE];
};

static int mglru_record_level;

DEFINE_PER_CPU(struct mglru_rec_ring, mglru_rec_ring);
DEFINE_PER_CPU(unsigned long, mglru_rec_look_ptes);
DEFINE_PER_CPU(unsigned long, mglru_rec_look_young);
DEFINE_PER_CPU(unsigned long, mglru_rec_look_promoted);
DEFINE_PER_CPU(unsigned long, mglru_rec_scan_scanned);
DEFINE_PER_CPU(unsigned long, mglru_rec_scan_sorted);
DEFINE_PER_CPU(unsigned long, mglru_rec_scan_isolated);
DEFINE_PER_CPU(unsigned long, mglru_rec_evict_scanned);
DEFINE_PER_CPU(unsigned long, mglru_rec_evict_reclaimed);
DEFINE_PER_CPU(unsigned long, mglru_rec_look_time_ns);
DEFINE_PER_CPU(unsigned long, mglru_rec_scan_time_ns);
DEFINE_PER_CPU(unsigned long, mglru_rec_evict_time_ns);
DEFINE_PER_CPU(unsigned long, mglru_rec_look_slow);
DEFINE_PER_CPU(unsigned long, mglru_rec_scan_slow);
DEFINE_PER_CPU(unsigned long, mglru_rec_evict_slow);

static void mglru_rec_event(unsigned char type, unsigned char flags,
			    unsigned long a, unsigned long b, unsigned long c,
			    unsigned long dur_ns)
{
	struct mglru_rec_ring *ring;
	struct mglru_rec_event *event;
	unsigned int slot;

	if (unlikely(READ_ONCE(mglru_record_level) < 3))
		return;

	preempt_disable();
	ring = this_cpu_ptr(&mglru_rec_ring);

	/* Keep every slow event and sample one in 16 ordinary events. */
	if (dur_ns < MGLRU_REC_SLOW_NS && (ring->sample++ & 15)) {
		preempt_enable();
		return;
	}

	slot = ring->head++ & MGLRU_REC_RING_MASK;
	if (ring->count < MGLRU_REC_RING_SIZE)
		ring->count++;

	event = &ring->event[slot];
	event->ts_ns = (unsigned long)ktime_get_ns();
	event->dur_ns = dur_ns;
	event->a = a;
	event->b = b;
	event->c = c;
	event->pid = current->pid;
	event->cpu = raw_smp_processor_id();
	event->type = type;
	event->flags = flags;
	preempt_enable();
}

'''
s = replace_once(s, decl_anchor, recorder_decl, "recorder declarations")

# P1's original path counters were unconditional. Once P2 provides the runtime
# level, gate those increments too so level 0 is a true low-overhead state.
for a52_counter in (
    "try_inc_max_seq",
    "walk_pte_range",
    "walk_pmd_range",
    "look_around",
    "scan_pages",
    "evict_pages",
):
    a52_old = f"this_cpu_inc(mglru_diag_{a52_counter}); /* A52 MGLRU diag */"
    a52_new = (
        "if (unlikely(READ_ONCE(mglru_record_level) >= 1))\n"
        f"\t\tthis_cpu_inc(mglru_diag_{a52_counter}); /* A52 MGLRU diag */"
    )
    if s.count(a52_old) != 1:
        raise SystemExit(
            f"MGLRU diag gate {a52_counter}: expected one counter, found {s.count(a52_old)}"
        )
    s = s.replace(a52_old, a52_new, 1)

def replace_func(text, name, mutate):
    start, brace, end = function_bounds(text, name)
    func = text[start:end]
    new = mutate(func)
    if new == func:
        raise SystemExit(f"{name}: recorder mutation made no change")
    return text[:start] + new + text[end:]

def add_start_decl(func):
    pos = func.find("{")
    return func[:pos + 1] + (
        "\n\tunsigned long a52_rec_start_ns = "
        "unlikely(READ_ONCE(mglru_record_level) >= 2) ? "
        "(unsigned long)ktime_get_ns() : 0;"
    ) + func[pos + 1:]

def mutate_look(func):
    func = add_start_decl(func)
    pos = func.find("{")
    func = func[:pos + 1] + (
        "\n\tunsigned long a52_rec_ptes = 0;"
        "\n\tunsigned long a52_rec_young = 0;"
        "\n\tunsigned long a52_rec_promoted = 0;"
    ) + func[pos + 1:]

    loop = "for (i = 0, addr = start; addr != end; i++, addr += PAGE_SIZE) {"
    if loop not in func:
        raise SystemExit("look_around: PTE loop anchor missing")
    func = func.replace(
        loop,
        loop + "\n\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1))\n\t\t\ta52_rec_ptes++;",
        1,
    )

    clear = (
        "if (!ptep_clear_young_notify(pvmw->vma, addr, pte + i))\n"
        "\t\t\tcontinue;"
    )
    if clear not in func:
        raise SystemExit("look_around: notifier-aware young-clear anchor missing")
    func = func.replace(
        clear,
        clear + "\n\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1))\n\t\t\ta52_rec_young++;",
        1,
    )

    promote = (
        "old_gen = page_update_gen(page, new_gen);\n"
        "\t\tif (old_gen < 0 || old_gen == new_gen)\n"
        "\t\t\tcontinue;"
    )
    if promote in func:
        func = func.replace(
            promote,
            promote + "\n\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1))\n\t\t\ta52_rec_promoted++;",
            1,
        )

    end = func.rfind("}")
    done = r'''
	if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
		unsigned long a52_dur = 0;

		this_cpu_add(mglru_rec_look_ptes, a52_rec_ptes);
		this_cpu_add(mglru_rec_look_young, a52_rec_young);
		this_cpu_add(mglru_rec_look_promoted, a52_rec_promoted);

		if (a52_rec_start_ns) {
			a52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
			this_cpu_add(mglru_rec_look_time_ns, a52_dur);
			if (a52_dur >= MGLRU_REC_SLOW_NS)
				this_cpu_inc(mglru_rec_look_slow);
		}
		mglru_rec_event(MGLRU_REC_LOOK, 0, a52_rec_ptes,
				a52_rec_young, a52_rec_promoted, a52_dur);
	}
'''
    return func[:end] + done + func[end:]

def mutate_scan(func):
    func = add_start_decl(func)
    returns = list(re.finditer(r"(?m)^(\s*)return\s+scanned\s*;", func))
    if len(returns) != 1:
        raise SystemExit(f"scan_pages: expected one return scanned, found {len(returns)}")
    m = returns[0]
    indent = m.group(1)
    done = r'''if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
			unsigned long a52_dur = 0;

			this_cpu_add(mglru_rec_scan_scanned, scanned);
			this_cpu_add(mglru_rec_scan_sorted, sorted);
			this_cpu_add(mglru_rec_scan_isolated, isolated);
			if (a52_rec_start_ns) {
				a52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
				this_cpu_add(mglru_rec_scan_time_ns, a52_dur);
				if (a52_dur >= MGLRU_REC_SLOW_NS)
					this_cpu_inc(mglru_rec_scan_slow);
			}
			mglru_rec_event(MGLRU_REC_SCAN,
					(unsigned char)((type & 0xf) | ((tier & 0xf) << 4)),
					scanned, sorted, isolated, a52_dur);
		}
		'''
    return func[:m.start()] + indent + done + "return scanned;" + func[m.end():]

def mutate_evict(func):
    func = add_start_decl(func)

    early = "if (list_empty(&list))\n\t\treturn scanned;"
    if early not in func:
        raise SystemExit("evict_pages: empty-list return anchor missing")
    early_new = r'''if (list_empty(&list)) {
		if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
			unsigned long a52_dur = 0;

			this_cpu_add(mglru_rec_evict_scanned, scanned);
			if (a52_rec_start_ns) {
				a52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
				this_cpu_add(mglru_rec_evict_time_ns, a52_dur);
				if (a52_dur >= MGLRU_REC_SLOW_NS)
					this_cpu_inc(mglru_rec_evict_slow);
			}
			mglru_rec_event(MGLRU_REC_EVICT, 0, scanned, 0, 0, a52_dur);
		}
		return scanned;
	}'''
    func = func.replace(early, early_new, 1)

    returns = list(re.finditer(r"(?m)^(\s*)return\s+scanned\s*;", func))
    if len(returns) != 2:
        raise SystemExit(f"evict_pages: expected two return scanned sites after early expansion, found {len(returns)}")
    m = returns[-1]
    indent = m.group(1)
    done = r'''if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
			unsigned long a52_dur = 0;

			this_cpu_add(mglru_rec_evict_scanned, scanned);
			this_cpu_add(mglru_rec_evict_reclaimed, reclaimed);
			if (a52_rec_start_ns) {
				a52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
				this_cpu_add(mglru_rec_evict_time_ns, a52_dur);
				if (a52_dur >= MGLRU_REC_SLOW_NS)
					this_cpu_inc(mglru_rec_evict_slow);
			}
			mglru_rec_event(MGLRU_REC_EVICT, 0, scanned, reclaimed,
					sc->nr_reclaimed, a52_dur);
		}
		'''
    return func[:m.start()] + indent + done + "return scanned;" + func[m.end():]

s = replace_func(s, "lru_gen_look_around", mutate_look)
s = replace_func(s, "scan_pages", mutate_scan)
s = replace_func(s, "evict_pages", mutate_evict)

attrs_anchor = "static struct attribute *lru_gen_attrs[] = {"
attrs_pos = s.find(attrs_anchor)
if attrs_pos < 0:
    raise SystemExit("lru_gen_attrs anchor missing for recorder sysfs")

sysfs = r'''/* A52 MGLRU power recorder P2 controls and aggregate statistics. */
static void mglru_rec_zero(unsigned long __percpu *counter)
{
	int cpu;

	for_each_possible_cpu(cpu)
		per_cpu_ptr(counter, cpu)[0] = 0;
}

static void mglru_rec_reset_all(void)
{
	int cpu;

	mglru_rec_zero(&mglru_rec_look_ptes);
	mglru_rec_zero(&mglru_rec_look_young);
	mglru_rec_zero(&mglru_rec_look_promoted);
	mglru_rec_zero(&mglru_rec_scan_scanned);
	mglru_rec_zero(&mglru_rec_scan_sorted);
	mglru_rec_zero(&mglru_rec_scan_isolated);
	mglru_rec_zero(&mglru_rec_evict_scanned);
	mglru_rec_zero(&mglru_rec_evict_reclaimed);
	mglru_rec_zero(&mglru_rec_look_time_ns);
	mglru_rec_zero(&mglru_rec_scan_time_ns);
	mglru_rec_zero(&mglru_rec_evict_time_ns);
	mglru_rec_zero(&mglru_rec_look_slow);
	mglru_rec_zero(&mglru_rec_scan_slow);
	mglru_rec_zero(&mglru_rec_evict_slow);

	for_each_possible_cpu(cpu) {
		struct mglru_rec_ring *ring = per_cpu_ptr(&mglru_rec_ring, cpu);

		memset(ring, 0, sizeof(*ring));
	}
}

static ssize_t mglru_record_show(struct kobject *kobj,
				 struct kobj_attribute *attr, char *buf)
{
	return scnprintf(buf, PAGE_SIZE, "%d\n", READ_ONCE(mglru_record_level));
}

static ssize_t mglru_record_store(struct kobject *kobj,
				  struct kobj_attribute *attr,
				  const char *buf, size_t len)
{
	int level;

	if (kstrtoint(buf, 0, &level))
		return -EINVAL;
	if (level < 0 || level > 3)
		return -EINVAL;

	WRITE_ONCE(mglru_record_level, level);
	return len;
}

static ssize_t mglru_rec_stats_show(struct kobject *kobj,
				    struct kobj_attribute *attr, char *buf)
{
	return scnprintf(buf, PAGE_SIZE,
		"level=%d\n"
		"look_ptes=%llu\nlook_young=%llu\nlook_promoted=%llu\n"
		"scan_scanned=%llu\nscan_sorted=%llu\nscan_isolated=%llu\n"
		"evict_scanned=%llu\nevict_reclaimed=%llu\n"
		"look_time_ns=%llu\nscan_time_ns=%llu\nevict_time_ns=%llu\n"
		"look_slow_500us=%llu\nscan_slow_500us=%llu\nevict_slow_500us=%llu\n",
		READ_ONCE(mglru_record_level),
		mglru_diag_sum(&mglru_rec_look_ptes),
		mglru_diag_sum(&mglru_rec_look_young),
		mglru_diag_sum(&mglru_rec_look_promoted),
		mglru_diag_sum(&mglru_rec_scan_scanned),
		mglru_diag_sum(&mglru_rec_scan_sorted),
		mglru_diag_sum(&mglru_rec_scan_isolated),
		mglru_diag_sum(&mglru_rec_evict_scanned),
		mglru_diag_sum(&mglru_rec_evict_reclaimed),
		mglru_diag_sum(&mglru_rec_look_time_ns),
		mglru_diag_sum(&mglru_rec_scan_time_ns),
		mglru_diag_sum(&mglru_rec_evict_time_ns),
		mglru_diag_sum(&mglru_rec_look_slow),
		mglru_diag_sum(&mglru_rec_scan_slow),
		mglru_diag_sum(&mglru_rec_evict_slow));
}

static ssize_t mglru_rec_stats_store(struct kobject *kobj,
				     struct kobj_attribute *attr,
				     const char *buf, size_t len)
{
	if (!sysfs_streq(buf, "reset"))
		return -EINVAL;

	mglru_rec_reset_all();
	return len;
}

static struct kobj_attribute lru_gen_record_attr =
	__ATTR(record, 0644, mglru_record_show, mglru_record_store);
static struct kobj_attribute lru_gen_rec_stats_attr =
	__ATTR(stats, 0644, mglru_rec_stats_show, mglru_rec_stats_store);

'''
s = s[:attrs_pos] + sysfs + s[attrs_pos:]

s = replace_once(
    s,
    "static struct attribute *lru_gen_attrs[] = {\n\t&lru_gen_diag_attr.attr,\n",
    "static struct attribute *lru_gen_attrs[] = {\n"
    "\t&lru_gen_diag_attr.attr,\n"
    "\t&lru_gen_record_attr.attr,\n"
    "\t&lru_gen_rec_stats_attr.attr,\n",
    "recorder sysfs attributes",
)

init_anchor = "static int __init init_lru_gen(void)"
init_pos = s.find(init_anchor)
if init_pos < 0:
    raise SystemExit("init_lru_gen anchor missing")

debugfs = r'''/* A52 MGLRU recorder P2: bounded per-CPU flight recorder dump. */
static int mglru_rec_trace_show(struct seq_file *m, void *v)
{
	int cpu;

	seq_printf(m, "level=%d ring_per_cpu=%u sample=1/16 slow_ns=%lu\n",
		   READ_ONCE(mglru_record_level), MGLRU_REC_RING_SIZE,
		   MGLRU_REC_SLOW_NS);
	seq_puts(m, "ts_ns cpu pid event flags a b c dur_ns\n");

	for_each_possible_cpu(cpu) {
		struct mglru_rec_ring *ring = per_cpu_ptr(&mglru_rec_ring, cpu);
		unsigned int count = min(ring->count, MGLRU_REC_RING_SIZE);
		unsigned int first = ring->head - count;
		unsigned int i;

		for (i = 0; i < count; i++) {
			struct mglru_rec_event *event =
				&ring->event[(first + i) & MGLRU_REC_RING_MASK];
			const char *name;

			if (!event->ts_ns)
				continue;

			switch (event->type) {
			case MGLRU_REC_LOOK:
				name = "look";
				break;
			case MGLRU_REC_SCAN:
				name = "scan";
				break;
			case MGLRU_REC_EVICT:
				name = "evict";
				break;
			default:
				name = "?";
				break;
			}

			seq_printf(m, "%lu %u %d %s 0x%x %lu %lu %lu %lu\n",
				   event->ts_ns, event->cpu, event->pid, name,
				   event->flags, event->a, event->b, event->c,
				   event->dur_ns);
		}
	}

	return 0;
}

static int mglru_rec_trace_open(struct inode *inode, struct file *file)
{
	return single_open(file, mglru_rec_trace_show, inode->i_private);
}

static const struct file_operations mglru_rec_trace_fops = {
	.owner = THIS_MODULE,
	.open = mglru_rec_trace_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

'''
s = s[:init_pos] + debugfs + s[init_pos:]

dbg_anchor = 'debugfs_create_file("lru_gen_full", 0444, NULL, NULL, &lru_gen_ro_fops);'
s = replace_once(
    s,
    dbg_anchor,
    dbg_anchor + '\n\tdebugfs_create_file("lru_gen_trace", 0444, NULL, NULL, &mglru_rec_trace_fops);',
    "recorder debugfs file",
)

for required in (
    marker,
    "__ATTR(record, 0644, mglru_record_show, mglru_record_store)",
    "__ATTR(stats, 0644, mglru_rec_stats_show, mglru_rec_stats_store)",
    'debugfs_create_file("lru_gen_trace"',
    "this_cpu_add(mglru_rec_scan_scanned, scanned)",
    "this_cpu_add(mglru_rec_evict_reclaimed, reclaimed)",
    "a52_rec_young++",
):
    if required not in s:
        raise SystemExit(f"recorder structural audit missing: {required}")

vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-power-recorder-p2.txt").write_text(
    "phase=112-mglru-power-recorder-p2\n"
    "default_level=0\n"
    "levels=0-off,1-work,2-work-plus-time,3-work-time-ring\n"
    "sysfs_record=/sys/kernel/mm/lru_gen/record\n"
    "sysfs_stats=/sys/kernel/mm/lru_gen/stats\n"
    "debugfs_trace=/sys/kernel/debug/lru_gen_trace\n"
    "ring_entries_per_cpu=256\n"
    "ordinary_event_sampling=1-in-16\n"
    "slow_event_threshold_ns=500000\n"
    "look_work=ptes,young,promoted\n"
    "scan_work=scanned,sorted,isolated\n"
    "evict_work=scanned,reclaimed\n"
)

print("MGLRU power recorder P2 applied")
print("default_record_level=0")
print("record=/sys/kernel/mm/lru_gen/record")
print("stats=/sys/kernel/mm/lru_gen/stats")
print("trace=/sys/kernel/debug/lru_gen_trace")
