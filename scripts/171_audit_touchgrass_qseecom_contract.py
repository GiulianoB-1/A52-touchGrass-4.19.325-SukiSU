#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TG_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
ACK_HEAP = Path("drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c")
ACK_ION_HEADER = Path("include/linux/ion.h")
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


def require_any(text: str, alternatives: dict[str, list[str]], label: str) -> dict[str, str]:
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
        if "qseecom_load_app" in text and "qseecom_vaddr_map" in text:
            candidates.append(root)
    if len(candidates) != 1:
        raise SystemExit(
            "ACK QSEECOM source discovery failed: "
            f"expected 1, found {[str(p.relative_to(gki)) for p in candidates]}"
        )
    path = candidates[0]
    return path.relative_to(gki), read(path)


def find_ack_ion_mapping(gki: Path) -> tuple[list[str], str]:
    matches: list[str] = []
    combined: list[str] = []
    for path in (gki / "drivers/staging/android/ion").rglob("*.c"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "ion_buffer_kmap_get" in text or "ion_dma_buf_vmap" in text:
            matches.append(str(path.relative_to(gki)))
            combined.append(text)
    if not matches:
        raise SystemExit("ACK ION DMA-BUF mapping implementation not found")
    return matches, "\n".join(combined)


def audit(gki: Path, touchgrass: Path, output: Path) -> dict[str, object]:
    tg_cma = read(touchgrass / "ion_cma_heap.c")
    tg_parser = read(touchgrass / "msm_ion_of.c")
    tg_qseecom = read(touchgrass / "qseecom.c")

    ack_heap = read(gki / ACK_HEAP)
    ack_header = read(gki / ACK_ION_HEADER)
    ack_qseecom_path, ack_qseecom = find_ack_qseecom(gki)
    ack_ion_paths, ack_ion_mapping = find_ack_ion_mapping(gki)

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
            "hlos_accessible_buffer",
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

    tg_qseecom_required = require(
        tg_qseecom,
        [
            "dma_buf_get",
            "dma_buf_attach",
            "dma_buf_map_attachment",
            "dma_buf_unmap_attachment",
            "dma_buf_detach",
            "dma_buf_put",
            "qseecom_load_app",
            "qseecom_create_bridge_for_secbuf",
            "qseecom_vaddr_map",
        ],
        "TouchGrass QSEECOM",
    )
    tg_cpu_mapping = require_any(
        tg_qseecom,
        {"cpu_mapping": ["dma_buf_kmap", "dma_buf_vmap"]},
        "TouchGrass QSEECOM CPU mapping",
    )

    ack_heap_contract = require(
        ack_heap,
        [
            "A52_QSEECOM_TA_HEAP_ID 19U",
            'A52_QSEECOM_TA_HEAP_NAME "qsecom_ta"',
            "cma_alloc(ta->cma",
            "cma_release(ta->cma",
            "sg_set_page(table->sgl, pages, size, 0)",
            "ion_buffer_prep_noncached(buffer)",
            ".map_user = ion_heap_map_user",
            ".map_kernel = a52_qseecom_ta_map_kernel",
            ".unmap_kernel = a52_qseecom_ta_unmap_kernel",
            "ion_heap_map_kernel(heap, buffer)",
            "ion_heap_unmap_kernel(heap, buffer)",
            "IS_ERR(vaddr)",
            'a52_ackfr_record("ION heap19 map_kernel ret=%ld", rc)',
            'of_parse_phandle(heap_np, "memory-region", 0)',
            "of_reserved_mem_lookup(rmem_np)",
            "ion_device_add_heap(&a52_ta_heap.heap)",
            "subsys_initcall_sync(a52_qseecom_ta_heap_init)",
        ],
        "ACK heap-19 adaptation",
    )

    ack_abi = require_regex(
        ack_header,
        {
            "map_kernel_callback": r"void\s*\*\s*\(\s*\*\s*map_kernel\s*\)\s*\(",
            "unmap_kernel_callback": r"void\s*\(\s*\*\s*unmap_kernel\s*\)\s*\(",
            "map_user_callback": r"int\s*\(\s*\*\s*map_user\s*\)\s*\(",
        },
        "ACK ION heap operation ABI",
    )
    ack_mapping_contract = require(
        ack_ion_mapping,
        [
            "ion_buffer_kmap_get",
            "ion_heap_map_kernel",
            "ion_heap_unmap_kernel",
        ],
        "ACK ION DMA-BUF mapping path",
    )
    ack_qseecom_required = require(
        ack_qseecom,
        [
            "dma_buf_get",
            "dma_buf_attach",
            "dma_buf_map_attachment",
            "dma_buf_unmap_attachment",
            "dma_buf_detach",
            "dma_buf_put",
            "qseecom_load_app",
            "qseecom_create_bridge_for_secbuf",
            "qseecom_vaddr_map",
        ],
        "ACK QSEECOM",
    )
    ack_cpu_mapping = require_any(
        ack_qseecom,
        {"cpu_mapping": ["dma_buf_vmap", "dma_buf_kmap"]},
        "ACK QSEECOM CPU mapping",
    )

    forbidden = [
        "ION_HEAP_SYSTEM_MASK",
        "redirect heap 19",
        "heap_id_mask = BIT(25)",
    ]
    forbidden_hits = [marker for marker in forbidden if marker in ack_heap]
    if forbidden_hits:
        raise SystemExit(f"heap-19 isolation audit failed: {forbidden_hits}")

    report: dict[str, object] = {
        "status": "touchgrass-qseecom-contract-parity-pass",
        "touchgrass_commit": TG_COMMIT,
        "hardware_validated": False,
        "touchgrass": {
            "cma_heap_contract": tg_cma_contract,
            "registration_contract": tg_registration_contract,
            "qseecom_consumer_contract": tg_qseecom_required,
            "cpu_mapping": tg_cpu_mapping,
        },
        "ack": {
            "heap_source": str(ACK_HEAP),
            "heap_contract": ack_heap_contract,
            "ion_heap_abi": ack_abi,
            "ion_mapping_sources": ack_ion_paths,
            "ion_mapping_contract": ack_mapping_contract,
            "qseecom_source": str(ack_qseecom_path),
            "qseecom_consumer_contract": ack_qseecom_required,
            "cpu_mapping": ack_cpu_mapping,
        },
        "semantic_adaptations": {
            "touchgrass_dma_preparation": "ion_pages_sync_for_device(device, pages, size, DMA_BIDIRECTIONAL)",
            "ack_dma_preparation": "ion_buffer_prep_noncached(buffer)",
            "touchgrass_reserved_pool_binding": "of_dma_configure plus device CMA area",
            "ack_reserved_pool_binding": "of_reserved_mem_lookup plus direct CMA handle",
            "fixed_vendor_heap_id_preserved": 19,
            "fixed_vendor_heap_mask_preserved": "0x80000",
            "system_heap_redirect": False,
        },
        "runtime_only_checks_remaining": [
            "actual map_kernel return on hardware",
            "QSEECOM shared-memory bridge registration result",
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
        description="Gate the ACK heap-19/QSEECOM adaptation against TouchGrass behavior."
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
