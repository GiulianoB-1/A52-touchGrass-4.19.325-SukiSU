#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 111_apply_mglru_power_diag_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"
if not vmscan.is_file():
    raise SystemExit(f"missing required file: {vmscan}")

s = vmscan.read_text()

# This phase is diagnostics only. It deliberately does not change any MGLRU
# policy, generation arithmetic, page selection, reclaim thresholds or runtime
# enable semantics. Per-CPU counters avoid a contended global atomic cacheline
# in hot paths; reads are approximate snapshots, which is sufficient for A/B
# rate comparisons.
marker = "/* A52 MGLRU power diagnostics P1 */"
if marker in s:
    raise SystemExit("MGLRU power diagnostics P1 already applied")

return_type = r"(?:bool|int|void|long|unsigned\s+long|unsigned\s+int|struct\s+[A-Za-z_]\w*\s*\*)"

def find_def(text: str, name: str):
    pat = re.compile(
        rf"(?m)^[ \t]*(?:static[ \t]+)?(?:inline[ \t]+)?{return_type}[ \t]+{re.escape(name)}[ \t]*\("
    )
    hits = []
    for m in pat.finditer(text):
        brace = text.find("{", m.end())
        semi = text.find(";", m.end())
        if brace < 0 or brace - m.start() > 2000:
            continue
        if semi >= 0 and semi < brace:
            continue
        hits.append((m.start(), brace))
    if len(hits) != 1:
        raise SystemExit(f"{name}: expected one function definition, found {len(hits)}")
    return hits[0]

# Only instrument MGLRU-specific / MGLRU-core helpers. Do not instrument generic
# reclaim paths because we want the 0x0000 state to be a clean control.
targets = [
    ("try_to_inc_max_seq", "try_inc_max_seq"),
    ("walk_pte_range", "walk_pte_range"),
    ("walk_pmd_range", "walk_pmd_range"),
    ("lru_gen_look_around", "look_around"),
    ("scan_pages", "scan_pages"),
    ("evict_pages", "evict_pages"),
]

# Resolve every definition before mutating text, then put declarations before
# the earliest instrumented function so every counter is visible at its use.
resolved = []
for func, counter in targets:
    start, brace = find_def(s, func)
    resolved.append((func, counter, start, brace))

earliest = min(x[2] for x in resolved)
decl = r'''/* A52 MGLRU power diagnostics P1 */
DEFINE_PER_CPU(unsigned long, mglru_diag_try_inc_max_seq);
DEFINE_PER_CPU(unsigned long, mglru_diag_walk_pte_range);
DEFINE_PER_CPU(unsigned long, mglru_diag_walk_pmd_range);
DEFINE_PER_CPU(unsigned long, mglru_diag_look_around);
DEFINE_PER_CPU(unsigned long, mglru_diag_scan_pages);
DEFINE_PER_CPU(unsigned long, mglru_diag_evict_pages);

'''
s = s[:earliest] + decl + s[earliest:]

# Insert from the bottom upward so offsets remain valid after each edit. Re-find
# definitions against the current text to avoid relying on stale offsets.
for func, counter in targets:
    _, brace = find_def(s, func)
    stmt = f"\n\tthis_cpu_inc(mglru_diag_{counter}); /* A52 MGLRU diag */"
    s = s[:brace + 1] + stmt + s[brace + 1:]

attrs_anchor = "static struct attribute *lru_gen_attrs[] = {"
attrs_pos = s.find(attrs_anchor)
if attrs_pos < 0:
    raise SystemExit("lru_gen_attrs anchor not found")

sysfs_block = r'''/* A52 MGLRU power diagnostics P1: low-overhead per-CPU call counters. */
static unsigned long long mglru_diag_sum(unsigned long __percpu *counter)
{
	unsigned long long total = 0;
	int cpu;

	for_each_possible_cpu(cpu)
		total += per_cpu_ptr(counter, cpu)[0];

	return total;
}

static void mglru_diag_zero(unsigned long __percpu *counter)
{
	int cpu;

	for_each_possible_cpu(cpu)
		per_cpu_ptr(counter, cpu)[0] = 0;
}

static ssize_t mglru_diag_show(struct kobject *kobj,
			       struct kobj_attribute *attr, char *buf)
{
	return scnprintf(buf, PAGE_SIZE,
		"try_inc_max_seq=%llu\n"
		"walk_pte_range=%llu\n"
		"walk_pmd_range=%llu\n"
		"look_around=%llu\n"
		"scan_pages=%llu\n"
		"evict_pages=%llu\n",
		mglru_diag_sum(&mglru_diag_try_inc_max_seq),
		mglru_diag_sum(&mglru_diag_walk_pte_range),
		mglru_diag_sum(&mglru_diag_walk_pmd_range),
		mglru_diag_sum(&mglru_diag_look_around),
		mglru_diag_sum(&mglru_diag_scan_pages),
		mglru_diag_sum(&mglru_diag_evict_pages));
}

static ssize_t mglru_diag_store(struct kobject *kobj,
				struct kobj_attribute *attr,
				const char *buf, size_t len)
{
	if (!sysfs_streq(buf, "reset"))
		return -EINVAL;

	mglru_diag_zero(&mglru_diag_try_inc_max_seq);
	mglru_diag_zero(&mglru_diag_walk_pte_range);
	mglru_diag_zero(&mglru_diag_walk_pmd_range);
	mglru_diag_zero(&mglru_diag_look_around);
	mglru_diag_zero(&mglru_diag_scan_pages);
	mglru_diag_zero(&mglru_diag_evict_pages);

	return len;
}

static struct kobj_attribute lru_gen_diag_attr =
	__ATTR(diag, 0644, mglru_diag_show, mglru_diag_store);

'''
s = s[:attrs_pos] + sysfs_block + s[attrs_pos:]

attrs_old = "static struct attribute *lru_gen_attrs[] = {\n"
attrs_new = "static struct attribute *lru_gen_attrs[] = {\n\t&lru_gen_diag_attr.attr,\n"
if s.count(attrs_old) != 1:
    raise SystemExit(f"lru_gen_attrs insertion: expected 1 anchor, found {s.count(attrs_old)}")
s = s.replace(attrs_old, attrs_new, 1)

# Structural audit: one declaration and exactly one instrumentation site per
# target, plus a writable diagnostic attribute with explicit reset semantics.
for func, counter in targets:
    token = f"this_cpu_inc(mglru_diag_{counter}); /* A52 MGLRU diag */"
    if s.count(token) != 1:
        raise SystemExit(f"{func}: diagnostic counter insertion count != 1")

for required in (
    marker,
    "__ATTR(diag, 0644, mglru_diag_show, mglru_diag_store)",
    "&lru_gen_diag_attr.attr,",
    "sysfs_streq(buf, \"reset\")",
):
    if required not in s:
        raise SystemExit(f"missing diagnostic structure: {required}")

vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-power-diag-p1.txt").write_text(
    "phase=111-mglru-power-diag-p1\n"
    "behavior_change=none\n"
    "counter_storage=per-cpu-unsigned-long\n"
    "interface=/sys/kernel/mm/lru_gen/diag\n"
    "reset=echo-reset-to-diag\n"
    "counters=try_inc_max_seq,walk_pte_range,walk_pmd_range,look_around,scan_pages,evict_pages\n"
)

print("MGLRU power diagnostics P1 applied")
print("interface=/sys/kernel/mm/lru_gen/diag")
print("behavior_change=none")
