#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"
if not dd.is_file():
    raise SystemExit(f"missing required source: {dd}")

s = dd.read_text()

if "A52 P156 EARLY PROBE CENSUS" in s:
    print("A52 P156 early probe census already applied")
    raise SystemExit(0)

old_defs = """#define A52_PROBE_CENSUS_MAX 512
#define A52_PROBE_CENSUS_DELAY_MS 12000
"""
new_defs = """#define A52_PROBE_CENSUS_MAX 512
#define A52_PROBE_CENSUS_CHECKPOINT_MS 2000
#define A52_PROBE_CENSUS_FINAL_MS 8000

/*
 * A52 P156 EARLY PROBE CENSUS
 *
 * P155 armed the recorder from late_initcall(), which proved too late:
 * global-async probe Oopses damaged init before the finalizer existed.
 * P156 starts the recorder from core_initcall() and snapshots ramoops every
 * two seconds.  The final 8-second checkpoint directly reboots to recovery.
 */
"""
if s.count(old_defs) != 1:
    raise SystemExit(f"P156 definitions anchor count={s.count(old_defs)}")
s = s.replace(old_defs, new_defs, 1)

start = s.find("static int a52_probe_census_finalizer(void *unused)")
end_marker = "late_initcall(a52_probe_census_init);"
end = s.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("P156 could not locate P155 finalizer block")
end += len(end_marker)

new_block = r'''static void a52_probe_census_checkpoint(unsigned int elapsed_ms, bool final)
{
	unsigned long flags;
	unsigned int i, used, overflow;
	unsigned int running = 0, fail = 0, defer = 0, reject = 0, ok = 0;

	spin_lock_irqsave(&a52_probe_census_lock, flags);
	used = a52_probe_census_used;
	overflow = a52_probe_census_overflow;
	spin_unlock_irqrestore(&a52_probe_census_lock, flags);

	pr_emerg("A52_PROBE_CENSUS CHECKPOINT_BEGIN t_ms=%u final=%u used=%u overflow=%u probe_count=%d\\n",
		 elapsed_ms, final ? 1 : 0, used, overflow,
		 atomic_read(&probe_count));

	for (i = 0; i < used; i++) {
		struct a52_probe_census_rec rec;

		spin_lock_irqsave(&a52_probe_census_lock, flags);
		rec = a52_probe_census[i];
		spin_unlock_irqrestore(&a52_probe_census_lock, flags);

		switch (rec.state) {
		case A52_PC_RUNNING: running++; break;
		case A52_PC_OK: ok++; break;
		case A52_PC_DEFER: defer++; break;
		case A52_PC_REJECT: reject++; break;
		case A52_PC_FAIL: fail++; break;
		default: break;
		}

		if (rec.state != A52_PC_OK)
			pr_emerg("A52_PROBE_CENSUS t_ms=%u state=%s attempts=%u ret=%d drv=%s dev=%s age_ms=%u\\n",
				 elapsed_ms,
				 a52_probe_census_state_name(rec.state),
				 rec.attempts, rec.ret, rec.driver, rec.device,
				 jiffies_to_msecs(jiffies - rec.started));
	}

	pr_emerg("A52_PROBE_CENSUS CHECKPOINT_END t_ms=%u ok=%u running=%u fail=%u defer=%u reject=%u overflow=%u probe_count=%d\\n",
		 elapsed_ms, ok, running, fail, defer, reject, overflow,
		 atomic_read(&probe_count));

	/*
	 * Snapshot every checkpoint, not only the final one.  If a later Oops
	 * deadlocks init or the scheduler, the most recent completed checkpoint
	 * is already in the ramoops dmesg zone.
	 */
	kmsg_dump(KMSG_DUMP_OOPS);
	pr_emerg("A52_PROBE_CENSUS SNAPSHOT_DONE t_ms=%u\\n", elapsed_ms);
}

static int a52_probe_census_finalizer(void *unused)
{
	unsigned int elapsed_ms;

	pr_notice("A52_PROBE_CENSUS early recorder thread running\\n");

	for (elapsed_ms = A52_PROBE_CENSUS_CHECKPOINT_MS;
	     elapsed_ms <= A52_PROBE_CENSUS_FINAL_MS;
	     elapsed_ms += A52_PROBE_CENSUS_CHECKPOINT_MS) {
		msleep(A52_PROBE_CENSUS_CHECKPOINT_MS);
		a52_probe_census_checkpoint(
			elapsed_ms,
			elapsed_ms == A52_PROBE_CENSUS_FINAL_MS);
	}

	pr_emerg("A52_PROBE_CENSUS FINAL_REBOOT recovery at %u ms\\n",
		 A52_PROBE_CENSUS_FINAL_MS);
	kmsg_dump(KMSG_DUMP_OOPS);
	machine_restart("recovery");

	pr_emerg("A52_PROBE_CENSUS machine_restart returned unexpectedly\\n");
	return 0;
}

static int __init a52_probe_census_init(void)
{
	struct task_struct *task;

	task = kthread_run(a52_probe_census_finalizer, NULL, "a52_probe_census");
	if (IS_ERR(task))
		pr_err("A52_PROBE_CENSUS failed to start early recorder: %ld\\n",
		       PTR_ERR(task));
	else
		pr_notice("A52_PROBE_CENSUS EARLY_ARMED interval_ms=%u final_ms=%u\\n",
			  A52_PROBE_CENSUS_CHECKPOINT_MS,
			  A52_PROBE_CENSUS_FINAL_MS);

	return 0;
}
core_initcall(a52_probe_census_init);'''

s = s[:start] + new_block + s[end:]

for token in (
    "A52 P156 EARLY PROBE CENSUS",
    "A52_PROBE_CENSUS_CHECKPOINT_MS 2000",
    "A52_PROBE_CENSUS_FINAL_MS 8000",
    "A52_PROBE_CENSUS CHECKPOINT_BEGIN",
    "A52_PROBE_CENSUS CHECKPOINT_END",
    "A52_PROBE_CENSUS SNAPSHOT_DONE",
    "A52_PROBE_CENSUS FINAL_REBOOT",
    "A52_PROBE_CENSUS EARLY_ARMED",
    "core_initcall(a52_probe_census_init)",
    "kmsg_dump(KMSG_DUMP_OOPS)",
    'machine_restart("recovery")',
):
    if token not in s:
        raise SystemExit(f"P156 audit missing: {token}")

if "late_initcall(a52_probe_census_init)" in s:
    raise SystemExit("P156 still contains late_initcall census registration")
if "A52_PROBE_CENSUS_DELAY_MS 12000" in s:
    raise SystemExit("P156 still contains old 12-second one-shot delay")

dd.write_text(s)

print("A52 P156 early periodic probe census applied")
print("  recorder initcall: core_initcall")
print("  checkpoints: 2s / 4s / 6s / 8s")
print("  every checkpoint: compact non-OK census + kmsg_dump(OOPS)")
print("  final 8s checkpoint: direct machine_restart(\"recovery\")")
