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
MARK = 'A52_PHASE296_USERSPACE_DRM_ATOMIC_FRONTIER_V2'
REC_INCLUDE = '#include <linux/a52_ack_secure_flight_recorder.h>\n'


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase296 {label}: expected exactly 1 anchor, found {count}')
    return text.replace(old, new, 1)


def ensure_include(text: str, include: str, anchor: str, label: str) -> str:
    if include in text:
        return text
    return one(text, anchor, anchor + include, f'{label} include')


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
    if REC_INCLUDE not in text:
        text = ensure_include(text, REC_INCLUDE, '#include "sde_dbg.h"\n', 'msm_drv recorder')
    text = ensure_include(text, '#include <linux/workqueue.h>\n', '#include <linux/atomic.h>\n', 'msm_drv workqueue')
    text = ensure_include(text, '#include <linux/jiffies.h>\n', '#include <linux/workqueue.h>\n', 'msm_drv jiffies')

    marker_anchor = REC_INCLUDE
    text = one(
        text,
        marker_anchor,
        marker_anchor +
        '\n/* ' + MARK + '\n'
        ' * Passive post-bind frontier. Records whether userspace reaches the\n'
        ' * DRM open/atomic/commit path. The delayed summary only snapshots\n'
        ' * sticky counters; it does not alter display state.\n'
        ' */\n',
        'msm_drv marker',
    )

    state_anchor = 'static atomic_t a52_r211_close_sequence = ATOMIC_INIT(0);\n'
    state_block = '''static atomic_t a52_r211_close_sequence = ATOMIC_INIT(0);\n\nstatic atomic_t a52_p296_open_count = ATOMIC_INIT(0);\nstatic atomic_t a52_p296_open_rc = ATOMIC_INIT(-61);\nstatic atomic_t a52_p296_check_count = ATOMIC_INIT(0);\nstatic atomic_t a52_p296_check_rc = ATOMIC_INIT(-61);\n\nstatic void a52_p296_snapshot_workfn(struct work_struct *work)\n{\n\t(void)work;\n\ta52_ackfr_record("P276 296S o=%d/%d a=%d/%d",\n\t\t\tatomic_read(&a52_p296_open_count),\n\t\t\tatomic_read(&a52_p296_open_rc),\n\t\t\tatomic_read(&a52_p296_check_count),\n\t\t\tatomic_read(&a52_p296_check_rc));\n}\n\nstatic DECLARE_DELAYED_WORK(a52_p296_snapshot_work,\n\t\ta52_p296_snapshot_workfn);\n'''
    text = one(text, state_anchor, state_block, 'sticky summary state')

    check_entry = '''\tint rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_check_sequence);\n'''
    check_entry_new = '''\tint rc;\n\n\ta52_ackfr_record("P276 296A e");\n\ttrace_id = atomic_inc_return(&a52_r211_check_sequence);\n'''
    text = one(text, check_entry, check_entry_new, 'msm_atomic_check entry')

    check_exit = '''\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 check-exit n=%u rc=%d",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n'''
    check_exit_new = '''\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 check-exit n=%u rc=%d",\n\t\t\t\t  trace_id, rc);\n\tatomic_inc(&a52_p296_check_count);\n\tatomic_set(&a52_p296_check_rc, rc);\n\ta52_ackfr_record("P276 296A x r=%d", rc);\n\treturn rc;\n}\n'''
    text = one(text, check_exit, check_exit_new, 'msm_atomic_check exit')

    open_entry = '''\tint rc;\n\n\ttrace_id = atomic_inc_return(&a52_r211_open_sequence);\n'''
    open_entry_new = '''\tint rc;\n\n\ta52_ackfr_record("P276 296O e");\n\ttrace_id = atomic_inc_return(&a52_r211_open_sequence);\n'''
    text = one(text, open_entry, open_entry_new, 'msm_open entry')

    open_exit = '''\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 open-exit n=%u rc=%d",\n\t\t\t\t  trace_id, rc);\n\treturn rc;\n}\n'''
    open_exit_new = '''\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 211 open-exit n=%u rc=%d",\n\t\t\t\t  trace_id, rc);\n\tatomic_inc(&a52_p296_open_count);\n\tatomic_set(&a52_p296_open_rc, rc);\n\ta52_ackfr_record("P276 296O x r=%d", rc);\n\treturn rc;\n}\n'''
    text = one(text, open_exit, open_exit_new, 'msm_open exit')

    register = '''\tret = drm_dev_register(ddev, 0);\n\ta52_ackfr_record("DRMPOST dev-register exit rc=%d", ret);\n\tif (ret)\n\t\tgoto fail;\n'''
    register_new = '''\tret = drm_dev_register(ddev, 0);\n\ta52_ackfr_record("DRMPOST dev-register exit rc=%d", ret);\n\ta52_ackfr_record("P276 296R r=%d", ret);\n\tif (!ret)\n\t\tschedule_delayed_work(&a52_p296_snapshot_work,\n\t\t\tmsecs_to_jiffies(15000));\n\tif (ret)\n\t\tgoto fail;\n'''
    text = one(text, register, register_new, 'drm_dev_register return')

    if behavior_counts(text) != before:
        raise SystemExit('Phase296 msm_drv hardware-affecting token count changed')
    return text


def patch_atomic(text: str) -> str:
    if 'P276 296C e n=%d' in text:
        return text
    before = behavior_counts(text)
    if REC_INCLUDE not in text:
        text = ensure_include(text, REC_INCLUDE, '#include "sde_trace.h"\n', 'msm_atomic recorder')

    text = inject_function_entry(
        text, 'msm_atomic_commit',
        'a52_ackfr_record("P276 296C e n=%d", nonblock);',
    )
    text = inject_function_entry(
        text, 'complete_commit',
        'a52_ackfr_record("P276 296W e");',
    )

    prepare_err = '''\tif (ret) {\n\t\tSDE_ATRACE_END("atomic_commit");\n\t\treturn ret;\n\t}\n\n\tc = commit_init(state, nonblock);\n'''
    prepare_err_new = '''\tif (ret) {\n\t\ta52_ackfr_record("P276 296C x r=%d q=1", ret);\n\t\tSDE_ATRACE_END("atomic_commit");\n\t\treturn ret;\n\t}\n\n\tc = commit_init(state, nonblock);\n'''
    text = one(text, prepare_err, prepare_err_new, 'prepare_planes error')

    success = '''\tA52_R210_REC_ID(a52_trace, a52_trace_index, "commit return rc=0");\n\n\treturn 0;\nerr_free:\n'''
    success_new = '''\tA52_R210_REC_ID(a52_trace, a52_trace_index, "commit return rc=0");\n\ta52_ackfr_record("P276 296C x r=0 q=0");\n\n\treturn 0;\nerr_free:\n'''
    text = one(text, success, success_new, 'atomic success')

    final_error = '''\tdrm_atomic_helper_cleanup_planes(dev, state);\n\tSDE_ATRACE_END("atomic_commit");\n\treturn ret;\n}\n\nstruct drm_atomic_state *msm_atomic_state_alloc'''
    final_error_new = '''\tdrm_atomic_helper_cleanup_planes(dev, state);\n\tSDE_ATRACE_END("atomic_commit");\n\ta52_ackfr_record("P276 296C x r=%d q=2", ret);\n\treturn ret;\n}\n\nstruct drm_atomic_state *msm_atomic_state_alloc'''
    text = one(text, final_error, final_error_new, 'atomic final error')

    if behavior_counts(text) != before:
        raise SystemExit('Phase296 msm_atomic hardware-affecting token count changed')
    return text


def patch_kms(text: str) -> str:
    if 'P276 296K p' in text:
        return text
    before = behavior_counts(text)
    if REC_INCLUDE not in text:
        text = ensure_include(text, REC_INCLUDE, '#include "sde_trace.h"\n', 'sde_kms recorder')
    text = inject_function_entry(text, 'sde_kms_prepare_commit', 'a52_ackfr_record("P276 296K p");')
    text = inject_function_entry(text, 'sde_kms_commit', 'a52_ackfr_record("P276 296K c");')
    text = inject_function_entry(text, 'sde_kms_complete_commit', 'a52_ackfr_record("P276 296K x");')
    if behavior_counts(text) != before:
        raise SystemExit('Phase296 sde_kms hardware-affecting token count changed')
    return text


def verify(root: Path) -> dict:
    files = {
        DRV: (root / DRV).read_text(),
        ATOMIC: (root / ATOMIC).read_text(),
        KMS: (root / KMS).read_text(),
    }
    required = {
        DRV: [
            MARK, 'P276 296R r=%d', 'P276 296S o=%d/%d a=%d/%d',
            'P276 296O e', 'P276 296O x r=%d',
            'P276 296A e', 'P276 296A x r=%d',
            'msecs_to_jiffies(15000)',
        ],
        ATOMIC: [
            'P276 296C e n=%d', 'P276 296C x r=%d q=1',
            'P276 296C x r=0 q=0', 'P276 296C x r=%d q=2',
            'P276 296W e',
        ],
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
        'status': 'phase296-userspace-drm-atomic-frontier-v2-staged',
        'functional_change': 'instrumentation-only-with-delayed-diagnostic-summary',
        'targets': [str(p) for p in files],
        'marker_count': sum(len(v) for v in required.values()),
        'summary_delay_ms': 15000,
        'recorder_transport': 'inherited P276 critical-after-capacity path',
        'recorder_modified': False,
        'display_hardware_control_flow_changed': False,
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
