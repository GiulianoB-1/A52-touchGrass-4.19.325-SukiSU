#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "489bc1bee4243fbd9bb28247cad6ba36cf046110/"
    "scripts/153_apply_a52xq_qseecom_ion_heaps.py"
)
UAPI_REL = Path("include/uapi/linux/ion.h")
WRAPPER_MARKER = "A52_QSECOM_ION_HEADER_SPLIT_AUDIT"
PR_FMT_LINE = '#define pr_fmt(fmt) "A52IONQSEE: " fmt\n\n'


def load_base() -> dict[str, object]:
    with urllib.request.urlopen(BASE_URL, timeout=60) as response:
        source = response.read().decode("utf-8")
    namespace: dict[str, object] = {
        "__file__": str(Path(__file__).resolve()),
        "__name__": "a52_qseecom_heap153_base",
    }
    exec(compile(source, BASE_URL, "exec"), namespace)
    for name in (
        "read", "stage", "self_test", "ION_HDR_REL", "REPORT", "C_SOURCE"
    ):
        if name not in namespace:
            raise SystemExit(f"immutable heap stage missing {name}")
    return namespace


def remove_redundant_pr_fmt(base: dict[str, object]) -> None:
    source = str(base["C_SOURCE"])
    count = source.count(PR_FMT_LINE)
    if count != 1:
        raise SystemExit(f"QSEECOM heap pr_fmt anchor expected 1, found {count}")
    source = source.replace(PR_FMT_LINE, "", 1)
    if "#define pr_fmt" in source:
        raise SystemExit("QSEECOM heap source still defines pr_fmt")
    base["C_SOURCE"] = source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = load_base()
    remove_redundant_pr_fmt(base)
    self_test = base["self_test"]
    assert callable(self_test)
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    kernel_header = root / base["ION_HDR_REL"]
    uapi_header = root / UAPI_REL
    if not kernel_header.is_file() or not uapi_header.is_file():
        raise SystemExit(
            "missing ACK ION split headers: "
            f"kernel={kernel_header.is_file()} uapi={uapi_header.is_file()}"
        )

    original_read = base["read"]
    assert callable(original_read)

    def split_header_read(path: Path) -> str:
        current = Path(path)
        text = original_read(current)
        if current.resolve() == kernel_header.resolve():
            text += "\n/* " + WRAPPER_MARKER + " */\n"
            text += original_read(uapi_header)
        return text

    # The ACK heap structure and registration APIs live in include/linux/ion.h,
    # while heap type constants live in include/uapi/linux/ion.h. The base stage
    # remains immutable; only its audit view is corrected to match that split.
    base["read"] = split_header_read
    stage = base["stage"]
    assert callable(stage)
    result = stage(root)

    staged_source = root / base["C_REL"]
    staged_text = staged_source.read_text(encoding="utf-8", errors="replace")
    if "#define pr_fmt" in staged_text:
        raise SystemExit("staged QSEECOM heap source redefines pr_fmt")

    report = {
        "status": "qseecom-ion-heaps19-27-cma-staged",
        "hardware_validated": False,
        "header_audit": {
            "marker": WRAPPER_MARKER,
            "kernel_header": str(base["ION_HDR_REL"]),
            "uapi_header": str(UAPI_REL),
            "split_header_contract": True,
        },
        "compile_compat": {
            "redundant_pr_fmt_removed": True,
        },
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
    report_path = output / str(base["REPORT"])
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
