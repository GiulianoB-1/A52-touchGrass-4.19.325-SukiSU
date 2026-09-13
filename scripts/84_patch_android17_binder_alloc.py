#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 84_patch_android17_binder_alloc.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
android = root / "drivers/android"
cpath = android / "binder_alloc.c"
hpath = android / "binder_alloc.h"
bpath = android / "binder.c"
tpath = android / "binder_trace.h"

def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 anchor, found {count}")
    return text.replace(old, new, 1)

def replace_function(text, signature, replacement):
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")
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
        raise SystemExit(f"missing closing brace: {signature}")
    return text[:start] + replacement.rstrip() + text[end:]

# ---------------------------------------------------------------------------
# binder_alloc.h: retain the exact Android 17 data model, but expose the
# Linux 4.19 list_lru callback shape and our existing compatibility macros.
# ---------------------------------------------------------------------------
h = hpath.read_text()
if '#include "binder_compat.h"' not in h:
    h = replace_once(
        h,
        "#include <uapi/linux/android/binder.h>\n",
        "#include <uapi/linux/android/binder.h>\n#include \"binder_compat.h\"\n",
        "alloc_header_compat",
    )

old_proto = """enum lru_status binder_alloc_free_page(struct list_head *item,
				       struct list_lru_one *lru,
				       void *cb_arg);"""
new_proto = """enum lru_status binder_alloc_free_page(struct list_head *item,
				       struct list_lru_one *lru,
				       spinlock_t *lock,
				       void *cb_arg);"""
if old_proto in h:
    h = h.replace(old_proto, new_proto, 1)
elif new_proto not in h:
    raise SystemExit("allocator free-page prototype anchor mismatch")
hpath.write_text(h)

# ---------------------------------------------------------------------------
# binder_alloc.c: Android 17 allocator logic with Linux 4.19 MM/LRU adapters.
# ---------------------------------------------------------------------------
c = cpath.read_text()
c = c.replace('#include <kunit/visibility.h>\n', '#include "binder_compat.h"\n')
c = c.replace('#include <trace/hooks/binder.h>\n', '')

# The GKI vendor hook is advisory. Samsung 4.19 has no Android vendor-hook
# framework, so keep allocator semantics and make the hook a no-op.
hooks = sorted(set(re.findall(r'\b(trace_android_vh_[A-Za-z0-9_]+)\s*\(', c)))
if hooks:
    anchor = '#include "binder_trace.h"\n'
    defs = "\n/* Android GKI allocator hooks are unavailable on Samsung 4.19. */\n"
    defs += "\n".join(f"#define {name}(...) do {{ }} while (0)" for name in hooks)
    defs += "\n"
    c = replace_once(c, anchor, anchor + defs, "allocator_vendor_hooks")

# Linux 6.18 list_lru takes nid/memcg at add/del time. Linux 4.19 derives
# those internally, so collapse the Android 17 calls to the classic ABI.
c = re.sub(
    r'list_lru_add\(alloc->freelist,\s*page_to_lru\(page\),\s*page_to_nid\(page\),\s*NULL\)',
    'list_lru_add(alloc->freelist, page_to_lru(page))',
    c,
)
c = re.sub(
    r'list_lru_del\(alloc->freelist,\s*page_to_lru\(page\),\s*page_to_nid\(page\),\s*NULL\)',
    'list_lru_del(alloc->freelist, page_to_lru(page))',
    c,
)

# Linux 4.19 has neither memset_page nor memcpy_{to,from}_page. Preserve the
# modern page-based allocator, but perform the same accesses through kmap.
helper_anchor = 'static struct list_lru binder_freelist;\n'
helpers = r'''
static void binder_memset_page_4_19(struct page *page, size_t off,
				   int value, size_t bytes)
{
	void *base = kmap_atomic(page);

	memset(base + off, value, bytes);
	kunmap_atomic(base);
}

static void binder_memcpy_to_page_4_19(struct page *page, size_t off,
				      const void *src, size_t bytes)
{
	void *base = kmap_atomic(page);

	memcpy(base + off, src, bytes);
	kunmap_atomic(base);
}

static void binder_memcpy_from_page_4_19(void *dst, struct page *page,
					 size_t off, size_t bytes)
{
	void *base = kmap_atomic(page);

	memcpy(dst, base + off, bytes);
	kunmap_atomic(base);
}

'''
if "binder_memset_page_4_19" not in c:
    c = replace_once(c, helper_anchor, helper_anchor + helpers, "allocator_page_helpers")
c = c.replace("memset_page(page, pgoff, 0, size);",
              "binder_memset_page_4_19(page, pgoff, 0, size);")
c = c.replace("memcpy_to_page(page, pgoff, ptr, size);",
              "binder_memcpy_to_page_4_19(page, pgoff, ptr, size);")
c = c.replace("memcpy_from_page(ptr, page, pgoff, size);",
              "binder_memcpy_from_page_4_19(ptr, page, pgoff, size);")

# kmap_local_page() is newer than 4.19. This path may sleep on 4.19, so use
# kmap()/kunmap() rather than atomic mapping.
old_user_copy = """		kptr = kmap_local_page(page) + pgoff;
		ret = copy_from_user(kptr, from, size);
		kunmap_local(kptr);"""
new_user_copy = """		kptr = kmap(page) + pgoff;
		ret = copy_from_user(kptr, from, size);
		kunmap(page);"""
if old_user_copy in c:
    c = c.replace(old_user_copy, new_user_copy, 1)
elif new_user_copy not in c:
    raise SystemExit("kmap_local_page copy path anchor mismatch")

# Android 17 uses per-VMA locking and vma_lookup(). Linux 4.19 predates those
# primitives. Serialize page installation through the allocator mutex and use
# mmap_sem + find_vma(), which is the proven 4.19 Binder mapping model.
install_fn = r'''static int binder_install_single_page(struct binder_alloc *alloc,
				      unsigned long index,
				      unsigned long addr)
{
	struct mm_struct *mm = alloc->mm;
	struct vm_area_struct *vma;
	struct page *page;
	int ret = -ESRCH;

	if (!mmget_not_zero(mm))
		return -ESRCH;

	page = binder_page_alloc(alloc, index);
	if (!page) {
		mmput_async(mm);
		return -ENOMEM;
	}

	down_read(&mm->mmap_sem);
	vma = find_vma(mm, addr);
	if (vma && addr >= vma->vm_start && binder_alloc_is_mapped(alloc))
		ret = vm_insert_page(vma, addr, page);
	up_read(&mm->mmap_sem);

	if (!ret)
		binder_set_installed_page(alloc, index, page);
	else
		binder_free_page(page);

	mmput_async(mm);
	return ret;
}'''
c = replace_function(c, "static int binder_install_single_page(", install_fn)

# Keep the allocator mutex held while installing pages. This mirrors the
# serialization guarantee of the original 4.19 allocator and makes the
# 6.18 EBUSY/GUP race-recovery path unnecessary on this backport.
old_newbuf_tail = """	buffer->data_size = data_size;
	buffer->offsets_size = offsets_size;
	buffer->extra_buffers_size = extra_buffers_size;
	buffer->pid = current->tgid;
	mutex_unlock(&alloc->mutex);

	ret = binder_install_buffer_pages(alloc, buffer, size);
	if (ret) {
		binder_alloc_free_buf(alloc, buffer);
		buffer = ERR_PTR(ret);
	}
out:
	return buffer;"""
new_newbuf_tail = """	buffer->data_size = data_size;
	buffer->offsets_size = offsets_size;
	buffer->extra_buffers_size = extra_buffers_size;
	buffer->pid = current->tgid;

	ret = binder_install_buffer_pages(alloc, buffer, size);
	if (ret) {
		binder_free_buf_locked(alloc, buffer);
		buffer = ERR_PTR(ret);
	}
	mutex_unlock(&alloc->mutex);
out:
	return buffer;"""
if old_newbuf_tail in c:
    c = c.replace(old_newbuf_tail, new_newbuf_tail, 1)
elif new_newbuf_tail not in c:
    raise SystemExit("allocator new-buffer install serialization anchor mismatch")

# Rewrite the 6.18 shrinker callback onto the Linux 4.19 list_lru callback
# contract. The modern Android 17 page-private metadata design is retained.
free_page_fn = r'''enum lru_status binder_alloc_free_page(struct list_head *item,
				       struct list_lru_one *lru,
				       spinlock_t *lock,
				       void *cb_arg)
	__must_hold(lock)
{
	struct binder_shrinker_mdata *mdata =
		container_of(item, struct binder_shrinker_mdata, lru);
	struct binder_alloc *alloc = mdata->alloc;
	struct mm_struct *mm = alloc->mm;
	struct vm_area_struct *vma;
	struct page *page_to_free;
	unsigned long page_addr;
	size_t index;

	if (!mutex_trylock(&alloc->mutex))
		return LRU_SKIP;

	if (!mmget_not_zero(mm))
		goto err_mmget;

	if (!down_read_trylock(&mm->mmap_sem))
		goto err_mmap_sem;

	index = mdata->page_index;
	page_addr = alloc->vm_start + index * PAGE_SIZE;
	vma = find_vma(mm, page_addr);
	if (vma && page_addr < vma->vm_start)
		vma = NULL;

	if (vma && !binder_alloc_is_mapped(alloc))
		vma = NULL;

	page_to_free = binder_get_installed_page(alloc, index);
	if (!page_to_free)
		goto err_page;

	trace_binder_unmap_kernel_start(alloc, index);
	binder_set_installed_page(alloc, index, NULL);
	trace_binder_unmap_kernel_end(alloc, index);

	list_lru_isolate(lru, item);
	spin_unlock(lock);

	if (vma) {
		trace_binder_unmap_user_start(alloc, index);
		zap_page_range(vma, page_addr, PAGE_SIZE);
		trace_binder_unmap_user_end(alloc, index);
	}

	up_read(&mm->mmap_sem);
	mmput_async(mm);
	binder_free_page(page_to_free);
	spin_lock(lock);
	mutex_unlock(&alloc->mutex);
	return LRU_REMOVED_RETRY;

err_page:
	up_read(&mm->mmap_sem);
err_mmap_sem:
	mmput_async(mm);
err_mmget:
	mutex_unlock(&alloc->mutex);
	return LRU_SKIP;
}'''
c = replace_function(c, "enum lru_status binder_alloc_free_page(", free_page_fn)

# Linux 4.19 uses a statically allocated shrinker registered with
# register_shrinker()/unregister_shrinker().
start = c.find("static struct shrinker *binder_shrinker;")
end_sig = "void binder_alloc_shrinker_exit(void)"
if start < 0 or end_sig not in c[start:]:
    raise SystemExit("modern shrinker block anchors missing")
end_start = c.find(end_sig, start)
brace = c.find("{", end_start)
depth = 0
end = None
for i in range(brace, len(c)):
    if c[i] == "{":
        depth += 1
    elif c[i] == "}":
        depth -= 1
        if depth == 0:
            end = i + 1
            break
if end is None:
    raise SystemExit("modern shrinker exit closing brace missing")
shrinker_block = r'''static struct shrinker binder_shrinker = {
	.count_objects = binder_shrink_count,
	.scan_objects = binder_shrink_scan,
	.seeks = DEFAULT_SEEKS,
};

VISIBLE_IF_KUNIT void __binder_alloc_init(struct binder_alloc *alloc,
					 struct list_lru *freelist)
{
	alloc->pid = current->group_leader->pid;
	alloc->mm = current->mm;
	mmgrab(alloc->mm);
	mutex_init(&alloc->mutex);
	INIT_LIST_HEAD(&alloc->buffers);
	alloc->freelist = freelist;
}
EXPORT_SYMBOL_IF_KUNIT(__binder_alloc_init);

void binder_alloc_init(struct binder_alloc *alloc)
{
	__binder_alloc_init(alloc, &binder_freelist);
}

int binder_alloc_shrinker_init(void)
{
	int ret = list_lru_init(&binder_freelist);

	if (ret)
		return ret;

	ret = register_shrinker(&binder_shrinker);
	if (ret)
		list_lru_destroy(&binder_freelist);
	return ret;
}

void binder_alloc_shrinker_exit(void)
{
	unregister_shrinker(&binder_shrinker);
	list_lru_destroy(&binder_freelist);
}'''
c = c[:start] + shrinker_block + c[end:]

cpath.write_text(c.rstrip() + "\n")

# Phase81 translated vm_start to the retained old allocator's 'buffer' field.
# With the real Android 17 allocator imported, restore the native vm_start ABI.
# Phase81 also adapted binder_alloc_new_buf() to the retained Samsung 4.19
# allocator by appending the sender pid. The native Android 17 allocator uses
# the upstream five-argument ABI, so remove that temporary compatibility arg.
b = bpath.read_text()
old_alloc_call = """t->buffer = binder_alloc_new_buf(&target_proc->alloc,
		tr->data_size, tr->offsets_size, extra_buffers_size,
		!reply && (t->flags & TF_ONE_WAY), thread->pid);"""
new_alloc_call = """t->buffer = binder_alloc_new_buf(&target_proc->alloc,
		tr->data_size, tr->offsets_size, extra_buffers_size,
		!reply && (t->flags & TF_ONE_WAY));"""
if old_alloc_call in b:
    b = b.replace(old_alloc_call, new_alloc_call, 1)
elif new_alloc_call not in b:
    raise SystemExit("Android17 binder_alloc_new_buf ABI restore anchor mismatch")

b = b.replace("(unsigned long)proc->alloc.buffer", "proc->alloc.vm_start")
bpath.write_text(b.rstrip() + "\n")

t = tpath.read_text()
t = t.replace("(unsigned long)alloc->buffer", "alloc->vm_start")
tpath.write_text(t.rstrip() + "\n")

print("Binder84: Android17 binder_alloc imported with Linux 4.19 MM/LRU compatibility")
