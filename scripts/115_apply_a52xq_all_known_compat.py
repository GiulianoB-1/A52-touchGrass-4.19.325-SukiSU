#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f'cannot load compatibility module: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def argument_path(flag: str) -> Path:
    try:
        index = sys.argv.index(flag)
        value = sys.argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit(f'missing required argument {flag}') from exc
    return Path(value).resolve()


def remove_duplicate_qseecom_object(gki: Path) -> int:
    path = gki / 'drivers/a52_secure/Makefile'
    if not path.is_file():
        raise SystemExit(f'missing staged secure Makefile: {path}')

    content = path.read_text(errors='replace')
    lines = content.splitlines()
    matches = [
        index for index, line in enumerate(lines)
        if line.startswith('obj-y +=') and 'compat_qseecom.o' in line.split()
    ]
    if not matches:
        if 'compat_qseecom.o' in content:
            raise SystemExit('compat_qseecom.o remains outside the expected obj-y line')
        return 0
    if len(matches) != 1:
        raise SystemExit('expected exactly one compat_qseecom.o build entry')

    index = matches[0]
    prefix, objects = lines[index].split('+=', 1)
    object_list = objects.split()
    if object_list.count('compat_qseecom.o') != 1:
        raise SystemExit('expected one compat_qseecom.o token')
    object_list.remove('compat_qseecom.o')
    if 'qseecom.o' not in object_list:
        raise SystemExit('qseecom.o missing while removing duplicate compat object')

    lines[index] = prefix + '+= ' + ' '.join(object_list)
    updated = '\n'.join(lines) + ('\n' if content.endswith('\n') else '')
    if 'compat_qseecom.o' in updated:
        raise SystemExit('failed to remove duplicate compat_qseecom.o entry')
    path.write_text(updated)
    return 1


def remove_static_display_export(gki: Path) -> dict[str, int]:
    relative = Path('msm/sde_io_util.c')
    export = 'EXPORT_SYMBOL(msm_dss_get_res_byname);'
    function = 'static struct resource *msm_dss_get_res_byname('
    report: dict[str, int] = {}

    for root in ('drivers/a52_display', 'techpack/display'):
        path = gki / root / relative
        if not path.is_file():
            continue
        content = path.read_text(errors='replace')
        if function not in content:
            raise SystemExit(f'missing static msm_dss_get_res_byname in {path}')
        count = content.count(export)
        if count > 1:
            raise SystemExit(f'multiple msm_dss_get_res_byname exports in {path}')
        if count == 1:
            content = content.replace(export + '\n', '', 1)
            if export in content:
                raise SystemExit(f'failed to remove static export in {path}')
            path.write_text(content)
        report[str(path.relative_to(gki))] = count

    if not report:
        raise SystemExit('no staged sde_io_util.c source found')
    return report


def main() -> int:
    root = Path(__file__).resolve().parent
    base = load_module(
        root / '115_apply_a52xq_all_known_compat_base.py',
        'a52_workflow115_base',
    )
    secondary = load_module(
        root / '115_apply_a52xq_secondary_compat.py',
        'a52_workflow115_secondary',
    )
    result = base.main()
    if result not in (None, 0):
        return int(result)
    result = secondary.main()
    if result not in (None, 0):
        return int(result)

    gki = argument_path('--gki')
    link_fix = {
        'status': 'post-compile-link-fixes-staged',
        'removed_compat_qseecom_object': remove_duplicate_qseecom_object(gki),
        'removed_static_display_exports': remove_static_display_export(gki),
    }
    print(json.dumps(link_fix, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
