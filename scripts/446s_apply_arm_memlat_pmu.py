#!/usr/bin/env python3
"""446S Qualcomm memlat PMU ABI parity and failed-start rollback.
Keep upstream ARMv8 PMU event filter unchanged. Source pinned to TG 6bf351bd.
"""
from pathlib import Path
import argparse
TAG="A52_MEMLAT_NO_EXCLUDE_IDLE"
F=Path("drivers/devfreq/arm-memlat-mon.c")
def once(s,old,new,what):
    n=s.count(old)
    if n!=1:
        raise RuntimeError("446S arm memlat %s anchor count %d"%(what,n))
    return s.replace(old,new,1)
def patch(s):
    if TAG in s:
        return s
    s=once(s,"\tattr->exclude_idle = 1;",
           "\t/* A52_MEMLAT_NO_EXCLUDE_IDLE: ACK arm64 PMU rejects exclude_idle. */\n"
           "\tattr->exclude_idle = 0;","exclude_idle")
    s=once(s,'''	if (IS_ERR(pevent))
		return PTR_ERR(pevent);''',
         '''	if (IS_ERR(pevent)) {
		int err = PTR_ERR(pevent);
		pr_err_ratelimited("P446S MEMLAT PERF_FAIL cpu=%d event=%#x exclude_idle=%u rc=%d\n",
				   cpu, event_id, attr->exclude_idle, err);
		return err;
	}''',"event failure")
    s=once(s,'''unlock_out:
	mutex_unlock(&cpu_grp->mons_lock);
	kfree(attr);

	return ret;
}

static void stop_hwmon''',
         '''unlock_out:
	/* The legacy path increments num_active_mons before allocating PMU
	 * counters. Roll back partial allocations and count on EVERY failure.
	 * Never queue the delayed work on these failure paths.
	 */
	if (ret) {
		for_each_cpu(cpu, &mon->cpus) {
			unsigned int idx = cpu - cpumask_first(&mon->cpus);

			if (mon->miss_ev)
				delete_event(&mon->miss_ev[idx]);
			if (mon->access_ev)
				delete_event(&mon->access_ev[idx]);
			if (mon->wb_ev)
				delete_event(&mon->wb_ev[idx]);
		}
		if (should_init_cpu_grp)
			free_common_evs(cpu_grp);
		cpu_grp->num_active_mons--;
		mon->is_active = false;
		pr_warn("P446S MEMLAT START_ROLLBACK dev=%s rc=%d active=%u\n",
			dev_name(hw->dev), ret, cpu_grp->num_active_mons);
	}
	mutex_unlock(&cpu_grp->mons_lock);
	kfree(attr);

	return ret;
}

static void stop_hwmon''',"start rollback")
    return s
def check(s):
    for token in (TAG,"attr->exclude_idle = 0;",
                  "P446S MEMLAT PERF_FAIL","P446S MEMLAT START_ROLLBACK",
                  "cpu_grp->num_active_mons--;"):
        if token not in s:
            raise RuntimeError("446S arm memlat missing "+token)
    if "attr->exclude_idle = 1;" in s:
        raise RuntimeError("446S arm memlat still requests exclude_idle")
    print("P446S PMU contract PASS: exclude_idle=0, balanced failed monitor start")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    x=p.parse_args()
    path=x.root/F
    if not x.check_only:
        path.write_text(patch(path.read_text()))
    check(path.read_text())
if __name__=="__main__":
    main()
