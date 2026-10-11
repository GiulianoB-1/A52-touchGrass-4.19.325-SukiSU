#!/usr/bin/env python3
"""FENCE1D: remove task_lock acquisition from interrupt-reentrant ACK recorder.

GKI g1afd3d0: CPU2 was interrupted inside a52_ackfr_record() by
lo_complete_rq(), which called the same recorder and deadlocked trying
to acquire task_lock from get_task_comm(). Only the best-effort task
name snapshot changes. Persistent event and mirror paths are retained.
"""
import argparse
from pathlib import Path

REL = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_FENCE1D_ACKFR_IRQ_COMM_SNAPSHOT_V1'
OLD = '\tget_task_comm(event.comm, current);'
NEW = (
    '\t/* ' + MARK + ': no task_lock in IRQ-reentrant recorder. */\n'
    '\tmemcpy(event.comm, current->comm, TASK_COMM_LEN);\n'
    "\tevent.comm[TASK_COMM_LEN - 1] = '\\0';"
)

def body(src):
    sig = 'void a52_ackfr_record(const char *fmt, ...)'
    if src.count(sig) != 1:
        raise RuntimeError('ACKFR definition must occur exactly once')
    start = src.index(sig)
    end = src.find('EXPORT_SYMBOL_GPL(a52_ackfr_record);', start)
    if end == -1:
        raise RuntimeError('ACKFR definition end missing')
    return start, end, src[start:end]

def audit(src):
    _, _, fn = body(src)
    if src.count(MARK) != 1 or fn.count(NEW) != 1 or OLD in fn:
        raise RuntimeError('ACKFR lockless comm copy missing or duplicated')
    if 'get_task_comm(' in fn or '__get_task_comm(' in fn:
        raise RuntimeError('ACKFR still takes task_lock')
    for anchor in ('a52_p392_gap_write(', 'a52_usr2_events[seq - 1] = event;',
                   'a52_usr2_persist_event(&event, A52_USR2_BANK_BOTH)'):
        if anchor not in fn:
            raise RuntimeError('Recorder persistence path changed: ' + anchor)
    print('FENCE1D PASS: IRQ-safe best-effort comm; P392 and R48 recording retained')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True, type=Path)
    p.add_argument('--check-only', action='store_true')
    args = p.parse_args()
    path = args.root / REL
    src = path.read_text()
    if not args.check_only and MARK not in src:
        first, last, fn = body(src)
        if fn.count(OLD) != 1:
            raise RuntimeError('Expected exactly one unsafe get_task_comm call')
        src = src[:first] + fn.replace(OLD, NEW, 1) + src[last:]
        path.write_text(src)
    audit(src)

if __name__ == '__main__':
    main()
