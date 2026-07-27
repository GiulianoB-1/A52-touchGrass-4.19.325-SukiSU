#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

ION_REL = Path("drivers/staging/android/ion/ion.c")
REPORT = "phase21-ion-legacy-system-heap-mask-report.json"
MARKER = "A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT"
LEGACY_SYSTEM_HEAP_ID = 25

ALLOC_CALL_RE = re.compile(
    r"(?P<indent>[ \t]*)fd = ion_alloc_fd\(data\.allocation\.len,\n"
    r"(?P=indent)[ \t]+data\.allocation\.heap_id_mask,\n"
    r"(?P=indent)[ \t]+data\.allocation\.flags\);"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replacement(indent: str) -> str:
    inner = indent + "\t"
    return (
        f"{indent}/* {MARKER}\n"
        f"{indent} * Samsung userspace requests its legacy system heap through bit 25.\n"
        f"{indent} * ACK generic ION exposes ION_HEAP_SYSTEM at bit 0. Preserve the\n"
        f"{indent} * original request and retry only an -ENODEV system-heap miss.\n"
        f"{indent} */\n"
        f"{indent}a52_ackfr_record(\n"
        f'{inner}"ION alloc_request len=%llu original=%x flags=%x",\n'
        f"{inner}(unsigned long long)data.allocation.len,\n"
        f"{inner}data.allocation.heap_id_mask, data.allocation.flags);\n"
        f"{indent}fd = ion_alloc_fd(data.allocation.len,\n"
        f"{inner}data.allocation.heap_id_mask,\n"
        f"{inner}data.allocation.flags);\n"
        f"{indent}if (fd == -ENODEV &&\n"
        f"{inner}(data.allocation.heap_id_mask & (1U << {LEGACY_SYSTEM_HEAP_ID}))) {{\n"
        f"{inner}unsigned int effective_heap_mask =\n"
        f"{inner}\t(data.allocation.heap_id_mask &\n"
        f"{inner}\t ~(1U << {LEGACY_SYSTEM_HEAP_ID})) | ION_HEAP_SYSTEM;\n\n"
        f"{inner}a52_ackfr_record(\n"
        f'{inner}\t"ION compat legacy_system original=%x effective=%x",\n'
        f"{inner}\tdata.allocation.heap_id_mask, effective_heap_mask);\n"
        f"{inner}fd = ion_alloc_fd(data.allocation.len,\n"
        f"{inner}\t\t  effective_heap_mask,\n"
        f"{inner}\t\t  data.allocation.flags);\n"
        f"{inner}a52_ackfr_record(\n"
        f'{inner}\t"ION compat_result fd=%d original=%x effective=%x",\n'
        f"{inner}\tfd, data.allocation.heap_id_mask, effective_heap_mask);\n"
        f"{indent}}}"
    )


def patch_ion(path: Path) -> dict[str, object]:
    text = read(path)

    if MARKER in text:
        if text.count(MARKER) != 1:
            raise SystemExit("ION legacy system-heap marker count is not one")
        state = "already-present"
    else:
        matches = list(ALLOC_CALL_RE.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                "ION allocation-call anchor mismatch: "
                f"expected 1, found {len(matches)}"
            )
        match = matches[0]
        text = text[: match.start()] + replacement(match.group("indent")) + text[match.end() :]
        state = "inserted"

    required = (
        MARKER,
        "fd == -ENODEV",
        f"1U << {LEGACY_SYSTEM_HEAP_ID}",
        "ION_HEAP_SYSTEM",
        "ION alloc_request len=%llu original=%x flags=%x",
        "ION compat legacy_system original=%x effective=%x",
        "ION compat_result fd=%d original=%x effective=%x",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("ION legacy system-heap audit failed: " + ", ".join(missing))

    if text.count("fd == -ENODEV") != 1:
        raise SystemExit("ION compatibility retry must have exactly one -ENODEV gate")
    if "if (fd < 0)" not in text:
        raise SystemExit("ION original failure return path is missing")

    write(path, text)
    return {
        "source": str(ION_REL),
        "state": state,
        "legacy_system_heap_id": LEGACY_SYSTEM_HEAP_ID,
        "upstream_system_heap_mask": "ION_HEAP_SYSTEM",
        "retry_condition": "original allocation returned -ENODEV and bit 25 was requested",
        "other_heap_requests_unchanged": True,
        "payload_capture": False,
    }


def self_test() -> None:
    sample = '''#include "ion_private.h"
static long ion_ioctl(void)
{
\tunion ion_ioctl_arg data;
\tint fd;

\tfd = ion_alloc_fd(data.allocation.len,
\t\t\t  data.allocation.heap_id_mask,
\t\t\t  data.allocation.flags);
\tif (fd < 0)
\t\treturn fd;
\treturn 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ION_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sample, encoding="utf-8")
        first = patch_ion(path)
        second = patch_ion(path)
        staged = path.read_text(encoding="utf-8")
        if first["state"] != "inserted":
            raise SystemExit("ION legacy mask self-test did not insert")
        if second["state"] != "already-present":
            raise SystemExit("ION legacy mask patch is not idempotent")
        if staged.count("ion_alloc_fd(") != 2:
            raise SystemExit("ION self-test expected original attempt plus one retry")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = root / ION_REL
    if not path.is_file():
        raise SystemExit(f"missing staged ACK ION source: {path}")

    result = patch_ion(path)
    report = {
        "status": "ion-legacy-system-heap-mask-compat-staged",
        "hardware_validated": False,
        "payload_capture": False,
        "observed_run29": {
            "qseecom_probe_return": 0,
            "boot_ready_reached": True,
            "ion_alloc_return": -19,
            "recorder_capacity_reached": 768,
        },
        "fix": result,
        "scope": (
            "retry only Samsung legacy system-heap bit 25 requests that fail with "
            "-ENODEV using ACK generic ION_HEAP_SYSTEM; preserve every other mask"
        ),
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
