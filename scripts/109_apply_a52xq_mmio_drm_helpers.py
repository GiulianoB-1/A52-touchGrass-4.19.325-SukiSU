#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def patch_compat_header(gki: Path) -> dict[str, int]:
    path = gki / 'a52-port-compat.h'
    text = read(path)
    include_marker = '/* A52_PHASE7_MMIO_DRM_INCLUDES */'
    include_count = 0
    if include_marker not in text:
        block = (
            '\n' + include_marker + '\n'
            '#include <linux/io.h>\n'
            '#include <drm/drm_gem.h>\n'
            '#include <drm/drm_fourcc.h>\n'
        )
        index = text.rfind('#endif')
        if index < 0:
            raise SystemExit('a52-port-compat.h has no closing #endif')
        path.write_text(text[:index] + block + text[index:])
        include_count = 1

    compat_marker = '/* A52_PHASE7_MMIO_DRM_HELPERS */'
    compat_block = r'''
/* A52_PHASE7_MMIO_DRM_HELPERS */
/*
 * Samsung 4.19 could bypass its MSM RTB I/O logging wrapper. Android 5.10
 * has no equivalent logging layer, so the native relaxed write is the exact
 * remaining access primitive.
 */
#ifndef writel_relaxed_no_log
#define writel_relaxed_no_log(value, address) \
	writel_relaxed((value), (address))
#endif

/* The unlocked GEM put helper was consolidated into drm_gem_object_put(). */
#ifndef drm_gem_object_put_unlocked
#define drm_gem_object_put_unlocked(object) drm_gem_object_put(object)
#endif

static inline int a52_drm_format_num_planes(u32 format)
{
	const struct drm_format_info *info = drm_format_info(format);

	return info ? info->num_planes : 1;
}

static inline int a52_drm_format_horz_chroma_subsampling(u32 format)
{
	const struct drm_format_info *info = drm_format_info(format);

	return info ? info->hsub : 1;
}

static inline int a52_drm_format_vert_chroma_subsampling(u32 format)
{
	const struct drm_format_info *info = drm_format_info(format);

	return info ? info->vsub : 1;
}

#define drm_format_num_planes(format) \
	a52_drm_format_num_planes(format)
#define drm_format_horz_chroma_subsampling(format) \
	a52_drm_format_horz_chroma_subsampling(format)
#define drm_format_vert_chroma_subsampling(format) \
	a52_drm_format_vert_chroma_subsampling(format)
'''
    helper_count = append_before_endif(path, compat_marker, compat_block)
    return {'includes': include_count, 'helpers': helper_count}


def validate(gki: Path) -> dict[str, bool]:
    text = read(gki / 'a52-port-compat.h')
    return {
        'linux_io_include': '#include <linux/io.h>' in text,
        'drm_gem_include': '#include <drm/drm_gem.h>' in text,
        'drm_fourcc_include': '#include <drm/drm_fourcc.h>' in text,
        'writel_bridge': '#define writel_relaxed_no_log(value, address)' in text,
        'gem_put_bridge': '#define drm_gem_object_put_unlocked(object)' in text,
        'num_planes_semantics': 'return info ? info->num_planes : 1;' in text,
        'horizontal_semantics': 'return info ? info->hsub : 1;' in text,
        'vertical_semantics': 'return info ? info->vsub : 1;' in text,
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
        'status': 'phase7-mmio-drm-helper-compat-staged',
        'flashable': False,
        'hardware_validated': False,
        'patches': patch_compat_header(gki),
    }
    report['validation'] = validate(gki)
    failed = [name for name, passed in report['validation'].items() if not passed]
    if report['patches']['includes'] != 1:
        failed.append('phase7 includes were not added exactly once')
    if report['patches']['helpers'] != 1:
        failed.append('phase7 helpers were not added exactly once')

    (output / 'phase7-mmio-drm-helper-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    if failed:
        raise SystemExit('Workflow 109 staging validation failed: ' + ', '.join(failed))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
