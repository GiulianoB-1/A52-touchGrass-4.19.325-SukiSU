#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

RAW_BYTES = 0x100000
BASE = 0xFC000
TOTAL = 0x3800
COPY = TOTAL // 2
SLOT = 64
SLOTS = COPY // SLOT
MAGIC = 0x3134335244353241
COMMIT = 0x341C0DE5
FMT = "<QQQQQIIIIII"


def parse(buf: bytes):
    if len(buf) != SLOT:
        return None
    magic, ns, seq, jiffies, reserved, version, index, event, cpu, tick, commit = struct.unpack(FMT, buf)
    if magic != MAGIC or commit != COMMIT:
        return None
    return {
        "index": index,
        "time_ms": ns / 1_000_000.0,
        "monotonic_ns": ns,
        "r48_sequence": seq,
        "jiffies64": jiffies,
        "event": event,
        "cpu": cpu,
        "tick": tick,
        "pid": (reserved >> 32) & 0xffffffff,
        "preempt_count": (reserved >> 1) & 0x7fffffff,
        "irqs_disabled": bool(reserved & 1),
        "version": version,
    }


def choose(a: bytes, b: bytes):
    candidates = [
        ("or", bytes(x | y for x, y in zip(a, b))),
        ("copy0", a), ("copy1", b),
        ("and", bytes(x & y for x, y in zip(a, b))),
    ]
    for source, buf in candidates:
        row = parse(buf)
        if row is not None:
            row["source"] = source
            return row
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode Phase398 dense R341 hardirq sideband")
    ap.add_argument("raw", type=Path)
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    data = ns.raw.read_bytes()
    if len(data) < RAW_BYTES:
        raise SystemExit(f"expected >=1 MiB raw ramoops, got {len(data)}")
    data = data[:RAW_BYTES]

    rows = {}
    for slot in range(SLOTS):
        a = data[BASE + slot*SLOT:BASE + (slot+1)*SLOT]
        b = data[BASE + COPY + slot*SLOT:BASE + COPY + (slot+1)*SLOT]
        row = choose(a, b)
        if row is None or row["index"] % SLOTS != slot:
            continue
        rows[row["index"]] = row

    ordered = [rows[k] for k in sorted(rows)]
    summary = {
        "records": len(ordered),
        "slots_per_copy": SLOTS,
        "first_index": ordered[0]["index"] if ordered else None,
        "last_index": ordered[-1]["index"] if ordered else None,
        "first_time_ms": ordered[0]["time_ms"] if ordered else None,
        "last_time_ms": ordered[-1]["time_ms"] if ordered else None,
    }
    print(json.dumps(summary, indent=2))
    for r in ordered:
        print(f'{r["time_ms"]:12.3f} idx={r["index"]:4d} ev={r["event"]} cpu={r["cpu"]} '
              f'tick={r["tick"]:3d} pid={r["pid"]:5d} pc={r["preempt_count"]:x} '
              f'irqoff={int(r["irqs_disabled"])} src={r["source"]}')

    if ns.json:
        ns.json.write_text(json.dumps({"summary": summary, "records": ordered}, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
