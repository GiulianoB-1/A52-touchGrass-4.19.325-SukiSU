#!/usr/bin/env python3
"""Decode the A52 phase-197 triple-copy Reed-Solomon recorder.

The record transport is unchanged from phase 179:
    b"R79" + base64(RS(157 data bytes + 32 parity bytes))

Phase 197 writes the same protected record independently to the record,
console, and ftrace 0x40000-byte banks in the 1 MiB A52 RAMOOPS region.
"""
from __future__ import annotations

import argparse
import base64
import csv
import importlib.util
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

PHASE = 197
BANK_BYTES = 0x40000
RAMOOPS_TOTAL_BYTES = 0x100000
BANK_OFFSETS = {
    "record": 0x00000,
    "console": 0x40000,
    "ftrace": 0x80000,
}
BANK_ORDER = ("record", "console", "ftrace")


def load_base():
    path = Path(__file__).with_name("decode-a52-r179-rs-recorder.py")
    if not path.is_file():
        raise RuntimeError(f"missing phase-179 decoder beside this file: {path}")
    spec = importlib.util.spec_from_file_location("a52_r179_decoder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load phase-179 decoder: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base()
DecodeFailure = BASE.DecodeFailure


def bank_name_for_file(path: Path) -> str:
    lower = path.name.lower()
    if "ftrace" in lower:
        return "ftrace"
    if "record" in lower or "dmesg" in lower:
        return "record"
    return "console"


def extract_banks(snapshot: Path) -> dict[str, bytes]:
    raw = snapshot.read_bytes()
    if len(raw) == RAMOOPS_TOTAL_BYTES:
        return {
            name: raw[offset : offset + BANK_BYTES]
            for name, offset in BANK_OFFSETS.items()
        }
    if len(raw) == BANK_BYTES:
        return {bank_name_for_file(snapshot): raw}
    raise DecodeFailure("unsupported snapshot size")


def merge_records(
    records_by_bank: dict[str, Sequence[object]],
) -> tuple[list[object], dict[str, object]]:
    by_seq: dict[int, list[object]] = defaultdict(list)
    for records in records_by_bank.values():
        for record in records:
            by_seq[record.seq].append(record)

    merged: list[object] = []
    only_by_bank = Counter()
    copy_count_histogram = Counter()
    disagreements = 0
    duplicates = 0
    for seq in sorted(by_seq):
        candidates = by_seq[seq]
        banks = {item.bank for item in candidates}
        copy_count_histogram[len(banks)] += 1
        if len(banks) == 1:
            only_by_bank[next(iter(banks))] += 1
        payloads = {item.raw_data_hex for item in candidates}
        if len(payloads) > 1:
            disagreements += 1
        if len(candidates) > len(banks):
            duplicates += len(candidates) - len(banks)
        merged.append(BASE.select_best(candidates))

    stats: dict[str, object] = {
        "merged_sequences": len(merged),
        "recovered_only_from_record": only_by_bank["record"],
        "recovered_only_from_console": only_by_bank["console"],
        "recovered_only_from_ftrace": only_by_bank["ftrace"],
        "recovered_from_one_bank": copy_count_histogram[1],
        "recovered_from_two_banks": copy_count_histogram[2],
        "recovered_from_all_three": copy_count_histogram[3],
        "sequence_disagreements": disagreements,
        "duplicate_records_within_banks": duplicates,
        "first_sequence": merged[0].seq if merged else None,
        "last_sequence": merged[-1].seq if merged else None,
        "missing_sequences_between_first_and_last": (
            merged[-1].seq - merged[0].seq + 1 - len(merged) if merged else 0
        ),
    }
    return merged, stats


def write_outputs(
    output_dir: Path,
    source: Path,
    records_by_bank: dict[str, Sequence[object]],
    merged: Sequence[object],
    summary: dict[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    fieldnames = [
        "seq",
        "monotonic_ns",
        "monotonic_ms",
        "pid",
        "tgid",
        "cpu",
        "kind",
        "comm",
        "message",
        "selected_bank",
        "corrected_symbols",
        "erasures",
        "prefix_distance",
        "bank_offset",
    ]
    with (output_dir / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in merged:
            writer.writerow(
                {
                    "seq": item.seq,
                    "monotonic_ns": item.monotonic_ns,
                    "monotonic_ms": f"{item.monotonic_ns / 1_000_000:.3f}",
                    "pid": item.pid,
                    "tgid": item.tgid,
                    "cpu": item.cpu,
                    "kind": item.kind,
                    "comm": item.comm,
                    "message": item.message,
                    "selected_bank": item.bank,
                    "corrected_symbols": item.corrected_symbols,
                    "erasures": item.erasures,
                    "prefix_distance": item.prefix_distance,
                    "bank_offset": item.offset,
                }
            )

    with (output_dir / "timeline.txt").open("w", encoding="utf-8") as handle:
        handle.write(f"source={source}\n")
        handle.write(f"records={len(merged)}\n\n")
        for item in merged:
            handle.write(
                f"{item.seq:08d} {item.monotonic_ns / 1_000_000:12.3f}ms "
                f"cpu={item.cpu:02d} pid={item.pid:<6d} {item.comm:<16.16s} "
                f"bank={item.bank:<7s} rs={item.corrected_symbols:<2d} "
                f"eras={item.erasures:<2d} {item.message}\n"
            )

    for name in BANK_ORDER:
        records = records_by_bank.get(name, ())
        with (output_dir / f"{name}-records.jsonl").open("w", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(asdict(item), sort_keys=True) + "\n")


def decode_snapshot(snapshot: Path, output_dir: Path) -> dict[str, object]:
    banks = extract_banks(snapshot)
    records_by_bank: dict[str, list[object]] = {}
    bank_stats: dict[str, object] = {}
    for bank_name, bank_data in banks.items():
        records, stats = BASE.decode_bank(bank_data, bank_name)
        records_by_bank[bank_name] = records
        bank_stats[bank_name] = stats

    merged, merge_stats = merge_records(records_by_bank)
    summary: dict[str, object] = {
        "status": "a52-r197-rs-triple-decoded",
        "phase": PHASE,
        "source": str(snapshot),
        "record_format": {
            "transport_bytes": BASE.TEXT_BYTES,
            "prefix": BASE.PREFIX.decode("ascii"),
            "data_bytes": BASE.DATA_BYTES,
            "parity_symbols": BASE.PARITY_BYTES,
            "maximum_correctable_unknown_symbols_per_copy": BASE.PARITY_BYTES // 2,
            "copies": list(BANK_ORDER),
            "crc": False,
        },
        "banks": bank_stats,
        "merge": merge_stats,
    }
    write_outputs(output_dir, snapshot, records_by_bank, merged, summary)
    return summary


def self_test() -> None:
    BASE.self_test()
    transport = BASE.encode_record_for_test(197, "KMSBLK triple-copy self-test")
    copies: dict[str, object] = {}
    corruption = {"record": 7, "console": 3, "ftrace": 1}
    rng = random.Random(0xA52197)
    for bank, errors in corruption.items():
        codeword = bytearray(base64.b64decode(transport[3:]))
        for position in rng.sample(range(BASE.CODE_BYTES), errors):
            codeword[position] ^= rng.randrange(1, 256)
        damaged = transport[:3] + base64.b64encode(codeword)
        copies[bank] = BASE.try_decode_transport(damaged, bank=bank, offset=0)

    merged, stats = merge_records({name: [record] for name, record in copies.items()})
    if len(merged) != 1 or merged[0].bank != "ftrace":
        raise AssertionError("triple-copy quality selection failed")
    if stats["recovered_from_all_three"] != 1:
        raise AssertionError("triple-copy merge accounting failed")

    record_only, stats = merge_records({"record": [copies["record"]]})
    if len(record_only) != 1 or stats["recovered_only_from_record"] != 1:
        raise AssertionError("record-bank-only recovery failed")
    print("phase197 triple-copy decoder self-test: PASS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decode A52 phase-197 triple-copy RS-protected RAMOOPS records"
    )
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=Path("decoded-a52-r197"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        self_test()
        return 0
    if args.input is None:
        parser.error("input is required unless --self-test is used")

    snapshots = BASE.find_snapshots(args.input)
    summaries = []
    for index, snapshot in enumerate(snapshots):
        destination = args.output if len(snapshots) == 1 else args.output / f"snapshot-{index:02d}"
        summaries.append(decode_snapshot(snapshot, destination))
    print(json.dumps(summaries[0] if len(summaries) == 1 else summaries, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DecodeFailure as exc:
        print(f"decode error: {exc}", file=sys.stderr)
        raise SystemExit(2)
