#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "17e3a834caf654a2c1ea9ad345e52e7b926710a6/"
    "scripts/149_apply_a52xq_ion_system_heap_secure_gate.py"
)
VENDOR_EXPR = "ION_FLAGS_CP_MASK | ION_FLAG_SECURE"
LOCAL_EXPR = "0x6FFE0000U | (1U << 31)"


def load_base() -> dict[str, object]:
    with urllib.request.urlopen(BASE_URL, timeout=60) as response:
        source = response.read().decode("utf-8")

    count = source.count(VENDOR_EXPR)
    if count < 2:
        raise SystemExit(
            f"immutable secure-gate vendor expression expected at least 2, found {count}"
        )
    source = source.replace(VENDOR_EXPR, LOCAL_EXPR)
    if VENDOR_EXPR in source:
        raise SystemExit("immutable secure-gate vendor expression remains")

    namespace: dict[str, object] = {
        "__file__": str(Path(__file__).resolve()),
        "__name__": "a52_secure_gate149_base",
    }
    exec(compile(source, BASE_URL, "exec"), namespace)
    for name in ("self_test", "patch", "ION_REL", "REPORT"):
        if name not in namespace:
            raise SystemExit(f"immutable secure-gate stage missing {name}")
    return namespace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = load_base()
    self_test = base["self_test"]
    patch = base["patch"]
    assert callable(self_test)
    assert callable(patch)
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = root / base["ION_REL"]
    if not path.is_file():
        raise SystemExit(f"missing staged ACK ION source: {path}")

    result = patch(path)
    staged = path.read_text(encoding="utf-8", errors="replace")
    required = (
        "A52_ION_SYSTEM_HEAP_NONSECURE_GATE",
        "0x6FFE0000U | (1U << 31)",
        "A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT",
    )
    missing = [token for token in required if token not in staged]
    if missing:
        raise SystemExit("local secure-gate audit failed: " + ", ".join(missing))
    forbidden = (VENDOR_EXPR, "#include <linux/msm_ion.h>")
    present = [token for token in forbidden if token in staged]
    if present:
        raise SystemExit("local secure-gate dependency remains: " + ", ".join(present))

    result["samsung_cp_mask"] = "0x6FFE0000U"
    result["samsung_secure_bit"] = 31
    result["vendor_header_dependency"] = False
    report = {
        "status": "ion-system-heap-secure-gate-staged",
        "hardware_validated": False,
        "payload_capture": False,
        "reason": (
            "Samsung system heap 25 rejects secure VMID allocations; translating "
            "those flags to ACK generic system memory would weaken the contract"
        ),
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
