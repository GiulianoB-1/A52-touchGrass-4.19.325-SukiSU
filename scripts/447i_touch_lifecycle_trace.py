#!/usr/bin/env python3
"""447I addendum 2: passive STM coordinate lifecycle diagnostic.
Record first 48 parsed finger actions for TG/GKI parity. No input change.
"""
from pathlib import Path
import argparse

TAG = "A52_PHASE447I_TOUCH_LIFECYCLE_DIAG_V1"
SRC = Path("drivers/input/touchscreen/stm/fts5cu56a/fts_ts.c")

def once(s, old, new, label):
    n = s.count(old)
    if n != 1:
        raise RuntimeError("P447I " + label + " anchor count=" + str(n))
    return s.replace(old, new, 1)

def patch(s):
    if TAG in s:
        return s
    if "P446X TOUCH EVENT" not in s:
        raise RuntimeError("P447I missing P446X STM touch prerequisite")
    anchor = "static u8 fts_event_handler_type_b(struct fts_ts_info *info)\n"
    s = once(s, anchor, r'''/* A52_PHASE447I_TOUCH_LIFECYCLE_DIAG_V1 */
static atomic_t a52i_touch_life_samples = ATOMIC_INIT(0);
''' + anchor, "counter")
    anchor = "info->finger[TouchID].y = (p_event_coord->y_11_4 << 4) | (p_event_coord->y_3_0);\n"
    s = once(s, anchor, anchor + r'''			/* Snapshot parser state BEFORE input reporting. */
			if (atomic_read(&a52i_touch_life_samples) < 48) {
				int seq = atomic_inc_return(&a52i_touch_life_samples);
				if (seq <= 48)
					pr_info("P447I TOUCH LIFE seq=%d slot=%u action=%u prev=%u count_before=%d\n",
						seq, TouchID, info->finger[TouchID].action,
						prev_action, info->touch_count);
			}
''', "event snapshot")
    return s

def check(root):
    s = (root / SRC).read_text()
    for token in (TAG, "P447I TOUCH LIFE seq=", "a52i_touch_life_samples",
                  "prev_action, info->touch_count", "P446X TOUCH EVENT"):
        if token not in s:
            raise RuntimeError("P447I missing token " + token)
    if s.count("P447I TOUCH LIFE seq=") != 1:
        raise RuntimeError("P447I duplicate lifecycle trace")
    print("P447I TRACE PASS: first 48 parsed actions, no input semantic changes")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()
    if not args.check_only:
        f = args.root / SRC
        f.write_text(patch(f.read_text()))
    check(args.root)
