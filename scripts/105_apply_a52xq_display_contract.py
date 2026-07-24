#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

MODE_MACROS = (
    'DRM_MODE_FLAG_SUPPORTS_RGB',
    'DRM_MODE_FLAG_SUPPORTS_YUV',
    'DRM_MODE_FLAG_VID_MODE_PANEL',
    'DRM_MODE_FLAG_CMD_MODE_PANEL',
    'DRM_MODE_FLAG_SEAMLESS',
)

CONTRACT_TYPES = (
    ('struct', 'drm_panel_hdr_properties'),
    ('enum', 'fps'),
)


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def insert_before_last_endif(path: Path, block: str) -> None:
    text = read(path)
    idx = text.rfind('#endif')
    if idx < 0:
        raise SystemExit(f'no closing #endif in {path}')
    write(path, text[:idx] + block.rstrip() + '\n\n' + text[idx:])


def extract_macro(text: str, name: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.match(rf'^\s*#define\s+{re.escape(name)}\b', line):
            out = [line]
            while out[-1].rstrip().endswith('\\'):
                index += 1
                if index >= len(lines):
                    break
                out.append(lines[index])
            return '\n'.join(out)
    raise SystemExit(f'macro {name} not found')


def extract_named_block(text: str, kind: str, name: str) -> str | None:
    match = re.search(rf'\b{kind}\s+{re.escape(name)}\s*\{{', text)
    if not match:
        return None
    brace = text.find('{', match.start())
    depth = 0
    index = brace
    while index < len(text):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                semi = text.find(';', index)
                if semi < 0:
                    return None
                return text[match.start():semi + 1]
        index += 1
    return None


def find_named_block(root: Path, kind: str, name: str) -> tuple[str, str]:
    matches: list[tuple[str, str]] = []
    for path in root.rglob('*.h'):
        block = extract_named_block(read(path), kind, name)
        if block:
            matches.append((str(path.relative_to(root)), block))
    if not matches:
        raise SystemExit(f'{kind} {name} not found below {root}')
    matches.sort(key=lambda item: (0 if item[0].startswith('include/') else 1, item[0]))
    return matches[0]


def add_include(path: Path, include_line: str) -> int:
    text = read(path)
    if include_line in text:
        return 0
    includes = list(re.finditer(r'^#include[^\n]*\n', text, flags=re.M))
    if includes:
        pos = includes[-1].end()
        write(path, text[:pos] + include_line + '\n' + text[pos:])
    else:
        write(path, include_line + '\n' + text)
    return 1


def copy_uapi(touchgrass: Path, gki: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    for name in ('msm_drm.h', 'sde_drm.h'):
        src = touchgrass / 'include/uapi/drm' / name
        dst = gki / 'include/uapi/drm' / name
        if not src.is_file():
            raise SystemExit(f'missing TouchGrass UAPI header: {src}')
        shutil.copy2(src, dst)
        copied[name] = str(src.relative_to(touchgrass))
    return copied


def patch_mode_flags(touchgrass: Path, gki: Path) -> list[str]:
    src_text = read(touchgrass / 'include/uapi/drm/drm_mode.h')
    dst = gki / 'include/uapi/drm/drm_mode.h'
    dst_text = read(dst)
    added = []
    blocks = []
    for name in MODE_MACROS:
        if re.search(rf'^\s*#define\s+{re.escape(name)}\b', dst_text, flags=re.M):
            continue
        blocks.append(extract_macro(src_text, name))
        added.append(name)
    if blocks:
        insert_before_last_endif(dst, '\n/* A52 downstream display mode ABI */\n' + '\n'.join(blocks))
    return added


def patch_fourcc_modifiers(touchgrass: Path, gki: Path) -> list[str]:
    src = read(touchgrass / 'include/uapi/drm/drm_fourcc.h')
    dst = gki / 'include/uapi/drm/drm_fourcc.h'
    dst_text = read(dst)
    names = []
    for match in re.finditer(r'^\s*#define\s+(DRM_FORMAT_MOD_QCOM_[A-Z0-9_]+)\b', src, flags=re.M):
        name = match.group(1)
        if name not in names:
            names.append(name)
    blocks = []
    added = []
    for name in names:
        if re.search(rf'^\s*#define\s+{re.escape(name)}\b', dst_text, flags=re.M):
            continue
        blocks.append(extract_macro(src, name))
        added.append(name)
    if blocks:
        insert_before_last_endif(dst, '\n/* A52 Qualcomm format modifier ABI */\n' + '\n'.join(blocks))
    return added


def patch_display_mode(gki: Path) -> list[str]:
    path = gki / 'include/drm/drm_modes.h'
    text = read(path)
    additions = []
    fields = []
    if not re.search(r'\bprivate_flags\s*;', text):
        fields.append('\tint private_flags;')
        additions.append('private_flags')
    if not re.search(r'\bvrefresh\s*;', text):
        fields.append('\tint vrefresh;')
        additions.append('vrefresh')
    if not re.search(r'\bhsync\s*;', text):
        fields.append('\tint hsync;')
        additions.append('hsync')
    if not fields:
        return additions
    anchors = (
        '\tenum hdmi_picture_aspect picture_aspect_ratio;',
        '\tstruct list_head export_head;',
        '\tunsigned int picture_aspect_ratio;',
    )
    anchor = next((item for item in anchors if item in text), None)
    if not anchor:
        mode_start = text.find('struct drm_display_mode {')
        if mode_start < 0:
            raise SystemExit('struct drm_display_mode not found')
        mode_end = text.find('\n};', mode_start)
        if mode_end < 0:
            raise SystemExit('struct drm_display_mode closing brace not found')
        block = '\n\t/* A52 downstream private mode state */\n' + '\n'.join(fields) + '\n'
        write(path, text[:mode_end] + block + text[mode_end:])
        return additions
    block = '\t/* A52 downstream private mode state */\n' + '\n'.join(fields) + '\n\n'
    write(path, text.replace(anchor, block + anchor, 1))
    return additions


def patch_encoder_bridge(gki: Path) -> int:
    path = gki / 'include/drm/drm_encoder.h'
    text = read(path)
    if re.search(r'struct\s+drm_bridge\s*\*\s*bridge\s*;', text):
        return 0
    if 'struct drm_bridge;' not in text:
        first_struct = text.find('struct drm_device;')
        if first_struct >= 0:
            text = text[:first_struct] + 'struct drm_bridge;\n' + text[first_struct:]
        else:
            guard = re.search(r'^#define[^\n]+\n', text, flags=re.M)
            pos = guard.end() if guard else 0
            text = text[:pos] + '\nstruct drm_bridge;\n' + text[pos:]
    anchors = ('\tstruct drm_crtc *crtc;', '\tstruct list_head head;')
    anchor = next((item for item in anchors if item in text), None)
    if not anchor:
        raise SystemExit('drm_encoder bridge insertion anchor not found')
    text = text.replace(anchor, '\t/* A52 downstream primary bridge linkage */\n\tstruct drm_bridge *bridge;\n\n' + anchor, 1)
    write(path, text)
    return 1


def patch_panel_notifier(gki: Path) -> dict[str, int]:
    header = gki / 'include/drm/drm_panel.h'
    source = gki / 'drivers/gpu/drm/drm_panel.c'
    htext = read(header)
    result = {'struct': 0, 'field': 0, 'prototypes': 0, 'init': 0, 'functions': 0}

    if '#include <linux/notifier.h>' not in htext:
        htext = htext.replace('#include <linux/list.h>\n', '#include <linux/list.h>\n#include <linux/notifier.h>\n', 1)

    if 'struct drm_panel_notifier {' not in htext:
        marker = 'struct drm_panel;'
        block = (
            '#define DRM_PANEL_EVENT_BLANK 0x01\n'
            '#define DRM_PANEL_EARLY_EVENT_BLANK 0x02\n'
            'enum {\n'
            '\tDRM_PANEL_BLANK_UNBLANK,\n'
            '\tDRM_PANEL_BLANK_POWERDOWN,\n'
            '\tDRM_PANEL_BLANK_LP,\n'
            '\tDRM_PANEL_BLANK_FPS_CHANGE,\n'
            '};\n'
            'struct drm_panel_notifier {\n'
            '\tint refresh_rate;\n'
            '\tvoid *data;\n'
            '\tu32 id;\n'
            '};\n\n'
        )
        if marker not in htext:
            raise SystemExit('drm_panel notifier type anchor not found')
        htext = htext.replace(marker, block + marker, 1)
        result['struct'] = 1

    if 'struct blocking_notifier_head nh;' not in htext:
        anchors = ('\tstruct list_head list;', '\tstruct mutex lock;')
        anchor = next((item for item in anchors if item in htext), None)
        if not anchor:
            raise SystemExit('drm_panel notifier field anchor not found')
        htext = htext.replace(anchor, anchor + '\n\tstruct blocking_notifier_head nh;', 1)
        result['field'] = 1

    if 'drm_panel_notifier_register(' not in htext:
        proto = (
            '\nint drm_panel_notifier_register(struct drm_panel *panel, struct notifier_block *nb);\n'
            'int drm_panel_notifier_unregister(struct drm_panel *panel, struct notifier_block *nb);\n'
            'int drm_panel_notifier_call_chain(struct drm_panel *panel, unsigned long val, void *v);\n'
        )
        idx = htext.rfind('#endif')
        if idx < 0:
            raise SystemExit('drm_panel.h has no closing #endif')
        htext = htext[:idx] + proto + '\n' + htext[idx:]
        result['prototypes'] = 1
    write(header, htext)

    stext = read(source)
    if 'BLOCKING_INIT_NOTIFIER_HEAD(&panel->nh);' not in stext:
        match = re.search(r'void\s+drm_panel_init\s*\([^)]*\)\s*\{', stext, flags=re.S)
        if not match:
            raise SystemExit('drm_panel_init definition not found')
        pos = match.end()
        stext = stext[:pos] + '\n\tBLOCKING_INIT_NOTIFIER_HEAD(&panel->nh);' + stext[pos:]
        result['init'] = 1

    if 'int drm_panel_notifier_register(' not in stext:
        functions = '''
int drm_panel_notifier_register(struct drm_panel *panel,
		struct notifier_block *nb)
{
	return blocking_notifier_chain_register(&panel->nh, nb);
}
EXPORT_SYMBOL_GPL(drm_panel_notifier_register);

int drm_panel_notifier_unregister(struct drm_panel *panel,
		struct notifier_block *nb)
{
	return blocking_notifier_chain_unregister(&panel->nh, nb);
}
EXPORT_SYMBOL_GPL(drm_panel_notifier_unregister);

int drm_panel_notifier_call_chain(struct drm_panel *panel,
		unsigned long val, void *v)
{
	return blocking_notifier_call_chain(&panel->nh, val, v);
}
EXPORT_SYMBOL_GPL(drm_panel_notifier_call_chain);
'''
        anchor = 'MODULE_AUTHOR'
        if anchor in stext:
            stext = stext.replace(anchor, functions + '\n' + anchor, 1)
        else:
            stext += '\n' + functions
        result['functions'] = 1
    write(source, stext)
    return result


def build_contract_header(touchgrass: Path, gki: Path) -> dict[str, str]:
    blocks = []
    origins: dict[str, str] = {}
    for kind, name in CONTRACT_TYPES:
        origin, block = find_named_block(touchgrass, kind, name)
        origins[name] = origin
        blocks.append(block)

    path = gki / 'include/drm/a52_display_contract.h'
    text = (
        '#ifndef __A52_DISPLAY_CONTRACT_H__\n'
        '#define __A52_DISPLAY_CONTRACT_H__\n'
        '#include <linux/types.h>\n'
        '#include <drm/drm_bridge.h>\n'
        '#include <uapi/drm/msm_drm.h>\n'
        '#include <uapi/drm/sde_drm.h>\n\n'
        + '\n\n'.join(blocks)
        + '\n\n#endif\n'
    )
    write(path, text)
    return origins


def include_contract_in_port(gki: Path) -> dict[str, int]:
    counts = {'msm_drv': 0, 'dsi_panel': 0, 'dsi_display': 0, 'compat_undef_pr_fmt': 0}
    for root in (gki / 'drivers/a52_display', gki / 'techpack/display'):
        if not root.exists():
            continue
        for path in root.rglob('*.h'):
            if path.name == 'msm_drv.h':
                counts['msm_drv'] += add_include(path, '#include <drm/a52_display_contract.h>')
            elif path.name == 'dsi_panel.h':
                counts['dsi_panel'] += add_include(path, '#include <drm/a52_display_contract.h>')
            elif path.name == 'dsi_display.h':
                counts['dsi_display'] += add_include(path, '#include <drm/a52_display_contract.h>')
    compat = gki / 'a52-port-compat.h'
    text = read(compat)
    marker = '/* A52_DISPLAY_ALLOW_LOCAL_PR_FMT */'
    if marker not in text:
        idx = text.rfind('#endif')
        if idx < 0:
            raise SystemExit('a52-port-compat.h has no closing #endif')
        text = text[:idx] + marker + '\n#ifdef pr_fmt\n#undef pr_fmt\n#endif\n' + text[idx:]
        write(compat, text)
        counts['compat_undef_pr_fmt'] = 1
    return counts


def validate(gki: Path) -> dict[str, bool]:
    checks = {
        'private_flags': 'private_flags' in read(gki / 'include/drm/drm_modes.h'),
        'vrefresh': re.search(r'\bvrefresh\s*;', read(gki / 'include/drm/drm_modes.h')) is not None,
        'seamless_flag': 'DRM_MODE_FLAG_SEAMLESS' in read(gki / 'include/uapi/drm/drm_mode.h'),
        'msm_event': 'struct drm_msm_event_resp' in read(gki / 'include/uapi/drm/msm_drm.h'),
        'sde_scaler': 'struct sde_drm_scaler_v2' in read(gki / 'include/uapi/drm/sde_drm.h'),
        'panel_contract': (gki / 'include/drm/a52_display_contract.h').is_file(),
        'panel_notifier_field': 'struct blocking_notifier_head nh;' in read(gki / 'include/drm/drm_panel.h'),
        'encoder_bridge': re.search(r'struct\s+drm_bridge\s*\*\s*bridge\s*;', read(gki / 'include/drm/drm_encoder.h')) is not None,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit('display contract validation failed: ' + ', '.join(failed))
    return checks


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

    report = {
        'status': 'a52-display-contract-staged',
        'flashable': False,
        'hardware_validated': False,
        'uapi_headers': copy_uapi(touchgrass, gki),
        'mode_flags_added': patch_mode_flags(touchgrass, gki),
        'format_modifiers_added': patch_fourcc_modifiers(touchgrass, gki),
        'display_mode_fields_added': patch_display_mode(gki),
        'encoder_bridge_added': patch_encoder_bridge(gki),
        'panel_notifier': patch_panel_notifier(gki),
        'contract_type_origins': build_contract_header(touchgrass, gki),
        'contract_includes': include_contract_in_port(gki),
    }
    report['validation'] = validate(gki)
    (output / 'phase3-display-contract-report.json').write_text(
        json.dumps(report, indent=2, sort_keys=True) + '\n'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
