#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "9b9860de654a579462613d6499442074c0c7b110/"
    "scripts/143_run_a52xq_early_mirrored_boot_probe.py"
)
PHASE19 = "phase19-ack-early-mirrored-boot-probe-report.json"
PHASE20 = "phase20-qseecom-reserved-memory-shmbridge-report.json"
PHASE21 = "phase21-ion-legacy-system-heap-mask-report.json"
PHASE22 = "phase22-ion-dmabuf-contract-report.json"
PHASE23 = "phase23-ion-system-heap-secure-gate-report.json"
PHASE24 = "phase24-qseecom-ion-heap27-report.json"


def load_base() -> dict[str, object]:
    with urllib.request.urlopen(BASE_URL, timeout=60) as response:
        source = response.read().decode("utf-8")
    namespace: dict[str, object] = {
        "__file__": str(Path(__file__).resolve()),
        "__name__": "a52_probe143_base",
    }
    exec(compile(source, BASE_URL, "exec"), namespace)
    if not callable(namespace.get("main")):
        raise SystemExit("immutable Probe 143 base does not expose main()")
    return namespace


def run_stage(script_name: str, gki: Path, output: Path) -> None:
    script = Path(__file__).with_name(script_name)
    if not script.is_file():
        raise SystemExit(f"missing compatibility stage: {script}")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--gki",
            str(gki.resolve()),
            "--output",
            str(output.resolve()),
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()

    base = load_base()
    base_main = base["main"]
    assert callable(base_main)
    rc = int(base_main())
    if rc:
        return rc

    stages = (
        "144_apply_a52xq_qseecom_reserved_mem_shmbridge.py",
        "146_apply_a52xq_legacy_system_heap_mask.py",
        "149_apply_a52xq_ion_system_heap_secure_gate.py",
        "148_apply_a52xq_ion_dmabuf_contract.py",
        "152_apply_a52xq_qseecom_ion_heap27.py",
    )
    for script_name in stages:
        run_stage(script_name, args.gki, args.output)

    output = args.output.resolve()
    report_paths = {
        "phase19": output / PHASE19,
        "phase20": output / PHASE20,
        "phase21": output / PHASE21,
        "phase22": output / PHASE22,
        "phase23": output / PHASE23,
        "phase24": output / PHASE24,
    }
    if not all(path.is_file() for path in report_paths.values()):
        raise SystemExit("missing combined early-boot or memory-contract stage report")

    reports = {
        name: json.loads(path.read_text(encoding="utf-8"))
        for name, path in report_paths.items()
    }
    expected = (
        ("phase20", "qseecom-reserved-memory-shmbridge-staged"),
        ("phase21", "ion-legacy-system-heap-mask-compat-staged"),
        ("phase22", "ion-dmabuf-contract-compat-staged"),
        ("phase23", "ion-system-heap-secure-gate-staged"),
        ("phase24", "qseecom-ion-heap27-cma-staged"),
    )
    for name, status in expected:
        if reports[name].get("status") != status:
            raise SystemExit(f"combined stage did not pass: {status}")

    root = args.gki.resolve()
    qsee = root / "drivers/a52_secure/qseecom.c"
    heap27 = root / "drivers/a52_secure/a52_qseecom_ion_heap.c"
    ion = root / "drivers/staging/android/ion/ion.c"
    ion_dmabuf = root / "drivers/staging/android/ion/ion_dma_buf.c"
    texts = {
        "qsee": qsee.read_text(encoding="utf-8", errors="replace"),
        "heap27": heap27.read_text(encoding="utf-8", errors="replace"),
        "ion": ion.read_text(encoding="utf-8", errors="replace"),
        "ion_dmabuf": ion_dmabuf.read_text(encoding="utf-8", errors="replace"),
    }
    required = (
        ("qsee", "A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE"),
        ("qsee", "A52_QSEECOM_DMABUF_SHAPE_TRACE"),
        ("qsee", "DMABUF flags bridge fd=%d ret=%d flags=%lx n=%u"),
        ("qsee", "DMABUF shape fd=%d buf=%zu n=%u orig=%u"),
        ("ion", "A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT"),
        ("ion", "A52_ION_SYSTEM_HEAP_NONSECURE_GATE"),
        ("ion", "ION_FLAGS_CP_MASK | ION_FLAG_SECURE"),
        ("ion_dmabuf", "A52_ION_DMABUF_FLAGS_FALLBACK"),
        ("ion_dmabuf", "*flags = buffer->flags;"),
        ("heap27", "A52_QSECOM_ION_HEAP27_CMA"),
        ("heap27", "A52_QSECOM_HEAP_ID 27U"),
        ("heap27", "ION_HEAP_TYPE_CUSTOM"),
        ("heap27", "of_reserved_mem_device_init(&pdev->dev)"),
        ("heap27", "cma_alloc(state->cma"),
        ("heap27", "sg_alloc_table(table, 1"),
        ("heap27", "ion_device_add_heap(&a52_heap27.heap)"),
    )
    missing = [token for source, token in required if token not in texts[source]]
    if missing:
        raise SystemExit("combined Probe 143 audit failed: " + ", ".join(missing))

    phase19 = reports["phase19"]
    phase19["status"] = "ack-qseecom-heap27-memory-contract-staged"
    phase19["reserved_memory_shmbridge"] = reports["phase20"]
    phase19["legacy_system_heap_compat"] = reports["phase21"]
    phase19["ion_dmabuf_contract"] = reports["phase22"]
    phase19["system_heap_secure_gate"] = reports["phase23"]
    phase19["qseecom_heap27"] = reports["phase24"]
    report_paths["phase19"].write_text(
        json.dumps(phase19, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
