#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

ION_REL = Path('drivers/staging/android/ion/ion.c')
REPORT = 'phase23-ion-system-heap-secure-gate-report.json'
MARKER = 'A52_ION_SYSTEM_HEAP_NONSECURE_GATE'

OLD = '''\t\tif (fd == -ENODEV &&\n\t\t\t(data.allocation.heap_id_mask & (1U << 25))) {'''
NEW = '''\t\t/* A52_ION_SYSTEM_HEAP_NONSECURE_GATE\n\t\t * Downstream system heap 25 rejects secure/CP allocations. Do not\n\t\t * translate those requests to ACK generic HLOS system memory.\n\t\t */\n\t\tif (fd == -ENODEV &&\n\t\t\t(data.allocation.heap_id_mask & (1U << 25)) &&\n\t\t\t!(data.allocation.flags &\n\t\t\t  (ION_FLAGS_CP_MASK | ION_FLAG_SECURE))) {'''


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def patch(path: Path) -> dict[str, object]:
    text = read(path)
    if MARKER in text:
        if text.count(MARKER) != 1:
            raise SystemExit('secure-gate marker count is not one')
        state = 'already-present'
    else:
        count = text.count(OLD)
        if count != 1:
            raise SystemExit(f'secure-gate anchor expected 1, found {count}')
        text = text.replace(OLD, NEW, 1)
        state = 'inserted'

    required = (
        MARKER,
        'ION_FLAGS_CP_MASK | ION_FLAG_SECURE',
        '!(data.allocation.flags &',
        'A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT',
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit('secure-gate audit failed: ' + ', '.join(missing))
    path.write_text(text, encoding='utf-8')
    return {
        'source': str(ION_REL),
        'state': state,
        'legacy_system_heap_id': 25,
        'retry_limited_to_nonsecure': True,
        'secure_and_cp_requests_preserved': True,
    }


def self_test() -> None:
    sample = '''/* A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT */\nstatic void f(void)\n{\n\t\tif (fd == -ENODEV &&\n\t\t\t(data.allocation.heap_id_mask & (1U << 25))) {\n\t\t\tfd = 0;\n\t\t}\n}\n'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ION_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sample, encoding='utf-8')
        first = patch(path)
        second = patch(path)
        if first['state'] != 'inserted' or second['state'] != 'already-present':
            raise SystemExit('secure-gate idempotence self-test failed')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = root / ION_REL
    if not path.is_file():
        raise SystemExit(f'missing staged ACK ION source: {path}')

    result = patch(path)
    report = {
        'status': 'ion-system-heap-secure-gate-staged',
        'hardware_validated': False,
        'payload_capture': False,
        'reason': (
            'Samsung system heap 25 rejects secure VMID allocations; translating '
            'those flags to ACK generic system memory would weaken the contract'
        ),
        'fix': result,
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
