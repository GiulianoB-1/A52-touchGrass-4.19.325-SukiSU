#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

ION_REL = Path('drivers/staging/android/ion/ion.c')
REPORT = 'phase23-ion-system-heap-secure-gate-report.json'
MARKER = 'A52_ION_SYSTEM_HEAP_NONSECURE_GATE'
SAMSUNG_CP_MASK = '0x6FFE0000U'
SAMSUNG_SECURE_BIT = '(1U << 31)'

OLD = '''\t\tif (fd == -ENODEV &&\n\t\t\t(data.allocation.heap_id_mask & (1U << 25))) {'''
NEW = '''\t\t/* A52_ION_SYSTEM_HEAP_NONSECURE_GATE
\t\t * Downstream system heap 25 rejects secure/CP allocations. Samsung's
\t\t * ION_FLAGS_CP_MASK is 0x6FFE0000 and ION_FLAG_SECURE is bit 31.
\t\t * Keep those ABI constants local because generic ACK ION does not have
\t\t * the A52 vendor msm_ion.h include path.
\t\t */
\t\tif (fd == -ENODEV &&
\t\t\t(data.allocation.heap_id_mask & (1U << 25)) &&
\t\t\t!(data.allocation.flags &
\t\t\t  (0x6FFE0000U | (1U << 31)))) {'''


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8', errors='replace')


def patch(path: Path) -> dict[str, object]:
    text = read(path)
    # Retire the short-lived include-based variant if it is present in a staged tree.
    text = text.replace('#include <linux/msm_ion.h>\n', '')

    if MARKER in text:
        if text.count(MARKER) != 1:
            raise SystemExit('secure-gate marker count is not one')
        if 'ION_FLAGS_CP_MASK | ION_FLAG_SECURE' in text:
            old_condition = '(ION_FLAGS_CP_MASK | ION_FLAG_SECURE)'
            if text.count(old_condition) != 1:
                raise SystemExit('old secure-gate condition count is not one')
            text = text.replace(
                old_condition,
                f'({SAMSUNG_CP_MASK} | {SAMSUNG_SECURE_BIT})',
                1,
            )
            text = text.replace(
                'Downstream system heap 25 rejects secure/CP allocations. Do not\n'
                '\t\t * translate those requests to ACK generic HLOS system memory.\n',
                "Downstream system heap 25 rejects secure/CP allocations. Samsung's\n"
                '\t\t * ION_FLAGS_CP_MASK is 0x6FFE0000 and ION_FLAG_SECURE is bit 31.\n'
                '\t\t * Keep those ABI constants local because generic ACK ION does not have\n'
                '\t\t * the A52 vendor msm_ion.h include path.\n',
                1,
            )
            state = 'updated-local-masks'
        else:
            state = 'already-present'
    else:
        count = text.count(OLD)
        if count != 1:
            raise SystemExit(f'secure-gate anchor expected 1, found {count}')
        text = text.replace(OLD, NEW, 1)
        state = 'inserted'

    required = (
        MARKER,
        SAMSUNG_CP_MASK,
        SAMSUNG_SECURE_BIT,
        '!(data.allocation.flags &',
        'A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT',
        'generic ACK ION does not have',
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit('secure-gate audit failed: ' + ', '.join(missing))
    forbidden = ('#include <linux/msm_ion.h>', 'ION_FLAGS_CP_MASK | ION_FLAG_SECURE')
    present = [item for item in forbidden if item in text]
    if present:
        raise SystemExit('secure-gate forbidden dependency remains: ' + ', '.join(present))

    path.write_text(text, encoding='utf-8')
    return {
        'source': str(ION_REL),
        'state': state,
        'legacy_system_heap_id': 25,
        'retry_limited_to_nonsecure': True,
        'secure_and_cp_requests_preserved': True,
        'samsung_cp_mask': SAMSUNG_CP_MASK,
        'samsung_secure_bit': 31,
        'vendor_header_dependency': False,
    }


def self_test() -> None:
    sample = '''/* A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT */
#include <linux/kernel.h>
static void f(void)
{
\t\tif (fd == -ENODEV &&
\t\t\t(data.allocation.heap_id_mask & (1U << 25))) {
\t\t\tfd = 0;
\t\t}
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ION_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sample, encoding='utf-8')
        first = patch(path)
        second = patch(path)
        staged = path.read_text(encoding='utf-8')
        if first['state'] != 'inserted' or second['state'] != 'already-present':
            raise SystemExit('secure-gate idempotence self-test failed')
        if SAMSUNG_CP_MASK not in staged or SAMSUNG_SECURE_BIT not in staged:
            raise SystemExit('secure-gate local-mask self-test failed')
        if 'msm_ion.h' in staged:
            raise SystemExit('secure-gate self-test retained vendor include')


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
