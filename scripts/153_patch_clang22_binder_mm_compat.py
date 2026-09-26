#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 153_patch_clang22_binder_mm_compat.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
path = root / "drivers/android/binder_alloc.c"
text = path.read_text()

marker = "A52 Clang22 P153: Linux 4.19 binder_page_insert MM compat"
if marker in text:
    print("[skip] binder_page_insert compatibility already present")
    raise SystemExit(0)

signature = "static int binder_page_insert(struct binder_alloc *alloc,"
start = text.find(signature)
if start < 0:
    raise SystemExit("binder_page_insert() not found")

brace = text.find("{", start)
if brace < 0:
    raise SystemExit("binder_page_insert(): opening brace not found")

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
    raise SystemExit("binder_page_insert(): closing brace not found")

old = text[start:end]
required = (
    "lock_vma_under_rcu(mm, addr)",
    "vma_end_read(vma)",
    "vma_lookup(mm, addr)",
    "mmap_read_lock(mm)",
    "mmap_read_unlock(mm)",
)
missing = [x for x in required if x not in old]
if missing:
    raise SystemExit("unexpected binder_page_insert() body, missing: " + ", ".join(missing))

new = r'''static int binder_page_insert(struct binder_alloc *alloc,
			      unsigned long addr,
			      struct page *page)
{
	struct mm_struct *mm = alloc->mm;
	struct vm_area_struct *vma;
	int ret = -ESRCH;

	/*
	 * A52 Clang22 P153: Linux 4.19 binder_page_insert MM compat.
	 *
	 * Android 17 uses per-VMA locking (lock_vma_under_rcu/vma_end_read)
	 * and vma_lookup(). Samsung Linux 4.19 predates those APIs. Use the
	 * same mmap_sem + find_vma() model as the rest of our Binder allocator
	 * backport while preserving the mapped-state check and vm_insert_page().
	 */
	down_read(&mm->mmap_sem);
	vma = find_vma(mm, addr);
	if (vma && addr >= vma->vm_start && binder_alloc_is_mapped(alloc))
		ret = vm_insert_page(vma, addr, page);
	up_read(&mm->mmap_sem);

	return ret;
}'''

text = text[:start] + new + text[end:]
path.write_text(text)

check = path.read_text()
for forbidden in (
    "lock_vma_under_rcu(mm, addr)",
    "vma_end_read(vma)",
    "vma_lookup(mm, addr)",
):
    if forbidden in check:
        raise SystemExit(f"modern MM API remains after patch: {forbidden}")

if marker not in check:
    raise SystemExit("P153 marker missing after patch")

print("[patched] binder_page_insert(): Android 17 per-VMA APIs -> Linux 4.19 mmap_sem/find_vma")
