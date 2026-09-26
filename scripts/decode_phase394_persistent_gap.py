#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

GAP_BYTES = 2 * 1024 * 1024
GAP_OFFSET_IN_11M = 9 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
COPIES = 3
MAGIC = 0x323933474F4C5047
VERSION = 2
COMMIT = 0x392C0DE5
CAPACITY = (GAP_BYTES - HEADER_BYTES) // (RECORD_BYTES * COPIES)
COPY_BYTES = CAPACITY * RECORD_BYTES
EARLY_OFFSET = 512
EARLY_SLOTS = 6
EARLY_BYTES = 128
EARLY_MAGIC = 0x34393345524C5931
EARLY_COMMIT = 0x394C0DE5


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def choose_region(data: bytes) -> tuple[bytes, int]:
    if len(data) == GAP_BYTES:
        return data, 0
    if len(data) >= GAP_OFFSET_IN_11M + GAP_BYTES:
        return data[GAP_OFFSET_IN_11M:GAP_OFFSET_IN_11M + GAP_BYTES], GAP_OFFSET_IN_11M
    raise SystemExit(f"expected 2 MiB gap dump or 11 MiB reserved dump, got {len(data)} bytes")


def majority_bytes(a: bytes, b: bytes, c: bytes) -> bytes:
    return bytes((x & y) | (x & z) | (y & z) for x, y, z in zip(a, b, c))


def parse_record(rec: bytes) -> dict | None:
    if len(rec) != RECORD_BYTES:
        return None
    rseq, ts_ns, cpu, length, _reserved = struct.unpack_from("<QQHHI", rec, 0)
    stored_crc, commit = struct.unpack_from("<II", rec, 120)
    if commit != COMMIT or length > 96:
        return None
    if crc32c(rec[:120]) != stored_crc:
        return None
    text_raw = rec[24:120]
    msg = text_raw[:length].split(b"\x00", 1)[0].decode("utf-8", "replace")
    return {"seq": rseq, "ts_ns": ts_ns, "time_ms": ts_ns / 1_000_000.0,
            "cpu": cpu, "text": msg, "crc32c": stored_crc}


def parse_early(rec: bytes, index: int) -> dict | None:
    if len(rec) != EARLY_BYTES:
        return None
    (magic, ts_ns, r48_seq, j64, frontier_ns,
     target_ms, cpu, irq_index, retained,
     pseq, dropped, comm_raw, stored_crc, commit) = struct.unpack_from(
        "<QQQQQIIIIQQ16sII", rec, 0)
    if magic != EARLY_MAGIC or commit != EARLY_COMMIT:
        return None
    if crc32c(rec[:88]) != stored_crc:
        return None
    comm = comm_raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
    return {
        "slot": index, "target_ms": target_ms, "time_ms": ts_ns / 1_000_000.0,
        "r48_seq": r48_seq, "jiffies64": j64, "frontier_ns": frontier_ns,
        "cpu": cpu, "irq_sideband_index": irq_index, "retained": retained,
        "persistent_seq": pseq, "persistent_dropped": dropped, "comm": comm,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Phase394 persistent gap and fixed early lane")
    ap.add_argument("dump", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--text-out", type=Path)
    args = ap.parse_args()
    raw = args.dump.read_bytes()
    data, file_offset = choose_region(raw)
    hdr = struct.unpack_from("<QIIIIQQQQQ", data, 0)
    magic, version, record_bytes, capacity, _rsv, boot_id, write_seq, write_seq_inv, dropped, dropped_inv = hdr
    header_valid = magic == MAGIC and version == VERSION and record_bytes == RECORD_BYTES and capacity == CAPACITY

    early = []
    for i in range(EARLY_SLOTS):
        off = EARLY_OFFSET + i * EARLY_BYTES
        row = parse_early(data[off:off + EARLY_BYTES], i)
        if row:
            early.append(row)

    records_by_seq: dict[int, dict] = {}
    source_counts = {"majority": 0, "copy0": 0, "copy1": 0, "copy2": 0, "or": 0, "and": 0}
    for slot in range(CAPACITY):
        recs = []
        for copy in range(COPIES):
            off = HEADER_BYTES + copy * COPY_BYTES + slot * RECORD_BYTES
            recs.append(data[off:off + RECORD_BYTES])
        candidates = [
            ("majority", majority_bytes(recs[0], recs[1], recs[2])),
            ("copy0", recs[0]), ("copy1", recs[1]), ("copy2", recs[2]),
            ("or", bytes(x | y | z for x, y, z in zip(*recs))),
            ("and", bytes(x & y & z for x, y, z in zip(*recs))),
        ]
        for name, candidate in candidates:
            parsed = parse_record(candidate)
            if parsed is None:
                continue
            if ((parsed["seq"] - 1) % CAPACITY) != slot:
                continue
            existing = records_by_seq.get(parsed["seq"])
            if existing is None or name == "majority":
                parsed["recovery_source"] = name
                records_by_seq[parsed["seq"]] = parsed
                source_counts[name] += 1
            break

    records = [records_by_seq[k] for k in sorted(records_by_seq)]
    if header_valid and write_seq:
        first = max(1, write_seq - CAPACITY + 1)
        records = [r for r in records if first <= r["seq"] <= write_seq]

    summary = {
        "input_bytes": len(raw), "persistent_gap_file_offset": file_offset,
        "header_valid": header_valid, "boot_id": boot_id, "write_seq": write_seq,
        "records_recovered": len(records), "early_slots_recovered": len(early),
        "recovery_sources": source_counts,
        "write_seq_pair_valid": ((write_seq ^ write_seq_inv) & ((1 << 64) - 1)) == ((1 << 64) - 1),
        "dropped": dropped,
        "dropped_pair_valid": ((dropped ^ dropped_inv) & ((1 << 64) - 1)) == ((1 << 64) - 1),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("=== Phase394 fixed early lane ===")
    for r in early:
        print(f'EARLY slot={r["slot"]} target={r["target_ms"]:5d} actual={r["time_ms"]:10.3f}ms '
              f'r48={r["r48_seq"]} irqidx={r["irq_sideband_index"]} pseq={r["persistent_seq"]} '
              f'retained={r["retained"]} cpu={r["cpu"]} comm={r["comm"]}')
    print("=== Circular persistent records ===")
    for r in records:
        print(f'{r["time_ms"]:12.3f} ms seq={r["seq"]:8d} cpu={r["cpu"]:2d} '
              f'src={r["recovery_source"]:8s} {r["text"]}')

    if args.text_out:
        lines = ["=== Phase394 fixed early lane ===\n"]
        lines += [f'EARLY slot={r["slot"]} target={r["target_ms"]} actual_ms={r["time_ms"]:.3f} '
                  f'r48={r["r48_seq"]} irqidx={r["irq_sideband_index"]} pseq={r["persistent_seq"]} '
                  f'retained={r["retained"]} cpu={r["cpu"]} comm={r["comm"]}\n' for r in early]
        lines += ["=== Circular persistent records ===\n"]
        lines += [f'{r["time_ms"]:12.3f} ms seq={r["seq"]:8d} cpu={r["cpu"]:2d} src={r["recovery_source"]:8s} {r["text"]}\n' for r in records]
        args.text_out.write_text("".join(lines), encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps({"summary": summary, "early": early, "records": records}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
