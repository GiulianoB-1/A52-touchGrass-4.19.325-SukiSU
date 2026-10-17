#!/usr/bin/env python3
"""Phase446S: passive device PM timings, no GPU/power state changes."""
import argparse
from pathlib import Path
TAG="A52_PHASE446S_PM_SLOW_CALLBACK_V1"
F=Path("drivers/base/power/main.c")
def patch(s):
    if TAG in s:return s
    mark='nsecs = (s64) ktime_to_ns(ktime_sub(rettime, calltime));'
    if s.count(mark)!=1:raise RuntimeError("446S pm timing anchor missing")
    s=s.replace(mark,mark+r'''

	/* A52_PHASE446S_PM_SLOW_CALLBACK_V1.
	 * Keep existing PM callback timing, highlight stalls over 100 ms.
	 * This does not alter device power state or callbacks.
	 */
	if (nsecs >= 100000000LL)
		dev_warn(dev, "P446S PM_SLOW callback=%pS rc=%d elapsed_ms=%lld pid=%d\n",
			 cb, error, (long long)(nsecs / 1000000LL),
			 task_pid_nr(current));
''',1)
    return s
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    args=p.parse_args()
    path=args.root/F
    if not args.check_only:path.write_text(patch(path.read_text()))
    src=path.read_text()
    for x in (TAG,"P446S PM_SLOW","nsecs >= 100000000LL"):
        if x not in src:raise RuntimeError("missing "+x)
    print("P446S PM timing audit PASS: >100ms callbacks logged without changing PM state")
if __name__=="__main__":main()
