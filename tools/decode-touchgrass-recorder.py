#!/usr/bin/env python3
from __future__ import annotations

import argparse
import binascii
import struct
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

PHYS_SIZE = 0x100000
BANK_SIZE = 0x40000
BANKS = 3
HEADER_SIZE = 256
RECORD_SIZE = 256
SLOTS = (BANK_SIZE - HEADER_SIZE) // RECORD_SIZE

HEADER_MAGIC = 0x48344754  # TG4H
RECORD_MAGIC = 0x52344754  # TG4R
COMMIT = 0x5A52C476

REC = struct.Struct("<IHHQQIIHH16s172sIIIII12s")


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def load_blob(path: Path) -> tuple[bytes, str]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            preferred = [
                n for n in names
                if n.endswith("raw-ramoops-frozen-1MiB.bin")
            ]
            if not preferred:
                preferred = [n for n in names if n.endswith("ramoops-raw-1MiB.bin")]
            if not preferred:
                preferred = [n for n in names if n.lower().endswith(".bin")]
            if not preferred:
                raise SystemExit("no raw ramoops binary found in zip")
            name = preferred[0]
            return zf.read(name), name
    return path.read_bytes(), path.name


def cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def parse_record(raw: bytes, bank: int, slot: int):
    if len(raw) != RECORD_SIZE:
        return None
    fields = REC.unpack(raw)
    (
        magic, version, typ, seq, ns, pid, tgid, cpu, msglen,
        comm, msg, crc, commit, commit_inv, seq_low, seq_low_inv,
        build_id,
    ) = fields

    if magic != RECORD_MAGIC:
        return None
    valid_commit = commit == COMMIT and commit_inv == ((~COMMIT) & 0xFFFFFFFF)
    valid_seq = seq_low == (seq & 0xFFFFFFFF) and seq_low_inv == ((~seq_low) & 0xFFFFFFFF)
    valid_crc = crc == crc32c(raw[:224])
    msglen = min(msglen, len(msg))

    return {
        "bank": bank,
        "slot": slot,
        "version": version,
        "type": typ,
        "seq": seq,
        "ns": ns,
        "pid": pid,
        "tgid": tgid,
        "cpu": cpu,
        "comm": cstr(comm),
        "message": msg[:msglen].decode("utf-8", "replace"),
        "build_id": cstr(build_id),
        "crc_ok": valid_crc,
        "commit_ok": valid_commit,
        "seq_ok": valid_seq,
        "valid": valid_crc and valid_commit and valid_seq,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=Path)
    ap.add_argument("--all-copies", action="store_true")
    args = ap.parse_args()

    blob, source = load_blob(args.capture)
    if len(blob) < PHYS_SIZE:
        raise SystemExit(f"capture too small: {len(blob)} bytes")
    blob = blob[:PHYS_SIZE]

    headers = []
    for bank in range(BANKS):
        off = bank * BANK_SIZE
        magic, version, bank_id = struct.unpack_from("<IHH", blob, off)
        if magic == HEADER_MAGIC:
            build_id = cstr(blob[off + 32:off + 48])
            build_desc = cstr(blob[off + 48:off + 176])
            headers.append((bank, version, bank_id, build_id, build_desc))

    print(f"source={source}")
    if not headers:
        print("TGREC_STATUS=NOT_PRESENT")
        return 2

    for h in headers:
        print(
            f"header bank={h[0]} version={h[1]} id={h[2]} "
            f"build={h[3]} desc={h[4]}"
        )

    copies = defaultdict(list)
    for bank in range(BANKS):
        base = bank * BANK_SIZE + HEADER_SIZE
        for slot in range(SLOTS):
            off = base + slot * RECORD_SIZE
            r = parse_record(blob[off:off + RECORD_SIZE], bank, slot)
            if r is not None:
                copies[r["seq"]].append(r)

    if not copies:
        print("TGREC_STATUS=HEADER_ONLY")
        return 3

    print(f"record_sequences={len(copies)}")
    print("TGREC_STATUS=PRESENT")

    for seq in sorted(copies):
        group = copies[seq]
        valid = [r for r in group if r["valid"]]
        chosen = valid[0] if valid else group[0]
        copy_status = f"{len(valid)}/{len(group)}"
        print(
            f"{chosen['seq']:06d} {chosen['ns']/1e9:10.6f}s "
            f"type={chosen['type']} cpu={chosen['cpu']} "
            f"pid={chosen['pid']} comm={chosen['comm']} "
            f"copies={copy_status} build={chosen['build_id']} "
            f"{chosen['message']}"
        )
        if args.all_copies:
            for r in group:
                print(
                    f"  bank={r['bank']} slot={r['slot']} valid={int(r['valid'])} "
                    f"crc={int(r['crc_ok'])} commit={int(r['commit_ok'])} "
                    f"seq={int(r['seq_ok'])}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
