#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
from pathlib import Path

MAGIC = 0xA52F4420
COMMIT = 0x442C0DE5
SLOT = struct.Struct('<IIQQII')
SLOTS = 44
PHYS = (0xB1900500, 0xB1900A80, 0xB1A00800)
STAGES = [
    'START','PRE','TPG_CTRL_BEFORE','TPG_CTRL_AFTER','INIT0_BEFORE','INIT0_AFTER',
    'INIT1_BEFORE','INIT1_AFTER','DMA_CTRL_READ_BEFORE','DMA_CTRL_READ_AFTER',
    'DMA_CTRL_WRITE_BEFORE','DMA_CTRL_WRITE_AFTER','LENGTH_BEFORE','LENGTH_AFTER',
    'F1_TRIGGER_BEFORE','F1_TRIGGER_AFTER','F1_DONE','F1_TIMEOUT','RESET_BEFORE',
    'RESET_AFTER','F2_PRE','F2_TRIGGER_BEFORE','F2_TRIGGER_AFTER','F2_DONE',
    'F2_TIMEOUT','AXI2AHB_BEFORE','AXI2AHB_AFTER','TBU_0_BEFORE','TBU_0_AFTER',
    'TBU_20_BEFORE','TBU_20_AFTER','CB_FSR_BEFORE','CB_FSR_AFTER','CB_FAR_BEFORE',
    'CB_FAR_AFTER','ROUTE_BEFORE','ROUTE_AFTER','DBG_CTL_READ_BEFORE',
    'DBG_CTL_READ_AFTER','DBG_0171_SELECTED','DBG_STATUS_AFTER','DBG_RESTORE_AFTER',
    'FINAL','RESERVED43'
]


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def infer_base(path: Path, size: int) -> int:
    name = path.name.lower()
    if 'b1400000' in name or size == 7 * 1024 * 1024:
        return 0xB1400000
    if 'b1000000' in name or size == 11 * 1024 * 1024:
        return 0xB1000000
    raise SystemExit('Cannot infer physical base; pass --base 0x...')


def read_slot(blob: bytes, base: int, phys: int, stage: int):
    off = phys - base + stage * SLOT.size
    if off < 0 or off + SLOT.size > len(blob):
        return None
    raw = blob[off:off + SLOT.size]
    magic, st, ts, arg, crc, commit = SLOT.unpack(raw)
    good = magic == MAGIC and st == stage and commit == COMMIT and crc32c(raw[:24]) == crc
    present = magic == MAGIC or commit == COMMIT
    return dict(off=off, magic=magic, stage=st, ts=ts, arg=arg, crc=crc,
                commit=commit, good=good, present=present)


def main() -> int:
    ap = argparse.ArgumentParser(description='Decode Phase442 triple-copy FIFO->FETCH progress slots')
    ap.add_argument('image', type=Path)
    ap.add_argument('--base', type=lambda x: int(x, 0))
    ns = ap.parse_args()
    blob = ns.image.read_bytes()
    base = ns.base if ns.base is not None else infer_base(ns.image, len(blob))

    frontier = -1
    print(f'image={ns.image} size={len(blob)} base=0x{base:x}')
    for stage in range(SLOTS):
        rows = [read_slot(blob, base, p, stage) for p in PHYS]
        goods = [r for r in rows if r and r['good']]
        presents = [r for r in rows if r and r['present']]
        if not goods and not presents:
            continue
        if goods:
            frontier = max(frontier, stage)
        best = goods[0] if goods else presents[0]
        copies = ''.join('Y' if r and r['good'] else ('?' if r and r['present'] else '-') for r in rows)
        name = STAGES[stage] if stage < len(STAGES) else f'S{stage}'
        print(f'{stage:02d} {name:24s} copies={copies} ts={best["ts"]} arg=0x{best["arg"]:016x}')

    if frontier >= 0:
        print(f'\nfrontier={frontier:02d} {STAGES[frontier]}')
        if frontier in (2,4,6,8,10,12,14):
            print('Interpretation: FIFO leg stopped inside the next controller MMIO operation.')
        elif frontier == 15:
            print('Interpretation: FIFO SW trigger returned; no later durable outcome was reached.')
        elif frontier == 17:
            print('Interpretation: FIFO timed out; FETCH leg was intentionally skipped.')
        elif frontier >= 24 and frontier < 42:
            print('Interpretation: FETCH timed out; frontier identifies the last completed bus-forensic step.')
        elif frontier == 42:
            print('Interpretation: FETCH timeout forensics completed through debug-bus restore.')
    else:
        print('\nNo valid Phase442 slots found.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
