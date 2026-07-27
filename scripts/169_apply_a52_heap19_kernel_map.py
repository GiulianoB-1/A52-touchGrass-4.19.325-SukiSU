#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

HEAP_SOURCE = Path("drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c")
RECORDER_SOURCE = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
REPORT_NAME = "phase30-a52-heap19-dmabuf-flags-report.json"
PROFILE = "heap19-bufops-v1"
CAPTURE_SHA256 = "27a71572dc1a7ed4212dedfc96900c932c2a9d5563b159a3b616c128a0c16c7d"

INCLUDE_ANCHOR = "#include <linux/cma.h>\n#include <linux/errno.h>\n"
INCLUDE_REPLACEMENT = (
    "#include <linux/cma.h>\n"
    "#include <linux/dma-buf.h>\n"
    "#include <linux/errno.h>\n"
)

OPS_ANCHOR = """static struct ion_heap_ops a52_qseecom_ta_ops = {
\t.allocate = a52_qseecom_ta_allocate,
\t.free = a52_qseecom_ta_free,
};
"""

OPS_REPLACEMENT = """/*
 * TouchGrass exports every ION buffer through one global dma_buf_ops table
 * whose get_flags callback returns ion_buffer::flags. ACK 5.10 moved optional
 * exporter behavior to ion_heap::buf_ops and returns -EOPNOTSUPP when a heap
 * does not provide get_flags. Samsung QSEECOM requires that callback before
 * it can decide whether a contiguous buffer needs a secure-memory bridge.
 */
static int a52_qseecom_ta_get_flags(struct dma_buf *dmabuf,
\t\t\t\t    unsigned long *flags)
{
\tstruct ion_buffer *buffer;
\tunsigned long value = 0;
\tint ret = 0;

\tif (!dmabuf || !flags) {
\t\tret = -EINVAL;
\t\tgoto out;
\t}

\tbuffer = dmabuf->priv;
\tif (!buffer) {
\t\tret = -EINVAL;
\t\tgoto out;
\t}

\tvalue = buffer->flags;
\t*flags = value;

out:
\ta52_ackfr_record("ION heap19 get_flags ret=%d flags=0x%lx",
\t\t\t  ret, value);
\treturn ret;
}

static struct ion_heap_ops a52_qseecom_ta_ops = {
\t.allocate = a52_qseecom_ta_allocate,
\t.free = a52_qseecom_ta_free,
};
"""

INIT_ANCHOR = "\ta52_ta_heap.heap.name = A52_QSEECOM_TA_HEAP_NAME;\n"
INIT_REPLACEMENT = (
    "\ta52_ta_heap.heap.name = A52_QSEECOM_TA_HEAP_NAME;\n"
    "\ta52_ta_heap.heap.buf_ops.get_flags = a52_qseecom_ta_get_flags;\n"
)

RECORDER_ANCHOR = "policy=critical-after-capacity commit=%08x\\n"
RECORDER_REPLACEMENT = (
    f"policy=critical-after-capacity profile={PROFILE} commit=%08x\\n"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in text:
        return text, False
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}, expected 1")
    return text.replace(old, new, 1), True


def audit(heap_text: str, recorder_text: str) -> None:
    required_heap = [
        "#include <linux/dma-buf.h>",
        "static int a52_qseecom_ta_get_flags(struct dma_buf *dmabuf,",
        "buffer = dmabuf->priv",
        "value = buffer->flags",
        "*flags = value",
        'a52_ackfr_record("ION heap19 get_flags ret=%d flags=0x%lx",',
        ".buf_ops.get_flags = a52_qseecom_ta_get_flags",
    ]
    for marker in required_heap:
        if heap_text.count(marker) != 1:
            raise SystemExit(f"heap DMA-BUF flags audit failed: {marker}")

    forbidden = [
        ".map_user = ion_heap_map_user",
        ".map_kernel =",
        ".unmap_kernel =",
        ".buf_ops.map =",
        ".buf_ops.vmap =",
    ]
    hits = [marker for marker in forbidden if marker in heap_text]
    if hits:
        raise SystemExit(f"superseded old-ABI mapping callbacks remain: {hits}")

    if recorder_text.count(f"profile={PROFILE}") != 1:
        raise SystemExit("persistent profile audit failed")


def run(gki: Path, output: Path) -> dict[str, object]:
    heap_path = gki / HEAP_SOURCE
    recorder_path = gki / RECORDER_SOURCE
    if not heap_path.is_file():
        raise SystemExit(f"heap source missing: {heap_path}")
    if not recorder_path.is_file():
        raise SystemExit(f"recorder source missing: {recorder_path}")

    heap_text = read(heap_path)
    recorder_text = read(recorder_path)

    heap_text, include_changed = replace_once(
        heap_text, INCLUDE_ANCHOR, INCLUDE_REPLACEMENT, "dma-buf include"
    )
    heap_text, callback_changed = replace_once(
        heap_text, OPS_ANCHOR, OPS_REPLACEMENT, "heap get_flags callback"
    )
    heap_text, init_changed = replace_once(
        heap_text, INIT_ANCHOR, INIT_REPLACEMENT, "heap buf_ops registration"
    )
    recorder_text, profile_changed = replace_once(
        recorder_text, RECORDER_ANCHOR, RECORDER_REPLACEMENT, "recorder profile"
    )

    write(heap_path, heap_text)
    write(recorder_path, recorder_text)
    audit(read(heap_path), read(recorder_path))

    report = {
        "status": "a52-heap19-dmabuf-flags-v1-staged",
        "hardware_validated": False,
        "functional_change": "implement-ack-heap-specific-dmabuf-get-flags",
        "persistent_profile": PROFILE,
        "capture_sha256": CAPTURE_SHA256,
        "observed_failure": {
            "screen_result": "black",
            "heap_mask": "0x80000",
            "heap_allocation": "success",
            "qseecom_create_bridge_return": -95,
            "failure_statement": "dma_buf_get_flags",
            "qseecom_load_app_return": -12,
            "kernel_panic_observed": False,
        },
        "touchgrass_contract": {
            "exporter_scope": "global-ion-dma-buf-ops",
            "get_flags_behavior": "copy-ion-buffer-flags-and-return-zero",
        },
        "ack_adaptation": {
            "exporter_scope": "heap-specific-ion-heap-buf-ops",
            "callback": "a52_qseecom_ta_get_flags",
            "registration": "heap.buf_ops.get_flags",
            "default_map_fallback_preserved": True,
            "default_vmap_fallback_preserved": True,
            "old_ion_heap_mapping_callbacks_added": False,
        },
        "unchanged": {
            "heap19_allocator": True,
            "heap19_reserved_memory_binding": True,
            "heap19_id_and_mask": True,
            "ion_core_default_mapping": True,
            "qseecom_control_flow": True,
            "refgen_logic": True,
            "display_control_flow": True,
            "display_scope_set": True,
            "recorder_retention_policy": True,
            "dt_source": True,
            "ramdisk": True,
            "recovery_dtbo": True,
        },
        "changed": {
            "dma_buf_include": include_changed,
            "get_flags_callback": callback_changed,
            "buf_ops_registration": init_changed,
            "recorder_profile": profile_changed,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-heap19-bufops-") as tmp:
        root = Path(tmp)
        write(
            root / HEAP_SOURCE,
            "#include <linux/cma.h>\n"
            "#include <linux/errno.h>\n"
            "static struct ion_heap_ops a52_qseecom_ta_ops = {\n"
            "\t.allocate = a52_qseecom_ta_allocate,\n"
            "\t.free = a52_qseecom_ta_free,\n"
            "};\n"
            "static int init(void)\n{\n"
            "\ta52_ta_heap.heap.name = A52_QSEECOM_TA_HEAP_NAME;\n"
            "\treturn 0;\n}\n",
        )
        write(
            root / RECORDER_SOURCE,
            'const char *s = "policy=critical-after-capacity commit=%08x\\n";\n',
        )
        first = run(root, root / "report1")
        if not all(first["changed"].values()):
            raise SystemExit("first-pass self-test failed")
        second = run(root, root / "report2")
        if any(second["changed"].values()):
            raise SystemExit("idempotence self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Implement TouchGrass-equivalent DMA-BUF flag reporting on the "
            "ACK heap-19 exporter without replacing ACK mapping fallbacks."
        )
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
    print(json.dumps(run(args.gki.resolve(), args.output.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
