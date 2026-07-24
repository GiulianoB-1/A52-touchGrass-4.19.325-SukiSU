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


def replace_literal(path: Path, old: str, new: str) -> int:
    text = path.read_text(errors='replace')
    count = text.count(old)
    if count:
        path.write_text(text.replace(old, new))
    return count


def replace_regex(path: Path, pattern: str, replacement: str, flags: int = 0) -> int:
    text = path.read_text(errors='replace')
    updated, count = re.subn(pattern, replacement, text, flags=flags)
    if count:
        path.write_text(updated)
    return count


def add_include(path: Path, include: str) -> bool:
    text = path.read_text(errors='replace')
    line = f'#include <{include}>'
    if line in text:
        return False
    match = re.search(r'^(#include [^\n]+\n)', text, flags=re.M)
    if match:
        pos = match.end()
        path.write_text(text[:pos] + line + '\n' + text[pos:])
    else:
        path.write_text(line + '\n' + text)
    return True


def append_compat_includes(gki: Path) -> list[str]:
    path = gki / 'a52-port-compat.h'
    text = path.read_text(errors='replace')
    marker = '/* A52_PHASE2_NATIVE_API_INCLUDES */'
    if marker in text:
        return []
    block = (
        '\n' + marker + '\n'
        '#include <linux/interrupt.h>\n'
        '#include <linux/timekeeping.h>\n'
        '#include <linux/time64.h>\n'
        '#include <linux/dma-resv.h>\n'
        '#include <linux/fs.h>\n'
    )
    endif = text.rfind('#endif')
    if endif < 0:
        raise SystemExit('a52-port-compat.h has no closing #endif')
    path.write_text(text[:endif] + block + text[endif:])
    return [
        'linux/interrupt.h',
        'linux/timekeeping.h',
        'linux/time64.h',
        'linux/dma-resv.h',
        'linux/fs.h',
    ]


def patch_memory_accounting(gki: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    device = gki / 'drivers/gpu/msm/kgsl_device.h'
    result['mm_counter_current'] = replace_literal(
        device,
        '\tadd_mm_counter(current->mm, MM_UNRECLAIMABLE, (size >> PAGE_SHIFT));',
        '\t/* 5.10 removed MM_UNRECLAIMABLE; KGSL keeps its private byte stats. */',
    )
    result['mm_counter_task'] = replace_regex(
        device,
        r'\s*add_mm_counter\(mm, MM_UNRECLAIMABLE,\s*\n\s*-\(size >> PAGE_SHIFT\)\);',
        '\n\t\t\t\t/* 5.10 has no per-mm unreclaimable counter. */',
        flags=re.M,
    )

    page_state = 0
    for path in source_files(gki):
        page_state += replace_regex(
            path,
            r'\s*mod_node_page_state\([^;]*?NR_UNRECLAIMABLE_PAGES[^;]*?\);',
            '\n\t\t/* 5.10 removed NR_UNRECLAIMABLE_PAGES; private KGSL stats remain. */',
            flags=re.S,
        )
    result['node_page_state'] = page_state
    return result


def patch_mmap_locks(gki: Path) -> dict[str, int]:
    counts = {'read_lock': 0, 'read_unlock': 0, 'write_lock': 0, 'write_unlock': 0}
    patterns = (
        ('read_lock', r'down_read\(&([^;\n]+)->mmap_sem\);', r'mmap_read_lock(\1);'),
        ('read_unlock', r'up_read\(&([^;\n]+)->mmap_sem\);', r'mmap_read_unlock(\1);'),
        ('write_lock', r'down_write\(&([^;\n]+)->mmap_sem\);', r'mmap_write_lock(\1);'),
        ('write_unlock', r'up_write\(&([^;\n]+)->mmap_sem\);', r'mmap_write_unlock(\1);'),
    )
    for path in source_files(gki):
        for key, pattern, replacement in patterns:
            counts[key] += replace_regex(path, pattern, replacement)
    return counts


def patch_set_fs(gki: Path) -> dict[str, int]:
    path = gki / 'drivers/gpu/msm/kgsl.c'
    counts = {
        'old_fs_decl': replace_regex(path, r'^\s*mm_segment_t old_fs;\n', '', flags=re.M),
        'get_fs': replace_regex(path, r'^\s*old_fs = get_fs\(\);\n', '', flags=re.M),
        'set_fs_get_ds': replace_regex(path, r'^\s*set_fs\(get_ds\(\)\);\n', '', flags=re.M),
        'set_fs_restore': replace_regex(path, r'^\s*set_fs\(old_fs\);\n', '', flags=re.M),
        'vfs_read': replace_regex(
            path,
            r'vfs_read\(fp, \(char __user \*\)buf, ([^,]+), &fp->f_pos\)',
            r'kernel_read(fp, buf, \1, &fp->f_pos)',
        ),
    }
    return counts


def patch_timekeeping(gki: Path) -> dict[str, int]:
    replacements = (
        ('struct_timespec', 'struct timespec', 'struct timespec64'),
        ('ktime_to_timespec', 'ktime_to_timespec(', 'ktime_to_timespec64('),
        ('timespec_to_jiffies', 'timespec_to_jiffies(', 'timespec64_to_jiffies('),
        ('getboottime', 'getboottime(', 'ktime_get_boottime_ts64('),
        ('getnstimeofday', 'getnstimeofday(', 'ktime_get_real_ts64('),
    )
    counts = {key: 0 for key, _, _ in replacements}
    for path in source_files(gki):
        for key, old, new in replacements:
            counts[key] += replace_literal(path, old, new)
    return counts


def patch_reservation_objects(gki: Path) -> dict[str, int]:
    replacements = (
        ('include', '#include <linux/reservation.h>', '#include <linux/dma-resv.h>'),
        ('struct', 'struct reservation_object', 'struct dma_resv'),
        ('functions', 'reservation_object_', 'dma_resv_'),
    )
    counts = {key: 0 for key, _, _ in replacements}
    for path in source_files(gki):
        for key, old, new in replacements:
            counts[key] += replace_literal(path, old, new)
    return counts


def patch_qseecom_ioctl(gki: Path) -> dict[str, int]:
    source = gki / 'drivers/a52_secure/qseecom.c'
    header = gki / 'drivers/a52_secure/qseecom_kernel.h'
    source_count = replace_literal(
        source,
        'static long qseecom_ioctl(struct file *file,',
        'long qseecom_ioctl(struct file *file,',
    )
    text = header.read_text(errors='replace')
    prototype = 'long qseecom_ioctl(struct file *file, unsigned int cmd, unsigned long arg);'
    header_count = 0
    if prototype not in text:
        endif = text.rfind('#endif')
        if endif < 0:
            raise SystemExit('qseecom_kernel.h has no closing #endif')
        insertion = ('\nstruct file;\n' + prototype + '\n')
        header.write_text(text[:endif] + insertion + text[endif:])
        header_count = 1
    return {'source_visibility': source_count, 'header_prototype': header_count}


def patch_simple_renames(gki: Path) -> dict[str, int]:
    replacements = (
        ('kzfree', 'kzfree(', 'kfree_sensitive('),
        ('ptr_ret', 'PTR_RET(', 'PTR_ERR_OR_ZERO('),
        ('drm_debug', 'drm_debug', '__drm_debug'),
    )
    counts = {key: 0 for key, _, _ in replacements}
    for path in source_files(gki):
        for key, old, new in replacements:
            counts[key] += replace_literal(path, old, new)
    return counts


def count_tokens(gki: Path, tokens: list[str]) -> dict[str, int]:
    counts = {token: 0 for token in tokens}
    for path in source_files(gki):
        text = path.read_text(errors='replace')
        for token in tokens:
            counts[token] += text.count(token)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    includes = append_compat_includes(gki)
    report = {
        'status': 'phase2-kernel-api-compatibility-staged',
        'flashable': False,
        'hardware_validated': False,
        'scope': [
            'KGSL private accounting without removed VM counters',
            'mmap_sem to mmap_read/write_lock APIs',
            'set_fs and vfs_read removal using kernel_read',
            'timespec64 and 5.10 timekeeping helpers',
            'native interrupt and tasklet type visibility',
            'reservation_object to dma_resv naming',
            'QSEECom compat ioctl declaration ownership',
            'kzfree, PTR_RET and DRM debug symbol renames',
        ],
        'explicitly_deferred': [
            'drm_display_mode private_flags storage',
            'downstream DRM/SDE UAPI structs and format modifiers',
            'display panel notifier and FPS ABI',
            'Qualcomm IOMMU fault and domain attribute semantics',
            'DMA cache maintenance semantics',
            'clock and PM QoS semantic adapters',
        ],
        'compat_includes_added': includes,
        'memory_accounting': patch_memory_accounting(gki),
        'mmap_locks': patch_mmap_locks(gki),
        'set_fs': patch_set_fs(gki),
        'timekeeping': patch_timekeeping(gki),
        'reservation_objects': patch_reservation_objects(gki),
        'qseecom_ioctl': patch_qseecom_ioctl(gki),
        'simple_renames': patch_simple_renames(gki),
    }

    forbidden = [
        'MM_UNRECLAIMABLE',
        'NR_UNRECLAIMABLE_PAGES',
        'mmap_sem',
        'get_ds(',
        'set_fs(',
        'vfs_read(',
        'struct timespec ',
        'ktime_to_timespec(',
        'timespec_to_jiffies(',
        'getboottime(',
        'getnstimeofday(',
        'struct reservation_object',
        'reservation_object_',
        'kzfree(',
        'PTR_RET(',
    ]
    residual = count_tokens(gki, forbidden)
    report['residual_tokens'] = residual

    required_minimums = {
        ('memory_accounting', 'mm_counter_current'): 1,
        ('memory_accounting', 'mm_counter_task'): 1,
        ('memory_accounting', 'node_page_state'): 1,
        ('mmap_locks', 'read_lock'): 1,
        ('mmap_locks', 'read_unlock'): 1,
        ('set_fs', 'vfs_read'): 1,
        ('timekeeping', 'struct_timespec'): 1,
        ('timekeeping', 'ktime_to_timespec'): 1,
        ('reservation_objects', 'struct'): 1,
        ('qseecom_ioctl', 'source_visibility'): 1,
        ('qseecom_ioctl', 'header_prototype'): 1,
    }
    failed = []
    for (section, key), minimum in required_minimums.items():
        if report[section][key] < minimum:
            failed.append(f'{section}.{key}<{minimum}')
    failed.extend(f'residual:{token}={count}' for token, count in residual.items() if count)
    if failed:
        raise SystemExit('Phase 2 staging checks failed: ' + ', '.join(failed))

    (output / 'phase2-kernel-api-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
