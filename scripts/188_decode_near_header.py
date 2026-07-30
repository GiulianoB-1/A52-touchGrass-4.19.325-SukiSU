#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import struct
import sys
import tempfile
from pathlib import Path
from typing import Sequence


def load_soft_decoder():
    path = Path(__file__).with_name("decode-a52-r180-soft-rs.py")
    if not path.is_file():
        raise RuntimeError(f"missing sibling decoder: {path}")
    spec = importlib.util.spec_from_file_location("a52_r180_soft", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


S = load_soft_decoder()
M = S.M
MAX_SIGNATURE_BIT_DISTANCE = 1


def signature_bit_distance(signature: int) -> int:
    return (signature ^ M.PERSISTENT_RAM_SIG).bit_count()


def chronological_ring_near_header(bank: bytes) -> tuple[bytes, dict[str, object]]:
    if len(bank) != M.BANK_BYTES:
        raise M.DecodeFailure(f"bank has {len(bank)} bytes, expected {M.BANK_BYTES}")
    signature, start, size = struct.unpack_from("<III", bank, 0)
    data = bank[M.BANK_HEADER_BYTES:]
    distance = signature_bit_distance(signature)
    valid_header = (
        distance <= MAX_SIGNATURE_BIT_DISTANCE
        and 0 <= start < len(data)
        and 0 <= size <= len(data)
    )
    if not valid_header:
        return data, {
            "signature": f"0x{signature:08x}",
            "signature_bit_distance": distance,
            "start": start,
            "size": size,
            "header_valid": False,
            "fallback": "scanned entire bank data area",
        }
    begin = (start - size) % len(data)
    if size == 0:
        stream = b""
    elif begin < start:
        stream = data[begin:start]
    else:
        stream = data[begin:] + data[:start]
    return stream, {
        "signature": f"0x{signature:08x}",
        "signature_bit_distance": distance,
        "signature_recovered": distance != 0,
        "start": start,
        "size": size,
        "header_valid": True,
        "chronological_begin": begin,
    }


S.M.chronological_ring = chronological_ring_near_header


def make_bank(records: list[bytes], *, corrupt_signature: bool) -> bytes:
    payload = b"".join(records)
    if len(payload) > M.BANK_DATA_BYTES:
        raise AssertionError("test payload too large")
    signature = M.PERSISTENT_RAM_SIG
    if corrupt_signature:
        signature ^= 1 << 8
    start = len(payload)
    size = len(payload)
    bank = bytearray(M.BANK_BYTES)
    struct.pack_into("<III", bank, 0, signature, start, size)
    bank[M.BANK_HEADER_BYTES:M.BANK_HEADER_BYTES + len(payload)] = payload
    return bytes(bank)


def self_test() -> None:
    rng = random.Random(0xA52188)
    records = [
        M.encode_record_for_test(
            seq,
            f"PINCTRL decoder near-header test {seq}",
            monotonic_ns=seq * 1_000_000,
        )
        for seq in range(1, 9)
    ]
    left = [bytearray(record) for record in records]
    right = [bytearray(record) for record in records]
    for copies in (left, right):
        for record in copies:
            for index in range(3, len(record)):
                if rng.random() < 0.01:
                    set_bits = [bit for bit in range(8) if record[index] & (1 << bit)]
                    if set_bits:
                        record[index] &= ~(1 << rng.choice(set_bits))
    console = make_bank([bytes(item) for item in left], corrupt_signature=True)
    ftrace = make_bank([bytes(item) for item in right], corrupt_signature=False)
    raw = bytearray(M.RAMOOPS_TOTAL_BYTES)
    raw[M.CONSOLE_OFFSET:M.CONSOLE_OFFSET + M.BANK_BYTES] = console
    raw[M.FTRACE_OFFSET:M.FTRACE_OFFSET + M.BANK_BYTES] = ftrace
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot = root / "ramoops.bin"
        snapshot.write_bytes(raw)
        summary = S.decode_snapshot(snapshot, root / "decoded")
        if summary["first_sequence"] != 1 or summary["last_sequence"] != 8:
            raise AssertionError(summary)
        console_header = summary["alignment"]["console_header"]
        if not console_header["header_valid"]:
            raise AssertionError(console_header)
        if console_header["signature_bit_distance"] != 1:
            raise AssertionError(console_header)
        if not console_header["signature_recovered"]:
            raise AssertionError(console_header)
    print("phase188 near-header soft decoder self-test: PASS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decode A52 two-copy RS records while tolerating one damaged persistent-RAM signature bit"
    )
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=Path("decoded-a52-r188"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.input is None:
        parser.error("input is required unless --self-test is used")
    snapshots = M.find_snapshots(args.input)
    full_snapshots = [path for path in snapshots if path.stat().st_size == M.RAMOOPS_TOTAL_BYTES]
    if not full_snapshots:
        raise M.DecodeFailure("no full 1 MiB snapshot found for two-copy soft fusion")
    summaries = []
    for index, snapshot in enumerate(full_snapshots):
        destination = args.output if len(full_snapshots) == 1 else args.output / f"snapshot-{index:02d}"
        summaries.append(S.decode_snapshot(snapshot, destination))
    print(json.dumps(summaries[0] if len(summaries) == 1 else summaries, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (M.DecodeFailure, RuntimeError) as exc:
        print(f"decode error: {exc}", file=sys.stderr)
        raise SystemExit(2)
