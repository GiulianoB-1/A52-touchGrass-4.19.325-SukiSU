#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

QSEE_REL = Path("drivers/a52_secure/qseecom.c")
REPORT = "phase20-qseecom-reserved-memory-shmbridge-report.json"
MARKER = "A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE"

FUNCTION_RE = re.compile(
    r"static int qseecom_register_heap_shmbridge\(uint32_t heapid, uint64_t \*handle\)\n"
    r"\{[\s\S]*?\n\}\n\nstatic int qseecom_register_shmbridge\(void\)"
)

REPLACEMENT = r'''/* A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE
 *
 * The downstream QSEECOM implementation expects each legacy MSM ION heap
 * child to have a platform_device with an attached CMA area. ACK uses the
 * generic dma-buf ION allocator and does not instantiate those heap child
 * devices. Preserve the legacy CMA path when available, then fall back to
 * the heap node's memory-region reserved-memory pool.
 */
static int qseecom_register_heap_shmbridge(uint32_t heapid, uint64_t *handle)
{
	phys_addr_t heap_pa = 0;
	size_t heap_size = 0;
	uint32_t val = 0;
	struct device_node *ion_node, *node;
	struct device_node *rmem_node = NULL;
	struct platform_device *ion_pdev = NULL;
	struct reserved_mem *rmem = NULL;
	struct cma *cma = NULL;
	const char *path = "none";
	int rc = 0;
	uint32_t ns_vmids[] = {VMID_HLOS};
	uint32_t ns_vm_perms[] = {PERM_READ | PERM_WRITE};

	ion_node = of_find_compatible_node(NULL, NULL, "qcom,msm-ion");
	if (!ion_node) {
		a52_ackfr_record(
			"QSEEINIT heap_bridge_result heap=%u path=no-ion-node ret=%d",
			heapid, -ENODEV);
		return -ENODEV;
	}

	for_each_available_child_of_node(ion_node, node) {
		if (of_property_read_u32(node, "reg", &val) || val != heapid)
			continue;

		ion_pdev = of_find_device_by_node(node);
		if (ion_pdev) {
			cma = dev_get_cma_area(&ion_pdev->dev);
			put_device(&ion_pdev->dev);
		}
		if (cma) {
			heap_pa = cma_get_base(cma);
			heap_size = (size_t)cma_get_size(cma);
			path = "legacy-cma";
			goto register_bridge;
		}

		rmem_node = of_parse_phandle(node, "memory-region", 0);
		if (!rmem_node) {
			rc = -ENODEV;
			path = "no-memory-region";
			goto out_match;
		}
		rmem = of_reserved_mem_lookup(rmem_node);
		of_node_put(rmem_node);
		rmem_node = NULL;
		if (!rmem || !rmem->size) {
			rc = -ENODEV;
			path = "reserved-memory-unavailable";
			goto out_match;
		}

		heap_pa = rmem->base;
		heap_size = (size_t)rmem->size;
		path = "reserved-memory";

register_bridge:
		a52_ackfr_record(
			"QSEEINIT heap_bridge heap=%u path=%s pa=%llx size=%zu",
			heapid, path, (unsigned long long)heap_pa, heap_size);
		rc = qtee_shmbridge_register(heap_pa, heap_size,
				ns_vmids, ns_vm_perms, 1,
				PERM_READ | PERM_WRITE, handle);

out_match:
		a52_ackfr_record(
			"QSEEINIT heap_bridge_result heap=%u path=%s ret=%d",
			heapid, path, rc);
		of_node_put(rmem_node);
		of_node_put(node);
		of_node_put(ion_node);
		return rc;
	}

	of_node_put(ion_node);
	a52_ackfr_record(
		"QSEEINIT heap_bridge_result heap=%u path=heap-absent ret=0",
		heapid);
	return 0;
}

static int qseecom_register_shmbridge(void)'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def patch_qseecom(path: Path) -> dict[str, object]:
    text = read(path)

    include = "#include <linux/of_reserved_mem.h>\n"
    if include in text:
        include_state = "already-present"
    else:
        anchor = "#include <linux/of_platform.h>\n"
        if text.count(anchor) != 1:
            raise SystemExit("QSEECOM reserved-memory include anchor mismatch")
        text = text.replace(anchor, anchor + include, 1)
        include_state = "inserted"

    if MARKER in text:
        if text.count(MARKER) != 1:
            raise SystemExit("QSEECOM reserved-memory marker count is not one")
        function_state = "already-present"
    else:
        matches = list(FUNCTION_RE.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                "QSEECOM heap shmbridge function anchor mismatch: "
                f"expected 1, found {len(matches)}"
            )
        match = matches[0]
        text = text[: match.start()] + REPLACEMENT + text[match.end() :]
        function_state = "inserted"

    required = (
        MARKER,
        "of_reserved_mem_lookup(rmem_node)",
        'path = "legacy-cma"',
        'path = "reserved-memory"',
        "QSEEINIT heap_bridge_result heap=%u path=%s ret=%d",
        "qtee_shmbridge_register(heap_pa, heap_size",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("QSEECOM reserved-memory audit failed: " + ", ".join(missing))

    write(path, text)
    return {
        "source": str(QSEE_REL),
        "include": include_state,
        "function": function_state,
        "legacy_path_preserved": True,
        "reserved_memory_fallback": True,
        "payload_capture": False,
    }


def self_test() -> None:
    sample = '''#include <linux/of_platform.h>
static int qseecom_register_heap_shmbridge(uint32_t heapid, uint64_t *handle)
{
	return -ENODEV;
}

static int qseecom_register_shmbridge(void)
{
	return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / QSEE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sample, encoding="utf-8")
        first = patch_qseecom(path)
        second = patch_qseecom(path)
        if first["function"] != "inserted":
            raise SystemExit("reserved-memory shmbridge self-test did not insert")
        if second["function"] != "already-present":
            raise SystemExit("reserved-memory shmbridge patch is not idempotent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = root / QSEE_REL
    if not path.is_file():
        raise SystemExit(f"missing staged QSEECOM source: {path}")

    result = patch_qseecom(path)
    report = {
        "status": "qseecom-reserved-memory-shmbridge-staged",
        "hardware_validated": False,
        "payload_capture": False,
        "observed_failure": {
            "function": "qseecom_register_shmbridge",
            "return": -22,
            "last_completed_stage": "qseecom_create_kthreads",
        },
        "fix": result,
        "scope": (
            "preserve the downstream legacy heap-platform-device CMA path and "
            "fall back to each ION heap node's shared-dma-pool memory-region"
        ),
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
