#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 86_apply_zcomp_embedded_percpu_prereq.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
zc = root / "drivers/block/zram/zcomp.c"
zh = root / "drivers/block/zram/zcomp.h"

for p in (zc, zh):
    if not p.is_file():
        raise SystemExit(f"missing {p}")

c = zc.read_text()
h = zh.read_text()

old_free_alloc = r'''static void zcomp_strm_free(struct zcomp_strm *zstrm)
{
	if (!IS_ERR_OR_NULL(zstrm->tfm))
		crypto_free_comp(zstrm->tfm);
	free_pages((unsigned long)zstrm->buffer, 1);
	if (zstrm->tmpbuf) {
		free_pages((unsigned long)zstrm->tmpbuf, 1);
		zstrm->tmpbuf = NULL;
	}
	kfree(zstrm);
}

/*
 * allocate new zcomp_strm structure with ->tfm initialized by
 * backend, return NULL on error
 */
static struct zcomp_strm *zcomp_strm_alloc(struct zcomp *comp)
{
	struct zcomp_strm *zstrm = kmalloc(sizeof(*zstrm), GFP_KERNEL);
	if (!zstrm)
		return NULL;

	zstrm->tfm = crypto_alloc_comp(comp->name, 0, 0);
	/*
	 * allocate 2 pages. 1 for compressed data, plus 1 extra for the
	 * case when compressed size is larger than the original one
	 */
	zstrm->buffer = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, 1);
	zstrm->tmpbuf = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, 1);
	if (IS_ERR_OR_NULL(zstrm->tfm) || !zstrm->buffer || !zstrm->tmpbuf) {
		zcomp_strm_free(zstrm);
		zstrm = NULL;
	}
	return zstrm;
}
'''

new_free_alloc = r'''static void zcomp_strm_free(struct zcomp_strm *zstrm)
{
	if (!IS_ERR_OR_NULL(zstrm->tfm))
		crypto_free_comp(zstrm->tfm);
	zstrm->tfm = NULL;

	if (zstrm->buffer) {
		free_pages((unsigned long)zstrm->buffer, 1);
		zstrm->buffer = NULL;
	}

	if (zstrm->tmpbuf) {
		free_pages((unsigned long)zstrm->tmpbuf, 1);
		zstrm->tmpbuf = NULL;
	}
}

/*
 * Phase86 prerequisite for the modern zcomp stream model:
 * keep the stream object embedded in percpu storage and initialize only
 * its backend resources.  This changes no locking/preemption semantics.
 */
static int zcomp_strm_init(struct zcomp *comp, struct zcomp_strm *zstrm)
{
	memset(zstrm, 0, sizeof(*zstrm));

	zstrm->tfm = crypto_alloc_comp(comp->name, 0, 0);
	/*
	 * allocate 2 pages. 1 for compressed data, plus 1 extra for the
	 * case when compressed size is larger than the original one.
	 * Keep Samsung's tmpbuf allocation as-is.
	 */
	zstrm->buffer = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, 1);
	zstrm->tmpbuf = (void *)__get_free_pages(GFP_KERNEL | __GFP_ZERO, 1);
	if (IS_ERR_OR_NULL(zstrm->tfm) || !zstrm->buffer || !zstrm->tmpbuf) {
		zcomp_strm_free(zstrm);
		return -ENOMEM;
	}

	return 0;
}
'''
if old_free_alloc not in c:
    raise SystemExit("zcomp stream alloc/free block changed")
c = c.replace(old_free_alloc, new_free_alloc, 1)

old_get = r'''struct zcomp_strm *zcomp_stream_get(struct zcomp *comp)
{
	return *get_cpu_ptr(comp->stream);
}
'''
new_get = r'''struct zcomp_strm *zcomp_stream_get(struct zcomp *comp)
{
	return get_cpu_ptr(comp->stream);
}
'''
if old_get not in c:
    raise SystemExit("zcomp_stream_get block changed")
c = c.replace(old_get, new_get, 1)

old_lifecycle = r'''int zcomp_cpu_up_prepare(unsigned int cpu, struct hlist_node *node)
{
	struct zcomp *comp = hlist_entry(node, struct zcomp, node);
	struct zcomp_strm *zstrm;

	if (WARN_ON(*per_cpu_ptr(comp->stream, cpu)))
		return 0;

	zstrm = zcomp_strm_alloc(comp);
	if (IS_ERR_OR_NULL(zstrm)) {
		pr_err("Can't allocate a compression stream
");
		return -ENOMEM;
	}
	*per_cpu_ptr(comp->stream, cpu) = zstrm;
	return 0;
}

int zcomp_cpu_dead(unsigned int cpu, struct hlist_node *node)
{
	struct zcomp *comp = hlist_entry(node, struct zcomp, node);
	struct zcomp_strm *zstrm;

	zstrm = *per_cpu_ptr(comp->stream, cpu);
	if (!IS_ERR_OR_NULL(zstrm))
		zcomp_strm_free(zstrm);
	*per_cpu_ptr(comp->stream, cpu) = NULL;
	return 0;
}

static int zcomp_init(struct zcomp *comp)
{
	int ret;

	comp->stream = alloc_percpu(struct zcomp_strm *);
	if (!comp->stream)
		return -ENOMEM;

	ret = cpuhp_state_add_instance(CPUHP_ZCOMP_PREPARE, &comp->node);
	if (ret < 0)
		goto cleanup;
	return 0;

cleanup:
	free_percpu(comp->stream);
	return ret;
}
'''
new_lifecycle = r'''int zcomp_cpu_up_prepare(unsigned int cpu, struct hlist_node *node)
{
	struct zcomp *comp = hlist_entry(node, struct zcomp, node);
	struct zcomp_strm *zstrm = per_cpu_ptr(comp->stream, cpu);
	int ret;

	/*
	 * Embedded percpu storage is persistent; CPU hotplug only owns the
	 * compression backend resources stored inside it.
	 */
	if (WARN_ON(zstrm->buffer || zstrm->tmpbuf ||
		    !IS_ERR_OR_NULL(zstrm->tfm)))
		return 0;

	ret = zcomp_strm_init(comp, zstrm);
	if (ret)
		pr_err("Can't allocate a compression stream
");
	return ret;
}

int zcomp_cpu_dead(unsigned int cpu, struct hlist_node *node)
{
	struct zcomp *comp = hlist_entry(node, struct zcomp, node);
	struct zcomp_strm *zstrm = per_cpu_ptr(comp->stream, cpu);

	zcomp_strm_free(zstrm);
	return 0;
}

static int zcomp_init(struct zcomp *comp)
{
	int ret;

	comp->stream = alloc_percpu(struct zcomp_strm);
	if (!comp->stream)
		return -ENOMEM;

	ret = cpuhp_state_add_instance(CPUHP_ZCOMP_PREPARE, &comp->node);
	if (ret < 0)
		goto cleanup;
	return 0;

cleanup:
	free_percpu(comp->stream);
	return ret;
}
'''
if old_lifecycle not in c:
    raise SystemExit("zcomp CPU lifecycle block changed")
c = c.replace(old_lifecycle, new_lifecycle, 1)

old_decl = "	struct zcomp_strm * __percpu *stream;
"
new_decl = "	struct zcomp_strm __percpu *stream;
"
if old_decl not in h:
    raise SystemExit("zcomp stream declaration changed")
h = h.replace(old_decl, new_decl, 1)

zc.write_text(c)
zh.write_text(h)

cc = zc.read_text()
hh = zh.read_text()

checks = [
    ("embedded percpu declaration missing",
     "struct zcomp_strm __percpu *stream;" in hh),
    ("old pointer-percpu declaration remains",
     "struct zcomp_strm * __percpu *stream;" not in hh),
    ("embedded percpu allocation missing",
     "alloc_percpu(struct zcomp_strm);" in cc),
    ("old pointer-percpu allocation remains",
     "alloc_percpu(struct zcomp_strm *);" not in cc),
    ("dynamic stream kmalloc remains",
     "kmalloc(sizeof(*zstrm), GFP_KERNEL)" not in cc),
    ("stream object kfree remains",
     "kfree(zstrm);" not in cc),
    ("CPU pinning get semantics changed",
     "return get_cpu_ptr(comp->stream);" in cc),
    ("CPU pinning put missing",
     "put_cpu_ptr(comp->stream);" in cc),
    ("mutex stream conversion leaked in",
     "mutex_lock(&zstrm->lock)" not in cc),
    ("raw_cpu_ptr stream conversion leaked in",
     "raw_cpu_ptr(comp->stream)" not in cc),
]
for label, ok in checks:
    if not ok:
        raise SystemExit(label)

report = root.parent.parent / "artifacts" / "phase86-zcomp-embedded-percpu.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=86-zcomp-embedded-percpu-prereq\n"
    "base=phase84\n"
    "zcomp_storage=embedded-percpu-struct\n"
    "zcomp_cpu_hotplug=resource-init-free-only\n"
    "zcomp_get=get_cpu_ptr-still-pins-cpu\n"
    "zcomp_put=put_cpu_ptr-still-unpins-cpu\n"
    "zcomp_mutex=n\n"
    "zcomp_raw_cpu_ptr=n\n"
    "zram_sleepable_slot_lock=retained\n"
    "zsmalloc_phase83_fixes=retained\n"
)
print(report.read_text(), end="")
