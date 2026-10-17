#!/usr/bin/env python3
"""Phase446T unsampled first-300 successful swap observations from 15s.

446S intentionally starts its own 300-commit window at 85s, after the
observed 47s GPU failure. Preserve its late window and add an early one.
"""
import argparse
from pathlib import Path

P=Path("drivers/a52_display/msm/msm_atomic.c")
TAG="A52_PHASE446T_EARLY_UNSAMPLED_FRAME_V1"

def patch(s):
    if TAG in s:
        return s
    anchor="static atomic_t p446s_frame_seq = ATOMIC_INIT(0);"
    if s.count(anchor)!=1:
        raise RuntimeError("446T early frame expected one 446S counter")
    s=s.replace(anchor,
        "/* "+TAG+" */\n"
        "static atomic_t p446t_frame_early_seq = ATOMIC_INIT(0);\n"+anchor,1)
    anchor="    /* 85-SECOND START. Log consecutive 300 commits without modulo sampling. */"
    if s.count(anchor)!=1:
        raise RuntimeError("446T early frame expected one original 85s window")
    insert=r'''    /* A52_PHASE446T_EARLY_UNSAMPLED_FRAME_V1: one line per swapped
     * commit after 15s, no modulo selection, while preserving the original
     * 446S >85s 300-commit window. Metadata only, no framebuffer reads.
     */
    if (now_ns >= 15ULL * 1000000000ULL &&
        now_ns < 85ULL * 1000000000ULL &&
        !strcmp(stage, "swapped")) {
        int seq = atomic_inc_return(&p446t_frame_early_seq);

        if (seq <= 300)
            pr_info("P446T FRAME_EARLY seq=%d t_ns=%llu pid=%d comm=%s rc=%d nonblock=%u crtcs=%d enabled=%d planes=%d fb0=%u fmt0=%08x fb1=%u fmt1=%08x\n",
                seq, (unsigned long long)now_ns,
                task_pid_nr(current), current->comm, result, nonblock,
                ncrtcs, n_enabled, active, fb_id[0], format[0],
                fb_id[1], format[1]);
    }
'''
    return s.replace(anchor,insert+anchor,1)

def check(s):
    for expected in (TAG, 'p446t_frame_early_seq', 'P446T FRAME_EARLY',
        'now_ns < 85ULL * 1000000000ULL', '!strcmp(stage, "swapped")',
        'P446S FRAME seq=', '85ULL * 1000000000ULL'):
        if expected not in s:
            raise RuntimeError("446T frame trace missing "+expected)
    if s.count('P446T FRAME_EARLY seq=')!=1:
        raise RuntimeError("446T duplicate early frame trace")
    print("P446T FRAME_EARLY audit PASS: 15-85s, 300 consecutive swapped commits")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True,type=Path)
    ap.add_argument("--check-only",action="store_true")
    args=ap.parse_args()
    p=args.root/P
    s=p.read_text()
    if not args.check_only:
        s=patch(s)
        p.write_text(s)
    check(s)

if __name__=="__main__":
    main()
