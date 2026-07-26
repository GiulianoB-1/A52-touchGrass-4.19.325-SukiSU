#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import zipfile
from pathlib import Path

MODULE_B64 = """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"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    raw = base64.b64decode("".join(MODULE_B64.split()))
    args.output.write_bytes(raw)

    with zipfile.ZipFile(args.output) as zf:
        names = set(zf.namelist())
        required_entries = {
            "module.prop", "skip_mount", "customize.sh", "post-fs-data.sh",
            "service.sh", "boot-completed.sh", "collector.sh", "export.sh",
            "action.sh", "uninstall.sh", "README-FIRST.txt",
        }
        missing = sorted(required_entries - names)
        if missing:
            raise SystemExit("module ZIP missing entries: " + ", ".join(missing))
        text = "\n".join(
            zf.read(name).decode("utf-8")
            for name in required_entries
            if name.endswith(".sh") or name in {"module.prop", "README-FIRST.txt"}
        )
        required_tokens = (
            "version=2.0.0", "custom_kernel_recorder_required=false",
            "pid-events.log", "logcat -b crash", "logcat -b events",
            "boot-failures.log", "/proc/uptime", "stop-request", "COMPLETE.txt",
            "/sdcard/A52-Keystore-Recorder", "tar -czf",
        )
        missing_tokens = [token for token in required_tokens if token not in text]
        if missing_tokens:
            raise SystemExit("module audit missing tokens: " + ", ".join(missing_tokens))
        entries = sorted(names)

    digest = hashlib.sha256(raw).hexdigest()
    report = {
        "status": "a52xq-keystore-userspace-recorder-v2-built-audited",
        "hardware_validated": False,
        "module_id": "a52_keystore_recorder",
        "module_version": "2.0.0",
        "module_zip": args.output.name,
        "module_bytes": len(raw),
        "module_sha256": digest,
        "installation": "KernelSU-Manager-only-not-custom-recovery",
        "kernel_changes": False,
        "custom_kernel_recorder_required": False,
        "optional_kernel_proc_endpoint": "/proc/a52_keymaster_flight_recorder",
        "private_state_root": "/data/adb/a52-keystore-recorder",
        "internal_storage_root": "/sdcard/A52-Keystore-Recorder",
        "automatic_internal_storage_export": True,
        "action_stops_and_finalizes_before_archive": True,
        "starts_from_post_fs_data": True,
        "uses_real_kernel_uptime": True,
        "capture_window": {
            "maximum_seconds": 900,
            "post_boot_completed_seconds": 300,
            "one_second_sampling_until_seconds": 180,
            "later_sampling_interval_seconds": 5,
        },
        "captures": [
            "filtered-redacted-secure-service-logcat",
            "filtered-redacted-boot-failure-logcat",
            "crash-buffer", "relevant-events-buffer",
            "real-uptime-secure-process-health-timeline",
            "pid-and-init-service-state-transitions",
            "binder-and-hal-service-registration-metadata",
            "secure-process-status-without-memory",
            "full-and-filtered-dmesg-milestones", "pstore",
            "selected-and-sanitized-boot-properties",
        ],
        "privacy_exclusions": [
            "keystore-databases", "key-blobs", "plaintext-keys",
            "authentication-tokens", "command-response-buffers",
            "process-memory", "tombstone-files", "full-bugreport",
        ],
        "zip_entries": entries,
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"created {args.output} ({len(raw)} bytes)")
    print(f"sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
