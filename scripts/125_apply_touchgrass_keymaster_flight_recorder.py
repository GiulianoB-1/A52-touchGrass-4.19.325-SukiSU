#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import urllib.request
import zipfile
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
RECOVERY_COLLECTOR = "A52XQ-Failed-Boot-RAMOOPS-Collector-Flashable.zip"
RECOVERY_MARKER = "A52KMFR-PERSIST"

RECOVERY_INSTALLER = r'''#!/sbin/sh
OUTFD="$2"
ZIPFILE="$3"

ui_print() {
  echo "ui_print $1" > "/proc/self/fd/$OUTFD"
  echo "ui_print" > "/proc/self/fd/$OUTFD"
}

abort() {
  ui_print "ERROR: $1"
  exit 1
}

ui_print "A52 failed-boot RAMOOPS collector"
ui_print "Read-only collector: no partition will be flashed"

grep -q '[[:space:]]/data[[:space:]]' /proc/mounts 2>/dev/null ||
  mount /data >/dev/null 2>&1 || true

STORAGE=""
for CANDIDATE in /sdcard /data/media/0 /external_sd /usb_otg; do
  [ -d "$CANDIDATE" ] || continue
  TESTFILE="$CANDIDATE/.a52-kmfr-write-test-$$"
  if (echo test > "$TESTFILE") 2>/dev/null; then
    rm -f "$TESTFILE"
    STORAGE="$CANDIDATE"
    break
  fi
done

[ -n "$STORAGE" ] ||
  abort "No writable storage. Mount/decrypt Data or insert writable SD/USB storage."

mkdir -p /sys/fs/pstore || abort "Cannot create /sys/fs/pstore"
if ! grep -q '[[:space:]]/sys/fs/pstore[[:space:]]' /proc/mounts 2>/dev/null; then
  mount -t pstore pstore /sys/fs/pstore >/dev/null 2>&1 ||
    abort "Cannot mount the pstore filesystem"
fi

STAMP="$(date +%Y%m%d-%H%M%S 2>/dev/null)"
[ -n "$STAMP" ] || STAMP="unknown-time"
DEST="$STORAGE/A52-Failed-Boot-RAMOOPS-$STAMP-$$"
PSTORE_DEST="$DEST/pstore"
MATCHES="$DEST/A52KMFR-PERSIST-matches.txt"

mkdir -p "$PSTORE_DEST" || abort "Cannot create output directory"

cat /proc/mounts > "$DEST/proc-mounts.txt" 2>&1 || true
cat /proc/cmdline > "$DEST/proc-cmdline.txt" 2>&1 || true
cat /proc/version > "$DEST/proc-version.txt" 2>&1 || true
dmesg > "$DEST/recovery-dmesg.txt" 2>&1 || true
ls -la /sys/fs/pstore > "$DEST/pstore-listing.txt" 2>&1 || true
getprop > "$DEST/recovery-getprop.txt" 2>&1 || true

FOUND=0
for SOURCE in /sys/fs/pstore/*; do
  [ -e "$SOURCE" ] || continue
  NAME="$(basename "$SOURCE")"
  cp -p "$SOURCE" "$PSTORE_DEST/$NAME" 2>/dev/null ||
    cp "$SOURCE" "$PSTORE_DEST/$NAME" 2>/dev/null ||
    abort "Failed to copy pstore record: $NAME"
  FOUND=1
done

if [ "$FOUND" -eq 0 ]; then
  echo "No files were present in /sys/fs/pstore." > "$DEST/NO-PSTORE-FILES.txt"
fi

grep -R -n -F 'A52KMFR-PERSIST' "$PSTORE_DEST" > "$MATCHES" 2>/dev/null || true
if [ ! -s "$MATCHES" ]; then
  echo "No A52KMFR-PERSIST marker was found in the recovered pstore files." > "$MATCHES"
fi

cat > "$DEST/README-FIRST.txt" <<EOF
A52 FAILED-BOOT RAMOOPS CAPTURE

Raw records:
  pstore/

Recorder matches:
  A52KMFR-PERSIST-matches.txt

The collector is read-only. It does not erase pstore and does not write any
kernel, boot image, ramdisk, DTB, recovery, or other partition.
EOF

ARCHIVE="$DEST.tar.gz"
if command -v tar >/dev/null 2>&1; then
  PARENT="$(dirname "$DEST")"
  BASE="$(basename "$DEST")"
  if tar -czf "$ARCHIVE" -C "$PARENT" "$BASE" >/dev/null 2>&1; then
    ui_print "Compressed capture: $ARCHIVE"
  else
    ui_print "Could not compress; raw folder was retained"
  fi
else
  ui_print "tar unavailable; raw folder was retained"
fi

sync
ui_print "RAMOOPS capture saved to:"
ui_print "$DEST"
ui_print "Collection completed successfully"
exit 0
'''

RECOVERY_UPDATER_SCRIPT = "# A52 read-only RAMOOPS collector\n"

RECOVERY_README = '''A52XQ FAILED-BOOT RAMOOPS COLLECTOR

Flash this ZIP from OrangeFox immediately after a failed Android boot.
It copies /sys/fs/pstore and recovery diagnostics to writable storage.

Before flashing:
1. Reboot directly into OrangeFox after the failed boot.
2. Do not attempt another Android boot first.
3. Mount and decrypt Data, or provide writable SD/USB storage.
4. Flash this collector ZIP.

The collector does not flash any partition and does not erase pstore.
It does not require ADB, PowerShell, Android root, or Magisk.
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


def add_zip_bytes(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    mode: int = 0o644,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


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
    manifest = {
        "name": "A52XQ failed-boot RAMOOPS collector",
        "mode": "recovery-flashable-read-only",
        "marker": RECOVERY_MARKER,
        "writes_partitions": False,
        "erases_pstore": False,
        "requires_adb": False,
        "requires_powershell": False,
    }

    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        add_zip_bytes(
            archive,
            "META-INF/com/google/android/update-binary",
            RECOVERY_INSTALLER.encode("utf-8"),
            0o755,
        )
        add_zip_bytes(
            archive,
            "META-INF/com/google/android/updater-script",
            RECOVERY_UPDATER_SCRIPT.encode("utf-8"),
        )
        add_zip_bytes(
            archive,
            "README-FIRST.txt",
            RECOVERY_README.encode("utf-8"),
        )
        add_zip_bytes(
            archive,
            "collector-manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        required_entries = {
            "META-INF/com/google/android/update-binary",
            "META-INF/com/google/android/updater-script",
            "README-FIRST.txt",
            "collector-manifest.json",
        }
        missing_entries = sorted(required_entries - names)
        if missing_entries:
            raise SystemExit(
                "recovery collector ZIP audit failed: " + ", ".join(missing_entries)
            )
        installer = archive.read(
            "META-INF/com/google/android/update-binary"
        ).decode("utf-8")

    required_tokens = (
        "/sys/fs/pstore",
        RECOVERY_MARKER,
        "A52-Failed-Boot-RAMOOPS",
        "cp -p",
        "tar -czf",
        "Read-only collector",
    )
    missing_tokens = [token for token in required_tokens if token not in installer]
    if missing_tokens:
        raise SystemExit(
            "recovery collector installer audit failed: " + ", ".join(missing_tokens)
        )
    forbidden_tokens = ("dd if=", "/dev/block", "flash_image", "persistent_ram_zap")
    present_forbidden = [token for token in forbidden_tokens if token in installer]
    if present_forbidden:
        raise SystemExit(
            "recovery collector contains forbidden write path: "
            + ", ".join(present_forbidden)
        )
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
        "mode": "recovery-flashable-read-only",
        "marker": RECOVERY_MARKER,
        "copies_raw_pstore": True,
        "attempts_tar_gzip_archive": True,
        "writes_partitions": False,
        "erases_pstore": False,
        "requires_adb": False,
        "requires_powershell": False,
        "sha256": hashlib.sha256(collector_path.read_bytes()).hexdigest(),
        "bytes": collector_path.stat().st_size,
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
    run_failed_boot_persistence()
    run_ramoops_console_reservation()
    run_audio_boot_guard()
    collector_path = write_recovery_collector()
    merge_stage_reports(collector_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
