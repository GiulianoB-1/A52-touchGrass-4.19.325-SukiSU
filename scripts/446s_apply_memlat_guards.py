#!/usr/bin/env python3
"""Phase446S: guard QCOM memlat governor and 5.10 devfreq delayed-work lifecycle.

A failed HW monitor START (-EOPNOTSUPP) must never suspend or resume
an uninitialized devfreq delayed work item. Fail closed on unmatched source.
This script runs on the reconstructed FINAL 5.10 tree, not on repo scripts.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE446S_MEMLAT_LIFECYCLE_V1"
CORE_MARK = "A52_PHASE446S_DEVFREQ_TIMER_GUARD_V1"
MEMLAT = Path("drivers/devfreq/governor_memlat.c")
CORE = Path("drivers/devfreq/devfreq.c")

def once(s, before, after, what):
    n=s.count(before)
    if n != 1:
        raise RuntimeError("446S %s: expected one anchor, found %d" % (what, n))
    return s.replace(before, after, 1)

def replace_func(src, signature, body, what):
    pos=src.find(signature)
    if pos < 0 or src.find(signature,pos+1) >= 0:
        raise RuntimeError("446S %s: missing/duplicate signature" % what)
    begin=src.find("{", pos+len(signature))
    if begin < 0:
        raise RuntimeError("446S %s missing body" % what)
    depth=0
    end=None
    for i in range(begin,len(src)):
        if src[i]=="{":depth+=1
        elif src[i]=="}":
            depth-=1
            if depth==0:
                end=i+1
                break
    if end is None:
        raise RuntimeError("446S %s unterminated body" % what)
    return src[:pos]+signature+"\n{\n"+body+"\n}"+src[end:]

def patch_memlat(s):
    if MARK in s:
        return s
    s=once(s,"bool mon_started;\n","bool mon_started;\n\t/* "+MARK+": remains valid across suspend until STOP. */\n\tbool monitor_ready;\n","node flags")
    # The hw->df membership check prevents using df->data after START failure
    # restored it to the previous governor's private data.
    helper=r'''
/* A52_PHASE446S_MEMLAT_LIFECYCLE_V1: validate node ownership after START failure. */
static bool p446s_memlat_owner(struct devfreq *df)
{
	struct memlat_node *node = find_memlat_node(df);

	return node && node->hw && node->hw->df == df && df->data == node;
}
'''
    s=once(s,"static int start_monitor(struct devfreq *df)",helper+"\nstatic int start_monitor(struct devfreq *df)","ownership helper")
    s=replace_func(s,"static int start_monitor(struct devfreq *df)",r'''	struct memlat_node *node = df->data;
	struct memlat_hwmon *hw = node->hw;
	struct device *dev = df->dev.parent;
	int ret;

	node->monitor_ready = false;
	node->mon_started = false;
	ret = hw->start_hwmon(hw);
	if (ret) {
		dev_err(dev, "P446S MEMLAT start_hwmon dev=%s err=%d cpu/pmu/event=provider-dependent\n",
			dev_name(dev), ret);
		return ret;
	}
	if (!hw->should_ignore_df_monitor)
		devfreq_monitor_start(df);

	/* The timer only exists if devfreq_monitor_start actually initialized it. */
	node->monitor_ready = !hw->should_ignore_df_monitor &&
			      !!df->work.work.func;
	node->mon_started = true;
	return 0;''',"start_monitor")
    s=replace_func(s,"static void stop_monitor(struct devfreq *df)",r'''	struct memlat_node *node = df->data;
	struct memlat_hwmon *hw = node->hw;

	node->mon_started = false;
	if (node->monitor_ready)
		devfreq_monitor_stop(df);
	node->monitor_ready = false;
	hw->stop_hwmon(hw);''',"stop_monitor")
    s=replace_func(s,"static int gov_suspend(struct devfreq *df)",r'''	struct memlat_node *node = df->data;
	unsigned long prev_freq = df->previous_freq;

	if (!node->monitor_ready) {
		dev_warn_ratelimited(df->dev.parent,
			"P446S MEMLAT skip_monitor dev=%s event=suspend reason=not-initialized\n",
			dev_name(&df->dev));
		return 0;
	}
	node->mon_started = false;
	devfreq_monitor_suspend(df);
	mutex_lock(&df->lock);
	update_devfreq(df);
	mutex_unlock(&df->lock);
	node->resume_freq = max(prev_freq, 1UL);
	return 0;''',"gov_suspend")
    s=replace_func(s,"static int gov_resume(struct devfreq *df)",r'''	struct memlat_node *node = df->data;

	if (!node->monitor_ready) {
		dev_warn_ratelimited(df->dev.parent,
			"P446S MEMLAT skip_monitor dev=%s event=resume reason=not-initialized\n",
			dev_name(&df->dev));
		return 0;
	}
	mutex_lock(&df->lock);
	update_devfreq(df);
	mutex_unlock(&df->lock);
	node->resume_freq = 0;
	devfreq_monitor_resume(df);
	node->mon_started = true;
	return 0;''',"gov_resume")
    anchor="\tswitch (event) {\n\tcase DEVFREQ_GOV_START:"
    guard=r'''	/* Failed START restores df->data, so never reinterpret the old data. */
	if (event != DEVFREQ_GOV_START && !p446s_memlat_owner(df)) {
		dev_warn_ratelimited(df->dev.parent,
			"P446S MEMLAT skip_monitor dev=%s event=%u reason=start-failed\n",
			dev_name(&df->dev), event);
		return 0;
	}
	switch (event) {
	case DEVFREQ_GOV_START:'''
    s=once(s,anchor,guard,"failed START event guard")
    # The imported 5.10 memlat governor can differ from original TG 4.19:
    # some historical Phase429 replay branches have no interval-update call
    # at all. Scope this check to the EVENT interval case, not an exact
    # two-line 4.19 formatting anchor. Never bypass a call that still exists.
    begin = s.find("\tcase DEVFREQ_GOV_INTERVAL:")
    if begin < 0 or s.find("\tcase DEVFREQ_GOV_INTERVAL:", begin + 1) != -1:
        raise RuntimeError("446S interval guard: missing/duplicate interval case")
    tail = s[begin:]
    stop = __import__("re").search(r"(?m)^\t(?:case [A-Z0-9_]+:|default:|})", tail[1:])
    end = begin + 1 + stop.start() if stop else len(s)
    interval = s[begin:end]
    import re
    calls = list(re.finditer(r"\bdevfreq_interval_update\s*\(", interval))
    if len(calls) > 1:
        raise RuntimeError("446S interval guard: multiple update calls in interval case")
    if not calls:
        # The legacy 4.19 interval hook is absent in this final 5.10 source.
        # No devfreq_interval_update to invoke on uninitialized work here.
        print("P446S MEMLAT interval update ABSENT in final source; no guard needed")
    else:
        # Verify the call is a standalone C statement so a conditional guard
        # does not accidentally change a return value or a compound expression.
        matches = list(re.finditer(
            r"(?m)^(?P<indent>[ \t]*)devfreq_interval_update\(\s*df\s*,\s*&sample_ms\s*\);[ \t]*$",
            interval))
        if len(matches) != 1:
            raise RuntimeError(
                "446S interval guard: unsupported interval call syntax: " +
                repr(interval[max(0,calls[0].start()-100):calls[0].start()+110]))
        m = matches[0]
        guard = (m.group("indent") + "if (node->monitor_ready) {\n"
                 + m.group("indent") + "\t" + m.group(0).lstrip() + "\n"
                 + m.group("indent") + "}")
        interval = interval[:m.start()] + guard + interval[m.end():]
        s = s[:begin] + interval + s[end:]
        print("P446S MEMLAT interval update guarded in final source")

    return s

def patch_core(s):
    if CORE_MARK in s:
        return s
    for fn in ("devfreq_monitor_suspend","devfreq_monitor_resume","devfreq_monitor_stop"):
        sig="void "+fn+"(struct devfreq *devfreq)\n{"
        # devfreq_monitor_resume has declarations: insert after them, before mutex.
        start=s.find(sig)
        if start<0 or s.find(sig,start+len(sig))>=0:
            raise RuntimeError("446S core: cannot identify "+fn)
        end=s.find("EXPORT_SYMBOL("+fn+");",start)
        if end<0:raise RuntimeError("446S core: missing export "+fn)
        before=s[start:end]
        loc=before.find("\tmutex_lock(&devfreq->lock);")
        if loc<0:
            if fn=="devfreq_monitor_stop":
                # stop has a cancel_delayed_work_sync call, no mutex lock.
                loc=before.find("\tcancel_delayed_work_sync(&devfreq->work);")
            if loc<0:
                raise RuntimeError("446S core: no safe insertion "+fn)
        prefix=r'''	/* A52_PHASE446S_DEVFREQ_TIMER_GUARD_V1 */
	if (unlikely(!devfreq->work.work.func)) {
		dev_warn_once(&devfreq->dev,
			"P446S MEMLAT skip_monitor dev=%s event=FUNC uninitialized-work\n",
			dev_name(&devfreq->dev));
		return;
	}
'''.replace("FUNC",fn.replace("devfreq_monitor_",""))
        before=before[:loc]+prefix+before[loc:]
        s=s[:start]+before+s[end:]
    return s

def check(root):
    m=(root/MEMLAT).read_text()
    c=(root/CORE).read_text()
    must={
        "memlat lifecycle":(m,(MARK,"monitor_ready","p446s_memlat_owner(df)",
                                "P446S MEMLAT skip_monitor")),
        "devfreq core":(c,(CORE_MARK,"!devfreq->work.work.func",
                            "devfreq_monitor_resume","devfreq_monitor_suspend")),
    }
    for label,(src,tokens) in must.items():
        for token in tokens:
            if token not in src:raise RuntimeError("446S "+label+" missing "+token)
    event = m.find("case DEVFREQ_GOV_INTERVAL:")
    if event < 0:
        raise RuntimeError("446S memlat source audit missing interval event")
    event_tail = m[event:]
    import re
    boundary = re.search(r"(?m)^\t(?:case [A-Z0-9_]+:|default:|})", event_tail[1:])
    interval = event_tail[:1 + boundary.start()] if boundary else event_tail
    if "devfreq_interval_update(" in interval and "if (node->monitor_ready)" not in interval:
        raise RuntimeError("446S memlat interval update has no initialized-timer guard")
    print("P446S MEMLAT source audit PASS: failed-start ownership, initialized-work guards")

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",required=True,type=Path)
    parser.add_argument("--check-only",action="store_true")
    opt=parser.parse_args()
    m=opt.root/MEMLAT;c=opt.root/CORE
    if not opt.check_only:
        if not m.is_file() or not c.is_file():
            raise RuntimeError("446S requires imported memlat and devfreq core")
        m.write_text(patch_memlat(m.read_text()))
        c.write_text(patch_core(c.read_text()))
    check(opt.root)

if __name__=="__main__":
    main()
