#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE286C_PACKED_REPLAY_WIDTH_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1' not in text:
        raise SystemExit('Phase286C requires Phase286B typed retention')
    text = one(text,
               '/* A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1',
               '/* A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1\n * ' + MARK,
               'Phase286C marker')
    text = one(text,
               'a52_ackfr_record("P286 RH n=%llx first=%llx", (unsigned long long)total,\n\t\t(unsigned long long)first);',
               'a52_ackfr_record("P286 RH %llx %llx", (unsigned long long)total,\n\t\t(unsigned long long)first);',
               'retention header width')
    text = one(text,
               'a52_ackfr_record("P286 R0 q=%llx t=%x n=%x a=%llx b=%llx",\n\t\t\t(unsigned long long)s.seq, s.type, s.n,\n\t\t\t(unsigned long long)s.v[0], (unsigned long long)s.v[1]);',
               'a52_ackfr_record("P286 R0 %llx %x %x %llx %llx",\n\t\t\t(unsigned long long)s.seq, s.type, s.n,\n\t\t\t(unsigned long long)s.v[0], (unsigned long long)s.v[1]);',
               'R0 width')
    text = one(text,
               'a52_ackfr_record("P286 R1 q=%llx c=%llx d=%llx e=%llx",\n\t\t\t\t(unsigned long long)s.seq, (unsigned long long)s.v[2],\n\t\t\t\t(unsigned long long)s.v[3], (unsigned long long)s.v[4]);',
               'a52_ackfr_record("P286 R1 %llx %llx %llx",\n\t\t\t\t(unsigned long long)s.v[2], (unsigned long long)s.v[3],\n\t\t\t\t(unsigned long long)s.v[4]);',
               'R1 width')
    return text


def validate(text: str) -> None:
    for token in [MARK, 'P286 RH %llx %llx',
                  'P286 R0 %llx %x %x %llx %llx',
                  'P286 R1 %llx %llx %llx']:
        if token not in text:
            raise SystemExit('Phase286C marker missing: ' + token)
    for old in ['P286 RH n=%llx first=%llx',
                'P286 R0 q=%llx t=%x n=%x a=%llx b=%llx',
                'P286 R1 q=%llx c=%llx d=%llx e=%llx']:
        if old in text:
            raise SystemExit('Phase286C long replay format still present: ' + old)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    path = args.root / REC
    if not path.is_file():
        raise SystemExit('missing recorder source')
    text = path.read_text()
    if not args.check_only:
        text = patch(text)
        path.write_text(text)
    validate(text)
    print('Phase286C packed replay width: PASS')


if __name__ == '__main__':
    main()
