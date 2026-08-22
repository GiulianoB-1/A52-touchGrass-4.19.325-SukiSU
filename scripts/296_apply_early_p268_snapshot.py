#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE296_EARLY_P268_STICKY_SNAPSHOT_V1'
OLD = '''static void a52_r268_snapshot(unsigned int tick)\n{\n\tif (!(tick == 120U || tick == 150U || tick == 160U ||\n\t      tick == 170U || tick == 180U))\n\t\treturn;\n'''
NEW = '''/* A52_PHASE296_EARLY_P268_STICKY_SNAPSHOT_V1\n * The current failing boot preserves only about a 17-second ramoops tail.\n * Re-emit the already-latched composer/DRM state once at heartbeat 15 so\n * open/ioctl/atomic evidence survives that short window. Observation only.\n */\nstatic void a52_r268_snapshot(unsigned int tick)\n{\n\tif (!(tick == 15U || tick == 120U || tick == 150U || tick == 160U ||\n\t      tick == 170U || tick == 180U))\n\t\treturn;\n'''

def apply(text: str) -> str:
    if MARK in text:
        return text
    n = text.count(OLD)
    if n != 1:
        raise SystemExit(f'Phase296 P268 snapshot anchor count={n}, expected 1')
    return text.replace(OLD, NEW, 1)

def verify(text: str) -> None:
    if text.count(MARK) != 1:
        raise SystemExit('Phase296 P268 marker missing/duplicated')
    if text.count('tick == 15U || tick == 120U') != 1:
        raise SystemExit('Phase296 heartbeat-15 P268 snapshot missing/duplicated')
    if 'return !strncmp(message, "P276 ", 5)' not in text:
        raise SystemExit('Inherited P276 critical contract missing')
    if 'strncmp(fmt, "P276", 4)' not in text:
        raise SystemExit('Inherited P276 admission contract missing')

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    path = args.root / REC
    if not path.is_file():
        raise SystemExit(f'missing recorder: {path}')
    text = path.read_text()
    if not args.check_only:
        text = apply(text)
        path.write_text(text)
    verify(path.read_text())
    print('Phase296 early P268 sticky snapshot: PASS')

if __name__ == '__main__':
    main()
