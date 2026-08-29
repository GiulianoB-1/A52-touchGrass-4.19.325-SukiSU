#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

SELECTORS = ('171', '181', '191', '1a1', '1e1', '211')

# Authoritative matched Phase317 q2 raw values for the selected block-0 points.
# These are used only as a perturbation cross-check. No semantic labels are assigned.
PHASE317_Q2 = {
    'gki': {
        '171': 0x08000000,
        '181': 0x00110040,
        '191': 0x00210242,
        '1a1': 0x01020001,
        '1e1': 0x1e400102,
        '211': 0x4f000848,
    },
    'golden': {
        '171': 0x00000000,
        '181': 0x00010040,
        '191': 0x00000000,
        '1a1': 0x01020000,
        '1e1': 0x02080102,
        '211': 0x4f000548,
    },
}

GKI_RE = re.compile(
    r'P276 319B q=(?P<q>[012]) c=(?P<c>[0-9a-fA-F]+) '
    r'171=(?P<171>[0-9a-fA-F]+) 181=(?P<181>[0-9a-fA-F]+) '
    r'191=(?P<191>[0-9a-fA-F]+) 1a1=(?P<1a1>[0-9a-fA-F]+) '
    r'1e1=(?P<1e1>[0-9a-fA-F]+) 211=(?P<211>[0-9a-fA-F]+) '
    r'z=(?P<z>[0-9a-fA-F]+)')
GOLDEN_RE = re.compile(
    r'TG319 B q=(?P<q>[012]) c=(?P<c>[0-9a-fA-F]+) '
    r'171=(?P<171>[0-9a-fA-F]+) 181=(?P<181>[0-9a-fA-F]+) '
    r'191=(?P<191>[0-9a-fA-F]+) 1a1=(?P<1a1>[0-9a-fA-F]+) '
    r'1e1=(?P<1e1>[0-9a-fA-F]+) 211=(?P<211>[0-9a-fA-F]+) '
    r'z=(?P<z>[0-9a-fA-F]+)')


def parse(path: Path, rx: re.Pattern[str], label: str) -> dict[int, dict[str, int]]:
    text = path.read_text(errors='replace')
    out: dict[int, dict[str, int]] = {}
    counts = {0: 0, 1: 0, 2: 0}
    for m in rx.finditer(text):
        q = int(m.group('q'))
        counts[q] += 1
        out[q] = {k: int(m.group(k), 16) for k in ('c', *SELECTORS, 'z')}
    missing = [q for q in (0, 1, 2) if q not in out]
    if missing:
        raise SystemExit(f'{label}: missing Phase319 q points: {missing}')
    duplicates = {q: n for q, n in counts.items() if n != 1}
    if duplicates:
        raise SystemExit(f'{label}: expected exactly one record per q point, got {duplicates}')
    return out


def hx(v: int) -> str:
    return f'0x{v:08x}'


def baseline_check(label: str, observed: dict[int, dict[str, int]], baseline: dict[str, int]) -> bool:
    ok = True
    print(f'{label} Phase319 q2 vs Phase317 q2 raw baseline')
    print('selector   Phase319 q2   Phase317 q2          XOR   match')
    for s in SELECTORS:
        now = observed[2][s]
        old = baseline[s]
        same = now == old
        ok &= same
        print(f'0x0{s}   {hx(now)}   {hx(old)}   {hx(now ^ old)}   {"yes" if same else "NO"}')
    print()
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Compare matched Phase319 Golden/GKI six-selector temporal records as raw values only.')
    ap.add_argument('--gki', type=Path, required=True, help='Decoded GKI text containing P276 319B records')
    ap.add_argument('--golden', type=Path, required=True, help='Golden text containing TG319 B records')
    ns = ap.parse_args()

    gki = parse(ns.gki, GKI_RE, 'GKI')
    golden = parse(ns.golden, GOLDEN_RE, 'Golden')

    print('Phase319 matched raw DSI debug-bus temporal comparison')
    print('No undocumented semantic labels are assigned.')
    print()

    print('Selector values by timing point')
    print('q selector        GKI       Golden          XOR')
    for q in (0, 1, 2):
        for s in SELECTORS:
            gv, tv = gki[q][s], golden[q][s]
            print(f'{q}  0x0{s}  {hx(gv)}  {hx(tv)}  {hx(gv ^ tv)}')
        print()

    print('Within-kernel temporal transitions')
    print('selector   GKI q0^q1   GKI q1^q2   Golden q0^q1  Golden q1^q2')
    for s in SELECTORS:
        print(f'0x0{s}   {hx(gki[0][s] ^ gki[1][s])}  '
              f'{hx(gki[1][s] ^ gki[2][s])}  '
              f'{hx(golden[0][s] ^ golden[1][s])}  '
              f'{hx(golden[1][s] ^ golden[2][s])}')

    print()
    print('Cross-kernel divergence timing')
    for s in SELECTORS:
        diffs = [q for q in (0, 1, 2) if gki[q][s] != golden[q][s]]
        if not diffs:
            state = 'no raw GKI/Golden difference at q0/q1/q2'
        elif diffs[0] == 0:
            state = 'raw difference already present at q0'
        elif diffs[0] == 1:
            state = 'first raw difference appears at q1'
        else:
            state = 'first raw difference appears at q2'
        print(f'0x0{s}: {state}')

    print()
    gki_baseline_ok = baseline_check('GKI', gki, PHASE317_Q2['gki'])
    golden_baseline_ok = baseline_check('Golden', golden, PHASE317_Q2['golden'])
    if gki_baseline_ok and golden_baseline_ok:
        print('Phase317 q2 perturbation cross-check: PASS')
    else:
        print('Phase317 q2 perturbation cross-check: REVIEW REQUIRED')
        print('At least one Phase319 terminal raw value differs from the matched Phase317 q2 baseline.')

    for q in (0, 1, 2):
        if gki[q]['c'] != golden[q]['c']:
            print(f'NOTE: saved selector differs at q{q}: GKI={hx(gki[q]["c"])} Golden={hx(golden[q]["c"])}')


if __name__ == '__main__':
    main()
