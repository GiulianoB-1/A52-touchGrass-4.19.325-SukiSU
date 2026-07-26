#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
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
RECOVERY_COLLECTOR = "COLLECT-FAILED-BOOT-RAMOOPS-RECOVERY.ps1"
RECOVERY_MARKER = "A52KMFR-PERSIST"

RECOVERY_COLLECTOR_SOURCE = r'''$ErrorActionPreference = 'Stop'

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$OutputDirectory = Join-Path (Get-Location) "A52-Failed-Boot-RAMOOPS-$Stamp"
$PstoreDirectory = Join-Path $OutputDirectory 'pstore'
$MarkerReport = Join-Path $OutputDirectory 'A52KMFR-PERSIST-matches.txt'
$ArchivePath = "$OutputDirectory.zip"

New-Item -ItemType Directory -Path $PstoreDirectory -Force | Out-Null

function Save-AdbShell {
  param(
    [Parameter(Mandatory = $true)][string]$FileName,
    [Parameter(Mandatory = $true)][string]$Command
  )

  $Destination = Join-Path $OutputDirectory $FileName
  & adb shell $Command 2>&1 | Out-File -FilePath $Destination -Encoding utf8
}

adb wait-for-device
if ($LASTEXITCODE -ne 0) {
  throw 'adb wait-for-device failed.'
}

$RecoveryId = (& adb shell id 2>&1 | Out-String).Trim()
$RecoveryId | Out-File -FilePath (Join-Path $OutputDirectory 'recovery-id.txt') -Encoding utf8
if ($LASTEXITCODE -ne 0) {
  throw 'Could not query the recovery ADB identity.'
}
if ($RecoveryId -notmatch 'uid=0') {
  throw "Recovery ADB is not root. Reported identity: $RecoveryId"
}

& adb shell "mkdir -p /sys/fs/pstore; grep -q ' /sys/fs/pstore ' /proc/mounts || mount -t pstore pstore /sys/fs/pstore"
if ($LASTEXITCODE -ne 0) {
  throw 'Could not mount the pstore filesystem in recovery.'
}

Save-AdbShell -FileName 'pstore-listing.txt' -Command 'ls -la /sys/fs/pstore'
Save-AdbShell -FileName 'proc-mounts.txt' -Command 'cat /proc/mounts'
Save-AdbShell -FileName 'proc-cmdline.txt' -Command 'cat /proc/cmdline'
Save-AdbShell -FileName 'proc-version.txt' -Command 'cat /proc/version'
Save-AdbShell -FileName 'recovery-dmesg.txt' -Command 'dmesg'

& adb pull /sys/fs/pstore/. $PstoreDirectory
if ($LASTEXITCODE -ne 0) {
  throw 'Failed to pull raw files from /sys/fs/pstore.'
}

$Matches = @(
  Get-ChildItem -Path $PstoreDirectory -File -Recurse |
    Select-String -Pattern 'A52KMFR-PERSIST' -SimpleMatch
)

if ($Matches.Count -gt 0) {
  $Matches |
    ForEach-Object { '{0}:{1}:{2}' -f $_.Path, $_.LineNumber, $_.Line } |
    Out-File -FilePath $MarkerReport -Encoding utf8
} else {
  'No A52KMFR-PERSIST marker was found in the recovered pstore files.' |
    Out-File -FilePath $MarkerReport -Encoding utf8
}

Compress-Archive -Force -Path (Join-Path $OutputDirectory '*') -DestinationPath $ArchivePath
Write-Host "Saved raw failed-boot RAMOOPS capture to: $ArchivePath"
'''


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


def normalize_generated_recorder_locals() -> None:
    """Make the generated recorder tolerant of whitespace and declaration order drift."""
    kernel = argument_path("--kernel")
    path = kernel / "drivers/misc/a52_keymaster_flight_recorder.c"
    if not path.is_file():
        raise SystemExit(f"generated Keymaster recorder is missing: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    marker = "persistent_line[A52_KMFR_PERSIST_LINE_LEN]"
    if marker in text:
        return

    function = re.search(
        r"void\s+a52_kmfr_record\s*\([^)]*\)\s*\{(?P<body>.*?)\n\}",
        text,
        re.DOTALL,
    )
    if function is None:
        raise SystemExit("could not locate generated a52_kmfr_record function")

    body = function.group("body")
    anchor = re.search(
        r"(?m)^(?P<indent>[ \t]*)unsigned\s+long\s+irq_flags\s*;\s*$",
        body,
    )
    if anchor is None:
        raise SystemExit("could not locate irq_flags local in generated recorder")

    indent = anchor.group("indent")
    insertion = (
        anchor.group(0)
        + "\n"
        + indent
        + "char persistent_line[A52_KMFR_PERSIST_LINE_LEN];\n"
        + indent
        + "int persistent_len;"
    )
    body = body[: anchor.start()] + insertion + body[anchor.end() :]
    updated = text[: function.start("body")] + body + text[function.end("body") :]
    path.write_text(updated, encoding="utf-8")

    verified = path.read_text(encoding="utf-8", errors="replace")
    if verified.count(marker) != 1 or verified.count("int persistent_len;") != 1:
        raise SystemExit("generated recorder local normalization audit failed")


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


def run_ramoops_console_reservation() -> None:
    reservation = Path(__file__).with_name(
        "128_apply_touchgrass_reserve_keymaster_ramoops_console.py"
    )
    if not reservation.is_file():
        raise SystemExit(f"RAMOOPS console reservation script missing: {reservation}")
    subprocess.run(
        [sys.executable, str(reservation), *sys.argv[1:]],
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


def write_recovery_collector() -> Path:
    output = argument_path("--output")
    output.mkdir(parents=True, exist_ok=True)
    path = output / RECOVERY_COLLECTOR
    path.write_text(
        RECOVERY_COLLECTOR_SOURCE.rstrip() + "\n",
        encoding="utf-8",
    )

    source = path.read_text(encoding="utf-8")
    required = (
        "uid=0",
        "/sys/fs/pstore",
        "adb pull /sys/fs/pstore/.",
        RECOVERY_MARKER,
        "Compress-Archive",
    )
    missing = [token for token in required if token not in source]
    if missing:
        raise SystemExit("recovery collector audit failed: " + ", ".join(missing))
    if "su 0" in source or "adb root" in source:
        raise SystemExit("recovery collector must not depend on Android root commands")
    return path


def merge_stage_reports(collector_path: Path) -> None:
    output = argument_path("--output")
    recorder_path = output / RECORDER_REPORT
    guard_path = output / AUDIO_GUARD_REPORT
    if not recorder_path.is_file():
        raise SystemExit(f"recorder report missing: {recorder_path}")
    if not guard_path.is_file():
        raise SystemExit(f"audio guard report missing: {guard_path}")
    if collector_path.parent != output or not collector_path.is_file():
        raise SystemExit(f"recovery collector missing: {collector_path}")

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
    if persistence.get("console_frontend_reserved") is not True:
        raise SystemExit("RAMOOPS console zone was not reserved for the recorder")
    if persistence.get("pmsg_capture_retained") is not True:
        raise SystemExit("RAMOOPS pmsg capture was not retained")
    if persistence.get("panic_dmesg_capture_retained") is not True:
        raise SystemExit("RAMOOPS panic dmesg capture was not retained")
    if guard.get("status") != "touchgrass-audio-sysfs-boot-guard-staged":
        raise SystemExit("audio guard report has unexpected status")
    if guard.get("panic_removed") is not True:
        raise SystemExit("audio guard report does not confirm panic removal")

    persistence["recovery_collector"] = {
        "path": collector_path.name,
        "mode": "recovery-adb-root-no-android-su",
        "marker": RECOVERY_MARKER,
        "pulls_raw_pstore": True,
        "creates_zip_archive": True,
    }
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
        "A52_KMFR_CONSOLE_RESERVED",
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
    normalize_generated_recorder_locals()
    run_failed_boot_persistence()
    run_ramoops_console_reservation()
    run_audio_boot_guard()
    collector_path = write_recovery_collector()
    merge_stage_reports(collector_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
