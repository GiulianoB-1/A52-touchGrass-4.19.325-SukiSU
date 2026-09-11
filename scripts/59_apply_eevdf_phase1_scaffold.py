#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 59_apply_eevdf_phase1_scaffold.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
sched_h = kernel / "include/linux/sched.h"

if not sched_h.is_file():
    raise SystemExit(f"missing scheduler header: {sched_h}")

text = sched_h.read_text()

if "EEVDF phase 1 scaffold" in text:
    print("EEVDF phase 1 scaffold already present")
    raise SystemExit(0)

run_node_old = """	struct rb_node			run_node;
	struct list_head		group_node;
"""
run_node_new = """	struct rb_node			run_node;

	/*
	 * EEVDF phase 1 scaffold.
	 *
	 * These fields mirror the Linux 6.6 EEVDF sched_entity state but are
	 * intentionally unused in this phase.  Task selection remains the
	 * existing Qualcomm/Samsung CFS + WALT implementation.
	 */
	u64				deadline;
	u64				min_deadline;

	struct list_head		group_node;
"""

runtime_old = """	u64				sum_exec_runtime;
	u64				vruntime;
	u64				prev_sum_exec_runtime;
"""
runtime_new = """	u64				sum_exec_runtime;
	u64				vruntime;
	s64				vlag;
	u64				slice;
	u64				prev_sum_exec_runtime;
"""

if text.count(run_node_old) != 1:
    raise SystemExit("sched_entity run_node anchor mismatch")
if text.count(runtime_old) != 1:
    raise SystemExit("sched_entity runtime anchor mismatch")

text = text.replace(run_node_old, run_node_new, 1)
text = text.replace(runtime_old, runtime_new, 1)
sched_h.write_text(text)

checks = [
    "u64\t\t\t\tdeadline;",
    "u64\t\t\t\tmin_deadline;",
    "s64\t\t\t\tvlag;",
    "u64\t\t\t\tslice;",
]
for needle in checks:
    if needle not in text:
        raise SystemExit(f"missing expected field after patch: {needle!r}")

print("EEVDF phase 1 sched_entity scaffold applied")
print(f"patched={sched_h}")
print("task_selection=unchanged")
print("walt=unchanged")
print("bpf_repair=unchanged")
