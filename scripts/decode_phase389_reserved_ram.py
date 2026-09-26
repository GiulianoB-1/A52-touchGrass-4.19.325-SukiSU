#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

PHYS = 0xB1400000
TOTAL_BYTES = 7 * 1024 * 1024
HEADER_BYTES = 4096
TEXT_OFF = HEADER_BYTES
TEXT_BYTES = 4 * 1024 * 1024
TRACE_OFF = TEXT_OFF + TEXT_BYTES
TRACE_BYTES = TOTAL_BYTES - TRACE_OFF
REC_BYTES = 36
MAGIC = 0x393833524D415241
VERSION = 1

EVENTS = {
    0x1000: "DSI_WAIT_ENTER",
    0x1001: "DSI_WAIT_EXIT",
    0x1002: "DSI_FALLBACK_STATUS",
    0x1003: "DSI_FALLBACK_BRANCH",
    0x1004: "DSI_ISR",
    0x2000: "DRM_MODESET_FAIL",
    0x3000: "UFS_SAMPLE",
    0x4000: "USB_STAGE",
    0x5000: "BLK_FREEZE",
}


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def valid_pair(value: int, inv: int, bits: int = 64) -> bool:
    mask = (1 << bits) - 1
    return ((value ^ inv) & mask) == mask


def ordered_ring(data: bytes, total_written: int) -> bytes:
    size = len(data)
    if total_written <= size:
        return data[:total_written]
    pos = total_written % size
    return data[pos:] + data[:pos]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("reserved_bin", type=Path,
                    help="7 MiB dump beginning at physical 0xB1400000")
    ap.add_argument("--text-out", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    data = args.reserved_bin.read_bytes()
    if len(data) != TOTAL_BYTES:
        raise SystemExit(f"expected {TOTAL_BYTES} bytes, got {len(data)}")

    fields = struct.unpack_from("<QIIQQQQQQQIIQQ", data, 0)
    (
        magic, version, header_bytes, phys, bytes_total, boot_id,
        text_pos, text_pos_inv, trace_pos, trace_pos_inv,
        trace_seq, trace_seq_inv, dropped, dropped_inv,
    ) = fields

    summary = {
        "magic": f"0x{magic:016x}",
        "version": version,
        "header_bytes": header_bytes,
        "phys": f"0x{phys:016x}",
        "bytes": bytes_total,
        "boot_id": boot_id,
        "text_pos": text_pos,
        "text_pos_pair_valid": valid_pair(text_pos, text_pos_inv),
        "trace_pos": trace_pos,
        "trace_pos_pair_valid": valid_pair(trace_pos, trace_pos_inv),
        "trace_seq": trace_seq,
        "trace_seq_pair_valid": valid_pair(trace_seq, trace_seq_inv, 32),
        "dropped": dropped,
        "dropped_pair_valid": valid_pair(dropped, dropped_inv),
    }

    if magic != MAGIC or version != VERSION or header_bytes != HEADER_BYTES:
        print(json.dumps(summary, indent=2))
        raise SystemExit("Phase389 header not present/valid")

    text_ring = data[TEXT_OFF:TEXT_OFF + TEXT_BYTES]
    text_blob = ordered_ring(text_ring, text_pos)
    if args.text_out:
        args.text_out.write_bytes(text_blob)

    trace_blob = data[TRACE_OFF:TRACE_OFF + TRACE_BYTES]
    slots = TRACE_BYTES // REC_BYTES
    count = min(trace_pos, slots)
    start = 0 if trace_pos <= slots else trace_pos % slots

    records = []
    bad_crc = 0
    for i in range(count):
        slot = (start + i) % slots
        off = slot * REC_BYTES
        raw = trace_blob[off:off + REC_BYTES]
        if len(raw) != REC_BYTES:
            continue
        ts_ns, seq, event, cpu, a0, a1, a2, a3, stored_crc = \
            struct.unpack("<QIHHIIIII", raw)
        calc = crc32c(raw[:-4])
        if stored_crc != calc:
            bad_crc += 1
            continue
        records.append({
            "ts_ns": ts_ns,
            "time_ms": ts_ns / 1_000_000.0,
            "seq": seq,
            "event": event,
            "event_name": EVENTS.get(event, f"EVENT_0x{event:04x}"),
            "cpu": cpu,
            "arg0": a0,
            "arg1": a1,
            "arg2": a2,
            "arg3": a3,
            "crc32c": stored_crc,
        })

    summary.update({
        "text_bytes_recovered": len(text_blob),
        "trace_slots": slots,
        "trace_records_expected": count,
        "trace_records_crc_valid": len(records),
        "trace_records_bad_crc": bad_crc,
        "record_seq_min": min((r["seq"] for r in records), default=None),
        "record_seq_max": max((r["seq"] for r in records), default=None),
    })

    print(json.dumps(summary, indent=2, sort_keys=True))
    for r in records:
        print(
            f'{r["time_ms"]:12.3f} ms seq={r["seq"]:8d} '
            f'cpu={r["cpu"]:2d} {r["event_name"]:<22} '
            f'a0=0x{r["arg0"]:08x} a1=0x{r["arg1"]:08x} '
            f'a2=0x{r["arg2"]:08x} a3=0x{r["arg3"]:08x}'
        )

    if args.json:
        args.json.write_text(json.dumps({
            "summary": summary,
            "records": records,
        }, indent=2, sort_keys=True) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
