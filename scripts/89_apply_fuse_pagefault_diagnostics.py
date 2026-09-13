#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 89_apply_fuse_pagefault_diagnostics.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
fuse = root / "fs/fuse/passthrough.c"
fault = root / "arch/arm64/mm/fault.c"

for p in (fuse, fault):
    if not p.is_file():
        raise SystemExit(f"missing {p}")

# ---------------------------------------------------------------------------
# FUSE passthrough balance diagnostics.
# Preserve behavior exactly; only emit if a fast path returns with a changed
# preempt_count, which should never happen.
# ---------------------------------------------------------------------------
s = fuse.read_text()

def add_entry_balance(func_sig, local_anchor, tag):
    global s
    start = s.find(func_sig)
    if start < 0:
        raise SystemExit(f"missing function: {func_sig}")
    next_func = s.find("\n}\n", start)
    if next_func < 0:
        raise SystemExit(f"unable to isolate: {func_sig}")
    body = s[start:next_func+3]
    if f"A52FUSE_BALANCE {tag}" in body:
        return

    if local_anchor not in body:
        raise SystemExit(f"{tag}: local anchor missing")
    body = body.replace(local_anchor, local_anchor + "\tunsigned int a52_pc_entry = preempt_count();\n", 1)

    # Convert only the final return in this function to a checked return.
    idx = body.rfind("\treturn ret;")
    if idx < 0:
        raise SystemExit(f"{tag}: final return ret missing")
    checked = (
        "\tif (unlikely(preempt_count() != a52_pc_entry))\n"
        f"\t\tpr_emerg(\"A52FUSE_BALANCE {tag} pid=%d comm=%s cpu=%d before=%x after=%x pf_disabled=%d in_atomic=%d\\n\",\n"
        "\t\t\t current->pid, current->comm, raw_smp_processor_id(),\n"
        "\t\t\t a52_pc_entry, preempt_count(), pagefault_disabled(), in_atomic());\n"
        "\treturn ret;"
    )
    body = body[:idx] + checked + body[idx + len("\treturn ret;"):]
    s = s[:start] + body + s[next_func+3:]

add_entry_balance(
    "ssize_t fuse_passthrough_read_iter(struct kiocb *iocb_fuse,",
    "\tstruct file *passthrough_filp = ff->passthrough.filp;\n",
    "read"
)
add_entry_balance(
    "ssize_t fuse_passthrough_write_iter(struct kiocb *iocb_fuse,",
    "\tstruct inode *passthrough_inode = file_inode(passthrough_filp);\n",
    "write"
)
add_entry_balance(
    "int fuse_passthrough_mmap(struct file *file, struct vm_area_struct *vma)",
    "\tstruct file *passthrough_filp = ff->passthrough.filp;\n",
    "mmap"
)

fuse.write_text(s)

# ---------------------------------------------------------------------------
# ARM64 page-fault bad-context recorder.
# This runs only for a user-mode fault that Samsung is about to misclassify as
# no_context because pagefaults are disabled, the task is atomic, or mm vanished.
# ---------------------------------------------------------------------------
s = fault.read_text()
old = """	/*
	 * If we're in an interrupt or have no user context, we must not take
	 * the fault.
	 */
	if (faulthandler_disabled() || !mm)
		goto no_context;
"""
new = """	/*
	 * If we're in an interrupt or have no user context, we must not take
	 * the fault.
	 *
	 * A52 Phase89 recorder: if an EL0 fault arrives in a non-serviceable
	 * task context, preserve the exact state in ramoops before PANIC_ON_OOPS.
	 */
	if (unlikely(user_mode(regs) && (faulthandler_disabled() || !mm)))
		pr_emerg("A52PF_BADCTX pid=%d comm=%s cpu=%d addr=%016lx esr=%08x pc=%016llx sp=%016llx mm=%d pf_disabled=%d pf_depth=%d in_atomic=%d irqs_disabled=%d preempt_count=%08x\\n",
			 current->pid, current->comm, raw_smp_processor_id(), addr, esr,
			 (unsigned long long)regs->pc, (unsigned long long)regs->sp,
			 !!mm, pagefault_disabled(), current->pagefault_disabled,
			 in_atomic(), irqs_disabled(), preempt_count());

	if (faulthandler_disabled() || !mm)
		goto no_context;
"""

if old in s:
    s = s.replace(old, new, 1)
elif "A52PF_BADCTX" not in s:
    raise SystemExit("fault.c bad-context anchor not found")

fault.write_text(s)

# Structural validation.
f = fuse.read_text()
p = fault.read_text()
for needle in (
    "A52FUSE_BALANCE read",
    "A52FUSE_BALANCE write",
    "A52FUSE_BALANCE mmap",
    "a52_pc_entry = preempt_count()",
):
    if needle not in f:
        raise SystemExit(f"missing FUSE diagnostic: {needle}")

for needle in (
    "A52PF_BADCTX",
    "current->pagefault_disabled",
    "irqs_disabled()",
    "preempt_count()",
):
    if needle not in p:
        raise SystemExit(f"missing page-fault diagnostic: {needle}")

report = root.parent.parent / "artifacts" / "phase89-fuse-pagefault-diagnostics.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=89-runtime-crash-diagnostics\n"
    "behavior_change=none\n"
    "fuse_balance_markers=read,write,mmap\n"
    "pagefault_badctx_marker=A52PF_BADCTX\n"
    "records=pid,comm,cpu,addr,esr,pc,sp,mm,pagefault_disabled,pagefault_depth,in_atomic,irqs_disabled,preempt_count\n"
)
print(report.read_text(), end="")
