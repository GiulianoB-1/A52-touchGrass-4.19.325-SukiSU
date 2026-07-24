#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def append_before_endif(path: Path, marker: str, block: str) -> int:
    text = read(path)
    if marker in text:
        return 0
    index = text.rfind('#endif')
    if index < 0:
        raise SystemExit(f'{path} has no closing #endif')
    path.write_text(text[:index] + '\n' + block.rstrip() + '\n' + text[index:])
    return 1


def patch_cache_compat(gki: Path) -> dict[str, int]:
    path = gki / 'a52-port-compat.h'
    text = read(path)
    include_marker = '/* A52_PHASE8_CACHE_INCLUDES */'
    include_count = 0
    if include_marker not in text:
        block = (
            '\n' + include_marker + '\n'
            '#include <asm/cacheflush.h>\n'
        )
        index = text.rfind('#endif')
        if index < 0:
            raise SystemExit('a52-port-compat.h has no closing #endif')
        path.write_text(text[:index] + block + text[index:])
        include_count = 1

    helper_marker = '/* A52_PHASE8_CACHE_RANGE_HELPERS */'
    helper_block = r'''
/* A52_PHASE8_CACHE_RANGE_HELPERS */
/*
 * Samsung 4.19 exposed dmac_*_range wrappers around the ARM64 cache
 * maintenance primitives. Android 5.10 retains the same public operations
 * under their architectural names, but removed the legacy wrappers.
 */
#define A52_CACHE_RANGE_SIZE(start, end) \
	((size_t)((char *)(end) - (char *)(start)))

#ifndef dmac_flush_range
#define dmac_flush_range(start, end) \
	__dma_flush_area((void *)(start), A52_CACHE_RANGE_SIZE(start, end))
#endif

#ifndef dmac_inv_range
#define dmac_inv_range(start, end) \
	__inval_dcache_area((void *)(start), A52_CACHE_RANGE_SIZE(start, end))
#endif

#ifndef dmac_clean_range
#define dmac_clean_range(start, end) \
	__clean_dcache_area_poc((void *)(start), A52_CACHE_RANGE_SIZE(start, end))
#endif
'''
    helper_count = append_before_endif(path, helper_marker, helper_block)
    return {'includes': include_count, 'helpers': helper_count}


def patch_subsystem_restart(gki: Path) -> int:
    path = gki / 'a52-compat/include/soc/qcom/subsystem_restart.h'
    text = read(path)
    pattern = re.compile(
        r'static inline int subsystem_crash\(const char \*name\)\s*\{\s*\}'
    )
    updated, count = pattern.subn(
        'static inline int subsystem_crash(const char *name) { return 0; }',
        text,
        count=1,
    )
    if count:
        path.write_text(updated)
    return count


def patch_dma_debug(gki: Path) -> int:
    path = gki / 'a52-compat/include/linux/dma-debug.h'
    text = read(path)
    pattern = re.compile(
        r'static inline void debug_dma_mapping_error\(struct device \*dev,\s*'
        r'dma_addr_t dma_addr\)\s*\{\s*\}\s*',
        re.S,
    )
    updated, count = pattern.subn(
        '/* Android 5.10 provides debug_dma_mapping_error in dma-mapping.h. */\n',
        text,
        count=1,
    )
    if count:
        path.write_text(updated)
    return count


def patch_smcinvoke_size_add(gki: Path) -> int:
    path = gki / 'drivers/a52_secure/smcinvoke.c'
    text = read(path)
    updated, count = re.subn(r'\bsize_add\(', 'smcinvoke_size_add(', text)
    if count:
        path.write_text(updated)
    return count


def patch_secure_trace_path(gki: Path) -> int:
    path = gki / 'drivers/a52_secure/trace_secure_buffer.h'
    text = read(path)
    old = '#define TRACE_INCLUDE_PATH ../../drivers/soc/qcom/'
    new = '#define TRACE_INCLUDE_PATH ../../drivers/a52_secure/'
    count = text.count(old)
    if count == 1:
        path.write_text(text.replace(old, new, 1))
    return count


def validate(gki: Path) -> dict[str, bool]:
    compat = read(gki / 'a52-port-compat.h')
    subsystem = read(gki / 'a52-compat/include/soc/qcom/subsystem_restart.h')
    dma_debug = read(gki / 'a52-compat/include/linux/dma-debug.h')
    smcinvoke = read(gki / 'drivers/a52_secure/smcinvoke.c')
    trace = read(gki / 'drivers/a52_secure/trace_secure_buffer.h')
    return {
        'cacheflush_include': '#include <asm/cacheflush.h>' in compat,
        'flush_semantics': '__dma_flush_area((void *)(start)' in compat,
        'invalidate_semantics': '__inval_dcache_area((void *)(start)' in compat,
        'clean_semantics': '__clean_dcache_area_poc((void *)(start)' in compat,
        'subsystem_crash_returns_zero': (
            'static inline int subsystem_crash(const char *name) { return 0; }'
            in subsystem
        ),
        'duplicate_dma_debug_removed': (
            'static inline void debug_dma_mapping_error' not in dma_debug
        ),
        'smcinvoke_helper_renamed': (
            'static inline size_t smcinvoke_size_add' in smcinvoke
            and 'static inline size_t size_add' not in smcinvoke
            and smcinvoke.count('smcinvoke_size_add(') >= 2
        ),
        'secure_trace_path_relocated': (
            '#define TRACE_INCLUDE_PATH ../../drivers/a52_secure/' in trace
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        'status': 'phase8-cache-fallback-compat-staged',
        'flashable': False,
        'hardware_validated': False,
        'cache': patch_cache_compat(gki),
        'subsystem_restart_returns': patch_subsystem_restart(gki),
        'dma_debug_duplicates': patch_dma_debug(gki),
        'smcinvoke_size_add_references': patch_smcinvoke_size_add(gki),
        'secure_trace_paths': patch_secure_trace_path(gki),
    }
    report['validation'] = validate(gki)
    failures = [name for name, passed in report['validation'].items() if not passed]
    expected = {
        'cache.includes': report['cache']['includes'] == 1,
        'cache.helpers': report['cache']['helpers'] == 1,
        'subsystem_restart_returns': report['subsystem_restart_returns'] == 1,
        'dma_debug_duplicates': report['dma_debug_duplicates'] == 1,
        'smcinvoke_size_add_references': report['smcinvoke_size_add_references'] == 2,
        'secure_trace_paths': report['secure_trace_paths'] == 1,
    }
    failures.extend(name for name, passed in expected.items() if not passed)

    (output / 'phase8-cache-fallback-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    if failures:
        raise SystemExit('Workflow 110 staging validation failed: ' + ', '.join(failures))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
