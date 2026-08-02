#!/usr/bin/env python3
"""Decode Phase 210+ RS48 records by fusing Base64 transport bytes first.

The A52 ramoops failure mode can clear different ASCII bits in the record and
ftrace banks. Decoding each Base64 copy independently loses that complementary
information. This decoder aligns the two physical banks, bitwise-ORs the full
255-byte R48 transport, then performs Base64 decoding, RS48 correction, and
mandatory CRC32C validation.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import struct
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

BANK_BYTES = 0x40000
BANK_HEADER_BYTES = 12
BANK_DATA_BYTES = BANK_BYTES - BANK_HEADER_BYTES
RAMOOPS_BYTES = 0x100000
RECORD_OFFSET = 0x00000
FTRACE_OFFSET = 0x80000


def load_base():
    path = Path(__file__).with_name('decode-a52-r210-rs48-base.py')
    if not path.is_file():
        raise RuntimeError(f'missing Phase 210 base decoder beside this file: {path}')
    spec = importlib.util.spec_from_file_location('a52_r210_rs48_transport_base', path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load base decoder: {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_base()
DecodeFailure = BASE.DecodeFailure


def circular_slice(data: bytes, offset: int, length: int) -> bytes:
    offset %= len(data)
    if offset + length <= len(data):
        return data[offset:offset + length]
    first = data[offset:]
    return first + data[:length - len(first)]


def bitwise_or(left: bytes, right: bytes) -> bytes:
    if len(left) != len(right):
        raise ValueError('fusion blocks must have equal length')
    return bytes(a | b for a, b in zip(left, right))


def candidate_offsets(snapshot: bytes) -> list[int]:
    record = snapshot[RECORD_OFFSET + BANK_HEADER_BYTES:RECORD_OFFSET + BANK_BYTES]
    ftrace = snapshot[FTRACE_OFFSET + BANK_HEADER_BYTES:FTRACE_OFFSET + BANK_BYTES]
    offsets = set(BASE.candidate_offsets(record))
    offsets.update(BASE.candidate_offsets(ftrace))

    for bank_offset in (RECORD_OFFSET, FTRACE_OFFSET):
        signature, start, size = struct.unpack_from('<III', snapshot, bank_offset)
        if signature == BASE.PERSISTENT_RAM_SIG and 0 <= start < BANK_DATA_BYTES:
            for index in range(1, BANK_DATA_BYTES // BASE.TEXT_BYTES + 13):
                offsets.add((start - BASE.TEXT_BYTES * index) % BANK_DATA_BYTES)
    return sorted(offsets)


def decode_snapshot(path: Path, output: Path) -> dict[str, object]:
    raw = path.read_bytes()
    if len(raw) != RAMOOPS_BYTES:
        raise DecodeFailure(f'expected {RAMOOPS_BYTES} bytes, got {len(raw)}')

    record = raw[RECORD_OFFSET + BANK_HEADER_BYTES:RECORD_OFFSET + BANK_BYTES]
    ftrace = raw[FTRACE_OFFSET + BANK_HEADER_BYTES:FTRACE_OFFSET + BANK_BYTES]
    offsets = candidate_offsets(raw)

    records = []
    seen: set[tuple[int, str]] = set()
    failures: Counter[str] = Counter()
    for offset in offsets:
        fused = bitwise_or(
            circular_slice(record, offset, BASE.TEXT_BYTES),
            circular_slice(ftrace, offset, BASE.TEXT_BYTES),
        )
        try:
            item = BASE.try_decode_transport(
                fused, bank='ascii-or-record-ftrace', offset=offset
            )
        except DecodeFailure as exc:
            failures[str(exc)] += 1
            continue
        key = (item.seq, item.raw_data_hex)
        if key in seen:
            continue
        seen.add(key)
        records.append(item)

    records.sort(key=lambda item: (item.seq, item.quality, item.offset))
    by_sequence = defaultdict(list)
    for item in records:
        by_sequence[item.seq].append(item)
    merged = [BASE.select_best(items) for _, items in sorted(by_sequence.items())]

    summary: dict[str, object] = {
        'status': 'a52-r210-rs48-transport-fusion-decoded',
        'source': str(path),
        'candidate_offsets': len(offsets),
        'crc_required': True,
        'fusion_order': [
            'align record and ftrace physical transport offsets',
            'bitwise OR the 255-byte R48 ASCII transports',
            'decode Base64 with erasures',
            'correct shortened RS(255,207) with 48 parity symbols',
            'validate Phase 210 record metadata and CRC32C',
        ],
        'valid_records': len(merged),
        'first_sequence': merged[0].seq if merged else None,
        'last_sequence': merged[-1].seq if merged else None,
        'missing_sequences_between_first_and_last': (
            merged[-1].seq - merged[0].seq + 1 - len(merged) if merged else 0
        ),
        'failure_reasons': dict(failures.most_common()),
        'record_format': {
            'prefix': BASE.PREFIX.decode('ascii'),
            'transport_bytes': BASE.TEXT_BYTES,
            'data_bytes': BASE.DATA_BYTES,
            'parity_symbols': BASE.PARITY_BYTES,
            'crc': 'CRC32C',
        },
    }

    output.mkdir(parents=True, exist_ok=True)
    (output / 'summary.json').write_text(
        json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    with (output / 'timeline.txt').open('w', encoding='utf-8') as handle:
        handle.write(f'source={path}\nrecords={len(merged)}\n\n')
        for item in merged:
            handle.write(
                f'{item.seq:08d} {item.monotonic_ns / 1_000_000:12.3f}ms '
                f'cpu={item.cpu:02d} pid={item.pid:<6d} {item.comm:<16.16s} '
                f'src={item.bank:<24s} rs={item.corrected_symbols:<2d} '
                f'eras={item.erasures:<2d} {item.message}\n'
            )
    fields = [
        'seq', 'monotonic_ns', 'monotonic_ms', 'pid', 'tgid', 'cpu', 'kind',
        'comm', 'message', 'selected_source', 'corrected_symbols', 'erasures',
        'prefix_distance', 'bank_offset',
    ]
    with (output / 'events.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in merged:
            writer.writerow({
                'seq': item.seq,
                'monotonic_ns': item.monotonic_ns,
                'monotonic_ms': f'{item.monotonic_ns / 1_000_000:.3f}',
                'pid': item.pid,
                'tgid': item.tgid,
                'cpu': item.cpu,
                'kind': item.kind,
                'comm': item.comm,
                'message': item.message,
                'selected_source': item.bank,
                'corrected_symbols': item.corrected_symbols,
                'erasures': item.erasures,
                'prefix_distance': item.prefix_distance,
                'bank_offset': item.offset,
            })
    with (output / 'records.jsonl').open('w', encoding='utf-8') as handle:
        for item in merged:
            handle.write(json.dumps(asdict(item), sort_keys=True) + '\n')
    return summary


def self_test() -> None:
    transport = BASE.encode_record_for_test(
        211, 'DRMPOST 211 transport fusion self-test', monotonic_ns=211_000_000
    )
    record_copy = bytearray(transport)
    ftrace_copy = bytearray(transport)
    payload_positions = [i for i, value in enumerate(transport) if i >= 3 and value]
    for index, position in enumerate(payload_positions[:120]):
        if index % 2:
            record_copy[position] = 0
        else:
            ftrace_copy[position] = 0

    offset = 1024
    raw = bytearray(RAMOOPS_BYTES)
    for bank_offset, damaged in (
        (RECORD_OFFSET, record_copy),
        (FTRACE_OFFSET, ftrace_copy),
    ):
        struct.pack_into(
            '<III', raw, bank_offset, BASE.PERSISTENT_RAM_SIG,
            offset + BASE.TEXT_BYTES, offset + BASE.TEXT_BYTES,
        )
        start = bank_offset + BANK_HEADER_BYTES + offset
        raw[start:start + BASE.TEXT_BYTES] = damaged

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        snapshot = root / 'raw.bin'
        snapshot.write_bytes(raw)
        summary = decode_snapshot(snapshot, root / 'decoded')
        if summary['valid_records'] != 1:
            raise AssertionError('transport fusion fixture did not recover one record')
        timeline = (root / 'decoded' / 'timeline.txt').read_text(encoding='utf-8')
        if 'DRMPOST 211 transport fusion self-test' not in timeline:
            raise AssertionError('transport fusion message missing')
    print('phase210 RS48 transport-fusion decoder self-test: PASS')


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Decode A52 Phase 210+ RS48 records with ASCII transport fusion'
    )
    parser.add_argument('input', type=Path, nargs='?')
    parser.add_argument('--output', type=Path, default=Path('decoded-r210-transport-fusion'))
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return 0
    if args.input is None:
        parser.error('input is required unless --self-test is used')
    summary = decode_snapshot(args.input, args.output)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except DecodeFailure as exc:
        print(f'decode error: {exc}', file=sys.stderr)
        raise SystemExit(2)
