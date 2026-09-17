#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 110b_finalize_uclamp_efficiency_p1.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
core = root / "kernel/sched/core.c"
if not core.is_file():
    raise SystemExit(f"missing core.c: {core}")

text = core.read_text()
start = text.find("unsigned int uclamp_task(struct task_struct *p)")
end = text.find("bool uclamp_boosted(struct task_struct *p)", start)
if start < 0 or end < 0:
    raise SystemExit("could not bound generated uclamp_task")

block = text[start:end].replace("\\t", "\t")
text = text[:start] + block + text[end:]

if "\\t" in text[start:start + len(block)]:
    raise SystemExit("literal backslash-t remains in uclamp_task C source")
if "schedtune_task_margin" in block:
    raise SystemExit("uclamp_task still stacks SchedTune task margin")
if "sysctl_sched_uclamp_util_min_rt_default = 0;" not in text:
    raise SystemExit("RT default clamp compatibility value missing")

core.write_text(text)
print("uclamp efficiency generated source finalized")
print("generated_helper_tabs=sanitized")
