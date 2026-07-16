#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


def parse_lock(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise SystemExit(f"invalid lock line: {raw!r}")
        values[key] = value
    return values


def align(value: int, page_size: int) -> int:
    return (value + page_size - 1) // page_size * page_size


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the checksum-locked A52 P1 boot source")
    parser.add_argument("boot_image", type=Path)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock = parse_lock(args.lock)
    image = args.boot_image
    if not image.is_file():
        raise SystemExit(f"boot image not found: {image}")

    data = image.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected_size = int(lock["BOOT_IMAGE_BYTES"])
    expected_digest = lock["BOOT_IMAGE_SHA256"].lower()

    if len(data) != expected_size:
        raise SystemExit(f"boot image size mismatch: got {len(data)}, expected {expected_size}")
    if digest != expected_digest:
        raise SystemExit(f"boot image SHA-256 mismatch: got {digest}, expected {expected_digest}")
    if data[:8] != b"ANDROID!":
        raise SystemExit("boot image does not start with ANDROID! magic")

    fields = struct.unpack_from("<10I", data, 8)
    (
        kernel_size,
        kernel_addr,
        ramdisk_size,
        ramdisk_addr,
        second_size,
        second_addr,
        tags_addr,
        page_size,
        header_version,
        os_version,
    ) = fields

    board = data[48:64].split(b"\0", 1)[0].decode("ascii", errors="replace")
    cmdline = data[64:576].split(b"\0", 1)[0].decode("ascii", errors="replace")
    extra_cmdline = data[608:1632].split(b"\0", 1)[0].decode("ascii", errors="replace")
    full_cmdline = (cmdline + extra_cmdline).strip()

    checks = {
        "header_version": (header_version, int(lock["BOOT_HEADER_VERSION"])),
        "page_size": (page_size, int(lock["BOOT_PAGE_SIZE"])),
        "kernel_size": (kernel_size, int(lock["BOOT_KERNEL_SIZE"])),
        "ramdisk_size": (ramdisk_size, int(lock["BOOT_RAMDISK_SIZE"])),
        "second_size": (second_size, int(lock["BOOT_SECOND_SIZE"])),
        "board": (board, lock["BOOT_BOARD"]),
    }
    mismatches = [f"{name}: got {actual!r}, expected {expected!r}" for name, (actual, expected) in checks.items() if actual != expected]
    if mismatches:
        raise SystemExit("boot header contract mismatch:\n" + "\n".join(mismatches))

    kernel_offset = page_size
    ramdisk_offset = kernel_offset + align(kernel_size, page_size)
    second_offset = ramdisk_offset + align(ramdisk_size, page_size)

    report = {
        "status": "source-validated",
        "flashable": False,
        "source_name": lock["BOOT_IMAGE_NAME"],
        "source_sha256": digest,
        "source_bytes": len(data),
        "magic": "ANDROID!",
        "header_version": header_version,
        "page_size": page_size,
        "kernel_size": kernel_size,
        "kernel_addr": kernel_addr,
        "kernel_offset": kernel_offset,
        "ramdisk_size": ramdisk_size,
        "ramdisk_addr": ramdisk_addr,
        "ramdisk_offset": ramdisk_offset,
        "second_size": second_size,
        "second_addr": second_addr,
        "second_offset": second_offset,
        "tags_addr": tags_addr,
        "os_version": os_version,
        "board": board,
        "cmdline": full_cmdline,
        "partition_limit_bytes": int(lock["BOOT_PARTITION_BYTES"]),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
