#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 85_apply_bpf_x25_live_counters.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
hdr = root / "include/linux/filter.h"
diag = root / "arch/arm64/net/bpf_x25_diag.c"
rec = root / "drivers/misc/a52_touchgrass_recorder.c"

for p in (hdr, diag, rec):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

# ---------------------------------------------------------------------------
# 1. Count every JIT invocation that passes through the Phase84 x25 wrapper.
# ---------------------------------------------------------------------------
h = hdr.read_text()

decl_anchor = """extern void a52_bpf_x25_report(const struct bpf_prog *prog,
				unsigned long before, unsigned long after);
"""
decl_new = decl_anchor + """extern void a52_bpf_x25_note_call(const struct bpf_prog *prog);
"""
if "a52_bpf_x25_note_call" not in h:
    if h.count(decl_anchor) != 1:
        raise SystemExit(f"filter.h declaration anchor count={h.count(decl_anchor)}")
    h = h.replace(decl_anchor, decl_new, 1)

call_anchor = """		ret = a52_bpf_call_x25_probe(prog->bpf_func, ctx,
					     prog->insnsi, &x25);
"""
call_new = """		a52_bpf_x25_note_call(prog);
""" + call_anchor

if h.count("a52_bpf_x25_note_call(prog);") == 0:
    n = h.count(call_anchor)
    if n != 2:
        raise SystemExit(f"expected 2 Phase84 JIT call anchors, found {n}")
    h = h.replace(call_anchor, call_new)
elif h.count("a52_bpf_x25_note_call(prog);") != 2:
    raise SystemExit("unexpected existing live-counter call count")

hdr.write_text(h)

# ---------------------------------------------------------------------------
# 2. Replace the mismatch-only diagnostic state with live counters and expose
#    them through /proc/a52_bpf_x25. The mismatch reporter is unchanged in
#    purpose and still persists the first 32 mismatches to TGREC.
# ---------------------------------------------------------------------------
d = diag.read_text()

for inc in ("#include <linux/init.h>\n", "#include <linux/proc_fs.h>\n", "#include <linux/seq_file.h>\n"):
    if inc not in d:
        d = d.replace("#include <linux/filter.h>\n", "#include <linux/filter.h>\n" + inc, 1)

old_counter = "static atomic_t a52_bpf_x25_mismatches = ATOMIC_INIT(0);\n"
new_counter = """static atomic64_t a52_bpf_x25_calls = ATOMIC64_INIT(0);
static atomic64_t a52_bpf_x25_mismatches = ATOMIC64_INIT(0);
"""
if old_counter in d:
    d = d.replace(old_counter, new_counter, 1)
elif "a52_bpf_x25_calls" not in d:
    raise SystemExit("Phase84 mismatch counter anchor missing")

report_anchor = """void a52_bpf_x25_report(const struct bpf_prog *prog,
			 unsigned long before, unsigned long after)
"""
note_func = """void a52_bpf_x25_note_call(const struct bpf_prog *prog)
{
	(void)prog;
	atomic64_inc(&a52_bpf_x25_calls);
}

"""
if note_func not in d:
    if d.count(report_anchor) != 1:
        raise SystemExit("report function anchor mismatch")
    d = d.replace(report_anchor, note_func + report_anchor, 1)

d = d.replace(
    "int n = atomic_inc_return(&a52_bpf_x25_mismatches);",
    "long long n = atomic64_inc_return(&a52_bpf_x25_mismatches);",
    1,
)
d = d.replace(
    'pr_err("A52BPF_X25 n=%d pid=%d comm=%s id=%u name=%s type=%u attach=%u len=%u jlen=%u is_func=%u fidx=%u fcnt=%u stack=%u before=%016lx after=%016lx func=%px\\n",',
    'pr_err("A52BPF_X25 n=%lld pid=%d comm=%s id=%u name=%s type=%u attach=%u len=%u jlen=%u is_func=%u fidx=%u fcnt=%u stack=%u before=%016lx after=%016lx func=%px\\n",',
    1,
)

proc_block = r'''
static int a52_bpf_x25_proc_show(struct seq_file *m, void *v)
{
	long long calls = atomic64_read(&a52_bpf_x25_calls);
	long long mismatches = atomic64_read(&a52_bpf_x25_mismatches);

	seq_puts(m, "phase=phase85-fixed-eevdf-llvm17-bpf-x25-live-counters\n");
	seq_puts(m, "probe=out-of-frame-capture-and-repair\n");
	seq_printf(m, "jit_calls=%lld\n", calls);
	seq_printf(m, "x25_mismatches=%lld\n", mismatches);
	seq_printf(m, "x25_repairs=%lld\n", mismatches);
	seq_printf(m, "clean_calls=%lld\n", calls >= mismatches ? calls - mismatches : 0);
	return 0;
}

static int __init a52_bpf_x25_proc_init(void)
{
	struct proc_dir_entry *p;

	p = proc_create_single("a52_bpf_x25", 0444, NULL,
			       a52_bpf_x25_proc_show);
	if (!p)
		pr_err("A52BPF_X25: failed to create /proc/a52_bpf_x25\n");
	else
		pr_info("A52BPF_X25: live counters ready at /proc/a52_bpf_x25\n");

	return 0;
}
late_initcall(a52_bpf_x25_proc_init);
'''
if "a52_bpf_x25_proc_show" not in d:
    d = d.rstrip() + "\n" + proc_block.lstrip()

diag.write_text(d)

# ---------------------------------------------------------------------------
# 3. Give the persistent recorder a unique Phase85 identity.
# ---------------------------------------------------------------------------
r = rec.read_text()
if "TG84P1" in r:
    r = r.replace("TG84P1", "TG85P1")
if "phase84 llvm17 fixed-eevdf real-jit x25-outofframe-probe" in r:
    r = r.replace(
        "phase84 llvm17 fixed-eevdf real-jit x25-outofframe-probe",
        "phase85 llvm17 fixed-eevdf real-jit x25-live-counters",
    )
rec.write_text(r)

checks = {
    "note_call_decl": "a52_bpf_x25_note_call" in hdr.read_text(),
    "two_call_sites": hdr.read_text().count("a52_bpf_x25_note_call(prog);") == 2,
    "atomic64_calls": "a52_bpf_x25_calls = ATOMIC64_INIT(0)" in diag.read_text(),
    "atomic64_mismatches": "a52_bpf_x25_mismatches = ATOMIC64_INIT(0)" in diag.read_text(),
    "proc_show": "a52_bpf_x25_proc_show" in diag.read_text(),
    "proc_create": 'proc_create_single("a52_bpf_x25"' in diag.read_text(),
    "jit_calls_field": '"jit_calls=%lld' in diag.read_text(),
    "mismatch_field": '"x25_mismatches=%lld' in diag.read_text(),
    "repair_field": '"x25_repairs=%lld' in diag.read_text(),
    "phase85_recorder_id": "TG85P1" in rec.read_text(),
    "phase85_recorder_desc": "phase85 llvm17 fixed-eevdf real-jit x25-live-counters" in rec.read_text(),
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase85 staging audit failed: " + ", ".join(failed))

print("phase85_bpf_x25_live_counters=applied")
for k in sorted(checks):
    print(f"{k}=PASS")
