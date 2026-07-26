#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

C_REL = Path("drivers/a52_secure/a52_qseecom_ion_heap.c")
MAKE_REL = Path("drivers/a52_secure/Makefile")
ION_HDR_REL = Path("include/linux/ion.h")
REPORT = "phase24-qseecom-ion-heaps-report.json"
MARKER = "A52_QSECOM_ION_CMA_HEAPS_19_27"
MAKE_LINE = "obj-y += a52_qseecom_ion_heap.o"

C_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52 ACK compatibility heaps for Samsung's legacy QSEECOM ION ABI.
 *
 * The preserved DT exposes two reusable shared-dma-pools:
 *   heap 19: QSEECOM trusted-app memory
 *   heap 27: QSEECOM listener/command memory
 * ACK generic ION reserves IDs 16..31 for custom heaps, so both heaps retain
 * their Samsung userspace IDs while using contiguous CMA allocation semantics.
 */
#define pr_fmt(fmt) "A52IONQSEE: " fmt

#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/bitops.h>
#include <linux/cma.h>
#include <linux/dma-contiguous.h>
#include <linux/highmem.h>
#include <linux/init.h>
#include <linux/ion.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/msm_ion.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/of_reserved_mem.h>
#include <linux/platform_device.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>

#define A52_QSECOM_ION_CMA_HEAPS_19_27
#define A52_QSEE_HEAP_TA 19U
#define A52_QSEE_HEAP_MAIN 27U
#define A52_QSEE_HEAP_COUNT 2U

struct a52_qseecom_ion_heap {
	struct ion_heap heap;
	struct platform_device *pdev;
	struct cma *cma;
	u32 legacy_id;
};

static struct a52_qseecom_ion_heap a52_qsee_heaps[A52_QSEE_HEAP_COUNT];

static int a52_qsee_heap_allocate(struct ion_heap *heap,
				  struct ion_buffer *buffer,
				  unsigned long len,
				  unsigned long flags)
{
	struct a52_qseecom_ion_heap *state =
		container_of(heap, struct a52_qseecom_ion_heap, heap);
	unsigned long size = PAGE_ALIGN(len);
	unsigned long nr_pages = size >> PAGE_SHIFT;
	unsigned int align = min_t(unsigned int, get_order(size),
					   CONFIG_CMA_ALIGNMENT);
	struct sg_table *table;
	struct page *pages;
	unsigned long i;
	int ret;

	/* Both downstream QSEECOM DMA heaps are non-secure HLOS memory. */
	if (flags & (ION_FLAGS_CP_MASK | ION_FLAG_SECURE)) {
		a52_ackfr_record(
			"ION qsee_heap=%u reject len=%lu flags=%lx reason=secure",
			state->legacy_id, len, flags);
		return -EINVAL;
	}

	pages = cma_alloc(state->cma, nr_pages, align, false);
	if (!pages) {
		a52_ackfr_record(
			"ION qsee_heap=%u alloc len=%lu flags=%lx ret=%d",
			state->legacy_id, len, flags, -ENOMEM);
		return -ENOMEM;
	}

	for (i = 0; i < nr_pages; i++)
		clear_highpage(pages + i);

	table = kzalloc(sizeof(*table), GFP_KERNEL);
	if (!table) {
		ret = -ENOMEM;
		goto release_cma;
	}

	ret = sg_alloc_table(table, 1, GFP_KERNEL);
	if (ret)
		goto free_table;

	sg_set_page(table->sgl, pages, size, 0);
	buffer->sg_table = table;

	a52_ackfr_record(
		"ION qsee_heap=%u alloc len=%lu size=%lu flags=%lx ret=0",
		state->legacy_id, len, size, flags);
	return 0;

free_table:
	kfree(table);
release_cma:
	cma_release(state->cma, pages, nr_pages);
	a52_ackfr_record(
		"ION qsee_heap=%u alloc len=%lu flags=%lx ret=%d",
		state->legacy_id, len, flags, ret);
	return ret;
}

static void a52_qsee_heap_free(struct ion_buffer *buffer)
{
	struct a52_qseecom_ion_heap *state =
		container_of(buffer->heap, struct a52_qseecom_ion_heap, heap);
	struct page *pages = sg_page(buffer->sg_table->sgl);
	unsigned long nr_pages = PAGE_ALIGN(buffer->size) >> PAGE_SHIFT;

	cma_release(state->cma, pages, nr_pages);
	sg_free_table(buffer->sg_table);
	kfree(buffer->sg_table);
}

static struct ion_heap_ops a52_qsee_heap_ops = {
	.allocate = a52_qsee_heap_allocate,
	.free = a52_qsee_heap_free,
};

static struct device_node *a52_find_legacy_heap_node(u32 wanted_id)
{
	struct device_node *ion_node;
	struct device_node *child;
	u32 heap_id;

	ion_node = of_find_compatible_node(NULL, NULL, "qcom,msm-ion");
	if (!ion_node)
		return NULL;

	for_each_available_child_of_node(ion_node, child) {
		if (!of_property_read_u32(child, "reg", &heap_id) &&
		    heap_id == wanted_id) {
			of_node_put(ion_node);
			return child;
		}
	}

	of_node_put(ion_node);
	return NULL;
}

static int a52_register_qsee_heap(struct a52_qseecom_ion_heap *state,
				  u32 legacy_id, const char *name)
{
	struct device_node *node;
	struct platform_device *pdev;
	struct cma *cma;
	int ret;

	node = a52_find_legacy_heap_node(legacy_id);
	if (!node)
		return -ENODEV;

	pdev = of_find_device_by_node(node);
	if (!pdev)
		pdev = of_platform_device_create(node, NULL, NULL);
	if (!pdev) {
		of_node_put(node);
		return -ENODEV;
	}

	cma = dev_get_cma_area(&pdev->dev);
	if (!cma) {
		ret = of_reserved_mem_device_init(&pdev->dev);
		if (ret) {
			of_node_put(node);
			return ret;
		}
		cma = dev_get_cma_area(&pdev->dev);
	}
	of_node_put(node);

	if (!cma)
		return -ENODEV;

	state->pdev = pdev;
	state->cma = cma;
	state->legacy_id = legacy_id;
	state->heap.ops = &a52_qsee_heap_ops;
	state->heap.type = (enum ion_heap_type)ION_HEAP_TYPE_CUSTOM;
	state->heap.id = BIT(legacy_id);
	state->heap.name = name;

	ret = ion_device_add_heap(&state->heap);
	a52_ackfr_record(
		"ION qsee_heap=%u register ret=%d assigned=%u pages=%lu",
		legacy_id, ret, state->heap.id, cma_get_size(cma) >> PAGE_SHIFT);
	return ret;
}

static int __init a52_qsee_heaps_init(void)
{
	int ret19;
	int ret27;

	ret19 = a52_register_qsee_heap(&a52_qsee_heaps[0],
				       A52_QSEE_HEAP_TA, "qsecom_ta");
	ret27 = a52_register_qsee_heap(&a52_qsee_heaps[1],
				       A52_QSEE_HEAP_MAIN, "qsecom");

	a52_ackfr_record("ION qsee_heaps init ret19=%d ret27=%d", ret19, ret27);
	if (ret19)
		return ret19;
	return ret27;
}
fs_initcall_sync(a52_qsee_heaps_init);
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def stage(root: Path) -> dict[str, object]:
    c_path = root / C_REL
    make_path = root / MAKE_REL
    ion_header = root / ION_HDR_REL
    if not make_path.is_file() or not ion_header.is_file():
        raise SystemExit("missing staged A52 secure Makefile or ACK ION header")

    header = read(ion_header)
    required_api = (
        "struct ion_heap_ops",
        "struct ion_heap",
        "ION_HEAP_TYPE_CUSTOM",
        "ion_device_add_heap",
        "struct dma_buf_ops buf_ops",
    )
    missing_api = [token for token in required_api if token not in header]
    if missing_api:
        raise SystemExit("ACK ION heap API mismatch: " + ", ".join(missing_api))

    old = read(c_path) if c_path.is_file() else ""
    c_state = "already-present" if MARKER in old else "written"
    c_path.write_text(C_SOURCE, encoding="utf-8")

    make_text = read(make_path)
    if MAKE_LINE not in make_text:
        if not make_text.endswith("\n"):
            make_text += "\n"
        make_text += f"# {MARKER}\n{MAKE_LINE}\n"
        make_state = "inserted"
    else:
        make_state = "already-present"
    if make_text.count(MAKE_LINE) != 1:
        raise SystemExit("QSEECOM ION heap Makefile entry count is not one")
    make_path.write_text(make_text, encoding="utf-8")

    staged = read(c_path)
    required_source = (
        MARKER,
        "A52_QSEE_HEAP_TA 19U",
        "A52_QSEE_HEAP_MAIN 27U",
        "of_reserved_mem_device_init(&pdev->dev)",
        "dev_get_cma_area(&pdev->dev)",
        "cma_alloc(state->cma",
        "sg_alloc_table(table, 1",
        "ION_HEAP_TYPE_CUSTOM",
        "state->heap.id = BIT(legacy_id)",
        "ion_device_add_heap(&state->heap)",
        "fs_initcall_sync(a52_qsee_heaps_init)",
    )
    missing_source = [token for token in required_source if token not in staged]
    if missing_source:
        raise SystemExit("QSEECOM ION heaps source audit failed: " + ", ".join(missing_source))

    return {
        "source": str(C_REL),
        "makefile": str(MAKE_REL),
        "source_state": c_state,
        "makefile_state": make_state,
        "userspace_heap_ids": [19, 27],
        "registration_type": "ION_HEAP_TYPE_CUSTOM",
        "backing": "each DT memory-region shared-dma-pool via CMA",
        "sg_entries_per_allocation": 1,
        "secure_requests_rejected": True,
        "payload_capture": False,
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / MAKE_REL).parent.mkdir(parents=True, exist_ok=True)
        (root / MAKE_REL).write_text("obj-y += existing.o\n", encoding="utf-8")
        (root / ION_HDR_REL).parent.mkdir(parents=True, exist_ok=True)
        (root / ION_HDR_REL).write_text(
            "struct ion_heap_ops; struct ion_heap { struct dma_buf_ops buf_ops; }; "
            "ION_HEAP_TYPE_CUSTOM ion_device_add_heap\n",
            encoding="utf-8",
        )
        first = stage(root)
        second = stage(root)
        if first["makefile_state"] != "inserted":
            raise SystemExit("QSEECOM heaps self-test did not insert Makefile entry")
        if second["makefile_state"] != "already-present":
            raise SystemExit("QSEECOM heaps stage is not idempotent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = stage(root)
    report = {
        "status": "qseecom-ion-heaps19-27-cma-staged",
        "hardware_validated": False,
        "observed_run32": {
            "allocation_mask": "0x00080000",
            "heap_id": 19,
            "flags": 1,
            "return": -19,
            "compat_bit25_retry_entered": False,
        },
        "proactive_heap27": {
            "reason": "same downstream DMA-heap contract and dedicated DT pool",
            "hardware_observed_yet": False,
        },
        "fix": result,
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
