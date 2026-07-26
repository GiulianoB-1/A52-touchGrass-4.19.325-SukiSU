#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def selective_copy_tree(source: Path, native: Path, fallback: Path) -> dict[str, int]:
    copied = 0
    skipped_native = 0
    for path in source.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(source)
        if (native / rel).exists():
            skipped_native += 1
            continue
        target = fallback / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return {'copied': copied, 'skipped_native': skipped_native}


def locate(root: Path, name: str, preferred_suffix: str | None = None) -> Path:
    candidates = [p for p in root.rglob(name) if p.is_file()]
    if preferred_suffix:
        preferred = [p for p in candidates if str(p).endswith(preferred_suffix)]
        if preferred:
            candidates = preferred
    if not candidates:
        raise SystemExit(f'missing TouchGrass source file: {name}')
    return sorted(candidates, key=lambda p: (len(p.parts), str(p)))[0]


def patch_drm_can_sleep(path: Path) -> int:
    text = path.read_text(errors='replace')
    pattern = re.compile(
        r'/\* returns true if currently okay to sleep \*/\n'
        r'static inline bool drm_can_sleep\(void\)\n'
        r'\{.*?\n\}\n',
        re.S,
    )
    text, count = pattern.subn(
        '/* drm_can_sleep is provided by the Android common 5.10 DRM core. */\n',
        text,
    )
    if count != 1:
        raise SystemExit(f'expected one drm_can_sleep fallback, found {count}')
    path.write_text(text)
    return count


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

    fallback = gki / 'a52-compat/include'
    if fallback.exists():
        shutil.rmtree(fallback)
    fallback.mkdir(parents=True)

    generic = selective_copy_tree(
        touchgrass / 'include',
        gki / 'include',
        fallback,
    )
    arch = selective_copy_tree(
        touchgrass / 'arch/arm64/include/asm',
        gki / 'arch/arm64/include/asm',
        fallback / 'asm',
    )

    # Downstream sources use this removed include only for SZ_* constants.
    asm_sizes = fallback / 'asm/sizes.h'
    asm_sizes.parent.mkdir(parents=True, exist_ok=True)
    asm_sizes.write_text(
        '#ifndef _A52_COMPAT_ASM_SIZES_H\n'
        '#define _A52_COMPAT_ASM_SIZES_H\n'
        '#include <linux/sizes.h>\n'
        '#endif\n'
    )

    # Avoid exposing 4.19 bootmem definitions that duplicate 5.10 memblock APIs.
    bootmem = fallback / 'linux/bootmem.h'
    bootmem.parent.mkdir(parents=True, exist_ok=True)
    bootmem.write_text(
        '#ifndef _A52_COMPAT_LINUX_BOOTMEM_H\n'
        '#define _A52_COMPAT_LINUX_BOOTMEM_H\n'
        '#include <linux/memblock.h>\n'
        '#endif\n'
    )

    msm_ion_source = locate(touchgrass, 'msm_ion.h', 'include/linux/msm_ion.h')
    msm_ion = fallback / 'linux/msm_ion.h'
    msm_ion.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(msm_ion_source, msm_ion)

    dma_iommu_source = locate(
        touchgrass, 'dma-iommu.h', 'arch/arm64/include/asm/dma-iommu.h'
    )
    dma_iommu = fallback / 'asm/dma-iommu.h'
    dma_iommu.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dma_iommu_source, dma_iommu)

    drmp = fallback / 'drm/drmP.h'
    if not drmp.is_file():
        raise SystemExit('selective fallback did not retain drm/drmP.h')
    drm_guard_count = patch_drm_can_sleep(drmp)

    secure = gki / 'drivers/a52_secure'
    smcinvoke = secure / 'smcinvoke.c'
    text = smcinvoke.read_text(errors='replace')
    old = '#include "../../misc/qseecom_kernel.h"'
    new = '#include "qseecom_kernel.h"'
    if old not in text:
        raise SystemExit('SMCInvoke relative QSEECom include not found')
    smcinvoke.write_text(text.replace(old, new, 1))

    trace_source = locate(
        touchgrass,
        'trace_secure_buffer.h',
        'drivers/soc/qcom/trace_secure_buffer.h',
    )
    trace_target = secure / 'trace_secure_buffer.h'
    shutil.copy2(trace_source, trace_target)

    required = {
        'asm_sizes_shim': asm_sizes.is_file(),
        'bootmem_memblock_wrapper': bootmem.is_file(),
        'msm_ion_fallback': msm_ion.is_file(),
        'dma_iommu_fallback': dma_iommu.is_file(),
        'drm_duplicate_removed': 'static inline bool drm_can_sleep' not in drmp.read_text(errors='replace'),
        'smcinvoke_include_relocated': new in smcinvoke.read_text(errors='replace'),
        'secure_trace_header': trace_target.is_file(),
        'native_linux_kernel_preserved': not (fallback / 'linux/kernel.h').exists(),
        'native_linux_mm_preserved': not (fallback / 'linux/mm.h').exists(),
        'native_drm_device_preserved': not (fallback / 'drm/drm_device.h').exists(),
    }
    failed = [name for name, passed in required.items() if not passed]
    if failed:
        raise SystemExit('Phase 1 staging checks failed: ' + ', '.join(failed))

    report = {
        'status': 'phase1-low-risk-compatibility-staged',
        'flashable': False,
        'hardware_validated': False,
        'scope': [
            'selective missing-only downstream header fallback',
            'asm/sizes.h shim to linux/sizes.h',
            'minimal bootmem wrapper to native memblock',
            'legacy msm_ion.h fallback',
            'legacy arm64 dma-iommu.h fallback',
            'remove duplicate drm_can_sleep fallback',
            'relocate SMCInvoke QSEECom include',
            'restore secure-buffer trace header',
        ],
        'explicitly_deferred': [
            'memory accounting and mmap locking',
            'timekeeping APIs',
            'interrupt and tasklet APIs',
            'clock and regulator APIs',
            'PM QoS',
            'display formats and notifier ABI',
            'IOMMU and DMA semantics',
            'QSEECom ioctl ownership and shared-memory semantics',
        ],
        'selective_copy': {'generic': generic, 'arm64_asm': arch},
        'sources': {
            'msm_ion': str(msm_ion_source.relative_to(touchgrass)),
            'dma_iommu': str(dma_iommu_source.relative_to(touchgrass)),
            'trace_secure_buffer': str(trace_source.relative_to(touchgrass)),
        },
        'drm_guard_count': drm_guard_count,
        'checks': required,
    }
    (output / 'phase1-compat-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
