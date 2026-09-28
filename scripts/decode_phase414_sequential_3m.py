#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

DEBUG_OFFSET = 0x800000
DISK_BYTES = 2 * 1024 * 1024
RAM_OFFSET_IN_11M = 0xA00000
RAM_BYTES = 1 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
DISK_CAP = (DISK_BYTES - HEADER_BYTES) // RECORD_BYTES
RAM_CAP = (RAM_BYTES - HEADER_BYTES) // RECORD_BYTES
MAGIC = 0x3431344D33434553
HDR_COMMIT = 0x414B0071
REC_COMMIT = 0x414C0DE5
HEADER = struct.Struct("<QIIQQIIIIIIII")
RECORD = struct.Struct("<QQQIHH88sII")


def slice_disk(data: bytes) -> bytes:
    if len(data) == DISK_BYTES:
        return data
    if len(data) >= DEBUG_OFFSET + DISK_BYTES:
        return data[DEBUG_OFFSET:DEBUG_OFFSET + DISK_BYTES]
    raise SystemExit(f"debug input too small: {len(data)}")


def slice_ram(data: bytes) -> bytes:
    if len(data) == RAM_BYTES:
        return data
    if len(data) >= RAM_OFFSET_IN_11M + RAM_BYTES:
        return data[RAM_OFFSET_IN_11M:RAM_OFFSET_IN_11M + RAM_BYTES]
    raise SystemExit(f"reserved-RAM input too small: {len(data)}")


def parse_header(data: bytes) -> dict[str, int | bool]:
    if len(data) < HEADER_BYTES:
        raise ValueError("short header")
    (
        magic, version, phase, boot_id, global_seq,
        disk_count, ram_count, disk_capacity, ram_capacity,
        record_bytes, state, dropped, commit,
    ) = HEADER.unpack_from(data, 0)
    return {
        "magic": magic,
        "version": version,
        "phase": phase,
        "boot_id": boot_id,
        "global_seq": global_seq,
        "disk_count": disk_count,
        "ram_count": ram_count,
        "disk_capacity": disk_capacity,
        "ram_capacity": ram_capacity,
        "record_bytes": record_bytes,
        "state": state,
        "dropped": dropped,
        "commit": commit,
        "valid": (
            magic == MAGIC and version == 1 and phase == 414 and
            disk_capacity == DISK_CAP and ram_capacity == RAM_CAP and
            record_bytes == RECORD_BYTES and commit == HDR_COMMIT
        ),
    }


def parse_records(data: bytes, count: int, capacity: int, boot_id: int) -> list[dict]:
    out = []
    limit = min(count, capacity)
    for i in range(limit):
        off = HEADER_BYTES + i * RECORD_BYTES
        seq, ts_ns, rec_boot, phase, cpu, length, raw, commit, reserved = RECORD.unpack_from(data, off)
        if commit != REC_COMMIT or phase != 414 or rec_boot != boot_id:
            continue
        length = min(int(length), len(raw))
        text = raw[:length].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        out.append({
            "slot": i,
            "seq": seq,
            "ts_ns": ts_ns,
            "cpu": cpu,
            "text": text,
            "commit": f"0x{commit:08x}",
            "reserved": reserved,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", type=Path, required=True,
                    help="10 MiB Samsung debug.bin or extracted 2 MiB Phase414 region")
    ap.add_argument("--reserved", type=Path, required=True,
                    help="11 MiB Samsung reserved RAM dump or extracted final 1 MiB")
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    disk = slice_disk(ns.debug.read_bytes())
    ram = slice_ram(ns.reserved.read_bytes())

    dh = parse_header(disk)
    rh = parse_header(ram)
    disk_records = parse_records(disk, int(dh["disk_count"]), DISK_CAP, int(dh["boot_id"])) if dh["valid"] else []
    ram_records = parse_records(ram, int(rh["ram_count"]), RAM_CAP, int(rh["boot_id"])) if rh["valid"] else []

    same_boot = bool(dh["valid"] and rh["valid"] and dh["boot_id"] == rh["boot_id"])
    records = sorted(disk_records + (ram_records if same_boot else []), key=lambda r: r["seq"])

    gaps = []
    for a, b in zip(records, records[1:]):
        if b["seq"] != a["seq"] + 1:
            gaps.append([a["seq"], b["seq"]])

    result = {
        "disk_header": dh,
        "ram_header": rh,
        "same_boot": same_boot,
        "disk_valid_records": len(disk_records),
        "ram_valid_records": len(ram_records),
        "combined_records": len(records),
        "sequence_gaps": gaps,
        "records": records,
    }

    print(
        f'Phase414 disk_valid={dh["valid"]} ram_valid={rh["valid"]} '
        f'same_boot={same_boot} boot={dh["boot_id"] if dh["valid"] else 0}'
    )
    print(
        f'disk_count={dh["disk_count"]} valid={len(disk_records)}/{DISK_CAP} '
        f'ram_count={rh["ram_count"]} valid={len(ram_records)}/{RAM_CAP} '
        f'combined={len(records)} state={dh["state"]}/{rh["state"]} '
        f'dropped={max(int(dh["dropped"]), int(rh["dropped"]))}'
    )
    for r in records:
        print(
            f'{r["seq"]:06d} {r["ts_ns"]/1e9:12.6f}s '
            f'cpu={r["cpu"]} {r["text"]}'
        )
    if gaps:
        print("sequence_gaps=" + json.dumps(gaps))

    if ns.json:
        ns.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    return 0 if dh["valid"] and rh["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
