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

def parse_slot(buf: bytes) -> dict:
    if len(buf) != SLOT:
        return {"valid": False}
    vals = struct.unpack("<QQQQQIIIIII", buf)
    magic, ns, seq, j64, reserved, ver, idx, event, cpu, tick, commit = vals
    interrupted_pid = (reserved >> 32) & 0xffffffff
    preempt_count = (reserved >> 1) & 0x7fffffff
    irqs_off = reserved & 1
    return {
        "valid": magic == MAGIC and commit == COMMIT and ver == 1,
        "magic": f"0x{magic:016x}",
        "monotonic_ns": ns,
        "seconds": ns / 1e9,
        "r48_sequence": seq,
        "jiffies64": j64,
        "interrupted_pid": interrupted_pid,
        "preempt_count": preempt_count,
        "irqs_off": irqs_off,
        "index": idx,
        "event": event,
        "cpu": cpu,
        "tick": tick,
        "commit": f"0x{commit:08x}",
    }

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Decode Phase393 10 ms hardirq sideband from frozen 1 MiB ramoops")
    ap.add_argument("image", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    data = args.image.read_bytes()
    if len(data) < RAW_BYTES:
        raise SystemExit(f"expected >=1 MiB image, got {len(data)} bytes")
    data = data[:RAW_BYTES]

    rows = []
    for slot in range(SLOTS):
        a = data[BASE + slot*SLOT:BASE + (slot+1)*SLOT]
        b = data[BASE + COPY + slot*SLOT:BASE + COPY + (slot+1)*SLOT]
        pa, pb = parse_slot(a), parse_slot(b)
        merged = bytes(x | y for x, y in zip(a, b))
        pm = parse_slot(merged)
        best = pm if pm["valid"] else (pa if pa["valid"] else pb)
        if not best.get("valid"):
            continue
        best = dict(best)
        best["slot"] = slot
        best["source"] = "or-merged" if pm["valid"] else ("copy0" if pa["valid"] else "copy1")
        rows.append(best)

    # The sideband is circular by physical slot but carries a monotonically
    # increasing logical index. Sort by that index to recover chronology.
    rows.sort(key=lambda r: r["index"])
    summary = {
        "valid_records": len(rows),
        "first_index": rows[0]["index"] if rows else None,
        "last_index": rows[-1]["index"] if rows else None,
        "first_seconds": rows[0]["seconds"] if rows else None,
        "last_seconds": rows[-1]["seconds"] if rows else None,
        "records": rows,
    }

    print(json.dumps(summary, indent=2, sort_keys=True))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
