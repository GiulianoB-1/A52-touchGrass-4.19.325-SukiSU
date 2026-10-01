#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 161_apply_zram_multicomp_zstd.py <kernel_tree>")

root = Path(sys.argv[1]).resolve()
drv = root / "drivers/block/zram/zram_drv.c"
hdr = root / "drivers/block/zram/zram_drv.h"
kcfg = root / "drivers/block/zram/Kconfig"
defcfg = root / "arch/arm64/configs/a52xq_defconfig"

for p in (drv, hdr, kcfg, defcfg):
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")

def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)

# Kconfig + defconfig.
s = kcfg.read_text()
if "config ZRAM_MULTI_COMP" not in s:
    anchor = '''config ZRAM_LRU_WRITEBACK
\tbool
\tdepends on ZRAM_WRITEBACK
\tdefault y
'''
    block = '''config ZRAM_MULTI_COMP
\tbool "Enable ZRAM secondary recompression"
\tdepends on ZRAM
\tdefault y
\thelp
\t  Keep the normal fast primary compressor and allow a secondary
\t  compressor to recompress selected pages through the
\t  recomp_algorithm and recompress sysfs attributes.

'''
    s = replace_once(s, anchor, block + anchor, "Kconfig multi-comp insertion")
kcfg.write_text(s)

s = defcfg.read_text()
if "CONFIG_ZRAM_MULTI_COMP=y" not in s:
    anchor = "CONFIG_ZRAM_LRU_WRITEBACK=y\n"
    if anchor not in s:
        raise SystemExit("defconfig ZRAM_LRU_WRITEBACK anchor missing")
    s = s.replace(anchor, anchor + "CONFIG_ZRAM_MULTI_COMP=y\n", 1)
for req in ("CONFIG_ZRAM=y", "CONFIG_CRYPTO_ZSTD=y"):
    if req not in s:
        raise SystemExit(f"required config missing: {req}")
defcfg.write_text(s)

# Data structures. Samsung's tree uses a 24-bit object-size field, leaving
# ample high flag bits. We use one flag to identify secondary-compressed data.
s = hdr.read_text()
if "ZRAM_RECOMP" not in s:
    s = replace_once(
        s,
        '''\tZRAM_LRU,

\t__NR_ZRAM_PAGEFLAGS,
''',
        '''\tZRAM_LRU,
#ifdef CONFIG_ZRAM_MULTI_COMP
\tZRAM_RECOMP,\t/* object compressed with secondary compressor */
\tZRAM_INCOMPRESSIBLE,\t/* secondary compressor made no useful progress */
#endif

\t__NR_ZRAM_PAGEFLAGS,
''',
        "header page flags",
    )

if "recompress_attempts" not in s:
    s = replace_once(
        s,
        '''\tatomic64_t meta_data_size;\t/* size of zram_entries */
};
''',
        '''\tatomic64_t meta_data_size;\t/* size of zram_entries */
#ifdef CONFIG_ZRAM_MULTI_COMP
\tatomic64_t recompress_attempts;
\tatomic64_t recompress_success;
\tatomic64_t recompress_saved_bytes;
#endif
};
''',
        "header stats",
    )

if "struct zcomp *recomp;" not in s:
    s = replace_once(
        s,
        '''\tstruct zcomp *comp;
\tstruct gendisk *disk;
''',
        '''\tstruct zcomp *comp;
#ifdef CONFIG_ZRAM_MULTI_COMP
\tstruct zcomp *recomp;
#endif
\tstruct gendisk *disk;
''',
        "header secondary comp",
    )

if "char recompressor[CRYPTO_MAX_ALG_NAME];" not in s:
    s = replace_once(
        s,
        '''\tchar compressor[CRYPTO_MAX_ALG_NAME];
\t/*
''',
        '''\tchar compressor[CRYPTO_MAX_ALG_NAME];
#ifdef CONFIG_ZRAM_MULTI_COMP
\tchar recompressor[CRYPTO_MAX_ALG_NAME];
#endif
\t/*
''',
        "header secondary compressor name",
    )
hdr.write_text(s)

s = drv.read_text()

# Helpers for selecting the decompressor for each object.
if "zram_comp_for_index" not in s:
    anchor = '''static inline bool zram_allocated(struct zram *zram, u32 index)
{
\treturn zram_get_obj_size(zram, index) ||
\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||
\t\t\tzram_test_flag(zram, index, ZRAM_WB);
}

'''
    helper = '''#ifdef CONFIG_ZRAM_MULTI_COMP
static inline struct zcomp *zram_comp_for_index(struct zram *zram, u32 index)
{
\tif (zram_test_flag(zram, index, ZRAM_RECOMP))
\t\treturn zram->recomp;
\treturn zram->comp;
}

static inline const char *zram_comp_name_for_index(struct zram *zram, u32 index)
{
\tif (zram_test_flag(zram, index, ZRAM_RECOMP))
\t\treturn zram->recompressor;
\treturn zram->compressor;
}
#endif

'''
    s = replace_once(s, anchor, anchor + helper, "driver comp selector")

# Secondary compressor configuration. Exactly one secondary compressor is
# intentional for this 4.19/Samsung backport; priority=1 matches upstream ABI.
if "static ssize_t recomp_algorithm_show" not in s:
    anchor = '''static ssize_t use_dedup_show(struct device *dev,
'''
    block = '''#ifdef CONFIG_ZRAM_MULTI_COMP
static ssize_t recomp_algorithm_show(struct device *dev,
\t\tstruct device_attribute *attr, char *buf)
{
\tstruct zram *zram = dev_to_zram(dev);
\tssize_t sz = 0;

\tdown_read(&zram->init_lock);
\tsz += scnprintf(buf + sz, PAGE_SIZE - sz, "#1: ");
\tsz += zcomp_available_show(zram->recompressor, buf + sz);
\tup_read(&zram->init_lock);
\treturn sz;
}

static ssize_t recomp_algorithm_store(struct device *dev,
\t\tstruct device_attribute *attr, const char *buf, size_t len)
{
\tstruct zram *zram = dev_to_zram(dev);
\tchar alg[CRYPTO_MAX_ALG_NAME] = { 0 };
\tunsigned int prio = 1;
\tint parsed;

\tparsed = sscanf(buf, "algo=%63s priority=%u", alg, &prio);
\tif (parsed < 1)
\t\tparsed = sscanf(buf, "priority=%u algo=%63s", &prio, alg);
\tif (parsed < 1 || !alg[0] || prio != 1)
\t\treturn -EINVAL;
\tif (!zcomp_available_algorithm(alg))
\t\treturn -EINVAL;

\tdown_write(&zram->init_lock);
\tif (init_done(zram)) {
\t\tup_write(&zram->init_lock);
\t\treturn -EBUSY;
\t}
\tif (!strcmp(alg, zram->compressor)) {
\t\tup_write(&zram->init_lock);
\t\treturn -EINVAL;
\t}
\tstrlcpy(zram->recompressor, alg, sizeof(zram->recompressor));
\tup_write(&zram->init_lock);
\treturn len;
}
#endif

'''
    s = replace_once(s, anchor, block + anchor, "recomp algorithm sysfs")

# Make normal reads select the algorithm recorded on the slot.
old = '''\t} else {
\t\tstruct zcomp_strm *zstrm = zcomp_stream_get(zram->comp);

\t\tdst = kmap_atomic(page);
\t\tret = zcomp_decompress(zstrm, src, size, dst);
\t\tkunmap_atomic(dst);
\t\tzcomp_stream_put(zram->comp);
\t}

\t/* Should NEVER happen. BUG() if it does. */
\tif (unlikely(ret))
\t\thandle_decomp_fail(zram->compressor, ret, index, src, size,
\t\t\t\t   NULL);
'''
if old in s:
    new = '''\t} else {
\t\tstruct zcomp *comp = zram->comp;
\t\tconst char *comp_name = zram->compressor;
\t\tstruct zcomp_strm *zstrm;
#ifdef CONFIG_ZRAM_MULTI_COMP
\t\tcomp = zram_comp_for_index(zram, index);
\t\tcomp_name = zram_comp_name_for_index(zram, index);
#endif
\t\tzstrm = zcomp_stream_get(comp);
\t\tdst = kmap_atomic(page);
\t\tret = zcomp_decompress(zstrm, src, size, dst);
\t\tkunmap_atomic(dst);
\t\tzcomp_stream_put(comp);

\t\t/* Should NEVER happen. BUG() if it does. */
\t\tif (unlikely(ret))
\t\t\thandle_decomp_fail((char *)comp_name, ret, index, src,
\t\t\t\t\t   size, NULL);
\t}
'''
    s = s.replace(old, new, 1)
elif "zram_comp_for_index(zram, index)" not in s:
    raise SystemExit("read decompressor anchor missing")

# Clear secondary-only flags at the final common free path.
# IMPORTANT: locate the real function definition, not the forward declaration.
free_sig = "static void zram_free_page(struct zram *zram, size_t index)\n{"
free_start = s.index(free_sig)
free_end = s.index("static int __zram_bvec_read", free_start)
free_body = s[free_start:free_end]
cleanup = '''#ifdef CONFIG_ZRAM_MULTI_COMP
\tif (zram_test_flag(zram, index, ZRAM_RECOMP))
\t\tzram_clear_flag(zram, index, ZRAM_RECOMP);
\tif (zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))
\t\tzram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE);
#endif
'''
if cleanup not in free_body:
    anchor = '''#endif
\tWARN_ON_ONCE(zram->table[index].flags &
\t\t~(1UL << ZRAM_LOCK | 1UL << ZRAM_UNDER_WB));
}
'''
    replacement = '''#endif
#ifdef CONFIG_ZRAM_MULTI_COMP
\tif (zram_test_flag(zram, index, ZRAM_RECOMP))
\t\tzram_clear_flag(zram, index, ZRAM_RECOMP);
\tif (zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))
\t\tzram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE);
#endif
\tWARN_ON_ONCE(zram->table[index].flags &
\t\t~(1UL << ZRAM_LOCK | 1UL << ZRAM_UNDER_WB));
}
'''
    # Restrict the replacement to the actual zram_free_page body.
    body = s[free_start:free_end]
    if body.count(anchor) != 1:
        raise SystemExit(f"zram_free_page final-cleanup anchor count: {body.count(anchor)}")
    body = body.replace(anchor, replacement, 1)
    s = s[:free_start] + body + s[free_end:]

# Hard proof that the real function body owns both clears before WARN_ON_ONCE.
free_start = s.index(free_sig)
free_end = s.index("static int __zram_bvec_read", free_start)
free_body = s[free_start:free_end]
for needle in (
    "zram_clear_flag(zram, index, ZRAM_RECOMP);",
    "zram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE);",
    "WARN_ON_ONCE(zram->table[index].flags &",
):
    if needle not in free_body:
        raise SystemExit(f"zram_free_page lifecycle cleanup missing: {needle}")

# Recompression engine. It preserves Samsung LRU state and never calls
# zram_free_page() during an in-RAM replacement.
if "static int zram_recompress_page" not in s:
    anchor = '''/*
 * zram_bio_discard - handler on discard request
'''
    block = r'''#ifdef CONFIG_ZRAM_MULTI_COMP
#define RECOMPRESS_IDLE		(1U << 0)
#define RECOMPRESS_HUGE		(1U << 1)
#define ZRAM_RECOMP_MIN_GAIN	128U

static int zram_recompress_page(struct zram *zram, u32 index,
				struct page *page, u32 threshold)
{
	struct zram_entry *old_entry, *new_entry;
	struct zcomp_strm *zstrm;
	unsigned int old_len, new_len = 0;
	unsigned long alloced_pages;
	void *src, *dst;
	int ret;

	old_entry = zram_get_entry(zram, index);
	if (!old_entry)
		return -EINVAL;
	old_len = zram_get_obj_size(zram, index);
	if (threshold && old_len < threshold)
		return 0;

	/* Samsung dedup entries can be shared by multiple slots. */
	if (zram_dedup_enabled(zram))
		return -EOPNOTSUPP;

	if (!zram->recomp || zram_test_flag(zram, index, ZRAM_RECOMP))
		return 0;

	dst = kmap_atomic(page);
	src = zs_map_object(zram->mem_pool,
			    zram_entry_handle(zram, old_entry), ZS_MM_RO);
	if (old_len == PAGE_SIZE) {
		memcpy(dst, src, PAGE_SIZE);
		ret = 0;
	} else {
		zstrm = zcomp_stream_get(zram->comp);
		ret = zcomp_decompress(zstrm, src, old_len, dst);
		zcomp_stream_put(zram->comp);
	}
	zs_unmap_object(zram->mem_pool, zram_entry_handle(zram, old_entry));
	kunmap_atomic(dst);
	if (ret)
		return ret;

	atomic64_inc(&zram->stats.recompress_attempts);
	zstrm = zcomp_stream_get(zram->recomp);
	src = kmap_atomic(page);
	ret = zcomp_compress(zstrm, src, &new_len);
	kunmap_atomic(src);
	if (ret) {
		zcomp_stream_put(zram->recomp);
		return ret;
	}

	if (new_len >= huge_class_size)
		new_len = PAGE_SIZE;

	if (new_len >= old_len ||
	    old_len - new_len < ZRAM_RECOMP_MIN_GAIN) {
		zcomp_stream_put(zram->recomp);
		zram_set_flag(zram, index, ZRAM_INCOMPRESSIBLE);
		return 0;
	}

	new_entry = zram_entry_alloc(zram, new_len,
				     __GFP_KSWAPD_RECLAIM |
				     __GFP_NOWARN |
				     __GFP_HIGHMEM |
				     __GFP_MOVABLE |
				     __GFP_CMA);
	if (!new_entry) {
		zcomp_stream_put(zram->recomp);
		return -ENOMEM;
	}

	alloced_pages = zs_get_total_pages(zram->mem_pool);
	update_used_max(zram, alloced_pages);

	dst = zs_map_object(zram->mem_pool,
			    zram_entry_handle(zram, new_entry), ZS_MM_WO);
	memcpy(dst, zstrm->buffer, new_len);
	zs_unmap_object(zram->mem_pool, zram_entry_handle(zram, new_entry));
	zcomp_stream_put(zram->recomp);

	/* Slot lock is held: replace only this slot and preserve Samsung LRU. */
	zram_entry_free(zram, old_entry);
	atomic64_sub(old_len, &zram->stats.compr_data_size);
	atomic64_add(new_len, &zram->stats.compr_data_size);
	zram_set_entry(zram, index, new_entry);
	zram_set_obj_size(zram, index, new_len);
	zram_set_flag(zram, index, ZRAM_RECOMP);
	zram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE);
	if (zram_test_flag(zram, index, ZRAM_HUGE)) {
		zram_clear_flag(zram, index, ZRAM_HUGE);
		atomic64_dec(&zram->stats.huge_pages);
	}
	atomic64_inc(&zram->stats.recompress_success);
	atomic64_add(old_len - new_len, &zram->stats.recompress_saved_bytes);
	return 0;
}

static ssize_t recompress_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t len)
{
	struct zram *zram = dev_to_zram(dev);
	unsigned long nr_pages = zram->disksize >> PAGE_SHIFT;
	unsigned long max_pages = ULONG_MAX, scanned = 0;
	unsigned long index;
	u32 mode = 0, threshold = 0;
	char *args, *cursor, *tok;
	struct page *page;
	ssize_t ret = len;

	args = kstrndup(buf, len, GFP_KERNEL);
	if (!args)
		return -ENOMEM;
	cursor = args;

	while ((tok = strsep(&cursor, " \t\n")) != NULL) {
		unsigned long ul;
		if (!*tok)
			continue;
		if (!strncmp(tok, "type=", 5)) {
			const char *v = tok + 5;
			if (!strcmp(v, "idle"))
				mode = RECOMPRESS_IDLE;
			else if (!strcmp(v, "huge"))
				mode = RECOMPRESS_HUGE;
			else if (!strcmp(v, "huge_idle"))
				mode = RECOMPRESS_IDLE | RECOMPRESS_HUGE;
			else {
				ret = -EINVAL;
				goto out_args;
			}
		} else if (!strncmp(tok, "threshold=", 10)) {
			if (kstrtouint(tok + 10, 10, &threshold)) {
				ret = -EINVAL;
				goto out_args;
			}
		} else if (!strncmp(tok, "priority=", 9)) {
			if (kstrtoul(tok + 9, 10, &ul) || ul != 1) {
				ret = -EINVAL;
				goto out_args;
			}
		} else if (!strncmp(tok, "algo=", 5)) {
			if (strcmp(tok + 5, zram->recompressor)) {
				ret = -EINVAL;
				goto out_args;
			}
		} else if (!strncmp(tok, "max_pages=", 10)) {
			if (kstrtoul(tok + 10, 10, &max_pages)) {
				ret = -EINVAL;
				goto out_args;
			}
		} else {
			ret = -EINVAL;
			goto out_args;
		}
	}

	if (threshold >= PAGE_SIZE) {
		ret = -EINVAL;
		goto out_args;
	}

	down_read(&zram->init_lock);
	if (!init_done(zram) || !zram->recomp) {
		ret = -EINVAL;
		goto out_unlock;
	}
	if (zram_dedup_enabled(zram)) {
		ret = -EOPNOTSUPP;
		goto out_unlock;
	}

	page = alloc_page(GFP_KERNEL | __GFP_HIGHMEM);
	if (!page) {
		ret = -ENOMEM;
		goto out_unlock;
	}

	for (index = 0; index < nr_pages && scanned < max_pages; index++) {
		int err = 0;

		zram_slot_lock(zram, index);
		if (!zram_allocated(zram, index) ||
		    zram_test_flag(zram, index, ZRAM_WB) ||
		    zram_test_flag(zram, index, ZRAM_UNDER_WB) ||
		    zram_test_flag(zram, index, ZRAM_SAME) ||
		    zram_test_flag(zram, index, ZRAM_RECOMP) ||
		    zram_test_flag(zram, index, ZRAM_INCOMPRESSIBLE))
			goto next;

		if ((mode & RECOMPRESS_IDLE) &&
		    !zram_test_flag(zram, index, ZRAM_IDLE))
			goto next;
		if ((mode & RECOMPRESS_HUGE) &&
		    !zram_test_flag(zram, index, ZRAM_HUGE))
			goto next;

		scanned++;
		err = zram_recompress_page(zram, index, page, threshold);
next:
		zram_slot_unlock(zram, index);
		if (err && err != -ENOMEM) {
			ret = err;
			break;
		}
		cond_resched();
	}
	__free_page(page);

out_unlock:
	up_read(&zram->init_lock);
out_args:
	kfree(args);
	return ret;
}

static ssize_t recomp_stat_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	struct zram *zram = dev_to_zram(dev);

	return scnprintf(buf, PAGE_SIZE, "%llu %llu %llu\n",
		(u64)atomic64_read(&zram->stats.recompress_attempts),
		(u64)atomic64_read(&zram->stats.recompress_success),
		(u64)atomic64_read(&zram->stats.recompress_saved_bytes));
}
#endif

'''
    s = replace_once(s, anchor, block + anchor, "recompression engine")

# Never send a secondary-compressed object through Samsung's compressed
# writeback format: that legacy format has no compressor-id field.
if "ZRAM_RECOMP))" not in s[s.index("static int zram_try_mark_page"):s.index("static void free_writeback_buffer")]:
    s = replace_once(
        s,
        '''\tif (!zram_allocated(zram, index) ||
\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_PPR)) {
''',
        '''\tif (!zram_allocated(zram, index) ||
\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_PPR)
#ifdef CONFIG_ZRAM_MULTI_COMP
\t\t\t|| zram_test_flag(zram, index, ZRAM_RECOMP)
#endif
\t\t\t) {
''',
        "LRU writeback recompressed skip",
    )

# Compressed writeback fill path.
fill_old = '''\tif (!zram_allocated(zram, index) ||
\t\t\t!zram_test_flag(zram, index, ZRAM_IDLE) ||
\t\t\tzram_test_flag(zram, index, ZRAM_WB) ||
\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||
\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB)) {
'''
if fill_old in s:
    fill_new = '''\tif (!zram_allocated(zram, index) ||
\t\t\t!zram_test_flag(zram, index, ZRAM_IDLE) ||
\t\t\tzram_test_flag(zram, index, ZRAM_WB) ||
\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||
\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB)
#ifdef CONFIG_ZRAM_MULTI_COMP
\t\t\t|| zram_test_flag(zram, index, ZRAM_RECOMP)
#endif
\t\t\t) {
'''
    s = s.replace(fill_old, fill_new, 1)

# Manual writeback path also skips secondary objects.
manual_old = '''\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||
\t\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||
\t\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB))
\t\t\tgoto next;
'''
if manual_old in s:
    manual_new = '''\t\tif (zram_test_flag(zram, index, ZRAM_WB) ||
\t\t\t\tzram_test_flag(zram, index, ZRAM_SAME) ||
\t\t\t\tzram_test_flag(zram, index, ZRAM_UNDER_WB)
#ifdef CONFIG_ZRAM_MULTI_COMP
\t\t\t\t|| zram_test_flag(zram, index, ZRAM_RECOMP)
#endif
\t\t\t\t)
\t\t\tgoto next;
'''
    s = s.replace(manual_old, manual_new, 1)

# Device attributes.
if "static DEVICE_ATTR_RW(recomp_algorithm);" not in s:
    anchor = '''static DEVICE_ATTR_RW(comp_algorithm);
'''
    s = replace_once(
        s, anchor,
        anchor + '''#ifdef CONFIG_ZRAM_MULTI_COMP
static DEVICE_ATTR_RW(recomp_algorithm);
static DEVICE_ATTR_WO(recompress);
static DEVICE_ATTR_RO(recomp_stat);
#endif
''',
        "secondary device attrs",
    )

if "&dev_attr_recomp_algorithm.attr" not in s:
    anchor = '''\t&dev_attr_comp_algorithm.attr,
'''
    s = replace_once(
        s, anchor,
        anchor + '''#ifdef CONFIG_ZRAM_MULTI_COMP
\t&dev_attr_recomp_algorithm.attr,
\t&dev_attr_recompress.attr,
\t&dev_attr_recomp_stat.attr,
#endif
''',
        "secondary attrs array",
    )

# Create/destroy the secondary compressor along with the primary.
old = '''static void zram_reset_device(struct zram *zram)
{
\tstruct zcomp *comp;
\tu64 disksize;

\tdown_write(&zram->init_lock);

\tzram->limit_pages = 0;

\tif (!init_done(zram)) {
\t\tup_write(&zram->init_lock);
\t\treturn;
\t}

\tcomp = zram->comp;
\tdisksize = zram->disksize;
\tzram->disksize = 0;

\tset_capacity(zram->disk, 0);
\tpart_stat_set_all(&zram->disk->part0, 0);

\tup_write(&zram->init_lock);
\t/* I/O operation under all of CPU are done so let's free */
\tzram_meta_free(zram, disksize);
\tmemset(&zram->stats, 0, sizeof(zram->stats));
\tzcomp_destroy(comp);
\treset_bdev(zram);
}
'''
if old in s:
    new = '''static void zram_reset_device(struct zram *zram)
{
\tstruct zcomp *comp;
#ifdef CONFIG_ZRAM_MULTI_COMP
\tstruct zcomp *recomp;
#endif
\tu64 disksize;

\tdown_write(&zram->init_lock);

\tzram->limit_pages = 0;

\tif (!init_done(zram)) {
\t\tup_write(&zram->init_lock);
\t\treturn;
\t}

\tcomp = zram->comp;
#ifdef CONFIG_ZRAM_MULTI_COMP
\trecomp = zram->recomp;
\tzram->recomp = NULL;
#endif
\tdisksize = zram->disksize;
\tzram->disksize = 0;

\tset_capacity(zram->disk, 0);
\tpart_stat_set_all(&zram->disk->part0, 0);

\tup_write(&zram->init_lock);
\t/* I/O operation under all of CPU are done so let's free */
\tzram_meta_free(zram, disksize);
\tmemset(&zram->stats, 0, sizeof(zram->stats));
\tzcomp_destroy(comp);
#ifdef CONFIG_ZRAM_MULTI_COMP
\tif (recomp)
\t\tzcomp_destroy(recomp);
#endif
\treset_bdev(zram);
}
'''
    s = s.replace(old, new, 1)
elif "struct zcomp *recomp;" not in s[s.index("static void zram_reset_device"):s.index("static ssize_t disksize_store")]:
    raise SystemExit("reset-device anchor missing")

old = '''\tcomp = zcomp_create(zram->compressor);
\tif (IS_ERR(comp)) {
\t\tpr_err("Cannot initialise %s compressing backend\\n",
\t\t\t\tzram->compressor);
\t\terr = PTR_ERR(comp);
\t\tgoto out_free_meta;
\t}

\tif (!strncmp(zram->compressor, "lzo-rle", 7))
\t\tis_lzorle = true;

\tzram->comp = comp;
\tzram->disksize = disksize;
'''
if old in s:
    new = '''\tcomp = zcomp_create(zram->compressor);
\tif (IS_ERR(comp)) {
\t\tpr_err("Cannot initialise %s compressing backend\\n",
\t\t\t\tzram->compressor);
\t\terr = PTR_ERR(comp);
\t\tgoto out_free_meta;
\t}

#ifdef CONFIG_ZRAM_MULTI_COMP
\tif (zram->recompressor[0]) {
\t\tzram->recomp = zcomp_create(zram->recompressor);
\t\tif (IS_ERR(zram->recomp)) {
\t\t\tpr_err("Cannot initialise %s secondary backend\\n",
\t\t\t\tzram->recompressor);
\t\t\terr = PTR_ERR(zram->recomp);
\t\t\tzram->recomp = NULL;
\t\t\tzcomp_destroy(comp);
\t\t\tgoto out_free_meta;
\t\t}
\t}
#endif

\tif (!strncmp(zram->compressor, "lzo-rle", 7))
\t\tis_lzorle = true;

\tzram->comp = comp;
\tzram->disksize = disksize;
'''
    s = s.replace(old, new, 1)
elif "secondary backend" not in s:
    raise SystemExit("disksize compressor anchor missing")

# Default secondary zstd so Android's early zram setup gets both compressors
# without requiring init-script changes before disksize is written.
if 'strlcpy(zram->recompressor, "zstd"' not in s:
    anchor = '''\tstrlcpy(zram->compressor, default_compressor, sizeof(zram->compressor));

\tzram_debugfs_register(zram);
'''
    repl = '''\tstrlcpy(zram->compressor, default_compressor, sizeof(zram->compressor));
#ifdef CONFIG_ZRAM_MULTI_COMP
\tstrlcpy(zram->recompressor, "zstd", sizeof(zram->recompressor));
#endif

\tzram_debugfs_register(zram);
'''
    s = replace_once(s, anchor, repl, "default zstd secondary")

drv.write_text(s)

# Final structural validation.
checks = {
    kcfg: ["config ZRAM_MULTI_COMP"],
    defcfg: ["CONFIG_ZRAM_MULTI_COMP=y", "CONFIG_CRYPTO_ZSTD=y"],
    hdr: ["ZRAM_RECOMP", "struct zcomp *recomp;", "recompress_saved_bytes"],
    drv: [
        "recomp_algorithm_show",
        "recompress_store",
        "recomp_stat_show",
        "zram_recompress_page",
        'strlcpy(zram->recompressor, "zstd"',
        "zram_comp_for_index",
        "zram_clear_flag(zram, index, ZRAM_RECOMP);",
        "zram_clear_flag(zram, index, ZRAM_INCOMPRESSIBLE);",
    ],
}
for p, needles in checks.items():
    text = p.read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{p}: missing {needle}")

print("P161 ZRAM multi-comp backport: PASS")
print("  primary: lzo-rle")
print("  secondary: zstd (priority 1)")
print("  sysfs: recomp_algorithm / recompress / recomp_stat")
print("  dedup safety: runtime recompress returns -EOPNOTSUPP when enabled")
print("  Samsung compressed writeback: secondary objects excluded (format has no compressor id)")
