#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SOURCE_SUFFIXES = {'.c', '.h'}
PORT_ROOTS = (
    'drivers/gpu/msm',
    'drivers/a52_display',
    'techpack/display',
    'drivers/a52_secure',
)


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def write(path: Path, text: str) -> None:
    path.write_text(text)


def source_files(gki: Path):
    seen: set[Path] = set()
    for rel in PORT_ROOTS:
        root = gki / rel
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if path.is_file() and path.suffix in SOURCE_SUFFIXES and path not in seen:
                seen.add(path)
                yield path


def replace_regex(path: Path, pattern: str, replacement: str, flags: int = 0) -> int:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, flags=flags)
    if count:
        write(path, updated)
    return count


def patch_local_pr_fmt(gki: Path) -> dict[str, int]:
    files = 0
    definitions = 0
    for path in source_files(gki):
        lines = read(path).splitlines(keepends=True)
        output: list[str] = []
        changed = 0
        for line in lines:
            if re.match(r'^\s*#define\s+pr_fmt\b', line):
                recent = ''.join(output[-4:])
                if '#undef pr_fmt' not in recent:
                    output.extend((
                        '#ifdef pr_fmt\n',
                        '#undef pr_fmt\n',
                        '#endif\n',
                    ))
                    changed += 1
            output.append(line)
        if changed:
            write(path, ''.join(output))
            files += 1
            definitions += changed
    return {'files': files, 'definitions': definitions}


def remove_affinity_block(path: Path) -> int:
    pattern = re.compile(
        r'(?m)^(?P<i>[ \t]*)cpumask_empty\(&[^\n]*cpus_affine\);\n'
        r'(?P=i)for_each_possible_cpu\(cpu\) \{\n'
        r'(?:^(?!\s*\}\s*$).*\n)*?'
        r'(?P=i)\}\n'
    )
    text = read(path)
    updated, count = pattern.subn(
        lambda m: m.group('i') + '/* Android 5.10 CPU latency QoS is global; affinity is deferred. */\n',
        text,
    )
    if count:
        write(path, updated)
    return count


def remove_cpu_declaration_from_function(path: Path, function_name: str) -> int:
    text = read(path)
    pattern = re.compile(
        rf'(?P<head>\b{re.escape(function_name)}\s*\([^)]*\)\s*\{{(?:(?!^\}}).)*?)'
        r'^[ \t]*int cpu;\n',
        re.M | re.S,
    )
    updated, count = pattern.subn(lambda m: m.group('head'), text, count=1)
    if count:
        write(path, updated)
    return count


def patch_pm_qos_file(path: Path) -> dict[str, int]:
    counts = {
        'affinity_blocks': remove_affinity_block(path),
        'request_type_assignments': 0,
        'request_irq_assignments': 0,
        'add_requests': 0,
        'update_requests': 0,
        'timeout_updates': 0,
        'remove_requests': 0,
    }

    # Remove the downstream explanation that names APIs no longer present in
    # Android 5.10. The semantic limitation is recorded in the report instead.
    replace_regex(
        path,
        r'(?s)/\*\n[ \t]*\* The default request type PM_QOS_REQ_ALL_CORES is.*?\*/\n',
        '/* Android 5.10 exposes a global CPU latency QoS vote. */\n',
    )

    counts['request_type_assignments'] += replace_regex(
        path,
        r'(?m)^[ \t]*[^\n;]+(?:->|\.)type\s*=\s*PM_QOS_REQ_[A-Z_]+;\n',
        '\t/* Legacy per-request QoS type is not present in Android 5.10. */\n',
    )
    counts['request_irq_assignments'] += replace_regex(
        path,
        r'(?m)^[ \t]*[^\n;]*pm_qos_req_dma\.irq\s*=\s*[^;]+;\n',
        '\t/* Legacy IRQ-affine QoS selection is deferred. */\n',
    )

    # The Android 5.10 CPU latency API has no timeout helper. Keep the vote
    # active until the next explicit update or removal, and record that semantic
    # gap in the generated report.
    counts['timeout_updates'] += replace_regex(
        path,
        r'pm_qos_update_request_timeout\(\s*([^,]+),\s*([^,]+),\s*[^)]+\)',
        r'cpu_latency_qos_update_request(\1, \2)',
        flags=re.S,
    )
    counts['add_requests'] += replace_regex(
        path,
        r'pm_qos_add_request\(\s*([^,]+),\s*PM_QOS_CPU_DMA_LATENCY,\s*([^)]+)\)',
        r'cpu_latency_qos_add_request(\1, \2)',
        flags=re.S,
    )
    counts['update_requests'] += replace_regex(
        path, r'\bpm_qos_update_request\(', 'cpu_latency_qos_update_request('
    )
    counts['remove_requests'] += replace_regex(
        path, r'\bpm_qos_remove_request\(', 'cpu_latency_qos_remove_request('
    )
    return counts


def patch_pm_qos(gki: Path) -> dict[str, object]:
    totals = {
        'affinity_blocks': 0,
        'request_type_assignments': 0,
        'request_irq_assignments': 0,
        'add_requests': 0,
        'update_requests': 0,
        'timeout_updates': 0,
        'remove_requests': 0,
        'cpu_declarations': 0,
    }
    changed_files: list[str] = []
    for path in source_files(gki):
        before = read(path)
        counts = patch_pm_qos_file(path)
        if read(path) != before:
            changed_files.append(str(path.relative_to(gki)))
        for key, value in counts.items():
            totals[key] += value

    targets = (
        (gki / 'drivers/gpu/msm/kgsl.c', 'kgsl_device_platform_probe'),
        (gki / 'drivers/a52_display/rotator/sde_rotator_dev.c', 'sde_rotator_pm_qos_add'),
        (gki / 'techpack/display/rotator/sde_rotator_dev.c', 'sde_rotator_pm_qos_add'),
        (gki / 'drivers/a52_display/msm/sde/sde_encoder.c', '_sde_encoder_pm_qos_add_request'),
        (gki / 'techpack/display/msm/sde/sde_encoder.c', '_sde_encoder_pm_qos_add_request'),
    )
    for path, function_name in targets:
        if path.is_file():
            count = remove_cpu_declaration_from_function(path, function_name)
            totals['cpu_declarations'] += count
            if count:
                rel = str(path.relative_to(gki))
                if rel not in changed_files:
                    changed_files.append(rel)

    totals['changed_files'] = sorted(changed_files)
    totals['affinity_semantics'] = 'deferred-global-cpu-latency-vote'
    totals['timeout_semantics'] = 'deferred-vote-remains-until-next-update-or-remove'
    return totals


def find_pr_fmt_violations(gki: Path) -> list[str]:
    violations: list[str] = []
    for path in source_files(gki):
        lines = read(path).splitlines()
        for index, line in enumerate(lines):
            if not re.match(r'^\s*#define\s+pr_fmt\b', line):
                continue
            recent = '\n'.join(lines[max(0, index - 4):index])
            if '#undef pr_fmt' not in recent:
                violations.append(f'{path.relative_to(gki)}:{index + 1}')
    return violations


def count_legacy_pm_qos_tokens(gki: Path) -> dict[str, int]:
    patterns = {
        'pm_qos_add_request(': re.compile(r'(?<![A-Za-z0-9_])pm_qos_add_request\('),
        'pm_qos_update_request(': re.compile(r'(?<![A-Za-z0-9_])pm_qos_update_request\('),
        'pm_qos_update_request_timeout(': re.compile(r'(?<![A-Za-z0-9_])pm_qos_update_request_timeout\('),
        'pm_qos_remove_request(': re.compile(r'(?<![A-Za-z0-9_])pm_qos_remove_request\('),
        'PM_QOS_CPU_DMA_LATENCY': re.compile(r'\bPM_QOS_CPU_DMA_LATENCY\b'),
        'PM_QOS_REQ_AFFINE_CORES': re.compile(r'\bPM_QOS_REQ_AFFINE_CORES\b'),
        'PM_QOS_REQ_AFFINE_IRQ': re.compile(r'\bPM_QOS_REQ_AFFINE_IRQ\b'),
        'cpus_affine': re.compile(r'\bcpus_affine\b'),
    }
    counts = {token: 0 for token in patterns}
    for path in source_files(gki):
        text = read(path)
        for token, pattern in patterns.items():
            counts[token] += len(pattern.findall(text))
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        'status': 'phase4-prfmt-native-pm-qos-staged',
        'flashable': False,
        'hardware_validated': False,
        'pr_fmt': patch_local_pr_fmt(gki),
        'pm_qos': patch_pm_qos(gki),
        'semantic_debt': [
            'legacy per-core and IRQ-affine PM QoS votes are represented by global CPU latency votes',
            'legacy timed PM QoS updates remain active until the next explicit update or removal',
        ],
    }
    report['pr_fmt_violations'] = find_pr_fmt_violations(gki)
    report['legacy_pm_qos_tokens'] = count_legacy_pm_qos_tokens(gki)

    failures = []
    if report['pr_fmt']['definitions'] < 50:
        failures.append('expected at least 50 local pr_fmt definitions')
    if report['pm_qos']['add_requests'] < 5:
        failures.append('expected at least 5 PM QoS add requests')
    if report['pm_qos']['update_requests'] < 4:
        failures.append('expected at least 4 PM QoS update requests')
    if report['pm_qos']['remove_requests'] < 4:
        failures.append('expected at least 4 PM QoS remove requests')
    if report['pm_qos']['timeout_updates'] < 1:
        failures.append('expected at least 1 timed PM QoS update')
    if report['pr_fmt_violations']:
        failures.append('unprotected local pr_fmt definitions remain')
    failures.extend(
        f'legacy token remains: {token}={count}'
        for token, count in report['legacy_pm_qos_tokens'].items()
        if count
    )
    if failures:
        raise SystemExit('Workflow 106 staging validation failed: ' + '; '.join(failures))

    (output / 'phase4-prfmt-pm-qos-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
