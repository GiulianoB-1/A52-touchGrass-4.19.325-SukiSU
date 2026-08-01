#!/usr/bin/env python3
"""Decode the A52 Phase 199 triple-copy RS + CRC32C recorder.

Each record is written independently to the record, console, and ftrace banks.
The decoder first performs ordinary per-copy Reed-Solomon correction, then tries
same-offset bit-majority and clear-bit OR fusion across copies. Every accepted
record must pass CRC32C after correction or fusion.
"""
from __future__ import annotations

import argparse
import base64
import csv
import importlib.util
import itertools
import json
import random
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

PHASE = 199
BANK_BYTES = 0x40000
BANK_HEADER_BYTES = 12
RAMOOPS_TOTAL_BYTES = 0x100000
BANK_OFFSETS = {
    "record": 0x00000,
    "console": 0x40000,
    "ftrace": 0x80000,
}
BANK_ORDER = ("record", "console", "ftrace")


def load_base():
    path = Path(__file__).with_name("decode-a52-r199-crc32c-base.py")
    if not path.is_file():
        raise RuntimeError(f"missing Phase 199 base decoder beside this file: {path}")
    spec = importlib.util.spec_from_file_location("a52_r199_crc32c_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase 199 base decoder: {path}")
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
    records_by_source: dict[str, Sequence[object]],
) -> tuple[list[object], dict[str, object]]:
    by_seq: dict[int, list[object]] = defaultdict(list)
    for records in records_by_source.values():
        for record in records:
            by_seq[record.seq].append(record)

    merged: list[object] = []
    only_by_source = Counter()
    copy_count_histogram = Counter()
    disagreements = 0
    duplicates = 0
    for seq in sorted(by_seq):
        candidates = by_seq[seq]
        sources = {item.bank for item in candidates}
        physical_banks = {source for source in sources if source in BANK_ORDER}
        copy_count_histogram[len(physical_banks)] += 1
        if len(sources) == 1:
            only_by_source[next(iter(sources))] += 1
        payloads = {item.raw_data_hex for item in candidates}
        if len(payloads) > 1:
            disagreements += 1
        if len(candidates) > len(sources):
            duplicates += len(candidates) - len(sources)
        merged.append(BASE.select_best(candidates))

    stats: dict[str, object] = {
        "merged_sequences": len(merged),
        "recovered_only_from_record": only_by_source["record"],
        "recovered_only_from_console": only_by_source["console"],
        "recovered_only_from_ftrace": only_by_source["ftrace"],
        "recovered_only_from_fusion_or": only_by_source["fusion-or"],
        "recovered_only_from_fusion_majority": only_by_source["fusion-majority"],
        "recovered_from_one_physical_bank": copy_count_histogram[1],
        "recovered_from_two_physical_banks": copy_count_histogram[2],
        "recovered_from_all_three_physical_banks": copy_count_histogram[3],
        "sequence_disagreements": disagreements,
        "duplicate_records_within_sources": duplicates,
        "first_sequence": merged[0].seq if merged else None,
        "last_sequence": merged[-1].seq if merged else None,
        "missing_sequences_between_first_and_last": (
            merged[-1].seq - merged[0].seq + 1 - len(merged) if merged else 0
        ),
    }
    return merged, stats


def bit_majority(blocks: Sequence[bytes]) -> bytes:
    if len(blocks) != 3:
        raise ValueError("bit majority requires exactly three blocks")
    left, middle, right = blocks
    return bytes((a & b) | (a & c) | (b & c) for a, b, c in zip(left, middle, right))


def bit_or(blocks: Sequence[bytes]) -> bytes:
    if not blocks:
        raise ValueError("bit OR requires at least one block")
    output = bytearray(blocks[0])
    for block in blocks[1:]:
        for index, value in enumerate(block):
            output[index] |= value
    return bytes(output)


def decoded_codeword(block: bytes) -> bytes:
    if len(block) != BASE.TEXT_BYTES:
        raise DecodeFailure("short fusion transport")
    codeword, _erasures = BASE.decode_base64_with_erasures(block[3:])
    return codeword


def fusion_candidate_offsets(banks: dict[str, bytes]) -> list[int]:
    offsets: set[int] = set()
    for bank in banks.values():
        data = bank[BANK_HEADER_BYTES:]
        offsets.update(BASE.candidate_offsets(data))
    return sorted(offsets)


def decode_fused_records(
    banks: dict[str, bytes],
) -> tuple[list[object], dict[str, object]]:
    if len(banks) < 2:
        return [], {"candidate_offsets": 0, "valid_records": 0, "variants": {}}

    data_by_bank = {
        name: raw[BANK_HEADER_BYTES:]
        for name, raw in banks.items()
    }
    offsets = fusion_candidate_offsets(banks)
    records: list[object] = []
    seen: set[tuple[int, str]] = set()
    variant_success = Counter()
    failures = Counter()

    for offset in offsets:
        codewords: list[bytes] = []
        for name in BANK_ORDER:
            data = data_by_bank.get(name)
            if data is None or offset + BASE.TEXT_BYTES > len(data):
                continue
            try:
                codewords.append(decoded_codeword(data[offset : offset + BASE.TEXT_BYTES]))
            except DecodeFailure as exc:
                failures[str(exc)] += 1
        if len(codewords) < 2:
            continue

        variants: list[tuple[str, bytes]] = []
        if len(codewords) == 3:
            variants.append(("fusion-majority", bit_majority(codewords)))
            variants.append(("fusion-or", bit_or(codewords)))
        for pair in itertools.combinations(range(len(codewords)), 2):
            variants.append(("fusion-or", bit_or([codewords[pair[0]], codewords[pair[1]]])))

        unique_variants: set[bytes] = set()
        for source, codeword in variants:
            if codeword in unique_variants:
                continue
            unique_variants.add(codeword)
            transport = BASE.PREFIX + base64.b64encode(codeword)
            try:
                record = BASE.try_decode_transport(transport, bank=source, offset=offset)
            except DecodeFailure as exc:
                failures[str(exc)] += 1
                continue
            key = (record.seq, record.raw_data_hex)
            if key in seen:
                continue
            seen.add(key)
            records.append(record)
            variant_success[source] += 1

    records.sort(key=lambda item: (item.seq, item.quality, item.offset))
    return records, {
        "candidate_offsets": len(offsets),
        "valid_records": len(records),
        "variants": dict(variant_success),
        "failure_reasons": dict(failures.most_common()),
        "crc_required": True,
    }


def write_outputs(
    output_dir: Path,
    source: Path,
    records_by_source: dict[str, Sequence[object]],
    merged: Sequence[object],
    summary: dict[str, object],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    fieldnames = [
        "seq", "monotonic_ns", "monotonic_ms", "pid", "tgid", "cpu", "kind",
        "comm", "message", "selected_source", "corrected_symbols", "erasures",
        "prefix_distance", "bank_offset",
    ]
    with (output_dir / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in merged:
            writer.writerow({
                "seq": item.seq,
                "monotonic_ns": item.monotonic_ns,
                "monotonic_ms": f"{item.monotonic_ns / 1_000_000:.3f}",
                "pid": item.pid,
                "tgid": item.tgid,
                "cpu": item.cpu,
                "kind": item.kind,
                "comm": item.comm,
                "message": item.message,
                "selected_source": item.bank,
                "corrected_symbols": item.corrected_symbols,
                "erasures": item.erasures,
                "prefix_distance": item.prefix_distance,
                "bank_offset": item.offset,
            })

    with (output_dir / "timeline.txt").open("w", encoding="utf-8") as handle:
        handle.write(f"source={source}\n")
        handle.write(f"records={len(merged)}\n\n")
        for item in merged:
            handle.write(
                f"{item.seq:08d} {item.monotonic_ns / 1_000_000:12.3f}ms "
                f"cpu={item.cpu:02d} pid={item.pid:<6d} {item.comm:<16.16s} "
                f"src={item.bank:<15s} rs={item.corrected_symbols:<2d} "
                f"eras={item.erasures:<2d} {item.message}\n"
            )

    for name, records in records_by_source.items():
        safe_name = name.replace("/", "-")
        with (output_dir / f"{safe_name}-records.jsonl").open("w", encoding="utf-8") as handle:
            for item in records:
                handle.write(json.dumps(asdict(item), sort_keys=True) + "\n")


def decode_snapshot(snapshot: Path, output_dir: Path) -> dict[str, object]:
    banks = extract_banks(snapshot)
    records_by_source: dict[str, list[object]] = {}
    bank_stats: dict[str, object] = {}
    for bank_name, bank_data in banks.items():
        records, stats = BASE.decode_bank(bank_data, bank_name)
        records_by_source[bank_name] = records
        bank_stats[bank_name] = stats

    fused, fusion_stats = decode_fused_records(banks)
    by_fusion_source: dict[str, list[object]] = defaultdict(list)
    for record in fused:
        by_fusion_source[record.bank].append(record)
    records_by_source.update(by_fusion_source)

    merged, merge_stats = merge_records(records_by_source)
    summary: dict[str, object] = {
        "status": "a52-r199-crc32c-rs-triple-decoded",
        "phase": PHASE,
        "source": str(snapshot),
        "record_format": {
            "transport_bytes": BASE.TEXT_BYTES,
            "prefix": BASE.PREFIX.decode("ascii"),
            "data_bytes": BASE.DATA_BYTES,
            "parity_symbols": BASE.PARITY_BYTES,
            "maximum_correctable_unknown_symbols_per_copy": BASE.PARITY_BYTES // 2,
            "copies": list(BANK_ORDER),
            "crc": "CRC32C",
            "crc_scope": "record metadata and message before the CRC field",
        },
        "banks": bank_stats,
        "fusion": fusion_stats,
        "merge": merge_stats,
    }
    write_outputs(output_dir, snapshot, records_by_source, merged, summary)
    return summary


def self_test() -> None:
    BASE.self_test()
    transport = BASE.encode_record_for_test(199, "DRMPOST triple-copy CRC self-test")
    copies: dict[str, object] = {}
    corruption = {"record": 7, "console": 3, "ftrace": 1}
    rng = random.Random(0xA52199)
    for bank, errors in corruption.items():
        codeword = bytearray(base64.b64decode(transport[3:]))
        for position in rng.sample(range(BASE.CODE_BYTES), errors):
            codeword[position] ^= rng.randrange(1, 256)
        damaged = transport[:3] + base64.b64encode(codeword)
        copies[bank] = BASE.try_decode_transport(damaged, bank=bank, offset=0)

    merged, stats = merge_records({name: [record] for name, record in copies.items()})
    if len(merged) != 1 or merged[0].bank != "ftrace":
        raise AssertionError("triple-copy quality selection failed")
    if stats["recovered_from_all_three_physical_banks"] != 1:
        raise AssertionError("triple-copy merge accounting failed")

    original_codeword = base64.b64decode(transport[3:])
    nonzero_positions = [i for i, value in enumerate(original_codeword) if value]
    selected = nonzero_positions[:60]
    synthetic_banks: dict[str, bytes] = {}
    offset = 510
    for bank_index, bank in enumerate(BANK_ORDER):
        damaged = bytearray(original_codeword)
        for position in selected[bank_index * 20 : (bank_index + 1) * 20]:
            damaged[position] = 0
        damaged_transport = BASE.PREFIX + base64.b64encode(damaged)
        try:
            BASE.try_decode_transport(damaged_transport, bank=bank, offset=offset)
        except DecodeFailure:
            pass
        else:
            raise AssertionError("individually uncorrectable fusion fixture decoded")
        raw = bytearray(BANK_BYTES)
        struct.pack_into("<III", raw, 0, BASE.PERSISTENT_RAM_SIG, offset + BASE.TEXT_BYTES, offset + BASE.TEXT_BYTES)
        raw[BANK_HEADER_BYTES + offset : BANK_HEADER_BYTES + offset + BASE.TEXT_BYTES] = damaged_transport
        synthetic_banks[bank] = bytes(raw)

    fused, fusion_stats = decode_fused_records(synthetic_banks)
    if not any(record.seq == 199 and record.bank.startswith("fusion-") for record in fused):
        raise AssertionError("cross-copy fusion failed")
    if fusion_stats["valid_records"] < 1:
        raise AssertionError("fusion accounting failed")
    print("phase199 triple-copy RS + CRC32C decoder self-test: PASS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decode A52 Phase 199 triple-copy Reed-Solomon and CRC32C records"
    )
    parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, default=Path("decoded-a52-r199"))
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
