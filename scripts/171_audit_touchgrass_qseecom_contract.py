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
ACK_DMABUF_CORE = Path("drivers/dma-buf/dma-buf.c")
ACK_DMABUF_HEADER = Path("include/linux/dma-buf.h")
ACK_PORT_COMPAT = Path("a52-port-compat.h")
ACK_SECURE_MAKEFILE = Path("drivers/a52_secure/Makefile")
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
    ack_ion_dmabuf = read(gki / ACK_ION_DMABUF)
    ack_dmabuf_core = read(gki / ACK_DMABUF_CORE)
    ack_dmabuf_header = read(gki / ACK_DMABUF_HEADER)
    ack_port_compat = read(gki / ACK_PORT_COMPAT)
    ack_secure_makefile = read(gki / ACK_SECURE_MAKEFILE)
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

    qseecom_contract = {
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
        "cpu_access_then_page_map": (
            r"qseecom_vaddr_map\s*\([^)]*\)\s*\{"
            r".*?qseecom_dmabuf_map\s*\(.*?dma_buf_begin_cpu_access\s*\("
            r".*?dma_buf_kmap\s*\("
        ),
        "error_cleanup": (
            r"dma_buf_unmap_attachment\s*\(.*?dma_buf_detach\s*\(.*?dma_buf_put\s*\("
        ),
    }
    require_regex(tg_qseecom, qseecom_contract, "TouchGrass QSEECOM")
    require_regex(ack_qseecom, qseecom_contract, "ACK QSEECOM")

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

    ack_ion_exporter = require_regex(
        ack_ion_dmabuf,
        {
            "vmap_uses_heap_override_or_core_kmap": (
                r"static\s+void\s*\*\s*ion_dma_buf_vmap\s*\([^)]*\)\s*\{"
                r".*?if\s*\(heap->buf_ops\.vmap\).*?return\s+heap->buf_ops\.vmap\(dmabuf\)\s*;"
                r".*?vaddr\s*=\s*ion_buffer_kmap_get\(buffer\)\s*;"
            ),
            "vunmap_releases_core_kmap": (
                r"static\s+void\s+ion_dma_buf_vunmap\s*\([^)]*\)\s*\{"
                r".*?if\s*\(heap->buf_ops\.vunmap\).*?heap->buf_ops\.vunmap\(dmabuf,\s*vaddr\)"
                r".*?ion_buffer_kmap_put\(buffer\)\s*;"
            ),
            "get_flags_requires_heap_callback": (
                r"static\s+int\s+ion_dma_buf_get_flags\s*\([^)]*\)\s*\{"
                r".*?if\s*\(!heap->buf_ops\.get_flags\)\s*return\s+-EOPNOTSUPP\s*;"
                r".*?return\s+heap->buf_ops\.get_flags\(dmabuf,\s*flags\)\s*;"
            ),
            "exporter_registers_vmap_and_flags": (
                r"static\s+const\s+struct\s+dma_buf_ops\s+dma_buf_ops\s*=\s*\{"
                r".*?\.vmap\s*=\s*ion_dma_buf_vmap\s*,"
                r".*?\.vunmap\s*=\s*ion_dma_buf_vunmap\s*,"
                r".*?\.get_flags\s*=\s*ion_dma_buf_get_flags\s*,"
            ),
        },
        "ACK ION DMA-BUF exporter",
    )

    ack_generic_dmabuf = require_regex(
        ack_dmabuf_core,
        {
            "generic_vmap_calls_exporter": (
                r"void\s*\*\s*dma_buf_vmap\s*\([^)]*\)\s*\{"
                r".*?if\s*\(!dmabuf->ops->vmap\)\s*return\s+NULL\s*;"
                r".*?ptr\s*=\s*dmabuf->ops->vmap\(dmabuf\)\s*;"
            ),
            "generic_vunmap_calls_exporter": (
                r"void\s+dma_buf_vunmap\s*\([^)]*\)\s*\{"
                r".*?dmabuf->ops->vunmap\(dmabuf,\s*vaddr\)\s*;"
            ),
            "generic_flags_calls_exporter": (
                r"int\s+dma_buf_get_flags\s*\([^)]*\)\s*\{"
                r".*?if\s*\(dmabuf->ops->get_flags\)"
                r".*?ret\s*=\s*dmabuf->ops->get_flags\(dmabuf,\s*flags\)\s*;"
            ),
        },
        "ACK generic DMA-BUF core",
    )
    if "dma_buf_kmap" in ack_dmabuf_header:
        raise SystemExit("unexpected upstream dma_buf_kmap declaration; compat chain changed")

    ack_kmap_compat = require_regex(
        ack_port_compat,
        {
            "compat_kmap_uses_vmap_and_offset": (
                r"static\s+inline\s+void\s*\*\s*a52_kmap\s*\([^)]*\)\s*\{"
                r".*?void\s*\*\s*v\s*=\s*dma_buf_vmap\(b\)\s*;"
                r".*?return\s+v\s*\?\s*\(char\s*\*\)v\s*\+\s*n\s*\*\s*PAGE_SIZE\s*:\s*NULL\s*;"
            ),
            "compat_kunmap_uses_vunmap": (
                r"static\s+inline\s+void\s+a52_kunmap\s*\([^)]*\)\s*\{"
                r".*?dma_buf_vunmap\(b,\s*\(char\s*\*\)v\s*-\s*n\s*\*\s*PAGE_SIZE\)\s*;"
            ),
            "legacy_api_macros": (
                r"#ifndef\s+dma_buf_kmap.*?#define\s+dma_buf_kmap\(b,n\)\s+a52_kmap\(\(b\),\(n\)\)"
                r".*?#define\s+dma_buf_kunmap\(b,n,v\)\s+a52_kunmap\(\(b\),\(n\),\(v\)\)"
            ),
        },
        "A52 DMA-BUF page-map compatibility shim",
    )
    require(
        ack_secure_makefile,
        ["ccflags-y += -include $(srctree)/a52-port-compat.h"],
        "A52 secure-service forced compatibility include",
    )

    forbidden = [
        ".map_user = ion_heap_map_user",
        ".map_kernel =",
        ".unmap_kernel =",
        ".buf_ops.vmap =",
        ".buf_ops.vunmap =",
        "ION_HEAP_SYSTEM_MASK",
        "redirect heap 19",
    ]
    forbidden_hits = [marker for marker in forbidden if marker in ack_heap]
    if forbidden_hits:
        raise SystemExit(f"heap-19 isolation/default-mapping audit failed: {forbidden_hits}")

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
            "qseecom_contract": list(qseecom_contract),
        },
        "ack": {
            "heap_source": str(ACK_HEAP),
            "heap_contract": ack_heap_contract,
            "heap_abi": ack_heap_abi,
            "ion_exporter_contract": ack_ion_exporter,
            "generic_dma_buf_contract": ack_generic_dmabuf,
            "kmap_compatibility_contract": ack_kmap_compat,
            "qseecom_source": str(ack_qseecom_path),
            "qseecom_contract": list(qseecom_contract),
        },
        "semantic_adaptations": {
            "touchgrass_flags_provider": "global ION dma_buf_ops.get_flags",
            "ack_flags_provider": "heap-specific ion_heap.buf_ops.get_flags",
            "returned_value": "ion_buffer.flags",
            "qseecom_page_map_chain": [
                "dma_buf_kmap compatibility macro",
                "a52_kmap",
                "dma_buf_vmap",
                "ion_dma_buf_vmap",
                "ion_buffer_kmap_get",
            ],
            "qseecom_page_unmap_chain": [
                "dma_buf_kunmap compatibility macro",
                "a52_kunmap",
                "dma_buf_vunmap",
                "ion_dma_buf_vunmap",
                "ion_buffer_kmap_put",
            ],
            "default_ack_mapping_preserved": True,
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
            "a52_kmap/dma_buf_vmap result",
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
            "Gate the ACK heap-19 DMA-BUF exporter and A52 kmap shim against "
            "TouchGrass and Samsung QSEECOM behavior."
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
