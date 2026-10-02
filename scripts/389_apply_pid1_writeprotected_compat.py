#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE389_PID1_WRITEPROTECTED_COMPAT_V1"

def one(path, old, new, label):
    t=path.read_text()
    if new in t:
        print(f"{path}: {label} already applied")
        return
    n=t.count(old)
    if n!=1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(t.replace(old,new,1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv)!=2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    mp=root/"mm/mprotect.c"
    if not mp.is_file():
        raise SystemExit(f"missing {mp}")

    one(
        mp,
        "#include <linux/syscalls.h>\n",
        "#include <linux/syscalls.h>\n#include <linux/sched.h>\n#include <linux/sizes.h>\n#include <linux/a52_p383_dual_recorder.h>\n",
        "compat includes",
    )

    anchor='''/*
 * pkey==-1 when doing a legacy mprotect()
 */
static int do_mprotect_pkey(unsigned long start, size_t len,
'''
    helper=r'''/*
 * A52_PHASE389_PID1_WRITEPROTECTED_COMPAT_V1
 *
 * Phase388 proved that the current 4 KiB-built static /init aborts because
 * Bionic WriteProtected<T> issues:
 *
 *     mprotect(0x373000, 0x1000, PROT_READ)
 *
 * on a native 16 KiB kernel.  0x373000 is not a native-page boundary, so
 * do_mprotect_pkey() rejects it with -EINVAL before touching the VMA.
 *
 * The Phase385 loader intentionally keeps this exact legacy image in one
 * anonymous RWX native-page mapping.  Rounding the WriteProtected operation
 * out to 16 KiB would make unrelated neighboring data read-only.  For this
 * exact PID1 image only, emulate success for the one 4 KiB WriteProtected
 * cell and its later RW mutation form while leaving the enclosing native page
 * unchanged.
 *
 * This is a boot bridge, not general sub-page mprotect support.
 */
static bool a52_p389_pid1_writeprotected(unsigned long start, size_t len,
					 unsigned long prot, int pkey)
{
	struct mm_struct *mm = current->mm;

	if (PAGE_SIZE != SZ_16K || pkey != -1)
		return false;
	if (task_pid_nr(current) != 1 || !mm)
		return false;

	/* Exact Phase384/385 /init ELF accounting fingerprint. */
	if (READ_ONCE(mm->start_code) != 0x200000UL ||
	    READ_ONCE(mm->end_code) != 0x3692fcUL ||
	    READ_ONCE(mm->start_data) != 0x36f000UL ||
	    READ_ONCE(mm->end_data) != 0x36ffacUL)
		return false;

	if (start != 0x373000UL || len != SZ_4K)
		return false;

	return prot == PROT_READ || prot == (PROT_READ | PROT_WRITE);
}

/*
 * pkey==-1 when doing a legacy mprotect()
 */
static int do_mprotect_pkey(unsigned long start, size_t len,
'''
    one(mp,anchor,helper,"PID1 WriteProtected compatibility helper")

    old='''	prot &= ~(PROT_GROWSDOWN|PROT_GROWSUP);
	if (grows == (PROT_GROWSDOWN|PROT_GROWSUP)) /* can't be both */
		return -EINVAL;

	if (start & ~PAGE_MASK)
		return -EINVAL;
'''
    new=r'''	prot &= ~(PROT_GROWSDOWN|PROT_GROWSUP);
	if (grows == (PROT_GROWSDOWN|PROT_GROWSUP)) /* can't be both */
		return -EINVAL;

	if (a52_p389_pid1_writeprotected(start, len, prot, pkey)) {
		a52_p383_record("P389 WPCOMPAT start=%lx len=%zx prot=%lx",
			start, len, prot);
		return 0;
	}

	if (unlikely(PAGE_SIZE == SZ_16K &&
		     task_pid_nr(current) == 1 &&
		     current->mm &&
		     READ_ONCE(current->mm->start_code) == 0x200000UL &&
		     (start & ~PAGE_MASK))) {
		a52_p383_record("P389 OTHER start=%lx len=%zx prot=%lx",
			start, len, prot);
	}

	if (start & ~PAGE_MASK)
		return -EINVAL;
'''
    one(mp,old,new,"intercept exact legacy WriteProtected subpage")

    txt=mp.read_text()
    for tok in [MARK,"a52_p389_pid1_writeprotected","P389 WPCOMPAT","P389 OTHER"]:
        if tok not in txt:
            raise SystemExit(f"missing {tok}")
    print("A52 Phase389 PID1 WriteProtected compatibility shim applied successfully")

if __name__=="__main__":
    main()
