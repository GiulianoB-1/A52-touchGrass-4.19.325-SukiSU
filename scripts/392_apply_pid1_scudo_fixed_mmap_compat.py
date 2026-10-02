#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE392_PID1_SCUDO_FIXED_MMAP_COMPAT_V1"

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
    util=root/"mm/util.c"
    if not util.is_file():
        raise SystemExit(f"missing {util}")

    one(
        util,
        "#include <linux/userfaultfd_k.h>\n",
        "#include <linux/userfaultfd_k.h>\n#include <linux/sizes.h>\n#include <linux/a52_p383_dual_recorder.h>\n",
        "Phase392 includes",
    )

    anchor='''unsigned long vm_mmap_pgoff(struct file *file, unsigned long addr,
	unsigned long len, unsigned long prot,
	unsigned long flag, unsigned long pgoff)
{
'''
    helper=r'''/*
 * A52_PHASE392_PID1_SCUDO_FIXED_MMAP_COMPAT_V1
 *
 * Phase391 hardware evidence:
 *
 *   mmap(NULL, 0x210000000, PROT_NONE, ...) = native-aligned arena
 *   mmap(0x...6000, 0x40000, PROT_READ|PROT_WRITE,
 *        MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED, -1, 0) = -EINVAL
 *
 * The second request is Scudo committing a subrange inside its already
 * reserved PROT_NONE arena. A 4 KiB-built Scudo may choose a fixed address
 * aligned to 4 KiB but not to the native 16 KiB Linux page size.
 *
 * For the exact PID1 bootstrap image only, and only when the entire rounded
 * native range is already contained in one anonymous PROT_NONE VMA, expand
 * the fixed commit to native-page boundaries. Return the original requested
 * address to userspace so Scudo keeps its 4 KiB-era address arithmetic.
 *
 * This is not general misaligned MAP_FIXED support.
 */
static bool a52_p392_pid1_scudo_fixed_mmap(struct mm_struct *mm,
					   struct file *file,
					   unsigned long *addr,
					   unsigned long *len,
					   unsigned long prot,
					   unsigned long flags,
					   unsigned long *user_addr)
{
	struct vm_area_struct *vma;
	unsigned long req_start = *addr;
	unsigned long req_len = *len;
	unsigned long native_start;
	unsigned long native_end;

	if (PAGE_SIZE != SZ_16K || !mm || file)
		return false;
	if (task_pid_nr(current) != 1)
		return false;

	/* Exact Phase384/385 /init mm accounting fingerprint. */
	if (READ_ONCE(mm->start_code) != 0x200000UL ||
	    READ_ONCE(mm->end_code) != 0x3692fcUL ||
	    READ_ONCE(mm->start_data) != 0x36f000UL ||
	    READ_ONCE(mm->end_data) != 0x36ffacUL)
		return false;

	if (!(flags & MAP_FIXED) ||
	    !(flags & MAP_PRIVATE) ||
	    !(flags & MAP_ANONYMOUS) ||
	    (flags & MAP_FIXED_NOREPLACE))
		return false;
	if (prot != (PROT_READ | PROT_WRITE))
		return false;

	/* Legacy request must itself still obey 4 KiB alignment. */
	if ((req_start & (SZ_4K - 1)) || !(req_start & ~PAGE_MASK))
		return false;
	if (!req_len || (req_len & (SZ_4K - 1)))
		return false;
	if (req_start + req_len < req_start)
		return false;

	native_start = req_start & PAGE_MASK;
	native_end = PAGE_ALIGN(req_start + req_len);
	if (native_end <= native_start)
		return false;

	/*
	 * mmap_sem is held for write by vm_mmap_pgoff() here. Require the whole
	 * rounded range to live inside one anonymous PROT_NONE reservation.
	 */
	vma = find_vma(mm, native_start);
	if (!vma || vma->vm_start > native_start || vma->vm_end < native_end)
		return false;
	if (vma->vm_file)
		return false;
	if (vma->vm_flags & (VM_READ | VM_WRITE | VM_EXEC))
		return false;

	*user_addr = req_start;
	*addr = native_start;
	*len = native_end - native_start;

	a52_p383_record("P392 SCUDOMAP old=%lx/%lx new=%lx/%lx",
		req_start, req_len, *addr, *len);
	return true;
}

unsigned long vm_mmap_pgoff(struct file *file, unsigned long addr,
	unsigned long len, unsigned long prot,
	unsigned long flag, unsigned long pgoff)
{
'''
    one(util,anchor,helper,"Scudo fixed-mmap compatibility helper")

    old=r'''	unsigned long ret;
	struct mm_struct *mm = current->mm;
	unsigned long populate;
	LIST_HEAD(uf);

	ret = security_mmap_file(file, prot, flag);
	if (!ret) {
		if (down_write_killable(&mm->mmap_sem))
			return -EINTR;
		ret = do_mmap_pgoff(file, addr, len, prot, flag, pgoff,
				    &populate, &uf);
		up_write(&mm->mmap_sem);
'''
    new=r'''	unsigned long ret;
	struct mm_struct *mm = current->mm;
	unsigned long populate;
	unsigned long a52_p392_user_addr = 0;
	unsigned long a52_p392_native_addr = 0;
	bool a52_p392_compat = false;
	LIST_HEAD(uf);

	ret = security_mmap_file(file, prot, flag);
	if (!ret) {
		if (down_write_killable(&mm->mmap_sem))
			return -EINTR;

		a52_p392_compat = a52_p392_pid1_scudo_fixed_mmap(mm, file,
			&addr, &len, prot, flag, &a52_p392_user_addr);
		if (a52_p392_compat)
			a52_p392_native_addr = addr;

		ret = do_mmap_pgoff(file, addr, len, prot, flag, pgoff,
				    &populate, &uf);

		if (a52_p392_compat) {
			if (ret == a52_p392_native_addr) {
				a52_p383_record("P392 SCUDOOK user=%lx native=%lx len=%lx",
					a52_p392_user_addr, a52_p392_native_addr, len);
				ret = a52_p392_user_addr;
			} else {
				a52_p383_record_critical("P392 SCUDOFAIL ret=%lx native=%lx",
					ret, a52_p392_native_addr);
			}
		}

		up_write(&mm->mmap_sem);
'''
    one(util,old,new,"translate exact Scudo fixed mapping under mmap_sem")

    txt=util.read_text()
    for tok in [
        MARK,
        "a52_p392_pid1_scudo_fixed_mmap",
        "P392 SCUDOMAP",
        "P392 SCUDOOK",
        "P392 SCUDOFAIL",
        "vma->vm_flags & (VM_READ | VM_WRITE | VM_EXEC)",
    ]:
        if tok not in txt:
            raise SystemExit(f"missing {tok}")

    print("A52 Phase392 PID1 Scudo fixed-mmap compatibility applied successfully")

if __name__=="__main__":
    main()
