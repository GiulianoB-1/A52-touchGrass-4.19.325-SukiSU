#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 3:
    raise SystemExit("usage: 135_remove_project_diagnostics.py <kernel_tree> <report_dir>")

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)

def path(rel):
    p = root / rel
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")
    return p

def read(rel):
    return path(rel).read_text()

def write(rel, s):
    path(rel).write_text(s)

def replace_once(s, old, new, label, required=True):
    n = s.count(old)
    if n == 1:
        return s.replace(old, new, 1)
    if not required and n == 0:
        return s
    raise SystemExit(f"{label}: expected one anchor, found {n}")

def remove_function(s, name, required=True):
    pat = re.compile(rf"(?m)^[ \t]*static[ \t]+ssize_t[ \t]+{re.escape(name)}[ \t]*\(")
    m = pat.search(s)
    if not m:
        if required:
            raise SystemExit(f"function not found: {name}")
        return s
    brace = s.find("{", m.end())
    if brace < 0:
        raise SystemExit(f"opening brace not found: {name}")
    depth = 0
    end = None
    for i in range(brace, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f"closing brace not found: {name}")
    while end < len(s) and s[end] in " \t":
        end += 1
    if end < len(s) and s[end] == "\n":
        end += 1
    return s[:m.start()] + s[end:]

def remove_call_statements_containing(s, func, needle):
    pos = 0
    removed = 0
    while True:
        start = s.find(func + "(", pos)
        if start < 0:
            break
        depth = 0
        i = start + len(func)
        in_str = False
        esc = False
        end = None
        while i < len(s):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        j = i + 1
                        while j < len(s) and s[j] in " \t":
                            j += 1
                        if j < len(s) and s[j] == ";":
                            end = j + 1
                        break
            i += 1
        if end is None:
            raise SystemExit(f"could not parse {func} statement near offset {start}")
        stmt = s[start:end]
        if needle in stmt:
            line_start = s.rfind("\n", 0, start) + 1
            line_end = end
            while line_end < len(s) and s[line_end] in " \t":
                line_end += 1
            if line_end < len(s) and s[line_end] == "\n":
                line_end += 1
            s = s[:line_start] + s[line_end:]
            removed += 1
            pos = line_start
        else:
            pos = end
    return s, removed

# -------------------------------------------------------------------------
# MGLRU P1/P2 recorder and proc/debugfs export.
# Keep all MGLRU policy/efficiency code.
# -------------------------------------------------------------------------
vm = read("mm/vmscan.c")
mglru_diag_decl = r'''/* A52 MGLRU power diagnostics P1 */
DEFINE_PER_CPU(unsigned long, mglru_diag_try_inc_max_seq);
DEFINE_PER_CPU(unsigned long, mglru_diag_walk_pte_range);
DEFINE_PER_CPU(unsigned long, mglru_diag_walk_pmd_range);
DEFINE_PER_CPU(unsigned long, mglru_diag_look_around);
DEFINE_PER_CPU(unsigned long, mglru_diag_scan_pages);
DEFINE_PER_CPU(unsigned long, mglru_diag_evict_pages);

'''
if mglru_diag_decl in vm:
    vm = vm.replace(mglru_diag_decl, "", 1)
elif "/* A52 MGLRU power diagnostics P1 */" in vm:
    raise SystemExit("MGLRU cleanup: diagnostic declaration block shape changed")

# Remove P1+P2 sysfs implementation blocks that were inserted immediately
# before the standard lru_gen_attrs table.
for marker in (
    "/* A52 MGLRU power diagnostics P1: low-overhead per-CPU call counters. */",
    "/* A52 MGLRU power recorder P2 controls and aggregate statistics. */",
):
    if marker in vm:
        start = vm.index(marker)
        end = vm.index("static struct attribute *lru_gen_attrs[] = {", start)
        vm = vm[:start] + vm[end:]
        break

for line in (
    "\t&lru_gen_diag_attr.attr,\n",
    "\t&lru_gen_record_attr.attr,\n",
    "\t&lru_gen_rec_stats_attr.attr,\n",
):
    vm = vm.replace(line, "")

trace_marker = "/* A52 MGLRU recorder P2: bounded per-CPU flight recorder dump. */"
if trace_marker in vm:
    start = vm.index(trace_marker)
    end = vm.index("static int __init init_lru_gen(void)", start)
    vm = vm[:start] + vm[end:]

vm = re.sub(r'^[ \t]*debugfs_create_file\("lru_gen_trace"[^\n]*\);\n', "", vm, flags=re.M)
vm = re.sub(r'^[ \t]*proc_create\("lru_gen_trace"[^\n]*\);\n', "", vm, flags=re.M)
vm = vm.replace("\t/* A52 MGLRU recorder procfs export */\n", "")

# P1 counters gated by the recorder runtime level.
vm = re.sub(
    r'^[ \t]*if \(unlikely\(READ_ONCE\(mglru_record_level\) >= 1\)\)\n'
    r'[ \t]*this_cpu_inc\(mglru_diag_[A-Za-z0-9_]+\); /\* A52 MGLRU diag \*/\n',
    "", vm, flags=re.M)

# P2 timing/work locals.
vm = re.sub(
    r'\n[ \t]*unsigned long a52_rec_start_ns = '
    r'unlikely\(READ_ONCE\(mglru_record_level\) >= 2\) \? '
    r'\(unsigned long\)ktime_get_ns\(\) : 0;',
    "", vm)
for decl in (
    "\n\tunsigned long a52_rec_ptes = 0;",
    "\n\tunsigned long a52_rec_young = 0;",
    "\n\tunsigned long a52_rec_promoted = 0;",
):
    vm = vm.replace(decl, "")

for snippet in (
    "\n\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1))\n\t\t\ta52_rec_ptes++;",
    "\n\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1))\n\t\t\ta52_rec_young++;",
    "\n\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1))\n\t\t\ta52_rec_promoted++;",
):
    vm = vm.replace(snippet, "")

look_done = r'''
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
vm = vm.replace(look_done, "")

scan_done = r'''if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
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
vm = vm.replace(scan_done, "")

evict_early = r'''if (list_empty(&list)) {
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
vm = vm.replace(evict_early, "if (list_empty(&list))\n\t\treturn scanned;")

evict_done = r'''if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
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
vm = vm.replace(evict_done, "")

# Robust second pass: strip any recorder-only fragments left inside the three
# MGLRU hot-path functions.  Phase 115 changed surrounding reclaim code after
# the recorder was added, so exact reverse snippets are not sufficient.
def strip_mglru_recorder_from_function(text, name):
    pat = re.compile(
        rf"(?m)^[ \t]*(?:static[ \t]+)?(?:inline[ \t]+)?[^\n;]*\b{re.escape(name)}[ \t]*\("
    )
    m = pat.search(text)
    if not m:
        raise SystemExit(f"MGLRU cleanup: function not found: {name}")
    brace = text.find("{", m.end())
    depth = 0
    end = None
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise SystemExit(f"MGLRU cleanup: unbalanced function: {name}")

    func = text[m.start():end]

    # Recorder timing/work locals are never part of MGLRU policy.
    func = re.sub(
        r'\n[ \t]*unsigned long a52_rec_start_ns =.*?;',
        "", func, flags=re.S)
    func = re.sub(
        r'\n[ \t]*unsigned long a52_rec_(?:ptes|young|promoted) = 0;',
        "", func)

    # Remove braced recorder blocks regardless of indentation or small source
    # changes around them.
    needle = "if (unlikely(READ_ONCE(mglru_record_level) >= 1))"
    pos = 0
    while True:
        p = func.find(needle, pos)
        if p < 0:
            break
        line_start = func.rfind("\n", 0, p) + 1
        q = p + len(needle)
        while q < len(func) and func[q] in " \t\r\n":
            q += 1

        if q < len(func) and func[q] == "{":
            d = 0
            close = None
            for j in range(q, len(func)):
                if func[j] == "{":
                    d += 1
                elif func[j] == "}":
                    d -= 1
                    if d == 0:
                        close = j + 1
                        break
            if close is None:
                raise SystemExit(f"MGLRU cleanup: unterminated recorder block in {name}")
            while close < len(func) and func[close] in " \t":
                close += 1
            if close < len(func) and func[close] == "\n":
                close += 1
            func = func[:line_start] + func[close:]
            pos = line_start
            continue

        # P1/P2 path counters use an unbraced if followed by one statement.
        stmt_end = func.find(";", q)
        if stmt_end < 0:
            raise SystemExit(f"MGLRU cleanup: recorder statement end missing in {name}")
        stmt_end += 1
        while stmt_end < len(func) and func[stmt_end] in " \t":
            stmt_end += 1
        if stmt_end < len(func) and func[stmt_end] == "\n":
            stmt_end += 1
        func = func[:line_start] + func[stmt_end:]
        pos = line_start

    # No recorder symbol should remain in these functions.
    for token in ("mglru_rec_", "mglru_diag_", "mglru_record_level", "a52_rec_"):
        if token in func:
            raise SystemExit(f"MGLRU cleanup: {token} remains in {name}")

    return text[:m.start()] + func + text[end:]

for _mglru_fn in ("lru_gen_look_around", "scan_pages", "evict_pages"):
    vm = strip_mglru_recorder_from_function(vm, _mglru_fn)

# Remove recorder-only headers once no recorder code remains.
if not any(x in vm for x in ("mglru_rec_", "mglru_diag_", "lru_gen_trace", "mglru_record_level")):
    vm = vm.replace("#include <linux/proc_fs.h>\n", "")
    if "ktime_get_ns" not in vm:
        vm = vm.replace("#include <linux/ktime.h>\n", "")

write("mm/vmscan.c", vm)

# -------------------------------------------------------------------------
# Scheduler/CASS/UCLAMP/WALT recorder.
# -------------------------------------------------------------------------
sh = read("kernel/sched/sched.h")
decl_start = "extern __read_mostly int scheduler_running;\n\n/* A52 scheduler efficiency recorder: runtime disabled by default. */"
if decl_start in sh:
    start = sh.index(decl_start)
    after = sh.index("\nvoid a52_sched_diag_sugov_freq", start)
    semi = sh.index(";", after)
    end = semi + 1
    while end < len(sh) and sh[end] in "\r\n":
        end += 1
    sh = sh[:start] + "extern __read_mostly int scheduler_running;\n" + sh[end:]

inactive_diag = r'''	if (likely(!uclamp_is_used())) {
		if (unlikely(a52_sched_diag_enabled()))
			a52_sched_diag_uclamp(util, 0, SCHED_CAPACITY_SCALE,
					     util, false);
		return util;
	}
'''
sh = sh.replace(inactive_diag, "\tif (likely(!uclamp_is_used()))\n\t\treturn util;\n")

active_diag = r'''	if (unlikely(min_util >= max_util)) {
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
sh = sh.replace(active_diag,
r'''	if (unlikely(min_util >= max_util))
		return min_util;

	return clamp(util, min_util, max_util);
''')
write("kernel/sched/sched.h", sh)

cc = read("kernel/sched/core.c")
marker = "/* A52 scheduler efficiency recorder */"
if marker in cc:
    start = cc.index(marker)
    end_marker = "late_initcall(a52_sched_diag_init);"
    end = cc.index(end_marker, start) + len(end_marker)
    while end < len(cc) and cc[end] in "\r\n":
        end += 1
    cc = cc[:start] + cc[end:]

# Remove only custom one-shot efficiency logging; keep the uclamp behavior.
cc, _ = remove_call_statements_containing(cc, "pr_info_once", "A52 uclamp efficiency P1")
write("kernel/sched/core.c", cc)

ca = read("kernel/sched/cass.c")
ca = ca.replace(
r'''    bool has_idle = false;
    bool a52_diag = a52_sched_diag_enabled();
    unsigned int a52_candidates = 0;
    unsigned int a52_idle_candidates = 0;
    unsigned int a52_under_ucmin = 0;
    unsigned int a52_under_task = 0;
    int cidx = 0, cpu;
''',
r'''    bool has_idle = false;
    int cidx = 0, cpu;
''')
ca = ca.replace(
r'''
        if (unlikely(a52_diag)) {
            a52_candidates++;
            if (curr->cap_max < uc_min)
                a52_under_ucmin++;
            if (curr->cap_max < p_util)
                a52_under_task++;
        }
''', "")
ca = ca.replace(
r'''            curr->exit_lat = 1;
            if (unlikely(a52_diag))
                a52_idle_candidates++;
            idle_state = idle_get_state(rq);
''',
r'''            curr->exit_lat = 1;
            idle_state = idle_get_state(rq);
''')
ca = ca.replace(
r'''    rcu_read_unlock();

    if (unlikely(a52_diag))
        a52_sched_diag_cass(best->cpu, prev_cpu, sync, p_util,
                            uc_min, uc_max, a52_candidates,
                            a52_idle_candidates, a52_under_ucmin,
                            a52_under_task);

    return best->cpu;
''',
r'''    rcu_read_unlock();
    return best->cpu;
''')
ca, _ = remove_call_statements_containing(ca, "pr_info_once", "A52 CASS P3")
write("kernel/sched/cass.c", ca)

sg = read("kernel/sched/cpufreq_schedutil.c")
sg = sg.replace(
r'''		if (delta_ns > stale_ns) {
			if (unlikely(a52_sched_diag_enabled()))
				a52_sched_diag_sugov_stale(j, delta_ns);
			sugov_iowait_reset(j_sg_cpu, time, false);
			continue;
		}
''',
r'''		if (delta_ns > stale_ns) {
			sugov_iowait_reset(j_sg_cpu, time, false);
			continue;
		}
''')
sg = sg.replace(
r'''	sg_policy->need_freq_update = false;
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
''',
r'''	sg_policy->need_freq_update = false;
	sg_policy->prev_cached_raw_freq = sg_policy->cached_raw_freq;
	sg_policy->cached_raw_freq = freq;
	return cpufreq_driver_resolve_freq(policy, freq);
}
''')
write("kernel/sched/cpufreq_schedutil.c", sg)

# -------------------------------------------------------------------------
# FUSE diagnostic-only negotiation/runtime logging.
# Keep all negotiated feature state and passthrough behavior.
# -------------------------------------------------------------------------
fi = read("fs/fuse/inode.c")
fi = fi.replace(
r'''\tu32 probe_payload_len = 0;

	if (req->out.h.len >= sizeof(struct fuse_out_header))
		probe_payload_len = req->out.h.len - sizeof(struct fuse_out_header);

'''.replace("\\t", "\t"), "")
probe_start = fi.find("\t/*\n\t * Phase 102 diagnostic only: report the raw userspace FUSE_INIT reply.")
if probe_start >= 0:
    probe_end = fi.find("\n\n", fi.find("arg->request_timeout);", probe_start))
    if probe_end < 0:
        raise SystemExit("FUSE negotiation probe block end missing")
    fi = fi[:probe_start] + fi[probe_end+2:]
fi, _ = remove_call_statements_containing(fi, "pr_info", "FUSE_740_")
write("fs/fuse/inode.c", fi)

fb = read("fs/fuse/backing.c")
# Log-only wrappers first so no orphan if statement remains.
fb = re.sub(
    r'^[ \t]*if \(!legacy_once\)\n[ \t]*pr_info_ratelimited\("FUSE_740_BACKING_OPEN.*?\);\n',
    "", fb, flags=re.M | re.S)
fb, _ = remove_call_statements_containing(fb, "pr_info_ratelimited", "FUSE_740_")
write("fs/fuse/backing.c", fb)

fp = read("fs/fuse/passthrough.c")
fp = re.sub(
    r'^[ \t]*if \(persistent\)\n[ \t]*pr_info_ratelimited\("FUSE_740_PASSTHROUGH_SETUP.*?\);\n',
    "", fp, flags=re.M | re.S)
fp, _ = remove_call_statements_containing(fp, "pr_info_ratelimited", "FUSE_740_")
write("fs/fuse/passthrough.c", fp)

# -------------------------------------------------------------------------
# GPU undervolt diagnostics/status. Keep the undervolt P1/P2/P3 algorithms.
# -------------------------------------------------------------------------
gmu = read("drivers/gpu/msm/kgsl_gmu.c")
for phase in (1, 2, 3):
    # Remove status variables + status formatter, but not functional helpers.
    pat = re.compile(
        rf"(?ms)^static unsigned int a52_gpu_uv_p{phase}_applied;.*?"
        rf"^ssize_t a52_gpu_uv_p{phase}_status\(char \*buf, size_t size\)\n"
        rf"\{{.*?^\}}\n\n")
    gmu, n = pat.subn("", gmu, count=1)
    if n == 0:
        raise SystemExit(f"GPU UV P{phase} status block not found")

gmu = re.sub(r'^[ \t]*a52_gpu_uv_p[123]_[a-z0-9_]+[ \t]*=[^;]*;\n', "", gmu, flags=re.M)
for func in ("dev_info", "dev_warn"):
    gmu, _ = remove_call_statements_containing(gmu, func, "A52 GPU UV")
write("drivers/gpu/msm/kgsl_gmu.c", gmu)

gh = read("drivers/gpu/msm/kgsl_gmu.h")
gh = re.sub(r'^/\* A52 A619 GPU UV P[123] diagnostics:[^\n]*\*/\n', "", gh, flags=re.M)
gh = re.sub(r'^ssize_t a52_gpu_uv_p[123]_status\(char \*buf, size_t size\);\n', "", gh, flags=re.M)
write("drivers/gpu/msm/kgsl_gmu.h", gh)

pw = read("drivers/gpu/msm/kgsl_pwrctrl.c")
for phase in (1, 2, 3):
    pw = remove_function(pw, f"a52_gpu_uv_p{phase}_show", required=True)
    pw = pw.replace(f"static DEVICE_ATTR_RO(a52_gpu_uv_p{phase});\n", "")
    pw = pw.replace(f"\t&dev_attr_a52_gpu_uv_p{phase}.attr,\n", "")
write("drivers/gpu/msm/kgsl_pwrctrl.c", pw)

# -------------------------------------------------------------------------
# F2FS validation-only read-only sysfs nodes. Keep internal feature state.
# -------------------------------------------------------------------------
sf = read("fs/f2fs/sysfs.c")
f2fs_diag_attrs = (
    "atgc_enabled",
    "age_extent_cache_enabled",
    "age_extent_tree_count",
    "age_extent_node_count",
    "allocated_data_blocks",
    "ckpt_merge_active",
    "ckpt_merge_issued",
    "ckpt_merge_inflight",
    "ckpt_merge_accumulated",
    "ckpt_merge_queue_pending",
)
for name in f2fs_diag_attrs:
    sf = remove_function(sf, f"{name}_show", required=True)
    sf = sf.replace(f"F2FS_GENERAL_RO_ATTR({name});\n", "")
    sf = sf.replace(f"\tATTR_LIST({name}),\n", "")
write("fs/f2fs/sysfs.c", sf)

# -------------------------------------------------------------------------
# Negative source audit: project diagnostics must be gone.
# -------------------------------------------------------------------------
for rel, forbidden in {
    "mm/vmscan.c": (
        "mglru_diag_", "mglru_rec_", "mglru_record_level",
        "lru_gen_trace", "A52 MGLRU power diagnostics",
        "A52 MGLRU power recorder",
    ),
    "kernel/sched/core.c": ("a52_sched_diag", "A52 scheduler efficiency recorder"),
    "kernel/sched/sched.h": ("a52_sched_diag", "A52 scheduler efficiency recorder"),
    "kernel/sched/cass.c": ("a52_sched_diag", "a52_diag", "A52 CASS P3:"),
    "kernel/sched/cpufreq_schedutil.c": ("a52_sched_diag",),
    "fs/fuse/inode.c": ("FUSE_NEGOTIATION_PROBE", "FUSE_740_"),
    "fs/fuse/backing.c": ("FUSE_740_",),
    "fs/fuse/passthrough.c": ("FUSE_740_",),
    "drivers/gpu/msm/kgsl_gmu.c": (
        "a52_gpu_uv_p1_", "a52_gpu_uv_p2_", "a52_gpu_uv_p3_",
        "A52 GPU UV P1:", "A52 GPU UV P2:", "A52 GPU UV P3:",
    ),
    "drivers/gpu/msm/kgsl_gmu.h": ("a52_gpu_uv_p1_status", "a52_gpu_uv_p2_status", "a52_gpu_uv_p3_status"),
    "drivers/gpu/msm/kgsl_pwrctrl.c": ("dev_attr_a52_gpu_uv_p1", "dev_attr_a52_gpu_uv_p2", "dev_attr_a52_gpu_uv_p3"),
    "drivers/platform/msm/ipa/ipa_clients/rndis_ipa.c": ("a52_rndis_reftrace", "a52_rndis_fibtrace"),
    "net/core/dev.c": ("a52_rndis_reftrace", "a52_rndis_fibtrace"),
    "include/linux/netdevice.h": ("a52_rndis_reftrace", "a52_rndis_fibtrace"),
}.items():
    data = read(rel)
    for token in forbidden:
        if token in data:
            if rel == "mm/vmscan.c":
                lines = data.splitlines()
                print(f"--- remaining {token} context in {rel} ---")
                for idx, line in enumerate(lines):
                    if token in line:
                        lo = max(0, idx - 8)
                        hi = min(len(lines), idx + 12)
                        for n in range(lo, hi):
                            print(f"{n + 1}: {lines[n]}")
                        print("---")
            raise SystemExit(f"diagnostic token remains in {rel}: {token}")

sf = read("fs/f2fs/sysfs.c")
for name in f2fs_diag_attrs:
    if f"{name}_show" in sf or f"ATTR_LIST({name})" in sf or f"F2FS_GENERAL_RO_ATTR({name})" in sf:
        raise SystemExit(f"F2FS diagnostic sysfs remains: {name}")

# Positive audit: production features must remain.
required = {
    "mm/vmscan.c": (
        "A52 MGLRU Efficiency P4: clear seed PTE before look-around bailouts",
        "ptep_test_and_clear_young",
    ),
    "kernel/sched/core.c": (
        "sysctl_sched_uclamp_util_min_rt_default = 0;",
        "unsigned int uclamp_task(struct task_struct *p)",
    ),
    "kernel/sched/cass.c": (
        "cass_walt_cpu_util(int cpu, int this_cpu, bool sync)",
        "uc_min = uclamp_eff_value(p, UCLAMP_MIN);",
    ),
    "kernel/sched/cpufreq_schedutil.c": (
        "A52 WALT schedutil P2: expire stale sibling demand by wall time.",
        "delta_ns = time - j_sg_cpu->last_update;",
    ),
    "fs/fuse/inode.c": (
        "#define FUSE_KERNEL_MINOR_VERSION 40", # checked across tree below too
    ),
    "drivers/gpu/msm/kgsl_gmu.c": (
        "A52 A619 GPU UV P1: top OPP one ARC corner",
        "A52 A619 GPU UV P2: 650 MHz one ARC corner",
        "A52 A619 GPU UV P3: 565 MHz one ARC corner",
    ),
    "kernel/bpf/devmap.c": (
        "static void dev_map_hash_remove_netdev",
        "dev->bulkq = __alloc_percpu_gfp(sizeof(*dev->bulkq)",
    ),
}
# FUSE version macro lives in UAPI, not inode.c.
required["fs/fuse/inode.c"] = ("arg->flags2 = (u32)(FUSE_PASSTHROUGH_UPSTREAM >> 32);",)
required["include/uapi/linux/fuse.h"] = ("#define FUSE_KERNEL_MINOR_VERSION 40",)

for rel, tokens in required.items():
    data = read(rel)
    for token in tokens:
        if token not in data:
            raise SystemExit(f"production feature missing after cleanup: {rel}: {token}")

(out / "report.txt").write_text(
    "A52 Phase135 complete project diagnostics teardown\n"
    "base=Phase134 clean BPF DEVMAP/RNDIS production fix\n"
    "removed=mglru-counters+recorder+trace,scheduler-cass-uclamp-sugov-recorder,"
    "rndis-tracers,fuse-negotiation-runtime-logs,gpu-uv-status+logs,"
    "f2fs-validation-sysfs\n"
    "preserved=mglru-efficiency,cass,eevdf,uclamp,walt-sugov,fuse-740-passthrough,"
    "gpu-undervolt-p1-p2-p3,f2fs-atgc-block-age-gc-cp,devmap-rndis-fix\n"
    "scope=project-added diagnostics only; standard kernel/vendor diagnostics unchanged\n"
)

print((out / "report.txt").read_text(), end="")
