#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f'cannot load compatibility module: {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    return 0 if result is None else int(result)


if __name__ == '__main__':
    raise SystemExit(main())
