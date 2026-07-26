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

    run_stage(
        "144_apply_a52xq_qseecom_reserved_mem_shmbridge.py",
        args.gki,
        args.output,
    )
    run_stage(
        "146_apply_a52xq_legacy_system_heap_mask.py",
        args.gki,
        args.output,
    )
    run_stage(
        "149_apply_a52xq_ion_system_heap_secure_gate.py",
        args.gki,
        args.output,
    )
    run_stage(
        "148_apply_a52xq_ion_dmabuf_contract.py",
        args.gki,
        args.output,
    )

    output = args.output.resolve()
    phase19_path = output / PHASE19
    phase20_path = output / PHASE20
    phase21_path = output / PHASE21
    phase22_path = output / PHASE22
    phase23_path = output / PHASE23
    paths = (phase19_path, phase20_path, phase21_path, phase22_path, phase23_path)
    if not all(path.is_file() for path in paths):
        raise SystemExit("missing combined early-boot or memory-contract stage report")

    phase19 = json.loads(phase19_path.read_text(encoding="utf-8"))
    phase20 = json.loads(phase20_path.read_text(encoding="utf-8"))
    phase21 = json.loads(phase21_path.read_text(encoding="utf-8"))
    phase22 = json.loads(phase22_path.read_text(encoding="utf-8"))
    phase23 = json.loads(phase23_path.read_text(encoding="utf-8"))
    expected = (
        (phase20, "qseecom-reserved-memory-shmbridge-staged"),
        (phase21, "ion-legacy-system-heap-mask-compat-staged"),
        (phase22, "ion-dmabuf-contract-compat-staged"),
        (phase23, "ion-system-heap-secure-gate-staged"),
    )
    for report, status in expected:
        if report.get("status") != status:
            raise SystemExit(f"combined stage did not pass: {status}")

    qsee = args.gki.resolve() / "drivers/a52_secure/qseecom.c"
    ion = args.gki.resolve() / "drivers/staging/android/ion/ion.c"
    ion_dmabuf = args.gki.resolve() / "drivers/staging/android/ion/ion_dma_buf.c"
    qsee_text = qsee.read_text(encoding="utf-8", errors="replace")
    ion_text = ion.read_text(encoding="utf-8", errors="replace")
    ion_dmabuf_text = ion_dmabuf.read_text(encoding="utf-8", errors="replace")
    required = (
        (qsee_text, "A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE"),
        (qsee_text, "A52_QSEECOM_DMABUF_SHAPE_TRACE"),
        (qsee_text, "DMABUF flags bridge fd=%d ret=%d flags=%lx n=%u"),
        (qsee_text, "DMABUF shape fd=%d buf=%zu n=%u orig=%u"),
        (ion_text, "A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT"),
        (ion_text, "A52_ION_SYSTEM_HEAP_NONSECURE_GATE"),
        (ion_text, "ION_FLAGS_CP_MASK | ION_FLAG_SECURE"),
        (ion_dmabuf_text, "A52_ION_DMABUF_FLAGS_FALLBACK"),
        (ion_dmabuf_text, "*flags = buffer->flags;"),
    )
    missing = [token for text, token in required if token not in text]
    if missing:
        raise SystemExit("combined Probe 143 audit failed: " + ", ".join(missing))

    phase19["status"] = "ack-secure-memory-contract-audited-staged"
    phase19["reserved_memory_shmbridge"] = phase20
    phase19["legacy_system_heap_compat"] = phase21
    phase19["ion_dmabuf_contract"] = phase22
    phase19["system_heap_secure_gate"] = phase23
    phase19_path.write_text(
        json.dumps(phase19, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
