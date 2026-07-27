#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

REPORT_NAME = "phase29-a52-qseecom-ta-heap19-report.json"
MARKER = "A52_QSEECOM_TA_DMA_ID19_COMPAT"
CAPTURE_SHA256 = "e3daa915eddb43433735127c987f2c3febb9549b1fb8b42b7d67d1315acf84ad"
TOUCHGRASS_REPO = "micr0softstore/samsung_android_kernel_a52xq"
TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
ION_CORE = Path("drivers/staging/android/ion/ion.c")
HEAPS_MAKEFILE = Path("drivers/staging/android/ion/heaps/Makefile")
HEAP_SOURCE = Path("drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c")
ION_DTS = Path("arch/arm64/boot/dts/qcom/lagoon-ion.dtsi")
HEAP_ID = 19

HEAP_C = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * Galaxy A52 QSEECOM TA ION heap compatibility.
 *
 * TouchGrass registers qcom,ion-heap@19 as a DMA heap named qsecom_ta,
 * backed by the qseecom_ta_region shared-dma-pool. ACK has the same DT
 * nodes but no qcom,msm-ion parser, so Samsung userspace receives -ENODEV
 * for heap mask BIT(19). This file reproduces only that proven heap.
 */

#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/cma.h>
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
#define A52_QSEECOM_TA_HEAP_MASK BIT(A52_QSEECOM_TA_HEAP_ID)
#define A52_QSEECOM_TA_HEAP_NAME "qsecom_ta"

struct a52_qseecom_ta_heap {
	struct ion_heap heap;
	struct cma *cma;
};

static struct a52_qseecom_ta_heap a52_ta_heap;

static int a52_qseecom_ta_allocate(struct ion_heap *heap,
				   struct ion_buffer *buffer,
				   unsigned long len,
				   unsigned long flags)
{
	struct a52_qseecom_ta_heap *ta =
		container_of(heap, struct a52_qseecom_ta_heap, heap);
	struct sg_table *table;
	struct page *pages;
	unsigned long size = PAGE_ALIGN(len);
	unsigned long nr_pages = size >> PAGE_SHIFT;
	unsigned long align = get_order(size);
	int ret;

	if (align > CONFIG_CMA_ALIGNMENT)
		align = CONFIG_CMA_ALIGNMENT;

	pages = cma_alloc(ta->cma, nr_pages, align, false);
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
	cma_release(ta->cma, pages, nr_pages);
	return -ENOMEM;
}

static void a52_qseecom_ta_free(struct ion_buffer *buffer)
{
	struct a52_qseecom_ta_heap *ta =
		container_of(buffer->heap, struct a52_qseecom_ta_heap, heap);
	struct page *pages = buffer->priv_virt;
	unsigned long nr_pages = PAGE_ALIGN(buffer->size) >> PAGE_SHIFT;

	cma_release(ta->cma, pages, nr_pages);
	sg_free_table(buffer->sg_table);
	kfree(buffer->sg_table);
}

static struct ion_heap_ops a52_qseecom_ta_ops = {
	.allocate = a52_qseecom_ta_allocate,
	.free = a52_qseecom_ta_free,
};

static struct device_node *a52_find_qseecom_ta_heap_node(void)
{
	struct device_node *ion_np;
	struct device_node *child;
	u32 id;

	ion_np = of_find_compatible_node(NULL, NULL, "qcom,msm-ion");
	if (!ion_np)
		return NULL;

	for_each_available_child_of_node(ion_np, child) {
		if (!of_property_read_u32(child, "reg", &id) &&
		    id == A52_QSEECOM_TA_HEAP_ID) {
			of_node_put(ion_np);
			return child;
		}
	}

	of_node_put(ion_np);
	return NULL;
}

static int __init a52_qseecom_ta_heap_init(void)
{
	struct device_node *heap_np;
	struct device_node *rmem_np;
	struct reserved_mem *rmem;
	const char *heap_type;
	int ret;

	heap_np = a52_find_qseecom_ta_heap_node();
	if (!heap_np) {
		a52_ackfr_record("BOOT qseecom_ta_heap fail stage=heap_node rc=%d",
				  -ENODEV);
		return -ENODEV;
	}

	ret = of_property_read_string(heap_np, "qcom,ion-heap-type", &heap_type);
	if (ret || strcmp(heap_type, "DMA")) {
		a52_ackfr_record("BOOT qseecom_ta_heap fail stage=heap_type rc=%d",
				  ret ? ret : -EINVAL);
		ret = ret ? ret : -EINVAL;
		goto put_heap;
	}

	rmem_np = of_parse_phandle(heap_np, "memory-region", 0);
	if (!rmem_np) {
		ret = -ENODEV;
		a52_ackfr_record("BOOT qseecom_ta_heap fail stage=memory_region rc=%d",
				  ret);
		goto put_heap;
	}

	if (!of_device_is_compatible(rmem_np, "shared-dma-pool") ||
	    !of_property_read_bool(rmem_np, "reusable") ||
	    of_property_read_bool(rmem_np, "no-map")) {
		ret = -EINVAL;
		a52_ackfr_record("BOOT qseecom_ta_heap fail stage=pool_contract rc=%d",
				  ret);
		goto put_rmem;
	}

	rmem = of_reserved_mem_lookup(rmem_np);
	if (!rmem || !rmem->priv) {
		ret = -EPROBE_DEFER;
		a52_ackfr_record("BOOT qseecom_ta_heap fail stage=cma_lookup rc=%d",
				  ret);
		goto put_rmem;
	}

	memset(&a52_ta_heap, 0, sizeof(a52_ta_heap));
	a52_ta_heap.cma = rmem->priv;
	a52_ta_heap.heap.ops = &a52_qseecom_ta_ops;
	a52_ta_heap.heap.type = ION_HEAP_TYPE_DMA;
	a52_ta_heap.heap.id = A52_QSEECOM_TA_HEAP_MASK;
	a52_ta_heap.heap.name = A52_QSEECOM_TA_HEAP_NAME;

	ret = ion_device_add_heap(&a52_ta_heap.heap);
	if (ret) {
		a52_ackfr_record("BOOT qseecom_ta_heap fail stage=register rc=%d",
				  ret);
		goto put_rmem;
	}

	a52_ackfr_record(
		"BOOT qseecom_ta_heap registered id=%u base=0x%llx size=%llu",
		A52_QSEECOM_TA_HEAP_ID,
		(unsigned long long)cma_get_base(a52_ta_heap.cma),
		(unsigned long long)cma_get_size(a52_ta_heap.cma));
	pr_info("A52 ION heap %s registered id=%u at %pa size=%zu\n",
		A52_QSEECOM_TA_HEAP_NAME, A52_QSEECOM_TA_HEAP_ID,
		&rmem->base, rmem->size);

put_rmem:
	of_node_put(rmem_np);
put_heap:
	of_node_put(heap_np);
	return ret;
}

/* ACK's ION core is a regular subsys initcall; run after it at the same level. */
subsys_initcall_sync(a52_qseecom_ta_heap_init);
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def patch_core(text: str) -> tuple[str, bool]:
    if MARKER in text:
        return text, False
    anchor = "\tcase ION_HEAP_TYPE_DMA:\n\t\tstart_bit = __ffs(ION_HEAP_DMA_START);"
    replacement = (
        "\tcase ION_HEAP_TYPE_DMA:\n"
        f"\t\t/* {MARKER}\n"
        "\t\t * TouchGrass exposes its DMA-backed qsecom_ta pool at heap ID 19.\n"
        "\t\t * ACK normally limits DMA heap IDs to 1..7; allow only this exact\n"
        "\t\t * fixed mask so the generic allocator preserves the vendor ABI.\n"
        "\t\t */\n"
        f"\t\tif (heap->id == BIT({HEAP_ID})) {{\n"
        f"\t\t\tstart_bit = {HEAP_ID};\n"
        f"\t\t\tend_bit = {HEAP_ID};\n"
        "\t\t\tbreak;\n"
        "\t\t}\n"
        "\t\tstart_bit = __ffs(ION_HEAP_DMA_START);"
    )
    if text.count(anchor) != 1:
        raise SystemExit(f"ION DMA ID anchor count={text.count(anchor)}")
    return text.replace(anchor, replacement, 1), True


def patch_makefile(text: str) -> tuple[str, bool]:
    line = "obj-$(CONFIG_ION) += a52_qseecom_ta_heap.o"
    if line in text:
        return text, False
    if not text.endswith("\n"):
        text += "\n"
    return text + line + "\n", True


def validate_dt(gki: Path) -> dict[str, object]:
    dts = read(gki / ION_DTS)
    heap_match = re.search(
        r"qcom,ion-heap@19\s*\{(?P<body>.*?)\n\s*\};", dts, re.S
    )
    if not heap_match:
        raise SystemExit("TouchGrass heap-19 DT node missing")
    body = heap_match.group("body")
    required = [
        "reg = <19>;",
        "memory-region = <&qseecom_ta_mem>;",
        'qcom,ion-heap-type = "DMA";',
    ]
    for marker in required:
        if marker not in body:
            raise SystemExit(f"heap-19 DT marker missing: {marker}")

    rmem_hits = []
    for path in (gki / "arch/arm64/boot/dts/qcom").glob("*.dtsi"):
        text = read(path)
        if "qseecom_ta_mem:" in text:
            rmem_hits.append((path, text))
    if len(rmem_hits) != 1:
        raise SystemExit(f"qseecom_ta_mem definition count={len(rmem_hits)}")
    path, text = rmem_hits[0]
    match = re.search(
        r"qseecom_ta_mem:\s*qseecom_ta_region\s*\{(?P<body>.*?)\n\s*\};",
        text,
        re.S,
    )
    if not match:
        raise SystemExit("qseecom_ta_region body missing")
    region = match.group("body")
    for marker in [
        'compatible = "shared-dma-pool";',
        "reusable;",
        "size = <0x0 0x1000000>;",
    ]:
        if marker not in region:
            raise SystemExit(f"qseecom_ta_region marker missing: {marker}")
    if "no-map;" in region:
        raise SystemExit("qseecom_ta_region unexpectedly no-map")
    return {
        "heap_node": str(ION_DTS),
        "reserved_memory_node": str(path.relative_to(gki)),
        "heap_id": HEAP_ID,
        "heap_mask": hex(1 << HEAP_ID),
        "heap_type": "DMA",
        "reserved_bytes": 0x1000000,
        "shared_dma_pool": True,
        "reusable": True,
    }


def audit(gki: Path) -> None:
    core = read(gki / ION_CORE)
    makefile = read(gki / HEAPS_MAKEFILE)
    source = read(gki / HEAP_SOURCE)
    if core.count(MARKER) != 1:
        raise SystemExit("core fixed-ID audit failed")
    if makefile.count("a52_qseecom_ta_heap.o") != 1:
        raise SystemExit("heap Makefile audit failed")
    required = [
        "A52_QSEECOM_TA_HEAP_ID 19U",
        "A52_QSEECOM_TA_HEAP_MASK BIT(A52_QSEECOM_TA_HEAP_ID)",
        'A52_QSEECOM_TA_HEAP_NAME "qsecom_ta"',
        'of_find_compatible_node(NULL, NULL, "qcom,msm-ion")',
        'of_parse_phandle(heap_np, "memory-region", 0)',
        'of_reserved_mem_lookup(rmem_np)',
        "a52_ta_heap.heap.type = ION_HEAP_TYPE_DMA",
        "a52_ta_heap.heap.id = A52_QSEECOM_TA_HEAP_MASK",
        "ion_device_add_heap(&a52_ta_heap.heap)",
        "subsys_initcall_sync(a52_qseecom_ta_heap_init)",
        "BOOT qseecom_ta_heap registered id=%u base=0x%llx size=%llu",
    ]
    for marker in required:
        if marker not in source:
            raise SystemExit(f"heap source audit marker missing: {marker}")


def run(gki: Path, output: Path) -> dict[str, object]:
    dt = validate_dt(gki)
    core_path = gki / ION_CORE
    makefile_path = gki / HEAPS_MAKEFILE
    source_path = gki / HEAP_SOURCE

    core, core_changed = patch_core(read(core_path))
    makefile, makefile_changed = patch_makefile(read(makefile_path))
    write(core_path, core)
    write(makefile_path, makefile)
    source_changed = not source_path.exists() or read(source_path) != HEAP_C
    write(source_path, HEAP_C)
    audit(gki)

    report = {
        "status": "a52-qseecom-ta-heap19-touchgrass-parity-v1-staged",
        "hardware_validated": False,
        "functional_change": "register-touchgrass-qseecom-ta-ion-heap19",
        "capture_sha256": CAPTURE_SHA256,
        "observed_failure": {
            "screen_result": "black",
            "heap_mask": "0x80000",
            "heap_id": 19,
            "allocation_return": -19,
            "display_scope_events": 0,
            "heartbeat_last_tick": 144,
            "kernel_alive_past_seconds": 95,
        },
        "touchgrass_reference": {
            "repository": TOUCHGRASS_REPO,
            "commit": TOUCHGRASS_COMMIT,
            "parser": "drivers/staging/android/ion/msm/msm_ion_of.c",
            "allocator": "drivers/staging/android/ion/ion_cma_heap.c",
            "behaviour": [
                "parse qcom,msm-ion children",
                "preserve numeric heap id 19",
                "name heap qsecom_ta",
                "bind memory-region to reusable shared-dma-pool CMA",
                "register during subsys init",
            ],
        },
        "ack_adaptation": {
            "core": str(ION_CORE),
            "heap_source": str(HEAP_SOURCE),
            "makefile": str(HEAPS_MAKEFILE),
            "dma_fixed_id_exception": 19,
            "other_dma_heap_ids_unchanged": True,
            "other_ion_masks_unchanged": True,
            "legacy_system_bit25_compat_unchanged": True,
            "refgen_logic_unchanged": True,
            "recorder_policy_unchanged": True,
            "display_logic_unchanged": True,
            "dtb_source_unchanged": True,
        },
        "dt_contract": dt,
        "changed": {
            "core": core_changed,
            "makefile": makefile_changed,
            "heap_source": source_changed,
        },
        "payload_capture": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-qseecom-ta-heap19-") as tmp:
        root = Path(tmp)
        write(
            root / ION_CORE,
            "#include <linux/bitmap.h>\n"
            "static int ion_assign_heap_id(struct ion_heap *heap, struct ion_device *dev)\n"
            "{\n"
            "\tint id_bit = -EINVAL;\n"
            "\tint start_bit = -1, end_bit = -1;\n"
            "\tswitch (heap->type) {\n"
            "\tcase ION_HEAP_TYPE_DMA:\n"
            "\t\tstart_bit = __ffs(ION_HEAP_DMA_START);\n"
            "\t\tend_bit = __ffs(ION_HEAP_DMA_END);\n"
            "\t\tbreak;\n"
            "\tdefault:\n\t\treturn -EINVAL;\n\t}\n\treturn id_bit;\n}\n",
        )
        write(
            root / HEAPS_MAKEFILE,
            "obj-$(CONFIG_ION_SYSTEM_HEAP) += ion_system_heap.o ion_page_pool.o\n"
            "obj-$(CONFIG_ION_CMA_HEAP) += ion_cma_heap.o\n",
        )
        write(
            root / ION_DTS,
            "&soc {\nqcom,ion { compatible = \"qcom,msm-ion\";\n"
            "qcom,ion-heap@19 {\nreg = <19>;\nmemory-region = <&qseecom_ta_mem>;\n"
            "qcom,ion-heap-type = \"DMA\";\n};\n};\n};\n",
        )
        write(
            root / "arch/arm64/boot/dts/qcom/lagoon.dtsi",
            "qseecom_ta_mem: qseecom_ta_region {\n"
            "compatible = \"shared-dma-pool\";\nreusable;\n"
            "size = <0x0 0x1000000>;\n};\n",
        )
        report = run(root, root / "report")
        if not all(report["changed"].values()):
            raise SystemExit("first-pass change audit failed")
        second = run(root, root / "report2")
        if any(second["changed"].values()):
            raise SystemExit("idempotence self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add TouchGrass-compatible QSEECOM TA ION heap ID 19 to ACK."
    )
    parser.add_argument("--gki", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "self-test-passed"}, sort_keys=True))
        return 0
    if args.gki is None or args.output is None:
        parser.error("--gki and --output are required unless --self-test is used")
    report = run(args.gki.resolve(), args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
