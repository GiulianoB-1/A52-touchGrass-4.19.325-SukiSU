#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE302_ODSIGN_WAIT_CHANNEL_TRACE_V1'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase302 {label}: expected exactly 1 anchor, found {count}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text

    old = '''static void a52_r226_task_snapshot(unsigned int tick)\n{\n\tstruct task_struct *group;\n\tstruct task_struct *task;\n\tunsigned int found = 0;\n\n\tif (!a52_r226_snapshot_tick(tick))\n\t\treturn;\n\trcu_read_lock();\n\tfor_each_process_thread(group, task) {\n\t\tif (!a52_r226_od_task(task->comm))\n\t\t\tcontinue;\n\t\tfound++;\n\t\ta52_ackfr_record("ODSPOST 226 ts t=%u p=%d c=%.10s s=%lx x=%x r=%d o=%d i=%d",\n\t\t\ttick, task_pid_nr(task), task->comm,\n\t\t\tREAD_ONCE(task->state), READ_ONCE(task->exit_state),\n\t\t\tREAD_ONCE(task->on_rq), READ_ONCE(task->on_cpu),\n\t\t\ttask->in_iowait);\n\t}\n\trcu_read_unlock();\n\tif (!found)\n\t\ta52_ackfr_record("ODSPOST 226 ts t=%u none", tick);\n}\n'''

    new = '''/* A52_PHASE302_ODSIGN_WAIT_CHANNEL_TRACE_V1\n * Observation only: preserve the inherited Phase226 task-state snapshot and\n * add the scheduler wait channel for blocked odsign/odrefresh tasks.  %ps is\n * recorded first so KASLR does not prevent offline identification; the raw\n * address is emitted separately as a fallback.\n */\nstatic void a52_r226_task_snapshot(unsigned int tick)\n{\n\tstruct task_struct *group;\n\tstruct task_struct *task;\n\tunsigned int found = 0;\n\n\tif (!a52_r226_snapshot_tick(tick))\n\t\treturn;\n\trcu_read_lock();\n\tfor_each_process_thread(group, task) {\n\t\tunsigned long wchan;\n\n\t\tif (!a52_r226_od_task(task->comm))\n\t\t\tcontinue;\n\t\tfound++;\n\t\ta52_ackfr_record("ODSPOST 226 ts t=%u p=%d c=%.10s s=%lx x=%x r=%d o=%d i=%d",\n\t\t\ttick, task_pid_nr(task), task->comm,\n\t\t\tREAD_ONCE(task->state), READ_ONCE(task->exit_state),\n\t\t\tREAD_ONCE(task->on_rq), READ_ONCE(task->on_cpu),\n\t\t\ttask->in_iowait);\n\n\t\t/* get_wchan() returns zero when a usable blocked wait site is unavailable. */\n\t\twchan = get_wchan(task);\n\t\ta52_ackfr_record("P276 302W t=%u p=%d c=%.10s w=%ps",\n\t\t\ttick, task_pid_nr(task), task->comm, (void *)wchan);\n\t\ta52_ackfr_record("P276 302A t=%u p=%d a=%lx",\n\t\t\ttick, task_pid_nr(task), wchan);\n\t}\n\trcu_read_unlock();\n\tif (!found)\n\t\ta52_ackfr_record("ODSPOST 226 ts t=%u none", tick);\n}\n'''
    return one(text, old, new, 'Phase226 snapshot')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, default=Path('.'))
    ap.add_argument('--report', type=Path)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    path = args.root / REC
    before_b = path.read_bytes()
    before = before_b.decode()
    after = patch(before)

    required_before = [
        'A52_PHASE226_ODSIGN_GATE_TRACE',
        'ODSPOST 226 ts t=%u p=%d c=%.10s s=%lx x=%x r=%d o=%d i=%d',
    ]
    for token in required_before:
        if token not in before:
            raise SystemExit('Phase302 inherited recorder anchor missing: ' + token)

    required_after = [
        MARK,
        'P276 302W t=%u p=%d c=%.10s w=%ps',
        'P276 302A t=%u p=%d a=%lx',
        'get_wchan(task)',
    ]
    for token in required_after:
        if token not in after:
            raise SystemExit('Phase302 marker missing after patch: ' + token)

    # Preserve the inherited Phase226 snapshot and its schedule exactly.
    for token in [
        'return tick == 20 || tick == 30 || tick == 45 || tick == 60 ||',
        'tick == 90 || tick == 120 || tick == 150 || tick == 180;',
        'ODSPOST 226 ts t=%u p=%d c=%.10s s=%lx x=%x r=%d o=%d i=%d',
        'ODSPOST 226 ts t=%u none',
    ]:
        if before.count(token) != after.count(token):
            raise SystemExit('Phase302 changed inherited Phase226 contract: ' + token)

    report = {
        'phase': 302,
        'name': 'ODSIGN-WAIT-CHANNEL-TRACE-V1',
        'functional_change': 'instrumentation-only',
        'target': REC.as_posix(),
        'before_sha256': sha(before_b),
        'after_sha256': sha(after.encode()),
        'marker': MARK,
        'markers': [
            'P276 302W t=%u p=%d c=%.10s w=%ps',
            'P276 302A t=%u p=%d a=%lx',
        ],
        'phase226_snapshot_preserved': True,
    }

    if args.check_only:
        if after != before:
            raise SystemExit('Phase302 check-only: patch not already applied')
    else:
        path.write_text(after)

    if args.report:
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')

    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
