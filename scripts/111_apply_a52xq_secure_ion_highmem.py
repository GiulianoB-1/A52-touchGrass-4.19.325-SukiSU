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


def patch_runtime_helpers(gki: Path) -> dict[str, int]:
    path = gki / 'a52-port-compat.h'
    text = read(path)
    include_marker = '/* A52_PHASE9_HIGHMEM_INCLUDES */'
    include_count = 0
    if include_marker not in text:
        block = (
            '\n' + include_marker + '\n'
            '#include <linux/highmem.h>\n'
        )
        index = text.rfind('#endif')
        if index < 0:
            raise SystemExit('a52-port-compat.h has no closing #endif')
        path.write_text(text[:index] + block + text[index:])
        include_count = 1

    helper_marker = '/* A52_PHASE9_HIGHMEM_MMIO_HELPERS */'
    helper_block = r'''
/* A52_PHASE9_HIGHMEM_MMIO_HELPERS */
/* Samsung's MSM RTB logging layer is absent from Android 5.10. */
#ifndef readl_relaxed_no_log
#define readl_relaxed_no_log(address) readl_relaxed((address))
#endif

/* ARM64 direct mappings made this helper a no-op in the TouchGrass source. */
#ifndef kmap_atomic_flush_unused
#define kmap_atomic_flush_unused() do { } while (0)
#endif
'''
    helper_count = append_before_endif(path, helper_marker, helper_block)
    return {'includes': include_count, 'helpers': helper_count}


def patch_secure_buffer_header(gki: Path) -> int:
    path = gki / 'a52-compat/include/soc/qcom/secure_buffer.h'
    text = read(path)
    pattern = re.compile(
        r'#ifdef CONFIG_QCOM_SECURE_BUFFER\n'
        r'(?P<decls>.*?)'
        r'#else\n'
        r'.*?'
        r'#endif\n'
        r'#endif\s*$',
        re.S,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit('secure_buffer.h declaration/stub split not found')
    replacement = (
        '/* The A52 port builds secure_buffer.c directly, so expose declarations. */\n'
        + match.group('decls').rstrip()
        + '\n#endif\n'
    )
    updated, count = pattern.subn(replacement, text, count=1)
    if count:
        path.write_text(updated)
    return count


def patch_ion_kernel_header(gki: Path) -> int:
    path = gki / 'a52-compat/include/linux/ion_kernel.h'
    marker = 'A52_PHASE9_ION_KERNEL_COMPAT'
    text = read(path)
    if marker in text:
        return 0
    path.write_text(r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __A52_LINUX_ION_KERNEL_H__
#define __A52_LINUX_ION_KERNEL_H__

/* A52_PHASE9_ION_KERNEL_COMPAT */
#include <linux/bitmap.h>
#include <linux/dma-buf.h>
#include <linux/errno.h>
#include <linux/msm_ion.h>

#ifdef CONFIG_ION
struct dma_buf *ion_alloc(size_t len, unsigned int heap_id_mask,
			  unsigned int flags);

static inline unsigned int ion_get_flags_num_vm_elems(unsigned int flags)
{
	unsigned long vm_flags = flags & ION_FLAGS_CP_MASK;

	return (unsigned int)bitmap_weight(&vm_flags, BITS_PER_LONG);
}

int ion_populate_vm_list(unsigned long flags, unsigned int *vm_list,
			 int nelems);
#else
static inline struct dma_buf *ion_alloc(size_t len, unsigned int heap_id_mask,
					unsigned int flags)
{
	return ERR_PTR(-ENOMEM);
}

static inline unsigned int ion_get_flags_num_vm_elems(unsigned int flags)
{
	return 0;
}

static inline int ion_populate_vm_list(unsigned long flags,
				       unsigned int *vm_list, int nelems)
{
	return -EINVAL;
}
#endif

#endif /* __A52_LINUX_ION_KERNEL_H__ */
''')
    return 1


def validate(gki: Path) -> dict[str, bool]:
    compat = read(gki / 'a52-port-compat.h')
    secure = read(gki / 'a52-compat/include/soc/qcom/secure_buffer.h')
    ion = read(gki / 'a52-compat/include/linux/ion_kernel.h')
    return {
        'highmem_include': '#include <linux/highmem.h>' in compat,
        'readl_bridge': '#define readl_relaxed_no_log(address)' in compat,
        'highmem_noop': '#define kmap_atomic_flush_unused() do { } while (0)' in compat,
        'secure_declarations_retained': 'int msm_secure_table(struct sg_table *table);' in secure,
        'secure_stubs_removed': 'static inline int msm_secure_table' not in secure,
        'ion_relative_include_removed': '../../drivers/staging/android/ion/ion_kernel.h' not in ion,
        'ion_alloc_declaration': 'struct dma_buf *ion_alloc(size_t len' in ion,
        'ion_vmid_helpers': 'ion_get_flags_num_vm_elems' in ion and 'ion_populate_vm_list' in ion,
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
        'status': 'phase9-secure-ion-highmem-compat-staged',
        'flashable': False,
        'hardware_validated': False,
        'runtime_helpers': patch_runtime_helpers(gki),
        'secure_header_splits': patch_secure_buffer_header(gki),
        'ion_headers': patch_ion_kernel_header(gki),
    }
    report['validation'] = validate(gki)
    failures = [name for name, passed in report['validation'].items() if not passed]
    expected = {
        'runtime_helpers.includes': report['runtime_helpers']['includes'] == 1,
        'runtime_helpers.helpers': report['runtime_helpers']['helpers'] == 1,
        'secure_header_splits': report['secure_header_splits'] == 1,
        'ion_headers': report['ion_headers'] == 1,
    }
    failures.extend(name for name, passed in expected.items() if not passed)

    (output / 'phase9-secure-ion-highmem-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    if failures:
        raise SystemExit('Workflow 111 staging validation failed: ' + ', '.join(failures))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
