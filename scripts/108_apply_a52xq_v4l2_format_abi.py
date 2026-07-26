#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DISPLAY_ROOTS = (
    'drivers/a52_display',
    'techpack/display',
)
VENDOR_GENERIC_FORMATS = (
    'V4L2_PIX_FMT_RGBA8888_UBWC',
    'V4L2_PIX_FMT_NV12_UBWC',
    'V4L2_PIX_FMT_NV12_TP10_UBWC',
    'V4L2_PIX_FMT_NV12_P010_UBWC',
)


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def write(path: Path, text: str) -> None:
    path.write_text(text)


def display_sources(gki: Path):
    seen: set[Path] = set()
    for rel in DISPLAY_ROOTS:
        root = gki / rel
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if path.is_file() and path.suffix in {'.c', '.h'} and path not in seen:
                seen.add(path)
                yield path


def extract_macro(text: str, name: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(rf'^\s*#define\s+{re.escape(name)}\b', line):
            block = [line]
            while block[-1].rstrip().endswith('\\'):
                index += 1
                if index >= len(lines):
                    raise SystemExit(f'unterminated macro {name}')
                block.append(lines[index])
            return '\n'.join(block)
    raise SystemExit(f'missing TouchGrass pixel-format macro: {name}')


def vendor_format_names(text: str) -> list[str]:
    names = {
        match.group(1)
        for match in re.finditer(
            r'^\s*#define\s+(V4L2_PIX_FMT_SDE_[A-Z0-9_]+)\b',
            text,
            flags=re.M,
        )
    }
    names.update(
        name
        for name in VENDOR_GENERIC_FORMATS
        if re.search(rf'^\s*#define\s+{re.escape(name)}\b', text, flags=re.M)
    )
    return sorted(names)


def patch_pixel_formats(touchgrass: Path, gki: Path) -> dict[str, object]:
    source = touchgrass / 'include/uapi/linux/videodev2.h'
    target = gki / 'include/uapi/linux/videodev2.h'
    source_text = read(source)
    target_text = read(target)
    available = vendor_format_names(source_text)
    added: list[str] = []
    blocks: list[str] = []

    for name in available:
        if re.search(rf'^\s*#define\s+{re.escape(name)}\b', target_text, flags=re.M):
            continue
        blocks.append(extract_macro(source_text, name))
        added.append(name)

    if blocks:
        marker = '/* SDR formats - used only for Software Defined Radio devices */'
        insertion = (
            '/* A52 downstream SDE and Qualcomm pixel-format ABI */\n'
            + '\n'.join(blocks)
            + '\n\n'
        )
        if marker in target_text:
            target_text = target_text.replace(marker, insertion + marker, 1)
        else:
            index = target_text.rfind('#endif')
            if index < 0:
                raise SystemExit('native videodev2.h has no closing #endif')
            target_text = target_text[:index] + insertion + target_text[index:]
        write(target, target_text)

    return {
        'touchgrass_macro_count': len(available),
        'added_macro_count': len(added),
        'added_macros': added,
    }


def patch_vfl_grabber_alias(gki: Path) -> int:
    path = gki / 'a52-port-compat.h'
    text = read(path)
    if re.search(r'^\s*#define\s+VFL_TYPE_GRABBER\b', text, flags=re.M):
        return 0
    block = (
        '\n/* A52 legacy V4L2 video-device type name. */\n'
        '#define VFL_TYPE_GRABBER VFL_TYPE_VIDEO\n'
    )
    index = text.rfind('#endif')
    if index < 0:
        raise SystemExit('a52-port-compat.h has no closing #endif')
    write(path, text[:index] + block + text[index:])
    return 1


def used_vendor_tokens(gki: Path) -> list[str]:
    tokens: set[str] = set()
    pattern = re.compile(r'\bV4L2_PIX_FMT_[A-Z0-9_]+\b')
    for path in display_sources(gki):
        for token in pattern.findall(read(path)):
            if token.startswith('V4L2_PIX_FMT_SDE_') or token in VENDOR_GENERIC_FORMATS:
                tokens.add(token)
    return sorted(tokens)


def defined_macros(path: Path) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(
            r'^\s*#define\s+(V4L2_PIX_FMT_[A-Z0-9_]+)\b',
            read(path),
            flags=re.M,
        )
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--touchgrass', type=Path, required=True)
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    touchgrass = args.touchgrass.resolve()
    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    formats = patch_pixel_formats(touchgrass, gki)
    alias = patch_vfl_grabber_alias(gki)
    used = used_vendor_tokens(gki)
    defined = defined_macros(gki / 'include/uapi/linux/videodev2.h')
    missing = sorted(set(used) - defined)

    report = {
        'status': 'phase6-v4l2-format-abi-staged',
        'flashable': False,
        'hardware_validated': False,
        'pixel_formats': formats,
        'vfl_type_grabber_alias_added': alias,
        'used_vendor_format_count': len(used),
        'used_vendor_formats': used,
        'missing_used_vendor_formats': missing,
        'vfl_type_grabber_alias_present': (
            '#define VFL_TYPE_GRABBER VFL_TYPE_VIDEO'
            in read(gki / 'a52-port-compat.h')
        ),
    }

    failures = []
    if formats['added_macro_count'] < 30:
        failures.append(
            f'expected at least 30 vendor pixel-format macros, found '
            f'{formats["added_macro_count"]}'
        )
    if alias != 1:
        failures.append('VFL_TYPE_GRABBER alias was not added')
    if missing:
        failures.append('used vendor pixel formats remain undefined')
    if not report['vfl_type_grabber_alias_present']:
        failures.append('VFL_TYPE_GRABBER alias validation failed')

    (output / 'phase6-v4l2-format-abi-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    if failures:
        raise SystemExit('Workflow 108 staging validation failed: ' + '; '.join(failures))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
