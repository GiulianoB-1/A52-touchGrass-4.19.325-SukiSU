#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

CORE_COMMIT = "ea1a1db1fb0417779976a7be52778f6724ce138d"
CORE_BLOB_SHA1 = "246b4316c17eb8d46215a642ac4582dae275c6aa"
CORE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    f"{CORE_COMMIT}/scripts/125_apply_touchgrass_keymaster_flight_recorder.py"
)
RECORDER_REPORT = "touchgrass-keymaster-flight-recorder-report.json"
AUDIO_GUARD_REPORT = "touchgrass-audio-sysfs-boot-guard-report.json"
PERSISTENCE_STATUS = "touchgrass-keymaster-ramoops-persistence-staged"


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def argument_path(flag: str) -> Path:
    try:
        index = sys.argv.index(flag)
        value = sys.argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit(f"missing required argument {flag}") from exc
    return Path(value).resolve()


def run_pinned_recorder_core() -> None:
    try:
        with urllib.request.urlopen(CORE_URL, timeout=60) as response:
            raw = response.read()
    except Exception as exc:
        raise SystemExit(f"failed to fetch pinned recorder core: {exc}") from exc

    observed_blob = git_blob_sha1(raw)
    if observed_blob != CORE_BLOB_SHA1:
        raise SystemExit(
            "pinned recorder core integrity mismatch: "
            f"expected {CORE_BLOB_SHA1}, observed {observed_blob}"
        )

    source = raw.decode("utf-8")
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


def run_failed_boot_persistence() -> None:
    persistence = Path(__file__).with_name(
        "127_apply_touchgrass_failed_boot_keymaster_persistence.py"
    )
    if not persistence.is_file():
        raise SystemExit(f"failed-boot persistence script missing: {persistence}")
    subprocess.run(
        [sys.executable, str(persistence), *sys.argv[1:]],
        check=True,
    )


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


def merge_stage_reports() -> None:
    output = argument_path("--output")
    recorder_path = output / RECORDER_REPORT
    guard_path = output / AUDIO_GUARD_REPORT
    if not recorder_path.is_file():
        raise SystemExit(f"recorder report missing: {recorder_path}")
    if not guard_path.is_file():
        raise SystemExit(f"audio guard report missing: {guard_path}")

    recorder = json.loads(recorder_path.read_text(encoding="utf-8"))
    guard = json.loads(guard_path.read_text(encoding="utf-8"))
    persistence = recorder.get("failed_boot_persistence", {})
    if persistence.get("status") != PERSISTENCE_STATUS:
        raise SystemExit("failed-boot persistence report has unexpected status")
    if persistence.get("survives_incomplete_android_boot") is not True:
        raise SystemExit("failed-boot persistence report does not confirm survival")
    if persistence.get("payload_policy") != (
        "metadata-only-no-command-or-response-buffers"
    ):
        raise SystemExit("failed-boot recorder payload policy is not metadata-only")
    if guard.get("status") != "touchgrass-audio-sysfs-boot-guard-staged":
        raise SystemExit("audio guard report has unexpected status")
    if guard.get("panic_removed") is not True:
        raise SystemExit("audio guard report does not confirm panic removal")

    recorder["audio_sysfs_boot_guard"] = guard
    recorder["pinned_recorder_core"] = {
        "commit": CORE_COMMIT,
        "git_blob_sha1": CORE_BLOB_SHA1,
        "integrity_verified": True,
    }
    markers = list(recorder.get("markers", []))
    for marker in (
        guard.get("marker"),
        "A52_TOUCHGRASS_FAILED_BOOT_KEYMASTER_RAMOOPS",
    ):
        if marker and marker not in markers:
            markers.append(marker)
    recorder["markers"] = markers
    recorder_path.write_text(
        json.dumps(recorder, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    run_pinned_recorder_core()
    run_failed_boot_persistence()
    run_audio_boot_guard()
    merge_stage_reports()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
