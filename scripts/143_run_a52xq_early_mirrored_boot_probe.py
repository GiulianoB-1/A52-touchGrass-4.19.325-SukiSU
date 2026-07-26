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

    script = Path(__file__).with_name(
        "144_apply_a52xq_qseecom_reserved_mem_shmbridge.py"
    )
    if not script.is_file():
        raise SystemExit(f"missing reserved-memory shmbridge stage: {script}")
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--gki",
            str(args.gki.resolve()),
            "--output",
            str(args.output.resolve()),
        ],
        check=True,
    )

    output = args.output.resolve()
    phase19_path = output / PHASE19
    phase20_path = output / PHASE20
    if not phase19_path.is_file() or not phase20_path.is_file():
        raise SystemExit("missing combined early-boot or shmbridge stage report")

    phase19 = json.loads(phase19_path.read_text(encoding="utf-8"))
    phase20 = json.loads(phase20_path.read_text(encoding="utf-8"))
    if phase20.get("status") != "qseecom-reserved-memory-shmbridge-staged":
        raise SystemExit("reserved-memory shmbridge stage did not pass")
    qsee = args.gki.resolve() / "drivers/a52_secure/qseecom.c"
    text = qsee.read_text(encoding="utf-8", errors="replace")
    required = (
        "A52_QSEECOM_RESERVED_MEMORY_SHMBRIDGE",
        "of_reserved_mem_lookup(rmem_node)",
        "QSEEINIT heap_bridge_result heap=%u path=%s ret=%d",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("combined Probe 143 audit failed: " + ", ".join(missing))

    phase19["status"] = "ack-early-mirrored-boot-probe-plus-shmbridge-staged"
    phase19["reserved_memory_shmbridge"] = phase20
    phase19_path.write_text(
        json.dumps(phase19, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
