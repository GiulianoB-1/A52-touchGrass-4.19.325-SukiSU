#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

CORE_COMMIT = "ea1a1db1fb0417779976a7be52778f6724ce138d"
CORE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    f"{CORE_COMMIT}/scripts/125_apply_touchgrass_keymaster_flight_recorder.py"
)


def run_pinned_recorder_core() -> None:
    try:
        with urllib.request.urlopen(CORE_URL, timeout=60) as response:
            source = response.read().decode("utf-8")
    except Exception as exc:
        raise SystemExit(f"failed to fetch pinned recorder core: {exc}") from exc

    namespace: dict[str, object] = {
        "__name__": "a52_touchgrass_recorder_core",
        "__file__": CORE_URL,
    }
    exec(compile(source, CORE_URL, "exec"), namespace)
    main = namespace.get("main")
    if not callable(main):
        raise SystemExit("pinned recorder core does not expose main()")
    result = main()
    if result not in (None, 0):
        raise SystemExit(f"pinned recorder core returned {result}")


def run_audio_boot_guard() -> None:
    guard = Path(__file__).with_name(
        "126_apply_touchgrass_audio_sysfs_boot_guard.py"
    )
    if not guard.is_file():
        raise SystemExit(f"audio boot guard script missing: {guard}")
    subprocess.run(
        [sys.executable, str(guard), *sys.argv[1:]],
        check=True,
    )


def main() -> int:
    run_pinned_recorder_core()
    run_audio_boot_guard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
