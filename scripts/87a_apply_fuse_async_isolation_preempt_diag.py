#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 87a_apply_fuse_async_isolation_preempt_diag.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
filec = root / "fs/fuse/file.c"
fault = root / "arch/arm64/mm/fault.c"

for p in (filec, fault):
    if not p.is_file():
        raise SystemExit(f"missing {p}")

s = filec.read_text()

old_read = """#ifdef CONFIG_FUSE_PASSTHROUGH
	struct fuse_file *ff = iocb->ki_filp->private_data;

	if (ff->passthrough.filp)
		return fuse_passthrough_read_iter(iocb, to);
#endif
"""
new_read = """#ifdef CONFIG_FUSE_PASSTHROUGH
	struct fuse_file *ff = iocb->ki_filp->private_data;

	/*
	 * A52 Phase87A isolation:
	 * Keep the proven synchronous passthrough fast path, but route AIO
	 * through the native Samsung FUSE path until the 5.4 AIO adaptation
	 * is proven safe on this 4.19 core.
	 */
	if (ff->passthrough.filp && is_sync_kiocb(iocb)) {
		unsigned int a52_pc = preempt_count();
		ssize_t a52_ret = fuse_passthrough_read_iter(iocb, to);

		if (unlikely(preempt_count() != a52_pc))
			pr_emerg("A52FUSE_PREEMPT read before=%x after=%x pid=%d comm=%s\\n",
				 a52_pc, preempt_count(), current->pid, current->comm);
		return a52_ret;
	}
#endif
"""

old_write = """#ifdef CONFIG_FUSE_PASSTHROUGH
	struct fuse_file *ff = iocb->ki_filp->private_data;

	if (ff->passthrough.filp)
		return fuse_passthrough_write_iter(iocb, from);
#endif
"""
new_write = """#ifdef CONFIG_FUSE_PASSTHROUGH
	struct fuse_file *ff = iocb->ki_filp->private_data;

	/* Phase87A: isolate legacy-AIO passthrough on the 4.19 core. */
	if (ff->passthrough.filp && is_sync_kiocb(iocb)) {
		unsigned int a52_pc = preempt_count();
		ssize_t a52_ret = fuse_passthrough_write_iter(iocb, from);

		if (unlikely(preempt_count() != a52_pc))
			pr_emerg("A52FUSE_PREEMPT write before=%x after=%x pid=%d comm=%s\\n",
				 a52_pc, preempt_count(), current->pid, current->comm);
		return a52_ret;
	}
#endif
"""

old_mmap = """#ifdef CONFIG_FUSE_PASSTHROUGH
	struct fuse_file *ff = file->private_data;

	if (ff->passthrough.filp)
		return fuse_passthrough_mmap(file, vma);
#endif
"""
new_mmap = """#ifdef CONFIG_FUSE_PASSTHROUGH
	struct fuse_file *ff = file->private_data;

	if (ff->passthrough.filp) {
		unsigned int a52_pc = preempt_count();
		int a52_ret = fuse_passthrough_mmap(file, vma);

		if (unlikely(preempt_count() != a52_pc))
			pr_emerg("A52FUSE_PREEMPT mmap before=%x after=%x pid=%d comm=%s\\n",
				 a52_pc, preempt_count(), current->pid, current->comm);
		return a52_ret;
	}
#endif
"""

for name, old, new in (
    ("read route", old_read, new_read),
    ("write route", old_write, new_write),
    ("mmap route", old_mmap, new_mmap),
):
    if old in s:
        s = s.replace(old, new, 1)
    elif new not in s:
        raise SystemExit(f"{name}: Phase87 shape not found")

filec.write_text(s)

s = fault.read_text()
old_fault = """	/*
	 * If we're in an interrupt or have no user context, we must not take
	 * the fault.
	 */
	if (faulthandler_disabled() || !mm)
		goto no_context;
"""
new_fault = """	/*
	 * If we're in an interrupt or have no user context, we must not take
	 * the fault.
	 *
	 * A52 Phase87A diagnostic: an EL0 fault must normally arrive with a
	 * serviceable task context.  Record the exact task/preempt state before
	 * Samsung's panic-on-oops path consumes the evidence.
	 */
	if (unlikely(user_mode(regs) && (faulthandler_disabled() || !mm)))
		pr_emerg("A52PF_BADCTX comm=%s pid=%d cpu=%d addr=%016lx esr=%08x mm=%d pf_disabled=%d in_atomic=%d preempt_count=%x\\n",
			 current->comm, current->pid, raw_smp_processor_id(), addr, esr,
			 !!mm, pagefault_disabled(), in_atomic(), preempt_count());

	if (faulthandler_disabled() || !mm)
		goto no_context;
"""

if old_fault in s:
    s = s.replace(old_fault, new_fault, 1)
elif new_fault not in s:
    raise SystemExit("fault.c: page-fault context anchor not found")

fault.write_text(s)

# Verify narrow scope.
fs = filec.read_text()
pf = fault.read_text()
checks = [
    (fs, "ff->passthrough.filp && is_sync_kiocb(iocb)"),
    (fs, "A52FUSE_PREEMPT read"),
    (fs, "A52FUSE_PREEMPT write"),
    (fs, "A52FUSE_PREEMPT mmap"),
    (pf, "A52PF_BADCTX"),
    (pf, "pagefault_disabled()"),
    (pf, "preempt_count()"),
]
for data, needle in checks:
    if needle not in data:
        raise SystemExit(f"missing postcondition: {needle}")

report = root.parent.parent / "artifacts" / "phase87a-fuse-async-isolation-preempt-diag.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=87a-fuse-stability-isolation\\n"
    "base=phase87-fuse-passthrough\\n"
    "sync_passthrough=enabled\\n"
    "mmap_passthrough=enabled\\n"
    "async_passthrough=disabled-falls-back-to-native-fuse\\n"
    "pagefault_bad_context_recorder=enabled\\n"
    "preempt_balance_checks=read-write-mmap\\n"
    "f2fs_changes=none\\n"
)
print(report.read_text(), end="")
