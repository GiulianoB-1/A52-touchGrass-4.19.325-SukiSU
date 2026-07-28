#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

MAGIC = b"ANDROID!"
HEADER_V2_MIN = 1660
SAMSUNG_FOOTER = b"SEANDROIDENFORCE" + b"\xff" * 4


def align(value: int, page_size: int) -> int:
    return (value + page_size - 1) // page_size * page_size


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def c_string(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("ascii", errors="strict")


def parse_boot(data: bytes) -> dict[str, object]:
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
    recovery_offset = (
        recovery_dtbo_offset
        if recovery_dtbo_size and recovery_dtbo_offset
        else second_offset + align(second_size, page_size)
    )
    dtb_offset = (
        align(recovery_offset + recovery_dtbo_size, page_size)
        if recovery_dtbo_size
        else second_offset + align(second_size, page_size)
    )
    component_end = dtb_offset + dtb_size
    footer_offset = align(component_end, page_size)

    if component_end > len(data):
        raise SystemExit("boot image component extends past file end")
    if footer_offset + len(SAMSUNG_FOOTER) > len(data):
        raise SystemExit("Samsung footer does not fit in boot partition image")

    cmdline = (c_string(data[64:576]) + c_string(data[608:1632])).strip()
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
        "cmdline": cmdline,
        "recovery_dtbo_size": recovery_dtbo_size,
        "recovery_dtbo_offset": recovery_dtbo_offset,
        "header_size": header_size,
        "dtb_size": dtb_size,
        "dtb_addr": dtb_addr,
        "kernel_offset": kernel_offset,
        "ramdisk_offset": ramdisk_offset,
        "second_offset": second_offset,
        "recovery_offset": recovery_offset,
        "dtb_offset": dtb_offset,
        "component_end": component_end,
        "footer_offset": footer_offset,
        "kernel": data[kernel_offset : kernel_offset + kernel_size],
        "ramdisk": data[ramdisk_offset : ramdisk_offset + ramdisk_size],
        "second": data[second_offset : second_offset + second_size],
        "recovery_dtbo": (
            data[recovery_offset : recovery_offset + recovery_dtbo_size]
            if recovery_dtbo_size
            else b""
        ),
        "dtb": data[dtb_offset : dtb_offset + dtb_size] if dtb_size else b"",
        "id": data[576:608],
    }


def set_cmdline(header: bytearray, cmdline: str) -> None:
    encoded = cmdline.encode("ascii")
    if len(encoded) >= 1536:
        raise SystemExit(f"boot cmdline is too long: {len(encoded)} bytes")
    encoded += b"\0"
    header[64:576] = b"\0" * 512
    header[608:1632] = b"\0" * 1024
    header[64 : 64 + min(len(encoded), 512)] = encoded[:512]
    if len(encoded) > 512:
        extra = encoded[512:]
        header[608 : 608 + len(extra)] = extra


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch an audited A52 GKI 5.10 boot image for the fw_devlink isolation test"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--append-cmdline", default="fw_devlink=off")
    args = parser.parse_args()

    original_data = args.input.read_bytes()
    original = parse_boot(original_data)

    tokens = original["cmdline"].split()
    append_tokens = args.append_cmdline.split()
    for token in append_tokens:
        key = token.split("=", 1)[0]
        tokens = [item for item in tokens if item.split("=", 1)[0] != key]
        tokens.append(token)
    new_cmdline = " ".join(tokens)

    output = bytearray(original_data)
    set_cmdline(output, new_cmdline)

    footer_offset = int(original["footer_offset"])
    output[footer_offset : footer_offset + len(SAMSUNG_FOOTER)] = SAMSUNG_FOOTER

    rebuilt_data = bytes(output)
    rebuilt = parse_boot(rebuilt_data)

    invariants = {
        "partition_size_preserved": len(rebuilt_data) == len(original_data),
        "header_version_preserved": rebuilt["header_version"] == original["header_version"],
        "page_size_preserved": rebuilt["page_size"] == original["page_size"],
        "board_preserved": rebuilt["board"] == original["board"],
        "kernel_preserved": sha256(rebuilt["kernel"]) == sha256(original["kernel"]),
        "ramdisk_preserved": sha256(rebuilt["ramdisk"]) == sha256(original["ramdisk"]),
        "second_preserved": sha256(rebuilt["second"]) == sha256(original["second"]),
        "recovery_dtbo_preserved": sha256(rebuilt["recovery_dtbo"]) == sha256(original["recovery_dtbo"]),
        "dtb_preserved": sha256(rebuilt["dtb"]) == sha256(original["dtb"]),
        "boot_id_preserved": rebuilt["id"] == original["id"],
        "fw_devlink_off_present": "fw_devlink=off" in rebuilt["cmdline"].split(),
        "samsung_footer_present": rebuilt_data[
            footer_offset : footer_offset + len(SAMSUNG_FOOTER)
        ] == SAMSUNG_FOOTER,
    }
    failed = [name for name, passed in invariants.items() if not passed]
    if failed:
        raise SystemExit("post-repack audit failed: " + ", ".join(failed))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rebuilt_data)

    report = {
        "status": "a52-gki510-fw-devlink-off-boot-audited",
        "hardware_validated": False,
        "flashable_candidate": True,
        "purpose": "test whether Linux 5.10 fw_devlink supplier gating blocks the A52 display stack",
        "input_sha256": sha256(original_data),
        "output_sha256": sha256(rebuilt_data),
        "output_bytes": len(rebuilt_data),
        "original_cmdline": original["cmdline"],
        "patched_cmdline": rebuilt["cmdline"],
        "samsung_footer_hex": SAMSUNG_FOOTER.hex(),
        "samsung_footer_offset": footer_offset,
        "component_end": original["component_end"],
        "invariants": invariants,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
