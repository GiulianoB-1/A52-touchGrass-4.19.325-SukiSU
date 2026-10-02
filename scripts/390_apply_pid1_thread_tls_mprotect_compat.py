#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE390_PID1_THREAD_TLS_MPROTECT_COMPAT_V1"

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

    anchor=r'''static bool a52_p389_pid1_writeprotected(unsigned long start, size_t len,
					 unsigned long prot, int pkey)
{
'''
    if anchor not in mp.read_text():
        raise SystemExit("Phase389 helper anchor missing; apply Phase389 first")

    insert=r'''/*
 * A52_PHASE390_PID1_THREAD_TLS_MPROTECT_COMPAT_V1
 *
 * Phase389 hardware evidence proved the next legacy-4K Bionic failure:
 *
 *   mmap(NULL, 0x5000, PROT_NONE, ...) -> native-page-aligned VMA
 *   mprotect(base + 0x1000, 0x3000, PROT_READ|PROT_WRITE) -> -EINVAL
 *
 * This is __allocate_thread_mapping() for the main thread's static TLS.
 * A 4K-built Bionic expects: 4K leading guard + 12K RW + 4K trailing guard.
 * On a 16K kernel the 20K mmap occupies two native pages.  We cannot express
 * the 4K leading guard, so for the exact PID1 bootstrap image we expand the
 * requested RW subrange downward to the first native page:
 *
 *   [base+0x1000, base+0x4000)  ->  [base, base+0x4000)
 *
 * The second native page remains PROT_NONE, preserving a native-size trailing
 * guard.  Unlike Phase389 WriteProtected emulation, this applies a real
 * mprotect to the rounded native page.
 */
static bool a52_p390_pid1_thread_tls_adjust(unsigned long *start, size_t *len,
					    unsigned long prot, int pkey)
{
	struct mm_struct *mm = current->mm;
	unsigned long old_start;
	size_t old_len;

	if (PAGE_SIZE != SZ_16K || pkey != -1 || !mm)
		return false;
	if (task_pid_nr(current) != 1)
		return false;

	/* Exact Phase384/385 ramdisk /init mm fingerprint. */
	if (READ_ONCE(mm->start_code) != 0x200000UL ||
	    READ_ONCE(mm->end_code) != 0x3692fcUL ||
	    READ_ONCE(mm->start_data) != 0x36f000UL ||
	    READ_ONCE(mm->end_data) != 0x36ffacUL)
		return false;

	if (prot != (PROT_READ | PROT_WRITE))
		return false;
	if ((*start & ~PAGE_MASK) != SZ_4K)
		return false;
	if (*len != (3U * SZ_4K))
		return false;

	old_start = *start;
	old_len = *len;
	*start &= PAGE_MASK;
	*len = PAGE_SIZE;

	a52_p383_record("P390 TLSMP old=%lx/%zx new=%lx/%zx",
		old_start, old_len, *start, *len);
	return true;
}

'''
    text=mp.read_text()
    idx=text.index(anchor)
    mp.write_text(text[:idx]+insert+text[idx:])
    print(f"{mp}: applied PID1 main-thread TLS native-page mprotect adapter")

    old=r'''	if (a52_p389_pid1_writeprotected(start, len, prot, pkey)) {
		a52_p383_record("P389 WPCOMPAT start=%lx len=%zx prot=%lx",
			start, len, prot);
		return 0;
	}

	if (unlikely(PAGE_SIZE == SZ_16K &&
'''
    new=r'''	if (a52_p389_pid1_writeprotected(start, len, prot, pkey)) {
		a52_p383_record("P389 WPCOMPAT start=%lx len=%zx prot=%lx",
			start, len, prot);
		return 0;
	}

	a52_p390_pid1_thread_tls_adjust(&start, &len, prot, pkey);

	if (unlikely(PAGE_SIZE == SZ_16K &&
'''
    one(mp,old,new,"invoke TLS mprotect adapter before native alignment check")

    txt=mp.read_text()
    for tok in [
        MARK,
        "a52_p390_pid1_thread_tls_adjust",
        "P390 TLSMP old=%lx/%zx new=%lx/%zx",
        "*len = PAGE_SIZE",
        "(*start & ~PAGE_MASK) != SZ_4K",
    ]:
        if tok not in txt:
            raise SystemExit(f"missing {tok}")

    print("A52 Phase390 PID1 main-thread TLS mprotect compatibility applied successfully")

if __name__=="__main__":
    main()
