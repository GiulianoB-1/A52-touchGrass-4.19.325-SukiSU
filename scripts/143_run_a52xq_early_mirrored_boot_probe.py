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

    output = args.output.resolve()
    phase19_path = output / PHASE19
    phase20_path = output / PHASE20
    phase21_path = output / PHASE21
    if not all(path.is_file() for path in (phase19_path, phase20_path, phase21_path)):
        raise SystemExit("missing combined early-boot, shmbridge, or ION stage report")

    phase19 = json.loads(phase19_path.read_text(encoding="utf-8"))
    phase20 = json.loads(phase20_path.read_text(encoding="utf-8"))
    phase21 = json.loads(phase21_path.read_text(encoding="utf-8"))
    if phase20.get("status") != "qseecom-reserved-memory-shmbridge-staged":
        raise SystemExit("reserved-memory shmbridge stage did not pass")
    if phase21.get("status") != "ion-legacy-system-heap-mask-compat-staged":
        raise SystemExit("legacy ION system-heap stage did not pass")

    qsee = args.gki.resolve() / "drivers/a52_secure/qseecom.c"
    ion = args.gki.resolve() / "drivers/staging/android/ion/ion.c"
    qsee_text = qsee.read_text(encoding="utf-8", errors="replace")
    ion_text = ion.read_text(encoding="utf-8", errors="replace")
    qsee_required = (
        "A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE",
        "of_reserved_mem_lookup(rmem_node)",
        "QSEEINIT heap_bridge_result heap=%u path=%s ret=%d",
    )
    ion_required = (
        "A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT",
        "fd == -ENODEV",
        "ION compat legacy_system original=%x effective=%x",
        "ION compat_result fd=%d original=%x effective=%x",
    )
    missing = [item for item in qsee_required if item not in qsee_text]
    missing += [item for item in ion_required if item not in ion_text]
    if missing:
        raise SystemExit("combined Probe 143 audit failed: " + ", ".join(missing))

    phase19["status"] = "ack-early-mirrored-plus-shmbridge-plus-ion-compat-staged"
    phase19["reserved_memory_shmbridge"] = phase20
    phase19["legacy_system_heap_compat"] = phase21
    phase19_path.write_text(
        json.dumps(phase19, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
