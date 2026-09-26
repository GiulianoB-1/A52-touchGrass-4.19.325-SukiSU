#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import struct
from pathlib import Path

REGION_BYTES = 2 * 1024 * 1024
PAGE_BYTES = 4096
PAGE_COUNT = REGION_BYTES // PAGE_BYTES
HEADER_BYTES = 64
RECORD_BYTES = 32
MAGIC = 0x3838544655323541
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


def decode_page(page: bytes, index: int, abs_offset: int) -> dict | None:
    if len(page) != PAGE_BYTES:
        return None

    (
        magic, version, header_bytes, boot_id, page_seq,
        first_record_seq, record_count, dropped, reserved0,
        payload_bytes, flags,
    ) = struct.unpack_from("<QIIQQIIQQII", page, 0)

    stored_crc = struct.unpack_from("<I", page, PAGE_BYTES - 4)[0]
    calc_crc = crc32c(page[:-4])

    if magic != MAGIC or version != VERSION or header_bytes != HEADER_BYTES:
        return None

    valid = (
        stored_crc == calc_crc
        and record_count <= 125
        and payload_bytes == record_count * RECORD_BYTES
    )

    records = []
    if valid:
        off = HEADER_BYTES
        for _ in range(record_count):
            ts_ns, seq, event, cpu, a0, a1, a2, a3 = struct.unpack_from(
                "<QIHHIIII", page, off
            )
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
            })
            off += RECORD_BYTES

    return {
        "index": index,
        "offset": abs_offset,
        "boot_id": boot_id,
        "page_seq": page_seq,
        "first_record_seq": first_record_seq,
        "record_count": record_count,
        "dropped": dropped,
        "payload_bytes": payload_bytes,
        "flags": flags,
        "stored_crc32c": stored_crc,
        "calculated_crc32c": calc_crc,
        "valid": valid,
        "records": records,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Decode Phase388 A52 binary flight-recorder pages from Samsung debug partition"
    )
    ap.add_argument("debug_bin", type=Path)
    ap.add_argument("--boot-id", type=int)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--csv", type=Path)
    args = ap.parse_args()

    data = args.debug_bin.read_bytes()
    if len(data) < REGION_BYTES:
        raise SystemExit(f"file too small: {len(data)} bytes")

    base = len(data) - REGION_BYTES
    pages = []
    invalid_crc = 0

    for i in range(PAGE_COUNT):
        off = base + i * PAGE_BYTES
        rec = decode_page(data[off:off + PAGE_BYTES], i, off)
        if rec is None:
            continue
        if not rec["valid"]:
            invalid_crc += 1
            continue
        pages.append(rec)

    pages.sort(key=lambda p: p["page_seq"])
    boot_ids = sorted({p["boot_id"] for p in pages})
    chosen = args.boot_id if args.boot_id is not None else (
        boot_ids[-1] if boot_ids else None
    )

    selected = [p for p in pages if chosen is None or p["boot_id"] == chosen]
    records = [r for p in selected for r in p["records"]]
    records.sort(key=lambda r: (r["seq"], r["ts_ns"]))

    summary = {
        "file_bytes": len(data),
        "region_base": base,
        "valid_pages": len(pages),
        "invalid_crc_pages_with_phase388_magic": invalid_crc,
        "boot_ids": boot_ids,
        "selected_boot_id": chosen,
        "selected_pages": len(selected),
        "selected_records": len(records),
        "page_seq_min": min((p["page_seq"] for p in selected), default=None),
        "page_seq_max": max((p["page_seq"] for p in selected), default=None),
        "record_seq_min": min((r["seq"] for r in records), default=None),
        "record_seq_max": max((r["seq"] for r in records), default=None),
        "dropped_max": max((p["dropped"] for p in selected), default=0),
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    for r in records:
        print(
            f'{r["time_ms"]:12.3f} ms  seq={r["seq"]:8d} '
            f'cpu={r["cpu"]:2d}  {r["event_name"]:<22} '
            f'a0=0x{r["arg0"]:08x} a1=0x{r["arg1"]:08x} '
            f'a2=0x{r["arg2"]:08x} a3=0x{r["arg3"]:08x}'
        )

    if args.json:
        args.json.write_text(json.dumps({
            "summary": summary,
            "pages": [{k: v for k, v in p.items() if k != "records"} for p in selected],
            "records": records,
        }, indent=2, sort_keys=True) + "\n")

    if args.csv:
        with args.csv.open("w", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=[
                "ts_ns", "time_ms", "seq", "event", "event_name", "cpu",
                "arg0", "arg1", "arg2", "arg3",
            ])
            w.writeheader()
            w.writerows(records)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
