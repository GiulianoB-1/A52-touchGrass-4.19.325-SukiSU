#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

APIS = ('rpmh_mode_solver_set', 'rpmh_flush')


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def nm(path: Path, undefined: bool = False) -> dict:
    if not path.exists():
        return {'exists': False, 'path': str(path), 'matches': []}
    cmd = ['llvm-nm'] + (['-u'] if undefined else ['-n']) + [str(path)]
    cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, check=False, timeout=60)
    return {
        'exists': True,
        'path': str(path),
        'returncode': cp.returncode,
        'matches': [line for line in cp.stdout.splitlines()
                    if any(api in line for api in APIS)],
    }


def function_signatures(text: str, name: str) -> list[str]:
    pat = re.compile(
        r'(?m)^\s*(?:static\s+)?(?:inline\s+)?(?:int|void|bool)\s+'
        + re.escape(name) + r'\s*\([^;{]*\)'
    )
    return [' '.join(m.group(0).split()) for m in pat.finditer(text)]


def macro_lines(text: str, name: str) -> list[str]:
    pat = re.compile(r'(?m)^\s*#\s*define\s+' + re.escape(name) + r'\b.*$')
    return [m.group(0).strip() for m in pat.finditer(text)]


def call_count(text: str, name: str) -> int:
    return len(re.findall(r'\b' + re.escape(name) + r'\s*\(', text))


def source_context(text: str, needle: str, radius: int = 12) -> list[str]:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if needle in line:
            lo = max(0, idx - radius)
            hi = min(len(lines), idx + radius + 1)
            return [f'{i + 1}: {lines[i]}' for i in range(lo, hi)]
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--touchgrass', type=Path, required=True)
    ap.add_argument('--gki', type=Path, required=True)
    ap.add_argument('--build-out', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()

    tg = a.touchgrass.resolve()
    gki = a.gki.resolve()
    bout = a.build_out.resolve()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    required = [
        tg / 'techpack/display/msm/sde_rsc.c',
        tg / 'drivers/soc/qcom/rpmh.c',
        gki / 'drivers/a52_display/msm/sde_rsc.c',
        gki / 'drivers/soc/qcom/rpmh.c',
        gki / 'a52-port-compat.h',
    ]
    for p in required:
        if not p.exists():
            raise SystemExit(f'missing required input: {p}')

    tg_sde = read(tg / 'techpack/display/msm/sde_rsc.c')
    tg_rpmh = read(tg / 'drivers/soc/qcom/rpmh.c')
    gki_sde = read(gki / 'drivers/a52_display/msm/sde_rsc.c')
    gki_rpmh = read(gki / 'drivers/soc/qcom/rpmh.c')
    compat = read(gki / 'a52-port-compat.h')

    obj = nm(bout / 'drivers/a52_display/msm/sde_rsc.o', undefined=True)
    vmlinux = nm(bout / 'vmlinux', undefined=False)

    phase13_markers = {
        'all_known': 'A52_PHASE13_ALL_KNOWN_COMPAT_SHIMS' in compat,
        'diagnostic_non_flashable': 'diagnostic, non-flashable' in compat,
        'secondary_compile_only': 'A52_PHASE13_SECONDARY_COMPAT: compile-only follow-up.' in compat,
    }

    apis = {}
    for name in APIS:
        tg_calls = call_count(tg_sde, name)
        gki_calls = call_count(gki_sde, name)
        macros = macro_lines(compat, name)
        object_refs = [x for x in obj.get('matches', []) if name in x]
        linked = [x for x in vmlinux.get('matches', []) if name in x]
        tg_sigs = function_signatures(tg_rpmh, name)
        gki_sigs = function_signatures(gki_rpmh, name)
        if macros and gki_calls and not object_refs:
            if name == 'rpmh_flush' and gki_sigs and tg_sigs and gki_sigs != tg_sigs:
                classification = 'PROVEN_PHASE13_MACRO_ERASURE_WITH_API_DRIFT'
            else:
                classification = 'PROVEN_PHASE13_MACRO_ERASURE'
        elif gki_calls and object_refs:
            classification = 'CALL_SURVIVES_PREPROCESSING'
        elif not gki_calls:
            classification = 'CALL_NOT_PRESENT_IN_RECONSTRUCTED_SDE'
        else:
            classification = 'UNRESOLVED'
        apis[name] = {
            'classification': classification,
            'touchgrass_sde_call_count': tg_calls,
            'phase296_sde_call_count': gki_calls,
            'compat_macro_lines': macros,
            'touchgrass_rpmh_signatures': tg_sigs,
            'phase296_rpmh_signatures': gki_sigs,
            'sde_rsc_object_references': object_refs,
            'vmlinux_symbols': linked,
        }

    flush_ctx = source_context(gki_sde, 'rpmh_flush(rsc->rpmh_dev)', 16)
    solver_ctx = source_context(gki_sde, 'rpmh_mode_solver_set(rsc->rpmh_dev', 10)

    result = {
        'artifact_type': 'phase299s-phase13-rpmh-runtime-shim-audit-not-flashable',
        'phase13_markers': phase13_markers,
        'apis': apis,
        'sde_rsc_object': obj,
        'vmlinux': vmlinux,
        'phase296_files': {
            'a52-port-compat.h': sha256(gki / 'a52-port-compat.h'),
            'drivers/a52_display/msm/sde_rsc.c': sha256(gki / 'drivers/a52_display/msm/sde_rsc.c'),
            'drivers/soc/qcom/rpmh.c': sha256(gki / 'drivers/soc/qcom/rpmh.c'),
            'drivers/soc/qcom/rpmh-rsc.c': sha256(gki / 'drivers/soc/qcom/rpmh-rsc.c'),
        },
        'touchgrass_files': {
            'techpack/display/msm/sde_rsc.c': sha256(tg / 'techpack/display/msm/sde_rsc.c'),
            'drivers/soc/qcom/rpmh.c': sha256(tg / 'drivers/soc/qcom/rpmh.c'),
            'drivers/soc/qcom/rpmh-rsc.c': sha256(tg / 'drivers/soc/qcom/rpmh-rsc.c'),
        },
        'phase296_flush_call_context': flush_ctx,
        'phase296_solver_call_context': solver_ctx,
    }
    result['combined_classification'] = (
        'PROVEN_PHASE13_RUNTIME_SEMANTIC_ERASURE'
        if all(apis[n]['classification'].startswith('PROVEN_PHASE13_MACRO_ERASURE') for n in APIS)
        else 'PARTIAL_OR_UNRESOLVED'
    )

    (out / 'phase13-rpmh-runtime-shims.json').write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n')

    report = [
        '# Phase299S Phase13 RPMh runtime-shim audit',
        '',
        'Source/build evidence only. This workflow does not create a boot image and does not change kernel behavior.',
        '',
        f'**Combined classification: `{result["combined_classification"]}`**',
        '',
        '## Phase13 provenance markers',
        '',
    ]
    for key, value in phase13_markers.items():
        report.append(f'- {key}: `{value}`')
    report += ['', '## API results', '']
    for name in APIS:
        x = apis[name]
        report += [
            f'### `{name}()`',
            '',
            f'- classification: `{x["classification"]}`',
            f'- TouchGrass SDE call count: `{x["touchgrass_sde_call_count"]}`',
            f'- reconstructed Phase296 SDE call count: `{x["phase296_sde_call_count"]}`',
            f'- compatibility macro: `{x["compat_macro_lines"]}`',
            f'- TouchGrass provider signature(s): `{x["touchgrass_rpmh_signatures"]}`',
            f'- Phase296 5.10 provider signature(s): `{x["phase296_rpmh_signatures"]}`',
            f'- unresolved reference in compiled `sde_rsc.o`: `{x["sde_rsc_object_references"]}`',
            f'- final `vmlinux` symbol(s): `{x["vmlinux_symbols"]}`',
            '',
        ]
    report += [
        '## Interpretation',
        '',
        'A downstream SDE source call that remains visible in reconstructed source but has no unresolved reference in the compiled SDE object is being removed before link time. When the exact compatibility header contains a no-op macro for that API, this is direct build evidence of semantic erasure rather than a missing linker provider.',
        '',
        'If both APIs classify as proven Phase13 macro erasure, the next hardware phase should instrument the call sites and the underlying RPMh write/cache/control-TCS path without restoring either behavior. That separates runtime consequence from repair.',
        '',
        '## Phase296 `rpmh_flush()` call context',
        '',
        '```c',
        *flush_ctx,
        '```',
        '',
    ]
    (out / 'REPORT.md').write_text('\n'.join(report) + '\n')

    with (out / 'SHA256SUMS').open('w') as f:
        for p in sorted(x for x in out.iterdir() if x.is_file() and x.name != 'SHA256SUMS'):
            f.write(f'{sha256(p)}  {p.name}\n')

    print((out / 'REPORT.md').read_text())


if __name__ == '__main__':
    main()
