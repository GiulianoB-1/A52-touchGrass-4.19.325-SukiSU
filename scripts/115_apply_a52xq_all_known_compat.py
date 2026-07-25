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

    link_fix = {
        'status': 'qseecom-link-object-deduplicated',
        'removed_compat_qseecom_object': remove_duplicate_qseecom_object(
            argument_path('--gki')
        ),
    }
    print(json.dumps(link_fix, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
