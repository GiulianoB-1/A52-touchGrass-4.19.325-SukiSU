#!/usr/bin/env python3
# CI entry point: exact baseline -> MGLRU P1/P2/P3 -> diag P1/P2 -> scheduler efficiency P1
from pathlib import Path
import re
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 106_apply_mglru_modern_p1.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
p1 = here / "106_apply_mglru_modern_p1_core.py"
p2 = here / "107_apply_mglru_modern_p2.py"
p3 = here / "108_apply_mglru_modern_p3.py"
diag = here / "111_apply_mglru_power_diag_p1.py"
recorder = here / "112_apply_mglru_power_recorder_p2.py"
eevdf = here / "109_apply_eevdf_efficiency_p1.py"
eevdf_finalize = here / "109b_finalize_eevdf_efficiency_p1.py"
uclamp = here / "110_apply_uclamp_efficiency_p1.py"
uclamp_finalize = here / "110b_finalize_uclamp_efficiency_p1.py"

for script in (p1, p2, p3, diag, recorder, eevdf, eevdf_finalize, uclamp, uclamp_finalize):
    if not script.is_file():
        raise SystemExit(f"missing chained script: {script}")

print("Applying runtime-proven Modern MGLRU P1")
subprocess.run([sys.executable, str(p1), str(root)], check=True)

print("Applying runtime-proven Modern MGLRU P2")
subprocess.run([sys.executable, str(p2), str(root)], check=True)

print("Applying Modern MGLRU P3")
subprocess.run([sys.executable, str(p3), str(root)], check=True)

print("Applying MGLRU power diagnostics P1")
subprocess.run([sys.executable, str(diag), str(root)], check=True)

# Recorder P2 originally used an overly broad function regex. Replace only that
# helper in the checked-out patcher with the definition finder already proven by
# diagnostic P1. This is a CI patcher fix only; it does not alter kernel policy.
recorder_text = recorder.read_text()
old_bounds = '''def function_bounds(text, name):
    m = re.search(rf"(?m)^[ \\t]*(?:static[ \\t]+)?(?:inline[ \\t]+)?[^\\n;]*\\b{re.escape(name)}[ \\t]*\\([^;]*?\\)\\s*\\{{", text, re.S)
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
'''
new_bounds = '''def function_bounds(text, name):
    return_type = r"(?:bool|int|void|long|unsigned\\s+long|unsigned\\s+int|struct\\s+[A-Za-z_]\\w*\\s*\\*)"
    pat = re.compile(
        rf"(?m)^[ \\t]*(?:static[ \\t]+)?(?:inline[ \\t]+)?{return_type}[ \\t]+{re.escape(name)}[ \\t]*\\("
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
    start, brace = hits[0]
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, brace, i + 1
        i += 1
    raise SystemExit(f"{name}: unbalanced braces")
'''
if old_bounds not in recorder_text:
    raise SystemExit("MGLRU recorder P2: function_bounds patcher anchor changed")
recorder_text = recorder_text.replace(old_bounds, new_bounds, 1)
print("Hardened MGLRU recorder function-definition parser")

# The v9-era scan_pages() does not simply `return scanned`. It reports progress
# as `isolated || !remaining ? scanned : 0`, while scanned/sorted/isolated still
# describe real work done internally. Preserve that exact return contract and
# record the work independently, including the zero-generation early exit.
old_scan = '''def mutate_scan(func):
    func = add_start_decl(func)
    returns = list(re.finditer(r"(?m)^(\\s*)return\\s+scanned\\s*;", func))
    if len(returns) != 1:
        raise SystemExit(f"scan_pages: expected one return scanned, found {len(returns)}")
    m = returns[0]
    indent = m.group(1)
    done = r'''if (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
\t\t\tunsigned long a52_dur = 0;

\t\t\tthis_cpu_add(mglru_rec_scan_scanned, scanned);
\t\t\tthis_cpu_add(mglru_rec_scan_sorted, sorted);
\t\t\tthis_cpu_add(mglru_rec_scan_isolated, isolated);
\t\t\tif (a52_rec_start_ns) {
\t\t\t\ta52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
\t\t\t\tthis_cpu_add(mglru_rec_scan_time_ns, a52_dur);
\t\t\t\tif (a52_dur >= MGLRU_REC_SLOW_NS)
\t\t\t\t\tthis_cpu_inc(mglru_rec_scan_slow);
\t\t\t}
\t\t\tmglru_rec_event(MGLRU_REC_SCAN,
\t\t\t\t\t(unsigned char)((type & 0xf) | ((tier & 0xf) << 4)),
\t\t\t\t\tscanned, sorted, isolated, a52_dur);
\t\t}
\t\t'''
    return func[:m.start()] + indent + done + "return scanned;" + func[m.end():]
'''
new_scan = '''def mutate_scan(func):
    func = add_start_decl(func)

    early = "if (get_nr_gens(lruvec, type) == MIN_NR_GENS)\\n\\t\\treturn 0;"
    if func.count(early) != 1:
        raise SystemExit(f"scan_pages: expected one MIN_NR_GENS early return, found {func.count(early)}")
    early_new = r'''if (get_nr_gens(lruvec, type) == MIN_NR_GENS) {
\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
\t\t\tunsigned long a52_dur = 0;

\t\t\tif (a52_rec_start_ns) {
\t\t\t\ta52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
\t\t\t\tthis_cpu_add(mglru_rec_scan_time_ns, a52_dur);
\t\t\t\tif (a52_dur >= MGLRU_REC_SLOW_NS)
\t\t\t\t\tthis_cpu_inc(mglru_rec_scan_slow);
\t\t\t}
\t\t\tmglru_rec_event(MGLRU_REC_SCAN,
\t\t\t\t\t(unsigned char)((type & 0xf) | ((tier & 0xf) << 4)),
\t\t\t\t\t0, 0, 0, a52_dur);
\t\t}
\t\treturn 0;
\t}'''
    func = func.replace(early, early_new, 1)

    final = "return isolated || !remaining ? scanned : 0;"
    if func.count(final) != 1:
        raise SystemExit(f"scan_pages: expected one v9 return contract, found {func.count(final)}")
    final_new = r'''{
\t\tint a52_ret = isolated || !remaining ? scanned : 0;

\t\tif (unlikely(READ_ONCE(mglru_record_level) >= 1)) {
\t\t\tunsigned long a52_dur = 0;

\t\t\tthis_cpu_add(mglru_rec_scan_scanned, scanned);
\t\t\tthis_cpu_add(mglru_rec_scan_sorted, sorted);
\t\t\tthis_cpu_add(mglru_rec_scan_isolated, isolated);
\t\t\tif (a52_rec_start_ns) {
\t\t\t\ta52_dur = (unsigned long)ktime_get_ns() - a52_rec_start_ns;
\t\t\t\tthis_cpu_add(mglru_rec_scan_time_ns, a52_dur);
\t\t\t\tif (a52_dur >= MGLRU_REC_SLOW_NS)
\t\t\t\t\tthis_cpu_inc(mglru_rec_scan_slow);
\t\t\t}
\t\t\tmglru_rec_event(MGLRU_REC_SCAN,
\t\t\t\t\t(unsigned char)((type & 0xf) | ((tier & 0xf) << 4)),
\t\t\t\t\tscanned, sorted, isolated, a52_dur);
\t\t}
\t\treturn a52_ret;
\t}'''
    return func.replace(final, final_new, 1)
'''
if old_scan not in recorder_text:
    raise SystemExit("MGLRU recorder P2: mutate_scan patcher anchor changed")
recorder_text = recorder_text.replace(old_scan, new_scan, 1)
print("Adapted recorder to v9 MGLRU scan_pages return contract")

recorder.write_text(recorder_text)

# P1 diagnostics can leave more than one debugfs include in the reconstructed
# Samsung source. Recorder P2 only needs ktime.h to exist, so seed it at the
# first include block instead of depending on a globally unique debugfs anchor.
vmscan_c = root / "mm/vmscan.c"
vmscan_text = vmscan_c.read_text()
if "#include <linux/ktime.h>" not in vmscan_text:
    include_anchor = "#include <linux/debugfs.h>\n"
    include_pos = vmscan_text.find(include_anchor)
    if include_pos < 0:
        raise SystemExit("MGLRU recorder P2: debugfs include anchor missing")
    include_pos += len(include_anchor)
    vmscan_text = (
        vmscan_text[:include_pos]
        + "#include <linux/ktime.h>\n"
        + vmscan_text[include_pos:]
    )
    vmscan_c.write_text(vmscan_text)
    print("Seeded ktime include for MGLRU recorder P2")

print("Applying MGLRU power recorder P2")
subprocess.run([sys.executable, str(recorder), str(root)], check=True)

# Phase74 deliberately removed the stale EEVDF tick-deadline test because
# update_curr() became responsible for deadline-expiry rescheduling. The later
# Linux run-to-parity protection fix needs a tick hook again, but for protection
# expiry rather than deadline expiry. Normalize the current Phase74 shape into
# the historical anchor consumed by 109; 109 immediately replaces it with the
# new protection-aware form. This keeps the patch deterministic on the exact
# 35199901520 reconstruction without changing runtime behavior in-between.
fair_c = root / "kernel/sched/fair.c"
fc = fair_c.read_text()
phase74_tick = """\tif (cfs_rq->nr_running > 1 &&
\t    likely(!sched_eevdf_enabled))
\t\tcheck_preempt_tick(cfs_rq, curr);
"""
legacy_tick_anchor = """\tif (cfs_rq->nr_running > 1) {
\t\tif (unlikely(sched_eevdf_enabled)) {
\t\t\tif ((s64)(curr->vruntime - curr->deadline) >= 0)
\t\t\t\tresched_curr(rq_of(cfs_rq));
\t\t} else {
\t\t\tcheck_preempt_tick(cfs_rq, curr);
\t\t}
\t}
"""
phase74_count = fc.count(phase74_tick)
legacy_count = fc.count(legacy_tick_anchor)
if phase74_count == 1 and legacy_count == 0:
    fair_c.write_text(fc.replace(phase74_tick, legacy_tick_anchor, 1))
    print("Normalized Phase74 EEVDF tick anchor for protection-aware P1")
elif phase74_count == 0 and legacy_count == 1:
    print("EEVDF tick anchor already normalized")
else:
    raise SystemExit(
        f"EEVDF tick anchor shape mismatch: phase74={phase74_count} legacy={legacy_count}"
    )

print("Applying EEVDF efficiency/correctness P1")
subprocess.run([sys.executable, str(eevdf), str(root)], check=True)
subprocess.run([sys.executable, str(eevdf_finalize), str(root)], check=True)

print("Applying Android17 uclamp efficiency P1")
subprocess.run([sys.executable, str(uclamp), str(root)], check=True)
subprocess.run([sys.executable, str(uclamp_finalize), str(root)], check=True)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-phase108-p1-p2-p3-chain.txt").write_text(
    "mglru=phase108-modern-p1-p2-p3\n"
    "baseline_run=35193063669\n"
    "baseline_commit=810113f97015ba235d640bc7578ce1c3ece280f2\n"
    "p1=runtime-proven\n"
    "p2=runtime-proven\n"
    "p2_mmu_notifier_young_fix=1d4832becdc2cdb2cffe2a6050c9d9fd8ff1c58c\n"
    "p3=c28ac3c7eb945fee6e20f47d576af68fdff1392a\n"
    "p3_target=rmap-lookaround-special-vma-correctness\n"
    "diag_p1=per-cpu-path-counters-only\n"
    "diag_p2=runtime-gated-work-timing-flight-recorder\n"
    "diag_p2_default_level=0\n"
    "diag_interface=/sys/kernel/mm/lru_gen/diag\n"
    "recorder_control=/sys/kernel/mm/lru_gen/record\n"
    "recorder_stats=/sys/kernel/mm/lru_gen/stats\n"
    "recorder_trace=/sys/kernel/debug/lru_gen_trace\n"
)
(report_dir / "scheduler-efficiency-p1.txt").write_text(
    "experiment=scheduler-efficiency-p1\n"
    "requested_baseline_run=35199901520\n"
    "requested_baseline_commit=3de2b4cdb394a1c13c8fbfc68e8a081c8aa6a022\n"
    "eevdf=linux-6.17-protection-series-adapted\n"
    "eevdf_no_run_to_parity=not-applicable-always-enabled-backport\n"
    "uclamp=android17-a52-walt-efficiency-bridge\n"
    "uclamp_rt_default_min=0\n"
    "uclamp_task_schedtune_margin_stacking=removed\n"
    "schedtune_abi=retained\n"
    "walt_schedutil=retained\n"
)

print("Modern MGLRU P1 + P2 + P3 + power diagnostics P1/P2 applied")
print("Scheduler efficiency P1 applied")
