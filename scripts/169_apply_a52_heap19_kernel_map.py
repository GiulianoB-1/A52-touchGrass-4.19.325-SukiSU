#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

HEAP_SOURCE = Path("drivers/staging/android/ion/heaps/a52_qseecom_ta_heap.c")
RECORDER_SOURCE = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
REPORT_NAME = "phase30-a52-heap19-kernel-map-report.json"
PROFILE = "heap19-kmap-v1"
CAPTURE_SHA256 = "27a71572dc1a7ed4212dedfc96900c932c2a9d5563b159a3b616c128a0c16c7d"

INCLUDE_ANCHOR = "#include <linux/errno.h>\n"
INCLUDE_REPLACEMENT = "#include <linux/errno.h>\n#include <linux/err.h>\n"

OPS_ANCHOR = """static struct ion_heap_ops a52_qseecom_ta_ops = {
\t.allocate = a52_qseecom_ta_allocate,
\t.free = a52_qseecom_ta_free,
};
"""

OPS_REPLACEMENT = """static void *a52_qseecom_ta_map_kernel(struct ion_heap *heap,
\t\t\t\t\t       struct ion_buffer *buffer)
{
\tvoid *vaddr = ion_heap_map_kernel(heap, buffer);
\tlong rc = IS_ERR(vaddr) ? PTR_ERR(vaddr) : 0;

\ta52_ackfr_record("ION heap19 map_kernel ret=%ld", rc);
\treturn vaddr;
}

static void a52_qseecom_ta_unmap_kernel(struct ion_heap *heap,
\t\t\t\t\t struct ion_buffer *buffer)
{
\tion_heap_unmap_kernel(heap, buffer);
}

static struct ion_heap_ops a52_qseecom_ta_ops = {
\t.allocate = a52_qseecom_ta_allocate,
\t.free = a52_qseecom_ta_free,
\t.map_user = ion_heap_map_user,
\t.map_kernel = a52_qseecom_ta_map_kernel,
\t.unmap_kernel = a52_qseecom_ta_unmap_kernel,
};
"""

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
        "#include <linux/err.h>",
        "ion_heap_map_kernel(heap, buffer)",
        "ion_heap_unmap_kernel(heap, buffer)",
        'a52_ackfr_record("ION heap19 map_kernel ret=%ld", rc)',
        ".map_user = ion_heap_map_user",
        ".map_kernel = a52_qseecom_ta_map_kernel",
        ".unmap_kernel = a52_qseecom_ta_unmap_kernel",
    ]
    for marker in required_heap:
        if heap_text.count(marker) != 1:
            raise SystemExit(f"heap mapping audit failed: {marker}")
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
        heap_text, INCLUDE_ANCHOR, INCLUDE_REPLACEMENT, "err include"
    )
    heap_text, ops_changed = replace_once(
        heap_text, OPS_ANCHOR, OPS_REPLACEMENT, "heap ops"
    )
    recorder_text, profile_changed = replace_once(
        recorder_text, RECORDER_ANCHOR, RECORDER_REPLACEMENT, "recorder profile"
    )

    write(heap_path, heap_text)
    write(recorder_path, recorder_text)
    audit(read(heap_path), read(recorder_path))

    report = {
        "status": "a52-heap19-kernel-map-v1-staged",
        "hardware_validated": False,
        "functional_change": "add-standard-ion-kernel-mapping-callbacks-to-heap19",
        "persistent_profile": PROFILE,
        "capture_sha256": CAPTURE_SHA256,
        "observed_failure": {
            "screen_result": "black",
            "heap_mask": "0x80000",
            "heap_allocation": "success",
            "dma_buf_kernel_map_return": -95,
            "qseecom_load_app_return": -12,
            "kernel_panic_observed": False,
        },
        "mapping_callbacks": {
            "map_user": "ion_heap_map_user",
            "map_kernel": "ion_heap_map_kernel",
            "unmap_kernel": "ion_heap_unmap_kernel",
            "map_result_recorder_marker": "ION heap19 map_kernel ret=%ld",
        },
        "unchanged": {
            "heap19_allocator": True,
            "heap19_reserved_memory_binding": True,
            "refgen_logic": True,
            "display_control_flow": True,
            "display_scope_set": True,
            "recorder_retention_policy": True,
            "dt_source": True,
            "ramdisk": True,
            "recovery_dtbo": True,
        },
        "changed": {
            "err_include": include_changed,
            "heap_ops": ops_changed,
            "recorder_profile": profile_changed,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-heap19-kmap-") as tmp:
        root = Path(tmp)
        write(
            root / HEAP_SOURCE,
            "#include <linux/errno.h>\n"
            "static struct ion_heap_ops a52_qseecom_ta_ops = {\n"
            "\t.allocate = a52_qseecom_ta_allocate,\n"
            "\t.free = a52_qseecom_ta_free,\n"
            "};\n",
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
        description="Add standard ION kernel mapping callbacks to the A52 heap-19 driver."
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
