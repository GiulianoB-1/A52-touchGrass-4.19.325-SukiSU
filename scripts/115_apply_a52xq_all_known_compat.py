#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import shutil
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


def secure_makefile(gki: Path) -> Path:
    path = gki / 'drivers/a52_secure/Makefile'
    if not path.is_file():
        raise SystemExit(f'missing staged secure Makefile: {path}')
    return path


def remove_duplicate_qseecom_object(gki: Path) -> int:
    path = secure_makefile(gki)
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


def add_secure_object(gki: Path, object_name: str) -> int:
    path = secure_makefile(gki)
    content = path.read_text(errors='replace')
    lines = content.splitlines()
    matches = [index for index, line in enumerate(lines) if line.startswith('obj-y +=')]
    if len(matches) != 1:
        raise SystemExit('expected exactly one staged secure obj-y line')

    index = matches[0]
    prefix, objects = lines[index].split('+=', 1)
    object_list = objects.split()
    if object_name in object_list:
        return 0
    object_list.append(object_name)
    lines[index] = prefix + '+= ' + ' '.join(object_list)
    updated = '\n'.join(lines) + ('\n' if content.endswith('\n') else '')
    path.write_text(updated)
    return 1


def stage_legacy_scm(touchgrass: Path, gki: Path) -> dict[str, object]:
    source = touchgrass / 'drivers/soc/qcom/scm.c'
    destination = gki / 'drivers/a52_secure/a52_legacy_scm.c'
    if not source.is_file():
        raise SystemExit(f'missing TouchGrass legacy SCM implementation: {source}')

    text = source.read_text(errors='replace')
    required = (
        'int scm_call2(',
        'int scm_call2_noretry(',
        'int scm_call2_atomic(',
        'u32 scm_io_read(',
        'int scm_io_write(',
        'int scm_get_feat_version(',
    )
    missing = [symbol for symbol in required if symbol not in text]
    if missing:
        raise SystemExit('legacy SCM source is missing: ' + ', '.join(missing))

    destination.parent.mkdir(parents=True, exist_ok=True)
    changed = not destination.is_file() or destination.read_text(errors='replace') != text
    if changed:
        shutil.copyfile(source, destination)
    object_added = add_secure_object(gki, 'a52_legacy_scm.o')
    return {
        'source': str(source.relative_to(touchgrass)),
        'destination': str(destination.relative_to(gki)),
        'copied': changed,
        'object_added': object_added,
        'provided_symbols': [symbol.split('(')[0].split()[-1] for symbol in required],
    }


def restore_config_off_coresight_path(gki: Path) -> dict[str, int]:
    compat = gki / 'a52-port-compat.h'
    compat_text = compat.read_text(errors='replace')
    macro = re.compile(
        r'(?m)^#ifndef\s+of_get_coresight_platform_data\s*\n'
        r'#define\s+of_get_coresight_platform_data\(d,n\)\s+'
        r'coresight_get_platform_data\(\(d\)\)\s*\n'
        r'#endif\s*\n?'
    )
    compat_text, macro_count = macro.subn('', compat_text, count=1)
    if 'define of_get_coresight_platform_data' in compat_text:
        raise SystemExit('legacy CoreSight compatibility macro remains')
    if macro_count:
        compat.write_text(compat_text)

    source = gki / 'drivers/gpu/msm/adreno_coresight.c'
    if not source.is_file():
        raise SystemExit(f'missing staged adreno CoreSight source: {source}')
    source_text = source.read_text(errors='replace')
    direct = re.compile(
        r'desc\.pdata\s*=\s*coresight_get_platform_data\(\s*'
        r'&device->pdev->dev\s*\);'
    )
    replacement = (
        'desc.pdata = of_get_coresight_platform_data(&device->pdev->dev,\n'
        '\t\t\t\tchild);'
    )
    source_text, call_count = direct.subn(replacement, source_text, count=1)
    if 'coresight_get_platform_data(&device->pdev->dev)' in source_text:
        raise SystemExit('direct CoreSight platform-data call remains')
    if 'of_get_coresight_platform_data(&device->pdev->dev' not in source_text:
        raise SystemExit('legacy config-aware CoreSight call was not restored')
    if call_count:
        source.write_text(source_text)

    return {'removed_global_macro': macro_count, 'restored_legacy_call': call_count}


def force_lcd_class_provider(gki: Path) -> int:
    source = gki / 'drivers/video/backlight/lcd.c'
    makefile = gki / 'drivers/video/backlight/Makefile'
    if not source.is_file() or not makefile.is_file():
        raise SystemExit('Android 5.10 LCD class provider source is missing')

    content = makefile.read_text(errors='replace')
    marker = 'obj-y += lcd.o # A52_PORT_FORCE_LCD_CLASS_PROVIDER'
    if marker in content:
        return 0
    pattern = re.compile(
        r'(?m)^obj-\$\(CONFIG_LCD_CLASS_DEVICE\)\s*\+=\s*lcd\.o\s*$'
    )
    content, count = pattern.subn(marker, content, count=1)
    if count != 1:
        raise SystemExit('could not locate the Android 5.10 lcd.o Kbuild entry')
    makefile.write_text(content)
    return count


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
    touchgrass = argument_path('--touchgrass')
    link_fix = {
        'status': 'post-compile-link-fixes-staged',
        'removed_compat_qseecom_object': remove_duplicate_qseecom_object(gki),
        'legacy_scm': stage_legacy_scm(touchgrass, gki),
        'coresight': restore_config_off_coresight_path(gki),
        'forced_lcd_class_provider': force_lcd_class_provider(gki),
        'removed_static_display_exports': remove_static_display_export(gki),
    }
    print(json.dumps(link_fix, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
