#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

GAPLOG_BYTES = 2 * 1024 * 1024
GAPLOG_OFFSET_IN_11M = 9 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
MAGIC = 0x323933474F4C5047
VERSION = 1
COMMIT = 0x392C0DE5
CAPACITY = (GAPLOG_BYTES - HEADER_BYTES) // RECORD_BYTES


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def choose_region(data: bytes) -> tuple[bytes, int]:
    if len(data) == GAPLOG_BYTES:
        return data, 0
    if len(data) >= GAPLOG_OFFSET_IN_11M + GAPLOG_BYTES:
        region = data[
            GAPLOG_OFFSET_IN_11M:
            GAPLOG_OFFSET_IN_11M + GAPLOG_BYTES
        ]
        return region, GAPLOG_OFFSET_IN_11M
    raise SystemExit(
        f"expected a 2 MiB persistent-gap dump or a larger reserved-RAM dump; got {len(data)} bytes"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path,
                    help="2 MiB Phase392 persistent-gap dump or 11 MiB Samsung reserved-RAM dump")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--text-out", type=Path)
    args = ap.parse_args()

    raw = args.dump.read_bytes()
    data, file_offset = choose_region(raw)

    (
        magic, version, record_bytes, capacity, reserved0,
        boot_id, write_seq, write_seq_inv, dropped, dropped_inv,
    ) = struct.unpack_from("<QIIIIQQQQQ", data, 0)

    summary = {
        "input_bytes": len(raw),
        "persistent_gap_file_offset": file_offset,
        "magic": f"0x{magic:016x}",
        "version": version,
        "record_bytes": record_bytes,
        "capacity": capacity,
        "boot_id": boot_id,
        "write_seq": write_seq,
        "write_seq_pair_valid": ((write_seq ^ write_seq_inv) & ((1 << 64) - 1)) == ((1 << 64) - 1),
        "dropped": dropped,
        "dropped_pair_valid": ((dropped ^ dropped_inv) & ((1 << 64) - 1)) == ((1 << 64) - 1),
    }

    if magic != MAGIC or version != VERSION:
        print(json.dumps(summary, indent=2, sort_keys=True))
        raise SystemExit("Phase392 header not present")
    if record_bytes != RECORD_BYTES or capacity != CAPACITY:
        print(json.dumps(summary, indent=2, sort_keys=True))
        raise SystemExit("Phase392 geometry mismatch")

    count = min(write_seq, CAPACITY)
    first_seq = write_seq - count + 1 if count else 0
    records = []
    bad_crc = 0
    bad_commit = 0

    for seq in range(first_seq, write_seq + 1):
        slot = (seq - 1) % CAPACITY
        off = HEADER_BYTES + slot * RECORD_BYTES
        rec = data[off:off + RECORD_BYTES]
        if len(rec) != RECORD_BYTES:
            continue

        rseq, ts_ns, cpu, length, _reserved = struct.unpack_from("<QQHHI", rec, 0)
        text_raw = rec[24:120]
        stored_crc, commit = struct.unpack_from("<II", rec, 120)

        if commit != COMMIT:
            bad_commit += 1
            continue
        calc = crc32c(rec[:120])
        if calc != stored_crc:
            bad_crc += 1
            continue
        if rseq != seq:
            continue

        length = min(length, len(text_raw))
        msg = text_raw[:length].split(b"\x00", 1)[0].decode("utf-8", "replace")
        records.append({
            "seq": rseq,
            "ts_ns": ts_ns,
            "time_ms": ts_ns / 1_000_000.0,
            "cpu": cpu,
            "text": msg,
            "crc32c": stored_crc,
        })

    summary.update({
        "records_expected": count,
        "records_crc_valid": len(records),
        "records_bad_crc": bad_crc,
        "records_bad_commit": bad_commit,
        "record_seq_min": records[0]["seq"] if records else None,
        "record_seq_max": records[-1]["seq"] if records else None,
        "time_ms_min": records[0]["time_ms"] if records else None,
        "time_ms_max": records[-1]["time_ms"] if records else None,
    })

    print(json.dumps(summary, indent=2, sort_keys=True))
    for r in records:
        print(f'{r["time_ms"]:12.3f} ms seq={r["seq"]:8d} cpu={r["cpu"]:2d} {r["text"]}')

    if args.text_out:
        args.text_out.write_text(
            "".join(
                f'{r["time_ms"]:12.3f} ms seq={r["seq"]:8d} cpu={r["cpu"]:2d} {r["text"]}\n'
                for r in records
            ),
            encoding="utf-8",
        )

    if args.json:
        args.json.write_text(
            json.dumps({"summary": summary, "records": records},
                       indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
