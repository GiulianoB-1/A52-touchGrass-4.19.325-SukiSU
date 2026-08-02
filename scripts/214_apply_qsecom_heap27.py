#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

ION_REL = Path('drivers/staging/android/ion/ion.c')
HEAP_REL = Path('drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c')

OLD_ION_BLOCK = '''\tcase ION_HEAP_TYPE_DMA:\n\t\t/* A52_QSEECOM_TA_DMA_ID19_COMPAT\n\t\t * TouchGrass exposes its DMA-backed qsecom_ta pool at heap ID 19.\n\t\t * ACK normally limits DMA heap IDs to 1..7; allow only this exact\n\t\t * fixed mask so the generic allocator preserves the vendor ABI.\n\t\t */\n\t\tif (heap->id == BIT(19)) {\n\t\t\tstart_bit = 19;\n\t\t\tend_bit = 19;\n\t\t\tbreak;\n\t\t}\n'''

NEW_ION_BLOCK = '''\tcase ION_HEAP_TYPE_DMA:\n\t\t/* A52_QSEECOM_DMA_ID19_ID27_COMPAT\n\t\t * The preserved Samsung DT exposes DMA-backed qsecom_ta and\n\t\t * qsecom pools at fixed vendor ABI heap IDs 19 and 27. ACK normally\n\t\t * limits DMA heap IDs to 1..7, so allow only these two proven IDs.\n\t\t */\n\t\tif (heap->id == BIT(19) || heap->id == BIT(27)) {\n\t\t\tstart_bit = __ffs(heap->id);\n\t\t\tend_bit = start_bit;\n\t\t\tbreak;\n\t\t}\n'''

NEW_HEAP_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Galaxy A52 QSEECOM ION DMA-heap compatibility.
 *
 * The preserved Samsung device tree exposes two reusable shared-dma-pools:
 *   - heap ID 19, qsecom_ta, backed by qseecom_ta_region (16 MiB)
 *   - heap ID 27, qsecom,    backed by qseecom_region    (20 MiB)
 *
 * ACK has the DT nodes but no qcom,msm-ion parser. Register only these two
 * proven vendor ABI heaps with the generic ACK ION core.
 */

#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/atomic.h>
#include <linux/cma.h>
#include <linux/dma-buf.h>
#include <linux/errno.h>
#include <linux/highmem.h>
#include <linux/init.h>
#include <linux/ion.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/of.h>
#include <linux/of_reserved_mem.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>
#include <linux/string.h>

#define A52_QSEECOM_TA_HEAP_ID 19U
#define A52_QSEECOM_HEAP_ID 27U
#define A52_QSEECOM_TA_HEAP_NAME "qsecom_ta"
#define A52_QSEECOM_HEAP_NAME "qsecom"

struct a52_qseecom_heap {
	struct ion_heap heap;
	struct cma *cma;
};

static struct a52_qseecom_heap a52_ta_heap;
static struct a52_qseecom_heap a52_qsecom_heap;

#define A52_R213_HEAP_FLAGS_LIMIT 32U
static atomic_t a52_r213_heap_flags_sequence = ATOMIC_INIT(0);

static int a52_qseecom_allocate(struct ion_heap *heap,
				struct ion_buffer *buffer,
				unsigned long len,
				unsigned long flags)
{
	struct a52_qseecom_heap *qheap =
		container_of(heap, struct a52_qseecom_heap, heap);
	struct sg_table *table;
	struct page *pages;
	unsigned long size = PAGE_ALIGN(len);
	unsigned long nr_pages = size >> PAGE_SHIFT;
	unsigned long align = get_order(size);
	int ret;

	if (align > CONFIG_CMA_ALIGNMENT)
		align = CONFIG_CMA_ALIGNMENT;

	pages = cma_alloc(qheap->cma, nr_pages, align, false);
	if (!pages)
		return -ENOMEM;

	if (PageHighMem(pages)) {
		unsigned long left = nr_pages;
		struct page *page = pages;

		while (left--) {
			void *vaddr = kmap_atomic(page);

			memset(vaddr, 0, PAGE_SIZE);
			kunmap_atomic(vaddr);
			page++;
		}
	} else {
		memset(page_address(pages), 0, size);
	}

	table = kmalloc(sizeof(*table), GFP_KERNEL);
	if (!table)
		goto release;

	ret = sg_alloc_table(table, 1, GFP_KERNEL);
	if (ret)
		goto free_table;

	sg_set_page(table->sgl, pages, size, 0);
	buffer->priv_virt = pages;
	buffer->sg_table = table;
	ion_buffer_prep_noncached(buffer);
	return 0;

free_table:
	kfree(table);
release:
	cma_release(qheap->cma, pages, nr_pages);
	return -ENOMEM;
}

static void a52_qseecom_free(struct ion_buffer *buffer)
{
	struct a52_qseecom_heap *qheap =
		container_of(buffer->heap, struct a52_qseecom_heap, heap);
	struct page *pages = buffer->priv_virt;
	unsigned long nr_pages = PAGE_ALIGN(buffer->size) >> PAGE_SHIFT;

	cma_release(qheap->cma, pages, nr_pages);
	sg_free_table(buffer->sg_table);
	kfree(buffer->sg_table);
}

static int a52_qseecom_get_flags(struct dma_buf *dmabuf,
				 unsigned long *flags)
{
	struct ion_buffer *buffer;
	unsigned long value = 0;
	int ret = 0;

	if (!dmabuf || !flags) {
		ret = -EINVAL;
		goto out;
	}

	buffer = dmabuf->priv;
	if (!buffer) {
		ret = -EINVAL;
		goto out;
	}

	value = buffer->flags;
	*flags = value;

out:
	{
		unsigned int trace_id = (unsigned int)atomic_inc_return(
			&a52_r213_heap_flags_sequence);

		if (trace_id <= A52_R213_HEAP_FLAGS_LIMIT)
			a52_ackfr_record(
				"IONPOST 213 F n=%u rc=%d fl=%lx",
				trace_id, ret, value);
	}
	return ret;
}

static struct ion_heap_ops a52_qseecom_ops = {
	.allocate = a52_qseecom_allocate,
	.free = a52_qseecom_free,
};

static struct device_node *a52_find_ion_heap_node(u32 wanted_id)
{
	struct device_node *ion_np;
	struct device_node *child;
	u32 id;

	ion_np = of_find_compatible_node(NULL, NULL, "qcom,msm-ion");
	if (!ion_np)
		return NULL;

	for_each_available_child_of_node(ion_np, child) {
		if (!of_property_read_u32(child, "reg", &id) &&
		    id == wanted_id) {
			of_node_put(ion_np);
			return child;
		}
	}

	of_node_put(ion_np);
	return NULL;
}

static int __init a52_register_qseecom_heap(struct a52_qseecom_heap *qheap,
					     u32 id, const char *name)
{
	struct device_node *heap_np;
	struct device_node *rmem_np;
	struct reserved_mem *rmem;
	const char *heap_type;
	int ret;

	heap_np = a52_find_ion_heap_node(id);
	if (!heap_np) {
		a52_ackfr_record("BOOT ion_dma_heap fail id=%u stage=heap_node rc=%d",
				  id, -ENODEV);
		return -ENODEV;
	}

	ret = of_property_read_string(heap_np, "qcom,ion-heap-type", &heap_type);
	if (ret || strcmp(heap_type, "DMA")) {
		a52_ackfr_record("BOOT ion_dma_heap fail id=%u stage=heap_type rc=%d",
				  id, ret ? ret : -EINVAL);
		ret = ret ? ret : -EINVAL;
		goto put_heap;
	}

	rmem_np = of_parse_phandle(heap_np, "memory-region", 0);
	if (!rmem_np) {
		ret = -ENODEV;
		a52_ackfr_record(
			"BOOT ion_dma_heap fail id=%u stage=memory_region rc=%d",
			id, ret);
		goto put_heap;
	}

	if (!of_device_is_compatible(rmem_np, "shared-dma-pool") ||
	    !of_property_read_bool(rmem_np, "reusable") ||
	    of_property_read_bool(rmem_np, "no-map")) {
		ret = -EINVAL;
		a52_ackfr_record(
			"BOOT ion_dma_heap fail id=%u stage=pool_contract rc=%d",
			id, ret);
		goto put_rmem;
	}

	rmem = of_reserved_mem_lookup(rmem_np);
	if (!rmem || !rmem->priv) {
		ret = -EPROBE_DEFER;
		a52_ackfr_record(
			"BOOT ion_dma_heap fail id=%u stage=cma_lookup rc=%d",
			id, ret);
		goto put_rmem;
	}

	memset(qheap, 0, sizeof(*qheap));
	qheap->cma = rmem->priv;
	qheap->heap.ops = &a52_qseecom_ops;
	qheap->heap.type = ION_HEAP_TYPE_DMA;
	qheap->heap.id = BIT(id);
	qheap->heap.name = name;
	qheap->heap.buf_ops.get_flags = a52_qseecom_get_flags;

	ret = ion_device_add_heap(&qheap->heap);
	if (ret) {
		a52_ackfr_record("BOOT ion_dma_heap fail id=%u stage=register rc=%d",
				  id, ret);
		goto put_rmem;
	}

	if (id == A52_QSEECOM_HEAP_ID)
		a52_ackfr_record(
			"BOOT qsecom_heap registered id=%u base=0x%llx size=%llu",
			id, (unsigned long long)cma_get_base(qheap->cma),
			(unsigned long long)cma_get_size(qheap->cma));
	else
		a52_ackfr_record(
			"BOOT qseecom_ta_heap registered id=%u base=0x%llx size=%llu",
			id, (unsigned long long)cma_get_base(qheap->cma),
			(unsigned long long)cma_get_size(qheap->cma));

	pr_info("A52 ION heap %s registered id=%u at %pa size=%zu\n",
		name, id, &rmem->base, rmem->size);

put_rmem:
	of_node_put(rmem_np);
put_heap:
	of_node_put(heap_np);
	return ret;
}

static int __init a52_qseecom_heaps_init(void)
{
	int ret;

	ret = a52_register_qseecom_heap(&a52_ta_heap,
					 A52_QSEECOM_TA_HEAP_ID,
					 A52_QSEECOM_TA_HEAP_NAME);
	if (ret)
		return ret;

	ret = a52_register_qseecom_heap(&a52_qsecom_heap,
					 A52_QSEECOM_HEAP_ID,
					 A52_QSEECOM_HEAP_NAME);
	return ret;
}

subsys_initcall_sync(a52_qseecom_heaps_init);
'''


def patch_ion(text: str) -> str:
    if 'A52_QSEECOM_DMA_ID19_ID27_COMPAT' in text:
        return text
    if text.count(OLD_ION_BLOCK) != 1:
        raise RuntimeError('expected exactly one Phase 213 DMA-ID19 compatibility block')
    return text.replace(OLD_ION_BLOCK, NEW_ION_BLOCK)


def patch_heap(text: str) -> str:
    if 'A52_QSEECOM_HEAP_ID 27U' in text and 'a52_qseecom_heaps_init' in text:
        return text
    for marker in (
        'A52_QSEECOM_TA_HEAP_ID 19U',
        'A52_R213_HEAP19_FLAGS_LIMIT 32U',
        'subsys_initcall_sync(a52_qseecom_ta_heap_init);',
    ):
        if marker not in text:
            raise RuntimeError(f'missing Phase 213 heap-19 source marker: {marker}')
    return NEW_HEAP_SOURCE


def apply(root: Path) -> tuple[str, str]:
    ion_path = root / ION_REL
    heap_path = root / HEAP_REL
    new_ion = patch_ion(ion_path.read_text(encoding='utf-8'))
    new_heap = patch_heap(heap_path.read_text(encoding='utf-8'))
    ion_path.write_text(new_ion, encoding='utf-8')
    heap_path.write_text(new_heap, encoding='utf-8')
    return new_ion, new_heap


def self_test() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ION_REL.parent).mkdir(parents=True)
        (root / HEAP_REL.parent).mkdir(parents=True)
        (root / ION_REL).write_text('head\n' + OLD_ION_BLOCK + 'tail\n', encoding='utf-8')
        (root / HEAP_REL).write_text(
            '#define A52_QSEECOM_TA_HEAP_ID 19U\n'
            '#define A52_R213_HEAP19_FLAGS_LIMIT 32U\n'
            'subsys_initcall_sync(a52_qseecom_ta_heap_init);\n',
            encoding='utf-8')
        ion, heap = apply(root)
        assert 'heap->id == BIT(19) || heap->id == BIT(27)' in ion
        assert 'A52_QSEECOM_HEAP_ID 27U' in heap
        assert 'BOOT qsecom_heap registered' in heap
        ion2, heap2 = apply(root)
        assert ion2 == ion and heap2 == heap
    print('phase214 qsecom heap-27 patcher self-test: PASS')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.root:
        ap.error('--root is required unless --self-test is used')
    ion, heap = apply(args.root.resolve())
    for marker in (
        'A52_QSEECOM_DMA_ID19_ID27_COMPAT',
        'heap->id == BIT(19) || heap->id == BIT(27)',
        'A52_QSEECOM_HEAP_ID 27U',
        'BOOT qsecom_heap registered',
        'subsys_initcall_sync(a52_qseecom_heaps_init);',
    ):
        if marker not in ion + '\n' + heap:
            raise RuntimeError(f'Phase 214 marker missing after apply: {marker}')
    print('Phase 214 qsecom heap ID 27 compatibility applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
