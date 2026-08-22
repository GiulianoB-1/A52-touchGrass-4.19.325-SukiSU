#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DRV = Path('drivers/a52_display/msm/msm_drv.c')
ATOMIC = Path('drivers/a52_display/msm/msm_atomic.c')
KMS = Path('drivers/a52_display/msm/sde/sde_kms.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE296_USERSPACE_DRM_ATOMIC_FRONTIER_V1'
REC_INCLUDE = '#include <linux/a52_ack_secure_flight_recorder.h>\n'


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase296 {label}: expected exactly 1 anchor, found {count}')
    return text.replace(old, new, 1)


def ensure_include(text: str, anchor: str, label: str) -> str:
    if REC_INCLUDE in text:
        return text
    return one(text, anchor, anchor + REC_INCLUDE, f'{label} recorder include')


def inject_function_entry(text: str, name: str, statement: str) -> str:
    hits: list[int] = []
    for m in re.finditer(r'\b' + re.escape(name) + r'\s*\(', text):
        p = m.end() - 1
        depth = 0
        i = p
        while i < len(text):
            ch = text[i]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            continue
        j = i + 1
        while j < len(text) and text[j].isspace():
            j += 1
        if j < len(text) and text[j] == '{':
            hits.append(j)
    if len(hits) != 1:
        raise SystemExit(f'Phase296 {name}: expected 1 definition, found {len(hits)}')
    brace = hits[0]
    return text[:brace + 1] + '\n\t' + statement + text[brace + 1:]


def behavior_counts(text: str) -> dict[str, int]:
    tokens = (
        'writel(', 'writel_relaxed(', 'DSI_W32(', 'clk_set_rate(',
        'gpio_set_value(', 'dsi_panel_tx_cmd_set(', 'dsi_ctrl_cmd_transfer(',
        'msleep(', 'usleep_range(', 'udelay(', 'wait_for_completion',
    )
    return {t: text.count(t) for t in tokens}


def patch_drv(text: str) -> str:
    if MARK in text:
        return text
    before = behavior_counts(text)
    text = ensure_include(text, '#include "sde_dbg.h"\n', 'msm_drv')
    text = one(
        text,
        '#include "sde_dbg.h"\n' + REC_INCLUDE,
        '#include "sde_dbg.h"\n' + REC_INCLUDE +
        '\n/* ' + MARK + '\n'
        ' * Passive post-bind frontier. Records only whether userspace reaches\n'
        ' * the DRM open/atomic path. No display state or control flow changes.\n'
        ' */\n',
        'msm_drv marker',
    )

    old = '''int msm_atomic_check(struct drm_device *dev,\n\t\t\t    struct drm_atomic_state *state)\n{\n\tstruct msm_drm_private *priv;\n\n\tpriv = dev->dev_private;\n\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->atomic_check)\n\t\treturn priv->kms->funcs->atomic_check(priv->kms, state);\n\n\treturn drm_atomic_helper_check(dev, state);\n}\n'''
    new = '''int msm_atomic_check(struct drm_device *dev,\n\t\t\t    struct drm_atomic_state *state)\n{\n\tstruct msm_drm_private *priv;\n\tint ret;\n\n\ta52_ackfr_record("P276 296A e");\n\tpriv = dev->dev_private;\n\tif (priv && priv->kms && priv->kms->funcs &&\n\t\t\tpriv->kms->funcs->atomic_check)\n\t\tret = priv->kms->funcs->atomic_check(priv->kms, state);\n\telse\n\t\tret = drm_atomic_helper_check(dev, state);\n\ta52_ackfr_record("P276 296A x r=%d", ret);\n\n\treturn ret;\n}\n'''
    text = one(text, old, new, 'msm_atomic_check')

    old = '''static int msm_open(struct drm_device *dev, struct drm_file *file)\n{\n\treturn context_init(dev, file);\n}\n'''
    new = '''static int msm_open(struct drm_device *dev, struct drm_file *file)\n{\n\tint ret;\n\n\ta52_ackfr_record("P276 296O e");\n\tret = context_init(dev, file);\n\ta52_ackfr_record("P276 296O x r=%d", ret);\n\treturn ret;\n}\n'''
    text = one(text, old, new, 'msm_open')

    old = '''\tret = drm_dev_register(ddev, 0);\n\tif (ret)\n\t\tgoto fail;\n\tpriv->registered = true;\n'''
    new = '''\tret = drm_dev_register(ddev, 0);\n\ta52_ackfr_record("P276 296R r=%d", ret);\n\tif (ret)\n\t\tgoto fail;\n\tpriv->registered = true;\n'''
    text = one(text, old, new, 'drm_dev_register return')

    if behavior_counts(text) != before:
        raise SystemExit('Phase296 msm_drv hardware-affecting token count changed')
    return text


def patch_atomic(text: str) -> str:
    if 'P276 296C e n=%d' in text:
        return text
    before = behavior_counts(text)
    text = ensure_include(text, '#include "sde_trace.h"\n', 'msm_atomic')

    text = inject_function_entry(
        text, 'msm_atomic_commit',
        'a52_ackfr_record("P276 296C e n=%d", nonblock);',
    )
    text = inject_function_entry(
        text, 'complete_commit',
        'a52_ackfr_record("P276 296W e");',
    )

    old = '''\tret = drm_atomic_helper_prepare_planes(dev, state);\n\tif (ret) {\n\t\tSDE_ATRACE_END("atomic_commit");\n\t\treturn ret;\n\t}\n'''
    new = '''\tret = drm_atomic_helper_prepare_planes(dev, state);\n\tif (ret) {\n\t\ta52_ackfr_record("P276 296C x r=%d q=1", ret);\n\t\tSDE_ATRACE_END("atomic_commit");\n\t\treturn ret;\n\t}\n'''
    text = one(text, old, new, 'prepare_planes error')

    old = '''\tdrm_atomic_state_get(state);\n\tmsm_atomic_commit_dispatch(dev, state, c);\n\n\tSDE_ATRACE_END("atomic_commit");\n\n\treturn 0;\nerr_free:\n'''
    new = '''\tdrm_atomic_state_get(state);\n\tmsm_atomic_commit_dispatch(dev, state, c);\n\n\tSDE_ATRACE_END("atomic_commit");\n\ta52_ackfr_record("P276 296C x r=0 q=0");\n\n\treturn 0;\nerr_free:\n'''
    text = one(text, old, new, 'atomic success')

    old = '''error:\n\tdrm_atomic_helper_cleanup_planes(dev, state);\n\tSDE_ATRACE_END("atomic_commit");\n\treturn ret;\n}\n\nstruct drm_atomic_state *msm_atomic_state_alloc'''
    new = '''error:\n\tdrm_atomic_helper_cleanup_planes(dev, state);\n\tSDE_ATRACE_END("atomic_commit");\n\ta52_ackfr_record("P276 296C x r=%d q=2", ret);\n\treturn ret;\n}\n\nstruct drm_atomic_state *msm_atomic_state_alloc'''
    text = one(text, old, new, 'atomic final error')

    if behavior_counts(text) != before:
        raise SystemExit('Phase296 msm_atomic hardware-affecting token count changed')
    return text


def patch_kms(text: str) -> str:
    if 'P276 296K p' in text:
        return text
    before = behavior_counts(text)
    text = ensure_include(text, '#include "sde_trace.h"\n', 'sde_kms')
    text = inject_function_entry(text, 'sde_kms_prepare_commit', 'a52_ackfr_record("P276 296K p");')
    text = inject_function_entry(text, 'sde_kms_commit', 'a52_ackfr_record("P276 296K c");')
    text = inject_function_entry(text, 'sde_kms_complete_commit', 'a52_ackfr_record("P276 296K x");')
    if behavior_counts(text) != before:
        raise SystemExit('Phase296 sde_kms hardware-affecting token count changed')
    return text


def verify(root: Path) -> dict:
    files = {DRV: (root / DRV).read_text(), ATOMIC: (root / ATOMIC).read_text(), KMS: (root / KMS).read_text()}
    required = {
        DRV: [MARK, 'P276 296R r=%d', 'P276 296O e', 'P276 296O x r=%d', 'P276 296A e', 'P276 296A x r=%d'],
        ATOMIC: ['P276 296C e n=%d', 'P276 296C x r=%d q=1', 'P276 296C x r=0 q=0', 'P276 296C x r=%d q=2', 'P276 296W e'],
        KMS: ['P276 296K p', 'P276 296K c', 'P276 296K x'],
    }
    for path, markers in required.items():
        for marker in markers:
            count = files[path].count(marker)
            if count != 1:
                raise SystemExit(f'Phase296 audit {path} marker {marker!r}: count={count}')
        if REC_INCLUDE not in files[path]:
            raise SystemExit(f'Phase296 recorder include missing from {path}')
    rec = (root / REC).read_text()
    if 'return !strncmp(message, "P276 ", 5)' not in rec or 'strncmp(fmt, "P276", 4)' not in rec:
        raise SystemExit('Phase296 inherited P276 critical/admission contract missing')
    return {
        'status': 'phase296-userspace-drm-atomic-frontier-v1-staged',
        'functional_change': 'instrumentation-only',
        'targets': [str(p) for p in files],
        'marker_count': sum(len(v) for v in required.values()),
        'recorder_transport': 'inherited P276 critical-after-capacity path',
        'recorder_modified': False,
        'hardware_control_flow_changed': False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--report', type=Path)
    args = ap.parse_args()
    root = args.root
    for p in (DRV, ATOMIC, KMS, REC):
        if not (root / p).is_file():
            raise SystemExit(f'Phase296 missing required source: {p}')

    if not args.check_only:
        (root / DRV).write_text(patch_drv((root / DRV).read_text()))
        (root / ATOMIC).write_text(patch_atomic((root / ATOMIC).read_text()))
        (root / KMS).write_text(patch_kms((root / KMS).read_text()))

    report = verify(root)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
