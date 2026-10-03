#!/usr/bin/env python3
from __future__ import annotations
import argparse
import struct
from pathlib import Path

PHYS = 0xB1400000
RAM_BYTES = 0x40000
HEADER_BYTES = 0x1000
RECORD_BYTES = 128
MAGIC = 0x47434D4B43323334
VERSION = 1
HEADER_COMMIT = 0x432C600D
RECORD_COMMIT = 0x432C0DE5
HFMT = struct.Struct("<QIIIIQII24s")
RFMT = struct.Struct("<QQQIIHH16s68sII")


def cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path, help="raw dump beginning at 0xB1400000, e.g. the existing 7 MiB Golden recovery dump")
    ap.add_argument("--offset", type=lambda x: int(x, 0), default=0, help="byte offset of 0xB1400000 within the file")
    ns = ap.parse_args()
    b = ns.dump.read_bytes()
    base = ns.offset
    if len(b) < base + RAM_BYTES:
        raise SystemExit(f"short dump: need at least 0x{base + RAM_BYTES:x} bytes")

    h = HFMT.unpack_from(b, base)
    magic, version, rec_bytes, capacity, _r0, boot_token, commit, _r1, _pad = h
    if magic != MAGIC or version != VERSION or rec_bytes != RECORD_BYTES or commit != HEADER_COMMIT:
        raise SystemExit(
            f"P432C-TG header invalid magic=0x{magic:x} version={version} rec_bytes={rec_bytes} commit=0x{commit:x}"
        )

    records = []
    for i in range(min(capacity, (RAM_BYTES - HEADER_BYTES) // RECORD_BYTES)):
        off = base + HEADER_BYTES + i * RECORD_BYTES
        v = RFMT.unpack_from(b, off)
        seq, ts_ns, token, pid, tgid, cpu, ln, comm, text, rec_commit, _res = v
        if rec_commit != RECORD_COMMIT or token != boot_token or seq != i + 1:
            continue
        records.append((seq, ts_ns, pid, tgid, cpu, cstr(comm), cstr(text[:min(ln, len(text))])))

    print(f"P432C-TG boot_token={boot_token} accepted={len(records)}/{capacity}")
    for seq, ns_, pid, tgid, cpu, comm, text in records:
        print(f"{ns_/1e9:10.6f}s  P432C K n={seq:04d} p={pid} t={tgid} cpu={cpu} c={comm:<15} {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
