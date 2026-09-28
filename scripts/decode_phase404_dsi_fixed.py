#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

GAP_OFFSET_IN_11M = 9 * 1024 * 1024
P404_OFF0 = 0x800
P404_OFF1 = 0xc00
SLOT_BYTES = 64
SLOTS = 16
MAGIC = 0x3430344953445841
COMMIT = 0x404C0DE5

EVENTS = {
    1: "INIT",
    2: "CALL",
    3: "TYPE29",
    4: "FETCH",
    5: "F0_PREFIX",
    6: "F0_EXACT",
    7: "ARMED",
    8: "P345_BEGIN",
    9: "P345_END",
}


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def choose_gap(raw: bytes) -> bytes:
    if len(raw) == 2 * 1024 * 1024:
        return raw
    if len(raw) >= GAP_OFFSET_IN_11M + 2 * 1024 * 1024:
        return raw[GAP_OFFSET_IN_11M:GAP_OFFSET_IN_11M + 2 * 1024 * 1024]
    raise SystemExit(f"expected 2 MiB gap or 11 MiB reserved dump, got {len(raw)} bytes")


def parse(rec: bytes) -> dict | None:
    if len(rec) != SLOT_BYTES:
        return None
    vals = struct.unpack_from("<QQIIIIIIIIIIII", rec, 0)
    (magic, ns, seq, event, cell, flags, msg_flags, type_len,
     payload, reason, state, cpu, stored_crc, commit) = vals
    if magic != MAGIC or commit != COMMIT:
        return None
    if crc32c(rec[:56]) != stored_crc:
        return None
    return {
        "time_ms": ns / 1_000_000.0,
        "seq": seq,
        "event": event,
        "event_name": EVENTS.get(event, f"EVT_{event}"),
        "cell": cell,
        "flags": flags,
        "msg_flags": msg_flags,
        "type": type_len & 0xffff,
        "tx_len": (type_len >> 16) & 0xffff,
        "payload": payload,
        "reason": reason,
        "state": state,
        "cpu": cpu,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    gap = choose_gap(ns.dump.read_bytes())
    rows = []
    for i in range(SLOTS):
        a = parse(gap[P404_OFF0 + i*SLOT_BYTES:P404_OFF0 + (i+1)*SLOT_BYTES])
        b = parse(gap[P404_OFF1 + i*SLOT_BYTES:P404_OFF1 + (i+1)*SLOT_BYTES])
        chosen = a or b
        if chosen:
            chosen = dict(chosen)
            chosen["slot"] = i
            chosen["copies_valid"] = int(a is not None) + int(b is not None)
            rows.append(chosen)

    rows.sort(key=lambda r: (r["seq"], r["slot"]))
    print(json.dumps({"records": len(rows)}, indent=2))
    for r in rows:
        print(
            f'slot={r["slot"]:02d} copies={r["copies_valid"]} '
            f't={r["time_ms"]:10.3f}ms seq={r["seq"]:3d} '
            f'{r["event_name"]:10s} cpu={r["cpu"]} cell={r["cell"]:08x} '
            f'flags={r["flags"]:08x} mf={r["msg_flags"]:08x} '
            f'type={r["type"]:02x} len={r["tx_len"]} payload={r["payload"]:08x} '
            f'reason={r["reason"]:08x} state={r["state"]:08x}'
        )
    if ns.json:
        ns.json.write_text(json.dumps({"records": rows}, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
