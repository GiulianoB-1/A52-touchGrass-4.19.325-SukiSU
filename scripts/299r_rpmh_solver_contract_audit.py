#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path

TEXT_EXT = {'.c', '.h', '.S', '.s', '.dts', '.dtsi', '.mk', '.txt', '.cfg', '.defconfig'}
TEXT_NAMES = {'Makefile', 'Kconfig'}

SYMBOLS = [
    'rpmh_mode_solver_set',
    'rpmh_write_ctrl_data',
    'rpmh_rsc_mode_solver_set',
    'rpmh_rsc_write_ctrl_data',
]
TOKENS = SYMBOLS + [
    'in_solver_mode',
    'CONTROL_TCS',
    'ACTIVE_TCS',
]

TOUCHGRASS_KEY_FILES = {
    'display_rsc': 'techpack/display/msm/sde_rsc.c',
    'rpmh_header': 'include/soc/qcom/rpmh.h',
    'rpmh_core': 'drivers/soc/qcom/rpmh.c',
    'rpmh_rsc': 'drivers/soc/qcom/rpmh-rsc.c',
}
GKI_KEY_FILES = {
    'display_rsc': 'drivers/a52_display/msm/sde_rsc.c',
    'rpmh_header': 'include/soc/qcom/rpmh.h',
    'rpmh_core': 'drivers/soc/qcom/rpmh.c',
    'rpmh_rsc': 'drivers/soc/qcom/rpmh-rsc.c',
    'display_makefile': 'drivers/a52_display/msm/Makefile',
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def is_text(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix in TEXT_EXT


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def config_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    yes = f'{key}='
    no = f'# {key} is not set'
    for line in read(path).splitlines():
        if line.startswith(yes):
            return line.split('=', 1)[1]
        if line == no:
            return 'n'
    return None


def source_locations(root: Path, label: str) -> list[dict]:
    rows: list[dict] = []
    for path in root.rglob('*'):
        if not path.is_file() or '.git' in path.parts or not is_text(path):
            continue
        try:
            text = read(path)
        except OSError:
            continue
        if not any(token in text for token in TOKENS):
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            hits = [token for token in TOKENS if token in line]
            if hits:
                rows.append({
                    'tree': label,
                    'file': rel,
                    'line': lineno,
                    'tokens': ','.join(hits),
                    'text': line.strip(),
                })
    return rows


def key_file_snapshot(root: Path, mapping: dict[str, str]) -> dict:
    out = {}
    for name, rel in mapping.items():
        p = root / rel
        item = {'path': rel, 'exists': p.exists()}
        if p.exists() and p.is_file():
            text = read(p) if is_text(p) else ''
            item['sha256'] = sha(p)
            item['bytes'] = p.stat().st_size
            item['token_counts'] = {token: text.count(token) for token in TOKENS}
            item['solver_calls'] = {
                symbol: len(re.findall(r'\b' + re.escape(symbol) + r'\s*\(', text))
                for symbol in SYMBOLS
            }
        out[name] = item
    return out


def run_nm(tool: str, path: Path, extra: list[str] | None = None) -> dict:
    if not path.exists():
        return {'exists': False, 'path': str(path), 'matches': []}
    cmd = [tool]
    if extra:
        cmd += extra
    cmd.append(str(path))
    try:
        cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'exists': True, 'path': str(path), 'error': str(exc), 'matches': []}
    matches = [line for line in cp.stdout.splitlines()
               if any(symbol in line for symbol in SYMBOLS)]
    return {
        'exists': True,
        'path': str(path),
        'returncode': cp.returncode,
        'matches': matches,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--touchgrass', type=Path, required=True)
    ap.add_argument('--gki', type=Path, required=True)
    ap.add_argument('--gki-config', type=Path, required=True)
    ap.add_argument('--build-out', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()

    tg = a.touchgrass.resolve()
    gki = a.gki.resolve()
    bout = a.build_out.resolve()
    out = a.output.resolve()
    for p in (tg, gki, a.gki_config):
        if not p.exists():
            raise SystemExit(f'missing required input: {p}')
    out.mkdir(parents=True, exist_ok=True)

    tg_rows = source_locations(tg, 'touchgrass')
    gki_rows = source_locations(gki, 'gki-reconstructed')
    rows = tg_rows + gki_rows
    with (out / 'symbol-locations.tsv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['tree', 'file', 'line', 'tokens', 'text'], delimiter='\t')
        w.writeheader()
        w.writerows(rows)

    tg_key = key_file_snapshot(tg, TOUCHGRASS_KEY_FILES)
    gki_key = key_file_snapshot(gki, GKI_KEY_FILES)
    tg_sde = tg / TOUCHGRASS_KEY_FILES['display_rsc']
    gki_sde = gki / GKI_KEY_FILES['display_rsc']

    object_path = bout / 'drivers/a52_display/msm/sde_rsc.o'
    vmlinux_path = bout / 'vmlinux'
    object_nm = run_nm('llvm-nm', object_path, ['-u'])
    vmlinux_nm = run_nm('llvm-nm', vmlinux_path, ['-n'])

    module_symvers = bout / 'Module.symvers'
    symvers_matches = []
    if module_symvers.exists():
        symvers_matches = [line for line in read(module_symvers).splitlines()
                           if any(symbol in line for symbol in SYMBOLS)]

    gki_symbol_files: dict[str, list[str]] = {}
    tg_symbol_files: dict[str, list[str]] = {}
    for symbol in SYMBOLS:
        gki_symbol_files[symbol] = sorted({r['file'] for r in gki_rows if symbol in r['tokens'].split(',')})
        tg_symbol_files[symbol] = sorted({r['file'] for r in tg_rows if symbol in r['tokens'].split(',')})

    display_identical = bool(tg_sde.exists() and gki_sde.exists() and sha(tg_sde) == sha(gki_sde))
    display_solver_counts = {}
    if gki_sde.exists():
        text = read(gki_sde)
        display_solver_counts = {s: len(re.findall(r'\b' + re.escape(s) + r'\s*\(', text)) for s in SYMBOLS}

    config = {
        'CONFIG_DRM_SDE_RSC': config_value(a.gki_config, 'CONFIG_DRM_SDE_RSC'),
        'CONFIG_QCOM_RPMH': config_value(a.gki_config, 'CONFIG_QCOM_RPMH'),
    }

    contract = {
        'artifact_type': 'phase299r-rpmh-solver-contract-audit-not-flashable',
        'touchgrass_root': str(tg),
        'gki_root': str(gki),
        'build_out': str(bout),
        'config': config,
        'display_sde_rsc_identical': display_identical,
        'reconstructed_display_solver_call_counts': display_solver_counts,
        'touchgrass_key_files': tg_key,
        'gki_key_files': gki_key,
        'touchgrass_symbol_files': tg_symbol_files,
        'gki_symbol_files': gki_symbol_files,
        'sde_rsc_object': object_nm,
        'vmlinux': vmlinux_nm,
        'module_symvers_matches': symvers_matches,
    }

    # Classification is deliberately evidence-only. No missing implementation is
    # converted into a functional fix by this audit.
    call_present = display_solver_counts.get('rpmh_mode_solver_set', 0) > 0
    source_provider_present = any(
        f != GKI_KEY_FILES['display_rsc']
        for f in gki_symbol_files.get('rpmh_mode_solver_set', [])
    )
    object_built = object_nm.get('exists', False)
    object_ref = any('rpmh_mode_solver_set' in x for x in object_nm.get('matches', []))
    linked_symbol = any('rpmh_mode_solver_set' in x for x in vmlinux_nm.get('matches', []))

    if not call_present:
        classification = 'C_OR_D: reconstructed SDE RSC no longer calls rpmh_mode_solver_set; inspect its diff for removal or translation'
    elif source_provider_present and linked_symbol:
        classification = 'A_OR_E: call retained and a linked source implementation exists; compare its semantics with TouchGrass'
    elif call_present and object_built and object_ref and not linked_symbol:
        classification = 'LINK_CONTRADICTION: built SDE RSC object references solver API but final vmlinux has no matching symbol'
    elif call_present and not source_provider_present:
        classification = 'B_OR_BUILD_GATING: call retained but no second source occurrence provides the API; inspect object/config/preprocessor path'
    else:
        classification = 'UNRESOLVED: inspect emitted source/object evidence'
    contract['classification'] = classification

    (out / 'rpmh-solver-contract.json').write_text(json.dumps(contract, indent=2, sort_keys=True) + '\n')

    lines = [
        '# Phase299R SDE RSC to RPMh solver contract audit',
        '',
        'This is source/build evidence only. It makes no kernel or hardware behavior change.',
        '',
        f'- CONFIG_DRM_SDE_RSC: `{config["CONFIG_DRM_SDE_RSC"]}`',
        f'- TouchGrass and reconstructed `sde_rsc.c` byte-identical: `{display_identical}`',
        f'- Reconstructed `rpmh_mode_solver_set()` call count in sde_rsc.c: `{display_solver_counts.get("rpmh_mode_solver_set", 0)}`',
        f'- Reconstructed `sde_rsc.o` exists: `{object_built}`',
        f'- `sde_rsc.o` has an unresolved solver reference: `{object_ref}`',
        f'- Final vmlinux exposes a solver symbol: `{linked_symbol}`',
        '',
        '## Classification',
        '',
        classification,
        '',
        '## Reconstructed source locations',
        '',
    ]
    for symbol in SYMBOLS:
        lines.append(f'### {symbol}')
        files_here = gki_symbol_files.get(symbol, [])
        if files_here:
            lines.extend(f'- `{p}`' for p in files_here)
        else:
            lines.append('- none')
        lines.append('')
    lines += [
        '## Interpretation rule',
        '',
        'If the downstream SDE RSC calls are preserved but the linked 5.10 implementation is absent or semantically different, the display can compile through compatibility work while still violating the original solver/TCS state contract. This audit only identifies that boundary. A later hardware phase must remain observation-only until the exact implementation is known.',
        '',
    ]
    (out / 'REPORT.md').write_text('\n'.join(lines))

    with (out / 'SHA256SUMS').open('w') as f:
        for p in sorted(x for x in out.rglob('*') if x.is_file() and x.name != 'SHA256SUMS'):
            f.write(f'{sha(p)}  {p.relative_to(out).as_posix()}\n')

    print(json.dumps({
        'classification': classification,
        'display_identical': display_identical,
        'solver_call_count': display_solver_counts.get('rpmh_mode_solver_set', 0),
        'sde_rsc_object_exists': object_built,
        'object_solver_reference': object_ref,
        'linked_solver_symbol': linked_symbol,
    }, indent=2))
    print((out / 'REPORT.md').read_text())


if __name__ == '__main__':
    main()
