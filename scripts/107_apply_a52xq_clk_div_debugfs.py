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


def patch_clk_regmap_div(gki: Path) -> dict[str, int]:
    header = gki / 'drivers/clk/qcom/clk-regmap-divider.h'
    source = gki / 'drivers/clk/qcom/clk-regmap-divider.c'
    htext = read(header)
    ctext = read(source)
    result = {'flags_field': 0, 'helper_uses': 0}

    struct_pattern = re.compile(
        r'(struct clk_regmap_div \{\n'
        r'\s*u32\s+reg;\n'
        r'\s*u32\s+shift;\n'
        r'\s*u32\s+width;\n)'
    )
    if not re.search(r'struct clk_regmap_div \{.*?\bu8\s+flags\s*;', htext, re.S):
        htext, count = struct_pattern.subn(
            r'\1\tu8\t\t\tflags;\n',
            htext,
            count=1,
        )
        if count != 1:
            raise SystemExit('clk_regmap_div flags insertion anchor not found')
        result['flags_field'] = 1

    old = 'CLK_DIVIDER_ROUND_CLOSEST'
    new = '(divider->flags | CLK_DIVIDER_ROUND_CLOSEST)'
    helper_patterns = (
        r'(divider_ro_round_rate\([^;]*?,\s*)' + re.escape(old) + r'(\s*,\s*val\);)',
        r'(divider_round_rate\([^;]*?,\s*)' + re.escape(old) + r'(\s*\);)',
        r'(divider_get_val\([^;]*?,\s*)' + re.escape(old) + r'(\s*\);)',
        r'(divider_recalc_rate\([^;]*?,\s*)' + re.escape(old) + r'(\s*,\s*divider->width\);)',
    )
    for pattern in helper_patterns:
        ctext, count = re.subn(pattern, rf'\1{new}\2', ctext, count=1, flags=re.S)
        result['helper_uses'] += count

    if result['helper_uses'] != 4:
        raise SystemExit(
            f'expected four clk divider helper adaptations, found {result["helper_uses"]}'
        )

    write(header, htext)
    write(source, ctext)
    return result


def matching_delimiter(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    state = 'code'
    index = start
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ''
        if state == 'code':
            if char == '/' and nxt == '*':
                state = 'block-comment'
                index += 2
                continue
            if char == '/' and nxt == '/':
                state = 'line-comment'
                index += 2
                continue
            if char == '"':
                state = 'string'
                index += 1
                continue
            if char == "'":
                state = 'char'
                index += 1
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index
        elif state == 'block-comment':
            if char == '*' and nxt == '/':
                state = 'code'
                index += 2
                continue
        elif state == 'line-comment':
            if char == '\n':
                state = 'code'
        elif state in {'string', 'char'}:
            if char == '\\':
                index += 2
                continue
            if (state == 'string' and char == '"') or (state == 'char' and char == "'"):
                state = 'code'
        index += 1
    raise SystemExit(f'unmatched {opening} at offset {start}')


def patch_void_debugfs_conditionals(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r'(?m)^(?P<indent>[ \t]*)if\s*\(\s*!\s*'
        r'(?P<fn>debugfs_create_(?:u32|u64))\s*\('
    )
    count = 0
    search_from = 0
    while True:
        match = pattern.search(text, search_from)
        if not match:
            break
        call_open = text.find('(', match.start('fn') + len(match.group('fn')))
        call_close = matching_delimiter(text, call_open, '(', ')')
        cursor = call_close + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != ')':
            search_from = match.end()
            continue
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != '{':
            search_from = match.end()
            continue
        block_close = matching_delimiter(text, cursor, '{', '}')
        end = block_close + 1
        if end < len(text) and text[end] == '\n':
            end += 1
        call = text[match.start('fn'):call_close + 1]
        replacement = match.group('indent') + call + ';\n'
        text = text[:match.start()] + replacement + text[end:]
        search_from = match.start() + len(replacement)
        count += 1
    return text, count


def patch_void_debugfs_assignments(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r'(?m)^(?P<indent>[ \t]*)(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*'
        r'(?P<fn>debugfs_create_(?:u32|u64))\s*\('
    )
    count = 0
    search_from = 0
    while True:
        match = pattern.search(text, search_from)
        if not match:
            break
        call_open = text.find('(', match.start('fn') + len(match.group('fn')))
        call_close = matching_delimiter(text, call_open, '(', ')')
        semicolon = call_close + 1
        while semicolon < len(text) and text[semicolon].isspace() and text[semicolon] != '\n':
            semicolon += 1
        if semicolon >= len(text) or text[semicolon] != ';':
            search_from = match.end()
            continue
        statement_end = semicolon + 1
        if statement_end < len(text) and text[statement_end] == '\n':
            statement_end += 1

        check = re.compile(
            rf'(?m)^[ \t]*if\s*\(\s*IS_ERR_OR_NULL\(\s*{re.escape(match.group("var"))}\s*\)\s*\)\s*\{{'
        ).match(text, statement_end)
        if not check:
            search_from = statement_end
            continue
        brace = text.find('{', check.start())
        block_close = matching_delimiter(text, brace, '{', '}')
        end = block_close + 1
        if end < len(text) and text[end] == '\n':
            end += 1
        call = text[match.start('fn'):call_close + 1]
        replacement = match.group('indent') + call + ';\n'
        text = text[:match.start()] + replacement + text[end:]
        search_from = match.start() + len(replacement)
        count += 1
    return text, count


def patch_debugfs_value_files(gki: Path) -> dict[str, object]:
    conditionals = 0
    assignments = 0
    changed_files: list[str] = []
    for path in display_sources(gki):
        before = read(path)
        text, conditional_count = patch_void_debugfs_conditionals(before)
        text, assignment_count = patch_void_debugfs_assignments(text)
        if text != before:
            write(path, text)
            changed_files.append(str(path.relative_to(gki)))
        conditionals += conditional_count
        assignments += assignment_count
    return {
        'conditionals': conditionals,
        'assignments': assignments,
        'changed_files': sorted(changed_files),
        'error_reporting_semantics': (
            'Android 5.10 debugfs scalar helpers return void, so scalar-node '
            'creation is no longer treated as a recoverable per-node error'
        ),
    }


def count_pattern(gki: Path, pattern: re.Pattern[str]) -> list[str]:
    locations: list[str] = []
    for path in display_sources(gki):
        for line_number, line in enumerate(read(path).splitlines(), 1):
            if pattern.search(line):
                locations.append(f'{path.relative_to(gki)}:{line_number}:{line.strip()}')
    return locations


def validate_clk_div(gki: Path) -> dict[str, bool]:
    header = read(gki / 'drivers/clk/qcom/clk-regmap-divider.h')
    source = read(gki / 'drivers/clk/qcom/clk-regmap-divider.c')
    checks = {
        'flags_field': re.search(
            r'struct clk_regmap_div \{.*?\bu8\s+flags\s*;', header, re.S
        ) is not None,
        'round_rate_flags': source.count(
            '(divider->flags | CLK_DIVIDER_ROUND_CLOSEST)'
        ) == 4,
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        'status': 'phase5-clk-divider-debugfs-staged',
        'flashable': False,
        'hardware_validated': False,
        'clock_divider': patch_clk_regmap_div(gki),
        'debugfs_scalar_api': patch_debugfs_value_files(gki),
    }
    report['clock_validation'] = validate_clk_div(gki)
    report['remaining_void_debugfs_conditionals'] = count_pattern(
        gki, re.compile(r'if\s*\(\s*!\s*debugfs_create_(?:u32|u64)\s*\(')
    )
    report['remaining_void_debugfs_assignments'] = count_pattern(
        gki,
        re.compile(
            r'\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*debugfs_create_(?:u32|u64)\s*\('
        ),
    )

    failures = []
    if report['clock_divider']['flags_field'] != 1:
        failures.append('clk_regmap_div flags field was not added')
    if report['clock_divider']['helper_uses'] != 4:
        failures.append(
            f'expected four divider helper adaptations, found '
            f'{report["clock_divider"]["helper_uses"]}'
        )
    if report['debugfs_scalar_api']['conditionals'] < 34:
        failures.append(
            f'expected at least 34 mirrored scalar debugfs conditionals, found '
            f'{report["debugfs_scalar_api"]["conditionals"]}'
        )
    if report['debugfs_scalar_api']['assignments'] < 2:
        failures.append(
            f'expected at least 2 mirrored scalar debugfs assignments, found '
            f'{report["debugfs_scalar_api"]["assignments"]}'
        )
    failures.extend(
        f'clock validation failed: {name}'
        for name, passed in report['clock_validation'].items()
        if not passed
    )
    if report['remaining_void_debugfs_conditionals']:
        failures.append('void debugfs scalar conditionals remain')
    if report['remaining_void_debugfs_assignments']:
        failures.append('void debugfs scalar assignments remain')

    (output / 'phase5-clk-divider-debugfs-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    if failures:
        raise SystemExit('Workflow 107 staging validation failed: ' + '; '.join(failures))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
