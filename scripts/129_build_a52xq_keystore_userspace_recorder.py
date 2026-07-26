#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path

MODULE_ZIP = "A52XQ-Keystore-Startup-Recorder-KSU.zip"
MODULE_ID = "a52_keystore_recorder"
SHARED_ROOT = "/sdcard/A52-Keystore-Recorder"
PRIVATE_ROOT = "/data/adb/a52-keystore-recorder"
PROC_RECORDER = "/proc/a52_keymaster_flight_recorder"
PERSIST_MARKER = "A52KMFR-PERSIST"

MODULE_PROP = f"""id={MODULE_ID}
name=A52 Keystore Startup Recorder
version=1.0.0
versionCode=1
author=GiulianoB and OpenAI
description=Metadata-only Keystore, KeyMint, QSEECOM and Kernel flight-recorder diagnostics exported to internal storage.
"""

CUSTOMIZE_SH = r'''#!/system/bin/sh
ui_print "- A52 Keystore Startup Recorder"
ui_print "- Metadata-only userspace diagnostics"

[ "$KSU" = "true" ] || abort "Install this ZIP through KernelSU Manager."
[ "$ARCH" = "arm64" ] || abort "Unsupported architecture: $ARCH"

set_perm_recursive "$MODPATH" 0 0 0755 0644
for SCRIPT in service.sh boot-completed.sh collector.sh export.sh action.sh uninstall.sh; do
  set_perm "$MODPATH/$SCRIPT" 0 0 0755
 done

mkdir -p /data/adb/a52-keystore-recorder/runs
chmod 0700 /data/adb/a52-keystore-recorder /data/adb/a52-keystore-recorder/runs

ui_print "- No system overlay and no sepolicy patch"
ui_print "- Captures will appear in Internal storage/A52-Keystore-Recorder"
ui_print "- Reboot after installation"
'''

SERVICE_SH = r'''#!/system/bin/sh
MODDIR=${0%/*}
STATE=/data/adb/a52-keystore-recorder
PIDFILE="$STATE/collector.pid"

mkdir -p "$STATE/runs"
chmod 0700 "$STATE" "$STATE/runs" 2>/dev/null || true

if [ -s "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    exit 0
  fi
fi

(
  exec sh "$MODDIR/collector.sh"
) >/dev/null 2>&1 &

echo "$!" > "$PIDFILE"
chmod 0600 "$PIDFILE" 2>/dev/null || true
exit 0
'''

BOOT_COMPLETED_SH = r'''#!/system/bin/sh
MODDIR=${0%/*}
sh "$MODDIR/export.sh" --copy-only >/dev/null 2>&1 || true
exit 0
'''

COLLECTOR_SH = r'''#!/system/bin/sh
MODDIR=${0%/*}
STATE=/data/adb/a52-keystore-recorder
RUNS="$STATE/runs"
PIDFILE="$STATE/collector.pid"
CURRENT="$STATE/current-run"
SHARED_NAME=A52-Keystore-Recorder
PROC_RECORDER=/proc/a52_keymaster_flight_recorder
FILTER='keystore2|keystore|keymaster|keymint|skeymast|qseecom|gatekeeper|DEAD_OBJECT|ErrorCode|Km\(|StrongBox|secure[[:space:]_-]*service'

mkdir -p "$RUNS"
chmod 0700 "$STATE" "$RUNS" 2>/dev/null || true

BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null | tr -cd 'A-Za-z0-9._-')"
if [ -z "$BOOT_ID" ]; then
  BOOT_ID="boot-$(date +%Y%m%d-%H%M%S 2>/dev/null)-$$"
fi
RUN="$RUNS/$BOOT_ID"
mkdir -p "$RUN/flight-recorder-milestones" "$RUN/pstore"
chmod 0700 "$RUN" "$RUN/flight-recorder-milestones" "$RUN/pstore" 2>/dev/null || true
printf '%s\n' "$BOOT_ID" > "$CURRENT"

LOG_PID=""
SHARED_DEST=""
START_EPOCH="$(date +%s 2>/dev/null)"
[ -n "$START_EPOCH" ] || START_EPOCH=0

log_note() {
  printf '%s %s\n' "$(date '+%s.%N' 2>/dev/null)" "$*" >> "$RUN/collector.log"
}

resolve_shared_root() {
  for ROOT in /sdcard /storage/emulated/0 /data/media/0; do
    [ -d "$ROOT" ] || continue
    TEST="$ROOT/.a52-keystore-recorder-write-test-$$"
    if (echo test > "$TEST") 2>/dev/null; then
      rm -f "$TEST"
      printf '%s/%s\n' "$ROOT" "$SHARED_NAME"
      return 0
    fi
  done
  return 1
}

copy_to_shared() {
  ROOT="$(resolve_shared_root 2>/dev/null)" || return 1
  SHARED_DEST="$ROOT/$BOOT_ID"
  mkdir -p "$SHARED_DEST" || return 1
  cp -af "$RUN/." "$SHARED_DEST/" 2>/dev/null || cp -rf "$RUN/." "$SHARED_DEST/" 2>/dev/null || return 1
  chmod -R a+rX "$SHARED_DEST" 2>/dev/null || true
  chown -R 1023:1023 "$SHARED_DEST" 2>/dev/null || true
  printf '%s\n' "$SHARED_DEST" > "$STATE/last-shared-path"
  sync
  return 0
}

pid_exact() {
  VALUE="$(pidof "$1" 2>/dev/null | tr ' ' ',')"
  [ -n "$VALUE" ] && printf '%s' "$VALUE" || printf 'missing'
}

pid_pattern() {
  VALUE="$(ps -A 2>/dev/null | grep -iE "$1" | grep -v grep | awk '{print $2}' | paste -sd, - 2>/dev/null)"
  [ -n "$VALUE" ] && printf '%s' "$VALUE" || printf 'missing'
}

snapshot_properties() {
  {
    echo "captured_epoch=$(date +%s 2>/dev/null)"
    echo "uname=$(uname -a 2>/dev/null)"
    echo "selinux=$(getenforce 2>/dev/null)"
    for PROP in \
      ro.product.device ro.product.model ro.build.fingerprint \
      ro.boot.verifiedbootstate ro.boot.vbmeta.device_state \
      ro.boot.flash.locked ro.boot.boot_recovery sys.boot_completed; do
      printf '%s=%s\n' "$PROP" "$(getprop "$PROP" 2>/dev/null)"
    done
  } > "$RUN/device-and-boot-properties.txt"
}

snapshot_services() {
  NOW="$(date '+%s.%N' 2>/dev/null)"
  {
    echo "===== epoch=$NOW service-list ====="
    service list 2>&1 | grep -iE "$FILTER" || true
    echo "===== epoch=$NOW lshal ====="
    lshal 2>&1 | grep -iE "$FILTER" || true
    echo "===== epoch=$NOW processes ====="
    ps -A -Z 2>&1 | grep -iE "$FILTER" || true
  } >> "$RUN/secure-service-snapshots.txt"
}

snapshot_kernel() {
  NOW="$(date '+%s.%N' 2>/dev/null)"
  {
    echo "===== epoch=$NOW ====="
    dmesg 2>&1 | grep -iE "$FILTER|A52KMFR" || true
  } > "$RUN/secure-kernel-messages-latest.txt.tmp"
  mv "$RUN/secure-kernel-messages-latest.txt.tmp" "$RUN/secure-kernel-messages-latest.txt"
}

snapshot_flight_recorder() {
  if [ -r "$PROC_RECORDER" ]; then
    cat "$PROC_RECORDER" > "$RUN/flight-recorder-latest.txt.tmp" 2>&1 || true
    mv "$RUN/flight-recorder-latest.txt.tmp" "$RUN/flight-recorder-latest.txt"
  else
    echo "$PROC_RECORDER is not readable on this kernel." > "$RUN/flight-recorder-unavailable.txt"
  fi
}

copy_pstore() {
  [ -d /sys/fs/pstore ] || return 0
  for SOURCE in /sys/fs/pstore/*; do
    [ -e "$SOURCE" ] || continue
    NAME="$(basename "$SOURCE")"
    cp -p "$SOURCE" "$RUN/pstore/$NAME" 2>/dev/null || cp "$SOURCE" "$RUN/pstore/$NAME" 2>/dev/null || true
  done
  grep -R -n -F 'A52KMFR-PERSIST' "$RUN/pstore" > "$RUN/A52KMFR-PERSIST-matches.txt" 2>/dev/null || true
  [ -s "$RUN/A52KMFR-PERSIST-matches.txt" ] || echo "No A52KMFR-PERSIST marker found in copied pstore files." > "$RUN/A52KMFR-PERSIST-matches.txt"
}

finish() {
  [ -n "$LOG_PID" ] && kill "$LOG_PID" 2>/dev/null || true
  snapshot_flight_recorder
  snapshot_services
  snapshot_kernel
  snapshot_properties
  copy_pstore
  copy_to_shared || true
  sync
  rm -f "$PIDFILE"
}
trap finish EXIT INT TERM

cat > "$RUN/README-FIRST.txt" <<'EOF'
A52 KEYSTORE STARTUP RECORDER

This capture contains metadata-only startup diagnostics for Keystore2, KeyMint,
Keymaster, QSEECOM, Gatekeeper and the existing A52 kernel flight recorder.

It deliberately does NOT collect:
- Keystore databases or database directories
- key blobs, plaintext keys or authentication tokens
- Binder, QSEE, KeyMint or Keymaster command/response buffers
- process memory, tombstones or full bugreports

Important files:
- health.csv
- secure-services.log
- secure-service-snapshots.txt
- secure-kernel-messages-latest.txt
- flight-recorder-latest.txt
- flight-recorder-milestones/
- pstore/
EOF

cat > "$RUN/capture-policy.txt" <<'EOF'
policy=metadata-only
kernel_recorder=existing-/proc/a52_keymaster_flight_recorder
persistent_marker=A52KMFR-PERSIST
keystore_database_read=false
key_blob_capture=false
command_response_buffer_capture=false
process_memory_capture=false
full_logcat_capture=false
EOF

cat /proc/version > "$RUN/proc-version.txt" 2>&1 || true
cat /proc/cmdline > "$RUN/proc-cmdline.txt" 2>&1 || true
cat /proc/mounts > "$RUN/proc-mounts.txt" 2>&1 || true
snapshot_properties
snapshot_services
snapshot_kernel
snapshot_flight_recorder
copy_pstore

printf 'elapsed_s,epoch,boot_completed,keystore2_pid,skeymast_pid,qseecomd_pid,keymint_pids,keymaster_pids,gatekeeper_pids\n' > "$RUN/health.csv"

(
  logcat -b all -v epoch -T 1 2>&1 |
    grep -iE "$FILTER" |
    sed -r \
      -e 's/[0-9a-fA-F]{32,}/<redacted-long-hex>/g' \
      -e 's/[A-Za-z0-9+\/=]{64,}/<redacted-long-token>/g'
) >> "$RUN/secure-services.log" &
LOG_PID="$!"

ELAPSED=0
POST_BOOT=0
BOOT_SEEN=0
NEXT_MILESTONE=0
MAX_SECONDS=900
POST_BOOT_SECONDS=180

log_note "collector-start boot_id=$BOOT_ID"

while [ "$ELAPSED" -lt "$MAX_SECONDS" ]; do
  EPOCH="$(date '+%s.%N' 2>/dev/null)"
  COMPLETE="$(getprop sys.boot_completed 2>/dev/null)"
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$ELAPSED" "$EPOCH" "${COMPLETE:-0}" \
    "$(pid_exact keystore2)" \
    "$(pid_exact skeymast)" \
    "$(pid_exact qseecomd)" \
    "$(pid_pattern 'keymint')" \
    "$(pid_pattern 'keymaster')" \
    "$(pid_pattern 'gatekeeper')" >> "$RUN/health.csv"

  snapshot_flight_recorder

  case "$ELAPSED" in
    0|5|15|30|60|120|180|300|600|900)
      if [ -r "$RUN/flight-recorder-latest.txt" ]; then
        cp "$RUN/flight-recorder-latest.txt" "$RUN/flight-recorder-milestones/${ELAPSED}s.txt" 2>/dev/null || true
      fi
      snapshot_services
      snapshot_kernel
      copy_pstore
      ;;
  esac

  if [ $((ELAPSED % 15)) -eq 0 ]; then
    copy_to_shared || true
  fi

  if [ "$COMPLETE" = "1" ]; then
    if [ "$BOOT_SEEN" -eq 0 ]; then
      BOOT_SEEN=1
      POST_BOOT=0
      log_note "sys.boot_completed=1 elapsed=$ELAPSED"
    fi
  fi

  if [ "$BOOT_SEEN" -eq 1 ] && [ "$POST_BOOT" -ge "$POST_BOOT_SECONDS" ]; then
    log_note "post-boot capture window complete"
    break
  fi

  if [ "$ELAPSED" -lt 180 ]; then
    INTERVAL=1
  else
    INTERVAL=5
  fi
  sleep "$INTERVAL"
  ELAPSED=$((ELAPSED + INTERVAL))
  if [ "$BOOT_SEEN" -eq 1 ]; then
    POST_BOOT=$((POST_BOOT + INTERVAL))
  fi
done

log_note "collector-stop elapsed=$ELAPSED"
exit 0
'''

EXPORT_SH = r'''#!/system/bin/sh
MODDIR=${0%/*}
STATE=/data/adb/a52-keystore-recorder
RUNS="$STATE/runs"
CURRENT="$STATE/current-run"
SHARED_NAME=A52-Keystore-Recorder
COPY_ONLY=0
[ "$1" = "--copy-only" ] && COPY_ONLY=1

BOOT_ID="$(cat "$CURRENT" 2>/dev/null)"
if [ -z "$BOOT_ID" ] || [ ! -d "$RUNS/$BOOT_ID" ]; then
  BOOT_ID="$(ls -1t "$RUNS" 2>/dev/null | head -n 1)"
fi
[ -n "$BOOT_ID" ] && [ -d "$RUNS/$BOOT_ID" ] || {
  echo "No recorder run is available yet."
  exit 1
}

ROOT=""
for CANDIDATE in /sdcard /storage/emulated/0 /data/media/0; do
  [ -d "$CANDIDATE" ] || continue
  TEST="$CANDIDATE/.a52-keystore-export-test-$$"
  if (echo test > "$TEST") 2>/dev/null; then
    rm -f "$TEST"
    ROOT="$CANDIDATE/$SHARED_NAME"
    break
  fi
done
[ -n "$ROOT" ] || {
  echo "Internal storage is not writable yet. Unlock Android and try again."
  exit 1
}

DEST="$ROOT/$BOOT_ID"
mkdir -p "$DEST" "$ROOT/Exports" || exit 1
cp -af "$RUNS/$BOOT_ID/." "$DEST/" 2>/dev/null || cp -rf "$RUNS/$BOOT_ID/." "$DEST/" 2>/dev/null || exit 1
chmod -R a+rX "$DEST" 2>/dev/null || true
chown -R 1023:1023 "$DEST" 2>/dev/null || true
printf '%s\n' "$DEST" > "$STATE/last-shared-path"

if [ "$COPY_ONLY" -eq 0 ]; then
  STAMP="$(date +%Y%m%d-%H%M%S 2>/dev/null)"
  [ -n "$STAMP" ] || STAMP=unknown-time
  ARCHIVE="$ROOT/Exports/A52-Keystore-Recorder-$STAMP.tar.gz"
  if tar -czf "$ARCHIVE" -C "$RUNS" "$BOOT_ID" 2>/dev/null; then
    chmod a+r "$ARCHIVE" 2>/dev/null || true
    chown 1023:1023 "$ARCHIVE" 2>/dev/null || true
    echo "Archive: $ARCHIVE"
  else
    echo "Archive creation failed; raw folder is still available."
  fi
fi

sync
echo "Capture folder: $DEST"
exit 0
'''

ACTION_SH = r'''#!/system/bin/sh
MODDIR=${0%/*}
exec sh "$MODDIR/export.sh"
'''

UNINSTALL_SH = r'''#!/system/bin/sh
MODDIR=${0%/*}
STATE=/data/adb/a52-keystore-recorder
if [ -s "$STATE/collector.pid" ]; then
  PID="$(cat "$STATE/collector.pid" 2>/dev/null)"
  [ -n "$PID" ] && kill "$PID" 2>/dev/null || true
fi
sh "$MODDIR/export.sh" >/dev/null 2>&1 || true
# Captures are intentionally preserved in /data/adb and internal storage.
exit 0
'''

README = f'''A52XQ KERNELSU KEYSTORE STARTUP RECORDER

Install this ZIP from KernelSU Manager, then reboot.
Do not flash it from OrangeFox or another custom recovery.

Automatic visible output:
  Internal storage/A52-Keystore-Recorder/<boot-id>/
  /sdcard/A52-Keystore-Recorder/<boot-id>/

Private working copy:
  {PRIVATE_ROOT}/runs/<boot-id>/

Kernel correlation source:
  {PROC_RECORDER}

The Action button in KernelSU copies the newest run to internal storage and
creates a shareable tar.gz under A52-Keystore-Recorder/Exports/.

This is a userspace companion to the existing kernel recorder. It does not add
another kernel recorder and it does not modify the kernel, boot image, system,
vendor, recovery, DTB or any block partition.

Privacy and key-safety policy:
- no Keystore database access
- no key blobs or plaintext keys
- no authentication tokens
- no command/response buffers
- no process-memory dumps
- no tombstones or full bugreports
- filtered and long-token-redacted logcat only
'''

FILES: dict[str, tuple[str, int]] = {
    "module.prop": (MODULE_PROP, 0o644),
    "skip_mount": ("", 0o644),
    "customize.sh": (CUSTOMIZE_SH, 0o755),
    "service.sh": (SERVICE_SH, 0o755),
    "boot-completed.sh": (BOOT_COMPLETED_SH, 0o755),
    "collector.sh": (COLLECTOR_SH, 0o755),
    "export.sh": (EXPORT_SH, 0o755),
    "action.sh": (ACTION_SH, 0o755),
    "uninstall.sh": (UNINSTALL_SH, 0o755),
    "README-FIRST.txt": (README, 0o644),
}


def add_text(zf: zipfile.ZipFile, name: str, text: str, mode: int) -> None:
    data = text.rstrip("\n").encode("utf-8") + b"\n"
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_zip(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        missing = sorted(set(FILES) - names)
        if missing:
            raise SystemExit("module ZIP missing entries: " + ", ".join(missing))

        contents = {
            name: zf.read(name).decode("utf-8")
            for name in FILES
            if name.endswith(".sh") or name in {"module.prop", "README-FIRST.txt"}
        }
        modes = {
            name: (zf.getinfo(name).external_attr >> 16) & 0o777
            for name in FILES
        }

    if f"id={MODULE_ID}" not in contents["module.prop"]:
        raise SystemExit("module.prop has the wrong module ID")
    if modes["service.sh"] != 0o755 or modes["collector.sh"] != 0o755:
        raise SystemExit("module boot scripts are not executable")

    combined = "\n".join(contents.values())
    required = (
        SHARED_ROOT,
        PRIVATE_ROOT,
        PROC_RECORDER,
        PERSIST_MARKER,
        "health.csv",
        "logcat -b all",
        "grep -iE",
        "ps -A -Z",
        "service list",
        "/sys/fs/pstore",
        "tar -czf",
        "full_logcat_capture=false",
    )
    absent = [token for token in required if token not in combined]
    if absent:
        raise SystemExit("module audit missing tokens: " + ", ".join(absent))

    script_combined = "\n".join(
        text for name, text in contents.items() if name.endswith(".sh")
    )
    forbidden = (
        "/data/misc/keystore",
        "/dev/block",
        "dd if=",
        "flash_image",
        "debuggerd",
        "/proc/$PID/mem",
        "gcore",
        "bugreportz",
        "/data/tombstones",
    )
    present = [token for token in forbidden if token in script_combined]
    if present:
        raise SystemExit("module contains forbidden collection/write path: " + ", ".join(present))

    return {
        "entries": sorted(names),
        "modes": {name: oct(mode) for name, mode in sorted(modes.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.output, "w", allowZip64=True) as zf:
        for name, (text, mode) in FILES.items():
            add_text(zf, name, text, mode)

    audit = audit_zip(args.output)
    report = {
        "status": "a52xq-keystore-userspace-recorder-built-audited",
        "hardware_validated": False,
        "module_id": MODULE_ID,
        "module_zip": args.output.name,
        "module_bytes": args.output.stat().st_size,
        "module_sha256": sha256(args.output),
        "installation": "KernelSU-Manager-only-not-custom-recovery",
        "kernel_changes": False,
        "another_kernel_recorder_added": False,
        "complements_existing_kernel_recorder": True,
        "kernel_proc_endpoint": PROC_RECORDER,
        "persistent_marker": PERSIST_MARKER,
        "private_state_root": PRIVATE_ROOT,
        "internal_storage_root": SHARED_ROOT,
        "automatic_internal_storage_export": True,
        "action_button_archive": True,
        "capture_window": {
            "maximum_seconds": 900,
            "post_boot_completed_seconds": 180,
            "one_second_sampling_until_seconds": 180,
            "later_sampling_interval_seconds": 5,
        },
        "captures": [
            "filtered-redacted-secure-service-logcat",
            "secure-process-health-timeline",
            "binder-service-registration-metadata",
            "filtered-secure-kernel-messages",
            "existing-kernel-flight-recorder-snapshots",
            "pstore-and-A52KMFR-PERSIST-matches",
            "selected-boot-properties",
        ],
        "privacy_exclusions": [
            "keystore-databases",
            "key-blobs",
            "plaintext-keys",
            "authentication-tokens",
            "command-response-buffers",
            "process-memory",
            "tombstones",
            "full-logcat",
            "full-bugreport",
        ],
        "zip_audit": audit,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"created {args.output} ({args.output.stat().st_size} bytes)")
    print(f"sha256 {report['module_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
