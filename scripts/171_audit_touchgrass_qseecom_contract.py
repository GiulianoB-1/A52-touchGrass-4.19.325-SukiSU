#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TG_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
ACK_HEAP = Path("drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c")
ACK_ION_HEADER = Path("include/linux/ion.h")
ACK_ION_DMABUF = Path("drivers/staging/android/ion/ion_dma_buf.c")
REPORT = "phase31-touchgrass-qseecom-contract-audit.json"


def read(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"required source missing: {path}")
    return path.read_text(encoding="utf-8", errors="strict")


def require(text: str, markers: list[str], label: str) -> list[str]:
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{label} missing markers: {missing}")
    return markers


def require_regex(text: str, patterns: dict[str, str], label: str) -> list[str]:
    missing = [name for name, pattern in patterns.items() if not re.search(pattern, text, re.S)]
    if missing:
        raise SystemExit(f"{label} missing semantic patterns: {missing}")
    return list(patterns)


def select_alternatives(
    text: str, alternatives: dict[str, list[str]], label: str
) -> dict[str, str]:
    selected: dict[str, str] = {}
    missing: list[str] = []
    for name, options in alternatives.items():
        match = next((option for option in options if option in text), None)
        if match is None:
            missing.append(name)
        else:
            selected[name] = match
    if missing:
        raise SystemExit(f"{label} missing alternative groups: {missing}")
    return selected


def find_ack_qseecom(gki: Path) -> tuple[Path, str]:
    candidates: list[Path] = []
    for root in (gki / "drivers").rglob("*.c"):
        try:
            text = root.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "qseecom_load_app" in text and "qseecom_create_bridge_for_secbuf" in text:
            candidates.append(root)
    if len(candidates) != 1:
        raise SystemExit(
            "ACK QSEECOM source discovery failed: "
            f"expected 1, found {[str(p.relative_to(gki)) for p in candidates]}"
        )
    path = candidates[0]
    return path.relative_to(gki), read(path)


def audit(gki: Path, touchgrass: Path, output: Path) -> dict[str, object]:
    tg_cma = read(touchgrass / "ion_cma_heap.c")
    tg_parser = read(touchgrass / "msm_ion_of.c")
    tg_ion = read(touchgrass / "ion.c")
    tg_qseecom = read(touchgrass / "qseecom.c")

    ack_heap = read(gki / ACK_HEAP)
    ack_header = read(gki / ACK_ION_HEADER)
    ack_dmabuf = read(gki / ACK_ION_DMABUF)
    ack_qseecom_path, ack_qseecom = find_ack_qseecom(gki)

    tg_cma_contract = require(
        tg_cma,
        [
            "cma_alloc(cma_heap->cma",
            "cma_release(cma_heap->cma",
            "sg_set_page(table->sgl, pages, size, 0)",
            ".map_user = ion_heap_map_user",
            ".map_kernel = ion_heap_map_kernel",
            ".unmap_kernel = ion_heap_unmap_kernel",
            "ion_pages_sync_for_device",
        ],
        "TouchGrass CMA heap",
    )
    tg_registration_contract = require(
        tg_parser,
        [
            "ION_QSECOM_TA_HEAP_ID",
            "ION_QSECOM_TA_HEAP_NAME",
            'of_parse_phandle(node, "memory-region", 0)',
            "of_dma_configure(&new_dev->dev, node, true)",
            "ion_heap_create(heap_data)",
            "ion_device_add_heap(new_dev, heaps[i])",
            "subsys_initcall(msm_ion_init)",
        ],
        "TouchGrass ION parser",
    )
    tg_flags_contract = require_regex(
        tg_ion,
        {
            "get_flags_copies_buffer_flags": (
                r"static\s+int\s+ion_dma_buf_get_flags\s*\([^)]*\)\s*\{"
                r".*?struct\s+ion_buffer\s*\*\s*buffer\s*=\s*dmabuf->priv\s*;"
                r".*?\*flags\s*=\s*buffer->flags\s*;.*?return\s+0\s*;"
            ),
            "global_exporter_registers_get_flags": (
                r"static\s+const\s+struct\s+dma_buf_ops\s+dma_buf_ops\s*=\s*\{"
                r".*?\.get_flags\s*=\s*ion_dma_buf_get_flags\s*,"
            ),
        },
        "TouchGrass global ION exporter",
    )

    qseecom_consumer_patterns = {
        "get_flags_before_secure_bridge_decision": (
            r"qseecom_create_bridge_for_secbuf\s*\([^)]*\)\s*\{"
            r".*?dma_buf_get_flags\s*\(\s*dmabuf\s*,\s*&dma_buf_flags\s*\)"
            r".*?ION_FLAG_SECURE"
        ),
        "attachment_then_bridge": (
            r"qseecom_dmabuf_map\s*\([^)]*\)\s*\{"
            r".*?dma_buf_get\s*\(.*?dma_buf_attach\s*\("
            r".*?dma_buf_map_attachment\s*\(.*?qseecom_create_bridge_for_secbuf\s*\("
        ),
        "cpu_access_then_kmap": (
            r"qseecom_vaddr_map\s*\([^)]*\)\s*\{"
            r".*?qseecom_dmabuf_map\s*\(.*?dma_buf_begin_cpu_access\s*\("
            r".*?dma_buf_kmap\s*\("
        ),
        "error_cleanup": (
            r"dma_buf_unmap_attachment\s*\(.*?dma_buf_detach\s*\(.*?dma_buf_put\s*\("
        ),
    }
    require_regex(tg_qseecom, qseecom_consumer_patterns, "TouchGrass QSEECOM")
    require_regex(ack_qseecom, qseecom_consumer_patterns, "ACK QSEECOM")

    ack_heap_contract = require(
        ack_heap,
        [
            "A52_QSEECOM_TA_HEAP_ID 19U",
            'A52_QSEECOM_TA_HEAP_NAME "qsecom_ta"',
            "cma_alloc(ta->cma",
            "cma_release(ta->cma",
            "sg_set_page(table->sgl, pages, size, 0)",
            "ion_buffer_prep_noncached(buffer)",
            "static int a52_qseecom_ta_get_flags(struct dma_buf *dmabuf,",
            "buffer = dmabuf->priv",
            "value = buffer->flags",
            "*flags = value",
            ".buf_ops.get_flags = a52_qseecom_ta_get_flags",
            'a52_ackfr_record("ION heap19 get_flags ret=%d flags=0x%lx",',
            'of_parse_phandle(heap_np, "memory-region", 0)',
            "of_reserved_mem_lookup(rmem_np)",
            "ion_device_add_heap(&a52_ta_heap.heap)",
            "subsys_initcall_sync(a52_qseecom_ta_heap_init)",
        ],
        "ACK heap-19 adaptation",
    )

    ack_heap_abi = require_regex(
        ack_header,
        {
            "heap_contains_dma_buf_ops": (
                r"struct\s+ion_heap\s*\{.*?struct\s+dma_buf_ops\s+buf_ops\s*;"
            ),
            "heap_ops_remains_allocator_only": (
                r"struct\s+ion_heap_ops\s*\{.*?allocate.*?free"
            ),
        },
        "ACK ION heap ABI",
    )

    ack_default_markers = require(
        ack_dmabuf,
        [
            "ion_dma_buf_vmap(",
            "ion_dma_buf_vunmap(",
            "ion_buffer_kmap_put(buffer)",
            "if (!heap->buf_ops.get_flags)",
            "return heap->buf_ops.get_flags(dmabuf, flags);",
            ".get_flags = ion_dma_buf_get_flags",
        ],
        "ACK default ION DMA-BUF exporter",
    )
    ack_default_variants = select_alternatives(
        ack_dmabuf,
        {
            "map_function": [
                "ion_dma_buf_map(",
                "ion_dma_buf_kmap(",
            ],
            "map_fallback": [
                "ion_buffer_kmap_get(buffer) + offset * PAGE_SIZE",
                "buffer->vaddr + offset * PAGE_SIZE",
            ],
            "vmap_behavior": [
                "vaddr = ion_buffer_kmap_get(buffer)",
                "return ERR_PTR(-EOPNOTSUPP)",
            ],
            "missing_get_flags_result": [
                "return -EOPNOTSUPP;",
            ],
        },
        "ACK default ION DMA-BUF fallback variants",
    )
    map_override_model = (
        "heap-specific-optional"
        if "heap->buf_ops.map" in ack_dmabuf
        else "core-owned"
    )
    vmap_override_model = (
        "heap-specific-optional"
        if "heap->buf_ops.vmap" in ack_dmabuf
        else "core-owned"
    )

    forbidden = [
        ".map_user = ion_heap_map_user",
        ".map_kernel =",
        ".unmap_kernel =",
        ".buf_ops.map =",
        ".buf_ops.vmap =",
        "ION_HEAP_SYSTEM_MASK",
        "redirect heap 19",
    ]
    forbidden_hits = [marker for marker in forbidden if marker in ack_heap]
    if forbidden_hits:
        raise SystemExit(f"heap-19 isolation/default-fallback audit failed: {forbidden_hits}")

    report: dict[str, object] = {
        "status": "touchgrass-qseecom-dmabuf-contract-parity-pass",
        "touchgrass_commit": TG_COMMIT,
        "hardware_validated": False,
        "observed_runtime_failure": {
            "statement": "dma_buf_get_flags",
            "return": -95,
            "downstream_qseecom_load_app_return": -12,
        },
        "touchgrass": {
            "cma_heap_contract": tg_cma_contract,
            "registration_contract": tg_registration_contract,
            "global_exporter_contract": tg_flags_contract,
            "qseecom_contract": list(qseecom_consumer_patterns),
        },
        "ack": {
            "heap_source": str(ACK_HEAP),
            "heap_contract": ack_heap_contract,
            "heap_abi": ack_heap_abi,
            "default_dma_buf_markers": ack_default_markers,
            "default_dma_buf_variants": ack_default_variants,
            "map_override_model": map_override_model,
            "vmap_override_model": vmap_override_model,
            "qseecom_source": str(ack_qseecom_path),
            "qseecom_contract": list(qseecom_consumer_patterns),
        },
        "semantic_adaptations": {
            "touchgrass_flags_provider": "global ION dma_buf_ops.get_flags",
            "ack_flags_provider": "heap-specific ion_heap.buf_ops.get_flags",
            "returned_value": "ion_buffer.flags",
            "default_ack_map_path_preserved": True,
            "default_ack_vmap_path_preserved": True,
            "touchgrass_dma_preparation": "ion_pages_sync_for_device",
            "ack_dma_preparation": "ion_buffer_prep_noncached",
            "fixed_vendor_heap_id_preserved": 19,
            "fixed_vendor_heap_mask_preserved": "0x80000",
            "system_heap_redirect": False,
        },
        "runtime_only_checks_remaining": [
            "heap19 get_flags returns zero and flags=0x1",
            "qseecom_create_bridge_for_secbuf advances past dma_buf_get_flags",
            "dma_buf_begin_cpu_access result",
            "dma_buf_kmap result",
            "secure application load result",
            "first display lifecycle scope after secure services become ready",
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Gate the ACK heap-19 DMA-BUF exporter against TouchGrass and "
            "Samsung QSEECOM behavior."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.gki.resolve(), args.touchgrass.resolve(), args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
