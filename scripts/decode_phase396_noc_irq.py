#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

RAW_BYTES = 0x100000
BASE = 0xFF800
TOTAL = 0x600
COPY = TOTAL // 2
SLOT = 64
SLOTS = COPY // SLOT
MAGIC = 0x363933434F4E4951
COMMIT = 0x396C0DE5
FMT = "<QQQQIIIIII8s"


def parse_slot(buf: bytes) -> dict | None:
    if len(buf) != SLOT:
        return None
    (magic, ns, r48_seq, hwirq, index, irq, cpu,
     flags, cpu_count, commit, _reserved) = struct.unpack(FMT, buf)
    if magic != MAGIC or commit != COMMIT or index >= SLOTS:
        return None
    return {
        "index": index,
        "monotonic_ns": ns,
        "time_ms": ns / 1_000_000.0,
        "r48_sequence": r48_seq,
        "hwirq": hwirq,
        "irq": irq,
        "cpu": cpu,
        "flags": flags,
        "irq_disabled": bool(flags & (1 << 0)),
        "irq_masked": bool(flags & (1 << 1)),
        "irq_inprogress": bool(flags & (1 << 2)),
        "irq_activated": bool(flags & (1 << 3)),
        "irq_started": bool(flags & (1 << 4)),
        "cpu_count": cpu_count,
    }


def choose(a: bytes, b: bytes) -> tuple[dict | None, str]:
    pa = parse_slot(a)
    pb = parse_slot(b)
    merged_or = parse_slot(bytes(x | y for x, y in zip(a, b)))
    merged_and = parse_slot(bytes(x & y for x, y in zip(a, b)))
    for name, row in (("or", merged_or), ("copy0", pa), ("copy1", pb), ("and", merged_and)):
        if row is not None:
            return row, name
    return None, "none"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Decode Phase396 two-copy non-wrapping NoC IRQ hit lane")
    ap.add_argument("image", type=Path, help="raw 1 MiB ramoops image")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    data = args.image.read_bytes()
    if len(data) < RAW_BYTES:
        raise SystemExit(f"expected >=1 MiB raw ramoops image, got {len(data)} bytes")
    data = data[:RAW_BYTES]

    rows = []
    for slot in range(SLOTS):
        a = data[BASE + slot * SLOT:BASE + (slot + 1) * SLOT]
        b = data[BASE + COPY + slot * SLOT:BASE + COPY + (slot + 1) * SLOT]
        row, source = choose(a, b)
        if row is None or row["index"] != slot:
            continue
        row["slot"] = slot
        row["source"] = source
        rows.append(row)

    rows.sort(key=lambda r: r["index"])
    summary = {
        "input_bytes": len(data),
        "lane_offset": BASE,
        "lane_bytes": TOTAL,
        "copy_bytes": COPY,
        "slot_bytes": SLOT,
        "slots": SLOTS,
        "records_recovered": len(rows),
        "first_index": rows[0]["index"] if rows else None,
        "last_index": rows[-1]["index"] if rows else None,
        "first_time_ms": rows[0]["time_ms"] if rows else None,
        "last_time_ms": rows[-1]["time_ms"] if rows else None,
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    for r in rows:
        print(
            f'{r["time_ms"]:12.3f} ms idx={r["index"]:2d} '
            f'irq={r["irq"]:4d} hwirq={r["hwirq"]:4d} cpu={r["cpu"]} '
            f'flags=0x{r["flags"]:02x} count={r["cpu_count"]} '
            f'r48={r["r48_sequence"]} src={r["source"]}'
        )

    if args.json:
        args.json.write_text(
            json.dumps({"summary": summary, "records": rows}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
