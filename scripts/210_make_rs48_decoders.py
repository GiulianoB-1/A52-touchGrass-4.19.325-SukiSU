#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def build(source: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    core = (source / 'decode-a52-r179-rs-recorder.py').read_text()
    base = (source / 'decode-a52-r199-crc32c-base.py').read_text()
    triple = (source / 'decode-a52-r199-crc32c-triple.py').read_text()

    core_repl = [
        ('Decode the A52 phase-179 Reed-Solomon mirrored display recorder.',
         'RS core adapted for the A52 Phase 210 R48 recorder.'),
        ('b"R79" + base64(RS(157 data bytes + 32 parity bytes))',
         'b"R48" + base64(RS(141 data bytes + 48 parity bytes))'),
        ('PHASE = 179', 'PHASE = 210'),
        ('PREFIX = b"R79"', 'PREFIX = b"R48"'),
        ('DATA_BYTES = 157', 'DATA_BYTES = 141'),
        ('PARITY_BYTES = 32', 'PARITY_BYTES = 48'),
        ('MAGIC = b"A52R0179"', 'MAGIC = b"A52R0210"'),
        ('VERSION = 1', 'VERSION = 3'),
        ('COMMIT = 0x5A52C179', 'COMMIT = 0x5A52C210'),
    ]
    for old, new in core_repl:
        core = replace_once(core, old, new, f'core {old}')
    core_name = 'decode-a52-r210-rs48-core.py'
    (output / core_name).write_text(core)

    base_repl = [
        ('Phase 199 CRC32C format adapter for the Phase 179 RS decoder core.',
         'Phase 210 CRC32C adapter for the 48-parity RS decoder core.'),
        ('decode-a52-r179-rs-recorder.py', core_name),
        ('a52_r179_core_for_r199', 'a52_r210_rs48_core'),
        ('PHASE = 199', 'PHASE = 210'),
        ('PREFIX = b"R99"', 'PREFIX = b"R48"'),
        ('MAGIC = b"A52R0199"', 'MAGIC = b"A52R0210"'),
        ('VERSION = 2', 'VERSION = 3'),
        ('COMMIT = 0x5A52C199', 'COMMIT = 0x5A52C210'),
        ('RECORD_STRUCT = struct.Struct("<8sHHQQIIIHH16s89sII")',
         'RECORD_STRUCT = struct.Struct("<8sHHQQIIIHH16s73sII")'),
        ('message.encode("utf-8")[:89]', 'message.encode("utf-8")[:73]'),
        ('message_bytes.ljust(89, b"\\0")', 'message_bytes.ljust(73, b"\\0")'),
        ('rng = random.Random(0xA52199)', 'rng = random.Random(0xA52210)'),
        ('for errors in (0, 1, 5, 16):', 'for errors in (0, 1, 8, 24):'),
        ('phase199 CRC32C decoder self-test: PASS',
         'phase210 RS48 CRC32C decoder self-test: PASS'),
        ('Test the Phase 199 CRC32C RS decoder core',
         'Test the Phase 210 RS48 CRC32C decoder core'),
    ]
    for old, new in base_repl:
        base = replace_once(base, old, new, f'base {old}')
    base_name = 'decode-a52-r210-rs48-base.py'
    (output / base_name).write_text(base)

    triple_repl = [
        ('Decode the A52 Phase 199 triple-copy RS + CRC32C recorder.',
         'Decode the A52 Phase 210 triple-copy RS48 + CRC32C recorder.'),
        ('PHASE = 199', 'PHASE = 210'),
        ('decode-a52-r199-crc32c-base.py', base_name),
        ('a52_r199_crc32c_base', 'a52_r210_rs48_base'),
        ('missing Phase 199 base decoder', 'missing Phase 210 RS48 base decoder'),
        ('"status": "a52-r199-crc32c-rs-triple-decoded"',
         '"status": "a52-r210-crc32c-rs48-triple-decoded"'),
        ('BASE.encode_record_for_test(199, "DRMPOST triple-copy CRC self-test")',
         'BASE.encode_record_for_test(210, "DRMPOST triple-copy CRC self-test")'),
        ('rng = random.Random(0xA52199)', 'rng = random.Random(0xA52210)'),
        ('selected = nonzero_positions[:60]', 'selected = nonzero_positions[:90]'),
        ('selected[bank_index * 20 : (bank_index + 1) * 20]',
         'selected[bank_index * 30 : (bank_index + 1) * 30]'),
        ('record.seq == 199', 'record.seq == 210'),
        ('phase199 triple-copy RS + CRC32C decoder self-test: PASS',
         'phase210 triple-copy RS48 + CRC32C decoder self-test: PASS'),
        ('Decode A52 Phase 199 triple-copy Reed-Solomon and CRC32C records',
         'Decode A52 Phase 210 triple-copy RS48 and CRC32C records'),
        ('Path("decoded-a52-r199")', 'Path("decoded-a52-r210-rs48")'),
    ]
    for old, new in triple_repl:
        triple = replace_once(triple, old, new, f'triple {old}')
    triple_name = 'decode-a52-r210-rs48-triple.py'
    (output / triple_name).write_text(triple)

    print(f'created {core_name}, {base_name}, {triple_name}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
