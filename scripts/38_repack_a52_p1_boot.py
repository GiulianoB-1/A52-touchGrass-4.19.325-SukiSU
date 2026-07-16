#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

MAGIC = b"ANDROID!"
HEADER_V2_MIN = 1660


def align(value: int, page_size: int) -> int:
    return (value + page_size - 1) // page_size * page_size


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def c_string(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("ascii", errors="replace")


def parse_boot(data: bytes) -> dict:
    if len(data) < HEADER_V2_MIN or data[:8] != MAGIC:
        raise SystemExit("invalid Android boot image")

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
    ) = struct.unpack_from("<10I", data, 8)

    if header_version != 2:
        raise SystemExit(f"expected boot header v2, got {header_version}")
    if page_size <= 0 or page_size & (page_size - 1):
        raise SystemExit(f"invalid page size {page_size}")

    recovery_dtbo_size = struct.unpack_from("<I", data, 1632)[0]
    recovery_dtbo_offset = struct.unpack_from("<Q", data, 1636)[0]
    header_size = struct.unpack_from("<I", data, 1644)[0]
    dtb_size = struct.unpack_from("<I", data, 1648)[0]
    dtb_addr = struct.unpack_from("<Q", data, 1652)[0]

    kernel_offset = page_size
    ramdisk_offset = kernel_offset + align(kernel_size, page_size)
    second_offset = ramdisk_offset + align(ramdisk_size, page_size)
    computed_recovery_offset = second_offset + align(second_size, page_size)
    actual_recovery_offset = (
        recovery_dtbo_offset
        if recovery_dtbo_size and recovery_dtbo_offset
        else computed_recovery_offset
    )
    dtb_offset = (
        align(actual_recovery_offset + recovery_dtbo_size, page_size)
        if recovery_dtbo_size
        else computed_recovery_offset
    )
    end_offset = dtb_offset + dtb_size

    component_end = max(
        kernel_offset + kernel_size,
        ramdisk_offset + ramdisk_size,
        second_offset + second_size,
        actual_recovery_offset + recovery_dtbo_size,
        end_offset,
    )
    if component_end > len(data):
        raise SystemExit("boot image component extends past file end")

    return {
        "kernel_size": kernel_size,
        "kernel_addr": kernel_addr,
        "ramdisk_size": ramdisk_size,
        "ramdisk_addr": ramdisk_addr,
        "second_size": second_size,
        "second_addr": second_addr,
        "tags_addr": tags_addr,
        "page_size": page_size,
        "header_version": header_version,
        "os_version": os_version,
        "board": c_string(data[48:64]),
        "cmdline": (c_string(data[64:576]) + c_string(data[608:1632])).strip(),
        "recovery_dtbo_size": recovery_dtbo_size,
        "recovery_dtbo_offset": recovery_dtbo_offset,
        "header_size": header_size,
        "dtb_size": dtb_size,
        "dtb_addr": dtb_addr,
        "kernel_offset": kernel_offset,
        "ramdisk_offset": ramdisk_offset,
        "second_offset": second_offset,
        "actual_recovery_dtbo_offset": actual_recovery_offset,
        "dtb_offset": dtb_offset,
        "end_offset": end_offset,
        "kernel": data[kernel_offset : kernel_offset + kernel_size],
        "ramdisk": data[ramdisk_offset : ramdisk_offset + ramdisk_size],
        "second": data[second_offset : second_offset + second_size],
        "recovery_dtbo": (
            data[actual_recovery_offset : actual_recovery_offset + recovery_dtbo_size]
            if recovery_dtbo_size
            else b""
        ),
        "dtb": data[dtb_offset : dtb_offset + dtb_size] if dtb_size else b"",
        "id": data[576:608],
        "header_page": data[:page_size],
    }


def calculate_boot_id(parts: list[tuple[bytes, int]]) -> bytes:
    digest = hashlib.sha1()
    for payload, size in parts:
        digest.update(payload)
        digest.update(struct.pack("<I", size))
    return digest.digest() + b"\0" * 12


def main() -> int:
    parser = argparse.ArgumentParser(description="Repack and audit the A52 P1 boot image")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.read_bytes()
    new_kernel = args.kernel.read_bytes()
    original = parse_boot(source)

    if not new_kernel.startswith(b"\x1f\x8b"):
        raise SystemExit("replacement kernel is not gzip-compressed Image.gz")

    page_size = original["page_size"]
    header = bytearray(original["header_page"])
    struct.pack_into("<I", header, 8, len(new_kernel))

    new_kernel_offset = page_size
    new_ramdisk_offset = new_kernel_offset + align(len(new_kernel), page_size)
    new_second_offset = new_ramdisk_offset + align(original["ramdisk_size"], page_size)
    new_recovery_offset = new_second_offset + align(original["second_size"], page_size)
    if original["recovery_dtbo_size"]:
        struct.pack_into("<Q", header, 1636, new_recovery_offset)

    new_id = calculate_boot_id(
        [
            (new_kernel, len(new_kernel)),
            (original["ramdisk"], original["ramdisk_size"]),
            (original["second"], original["second_size"]),
            (original["recovery_dtbo"], original["recovery_dtbo_size"]),
            (original["dtb"], original["dtb_size"]),
        ]
    )
    header[576:608] = new_id

    output = bytearray(header)

    def add_component(payload: bytes) -> None:
        output.extend(payload)
        output.extend(b"\0" * (align(len(output), page_size) - len(output)))

    add_component(new_kernel)
    add_component(original["ramdisk"])
    add_component(original["second"])
    add_component(original["recovery_dtbo"])
    add_component(original["dtb"])

    if len(output) > len(source):
        raise SystemExit(
            f"repacked image exceeds source partition size: {len(output)} > {len(source)}"
        )
    output.extend(b"\0" * (len(source) - len(output)))

    rebuilt = parse_boot(bytes(output))
    invariants = {
        "partition_size_preserved": len(output) == len(source),
        "header_version_preserved": rebuilt["header_version"] == original["header_version"],
        "page_size_preserved": rebuilt["page_size"] == original["page_size"],
        "board_preserved": rebuilt["board"] == original["board"],
        "cmdline_preserved": rebuilt["cmdline"] == original["cmdline"],
        "kernel_addr_preserved": rebuilt["kernel_addr"] == original["kernel_addr"],
        "ramdisk_addr_preserved": rebuilt["ramdisk_addr"] == original["ramdisk_addr"],
        "tags_addr_preserved": rebuilt["tags_addr"] == original["tags_addr"],
        "ramdisk_preserved": sha256(rebuilt["ramdisk"]) == sha256(original["ramdisk"]),
        "second_preserved": sha256(rebuilt["second"]) == sha256(original["second"]),
        "recovery_dtbo_preserved": sha256(rebuilt["recovery_dtbo"])
        == sha256(original["recovery_dtbo"]),
        "dtb_preserved": sha256(rebuilt["dtb"]) == sha256(original["dtb"]),
        "replacement_kernel_used": sha256(rebuilt["kernel"]) == sha256(new_kernel),
        "boot_id_recalculated": rebuilt["id"] == new_id,
    }
    failed = [name for name, passed in invariants.items() if not passed]
    if failed:
        raise SystemExit("repack audit failed: " + ", ".join(failed))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)

    report = {
        "status": "repacked-audited",
        "hardware_validated": False,
        "flashable_candidate": True,
        "source_sha256": sha256(source),
        "output_sha256": sha256(output),
        "source_bytes": len(source),
        "output_bytes": len(output),
        "source_kernel_size": original["kernel_size"],
        "replacement_kernel_size": len(new_kernel),
        "ramdisk_sha256": sha256(original["ramdisk"]),
        "dtb_sha256": sha256(original["dtb"]),
        "original_boot_id": original["id"][:20].hex(),
        "new_boot_id": new_id[:20].hex(),
        "header_version": original["header_version"],
        "page_size": page_size,
        "board": original["board"],
        "cmdline": original["cmdline"],
        "dtb_size": original["dtb_size"],
        "dtb_addr": original["dtb_addr"],
        "invariants": invariants,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
