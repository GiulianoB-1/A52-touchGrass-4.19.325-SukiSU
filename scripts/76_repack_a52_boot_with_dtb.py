#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import struct
from pathlib import Path


def load_boot_module():
    path = Path(__file__).with_name("38_repack_a52_p1_boot.py")
    spec = importlib.util.spec_from_file_location("a52_boot_repack", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_dtb(data: bytes) -> None:
    if len(data) < 40:
        raise SystemExit("replacement DTB is too small")
    magic, total_size = struct.unpack_from(">II", data, 0)
    if magic != 0xD00DFEED:
        raise SystemExit("replacement DTB has invalid FDT magic")
    if total_size != len(data):
        raise SystemExit(
            f"replacement DTB total-size mismatch: header={total_size}, file={len(data)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repack an A52 Android boot v2 image with a replacement kernel and DTB"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    boot = load_boot_module()
    source = args.source.read_bytes()
    new_kernel = args.kernel.read_bytes()
    new_dtb = args.dtb.read_bytes()
    original = boot.parse_boot(source)

    if not new_kernel.startswith(b"\x1f\x8b"):
        raise SystemExit("replacement kernel is not gzip-compressed Image.gz")
    validate_dtb(new_dtb)
    if not original["dtb"]:
        raise SystemExit("source boot image has no DTB")
    if sha256(new_dtb) == sha256(original["dtb"]):
        raise SystemExit("replacement DTB is byte-identical to source DTB")

    page_size = original["page_size"]
    header = bytearray(original["header_page"])
    struct.pack_into("<I", header, 8, len(new_kernel))
    struct.pack_into("<I", header, 1648, len(new_dtb))

    new_kernel_offset = page_size
    new_ramdisk_offset = new_kernel_offset + boot.align(len(new_kernel), page_size)
    new_second_offset = new_ramdisk_offset + boot.align(original["ramdisk_size"], page_size)
    new_recovery_offset = new_second_offset + boot.align(original["second_size"], page_size)
    if original["recovery_dtbo_size"]:
        struct.pack_into("<Q", header, 1636, new_recovery_offset)

    new_id = boot.calculate_boot_id(
        [
            (new_kernel, len(new_kernel)),
            (original["ramdisk"], original["ramdisk_size"]),
            (original["second"], original["second_size"]),
            (original["recovery_dtbo"], original["recovery_dtbo_size"]),
            (new_dtb, len(new_dtb)),
        ]
    )
    header[576:608] = new_id

    output = bytearray(header)

    def add_component(payload: bytes) -> None:
        output.extend(payload)
        output.extend(b"\0" * (boot.align(len(output), page_size) - len(output)))

    add_component(new_kernel)
    add_component(original["ramdisk"])
    add_component(original["second"])
    add_component(original["recovery_dtbo"])
    add_component(new_dtb)

    if len(output) > len(source):
        raise SystemExit(
            f"repacked image exceeds source partition size: {len(output)} > {len(source)}"
        )
    output.extend(b"\0" * (len(source) - len(output)))

    rebuilt = boot.parse_boot(bytes(output))
    invariants = {
        "partition_size_preserved": len(output) == len(source),
        "header_version_preserved": rebuilt["header_version"] == original["header_version"],
        "page_size_preserved": rebuilt["page_size"] == original["page_size"],
        "board_preserved": rebuilt["board"] == original["board"],
        "cmdline_preserved": rebuilt["cmdline"] == original["cmdline"],
        "kernel_addr_preserved": rebuilt["kernel_addr"] == original["kernel_addr"],
        "ramdisk_addr_preserved": rebuilt["ramdisk_addr"] == original["ramdisk_addr"],
        "tags_addr_preserved": rebuilt["tags_addr"] == original["tags_addr"],
        "dtb_addr_preserved": rebuilt["dtb_addr"] == original["dtb_addr"],
        "ramdisk_preserved": sha256(rebuilt["ramdisk"]) == sha256(original["ramdisk"]),
        "second_preserved": sha256(rebuilt["second"]) == sha256(original["second"]),
        "recovery_dtbo_preserved": sha256(rebuilt["recovery_dtbo"])
        == sha256(original["recovery_dtbo"]),
        "replacement_kernel_used": sha256(rebuilt["kernel"]) == sha256(new_kernel),
        "replacement_dtb_used": sha256(rebuilt["dtb"]) == sha256(new_dtb),
        "source_dtb_changed": sha256(rebuilt["dtb"]) != sha256(original["dtb"]),
        "boot_id_recalculated": rebuilt["id"] == new_id,
    }
    failed = [name for name, passed in invariants.items() if not passed]
    if failed:
        raise SystemExit("repack audit failed: " + ", ".join(failed))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)

    report = {
        "status": "kernel-and-dtb-repacked-audited",
        "hardware_validated": False,
        "flashable_candidate": True,
        "source_sha256": sha256(source),
        "output_sha256": sha256(output),
        "source_bytes": len(source),
        "output_bytes": len(output),
        "replacement_kernel_sha256": sha256(new_kernel),
        "source_dtb_sha256": sha256(original["dtb"]),
        "replacement_dtb_sha256": sha256(new_dtb),
        "source_dtb_size": original["dtb_size"],
        "replacement_dtb_size": len(new_dtb),
        "ramdisk_sha256": sha256(original["ramdisk"]),
        "original_boot_id": original["id"][:20].hex(),
        "new_boot_id": new_id[:20].hex(),
        "header_version": original["header_version"],
        "page_size": page_size,
        "board": original["board"],
        "cmdline": original["cmdline"],
        "dtb_addr": original["dtb_addr"],
        "invariants": invariants,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
