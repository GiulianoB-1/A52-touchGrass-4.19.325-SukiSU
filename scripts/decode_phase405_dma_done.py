#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

GAP_OFFSET_IN_11M = 9 * 1024 * 1024
GAP_BYTES = 2 * 1024 * 1024
OFF0 = 0x500
OFF1 = 0xA80
SLOT_BYTES = 128
SLOTS = 11
MAGIC = 0x3530344953445841
COMMIT = 0x405C0DE5

STAGES = [
    "S00 exact F0 matched",
    "S03 before IRQ arm",
    "S04 after IRQ arm",
    "S05 DMA programmed",
    "S06 after SW trigger",
    "387D wait entry",
    "387I ISR entry",
    "387D wait return",
    "387D fallback status",
    "S08 completion result",
    "S09 final HW snapshot",
]


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def choose_gap(raw: bytes) -> bytes:
    if len(raw) == GAP_BYTES:
        return raw
    if len(raw) >= GAP_OFFSET_IN_11M + GAP_BYTES:
        return raw[GAP_OFFSET_IN_11M:GAP_OFFSET_IN_11M + GAP_BYTES]
    raise SystemExit(f"expected 2 MiB gap or >=11 MiB reserved dump; got {len(raw)} bytes")


def parse_record(rec: bytes) -> dict | None:
    if len(rec) != SLOT_BYTES:
        return None
    magic, ns, seq, stage, cpu, length, _rsv = struct.unpack_from("<QQIIHHI", rec, 0)
    text_raw = rec[32:120]
    stored_crc, commit = struct.unpack_from("<II", rec, 120)
    if magic != MAGIC or commit != COMMIT or stage >= SLOTS or length > len(text_raw):
        return None
    if crc32c(rec[:120]) != stored_crc:
        return None
    text = text_raw[:length].split(b"\0", 1)[0].decode("utf-8", "replace")
    return {
        "seq": seq,
        "stage": stage,
        "stage_name": STAGES[stage],
        "time_ms": ns / 1_000_000.0,
        "cpu": cpu,
        "text": text,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Phase405 fixed DSI DMA_DONE records")
    ap.add_argument("dump", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    gap = choose_gap(args.dump.read_bytes())
    rows = []
    for stage in range(SLOTS):
        a0 = OFF0 + stage * SLOT_BYTES
        a1 = OFF1 + stage * SLOT_BYTES
        c0 = parse_record(gap[a0:a0 + SLOT_BYTES])
        c1 = parse_record(gap[a1:a1 + SLOT_BYTES])
        chosen = c0 or c1
        if chosen:
            row = dict(chosen)
            row["copies_valid"] = int(c0 is not None) + int(c1 is not None)
            rows.append(row)

    rows.sort(key=lambda r: r["stage"])
    print(json.dumps({"records": len(rows), "highest_stage": rows[-1]["stage"] if rows else None}, indent=2))
    for r in rows:
        print(
            f'{r["stage"]:02d} {r["stage_name"]:<24} '
            f't={r["time_ms"]:10.3f}ms seq={r["seq"]:3d} cpu={r["cpu"]} '
            f'copies={r["copies_valid"]} {r["text"]}'
        )

    if args.json:
        args.json.write_text(json.dumps({"records": rows}, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
