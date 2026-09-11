#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 63_apply_eevdf_phase5_picker_integration.py <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
fair_c = kernel / "kernel/sched/fair.c"
fc = fair_c.read_text()

if "EEVDF phase 5 picker integration" not in fc:
    anchor = """static struct sched_entity *
pick_next_entity(struct cfs_rq *cfs_rq, struct sched_entity *curr)
{
	struct sched_entity *left = __pick_first_entity(cfs_rq);
	struct sched_entity *se;
"""
    block = """static bool sched_eevdf_enabled __read_mostly;

static int __init sched_eevdf_setup(char *str)
{
	bool enable;

	if (!str || kstrtobool(str, &enable))
		return 0;

	sched_eevdf_enabled = enable;
	pr_info("EEVDF phase 5 picker %s\\\\n",
		enable ? "enabled" : "disabled");
	return 1;
}
__setup("sched_eevdf=", sched_eevdf_setup);

/* EEVDF phase 5 picker integration. */
static struct sched_entity *
pick_next_entity(struct cfs_rq *cfs_rq, struct sched_entity *curr)
{
	struct sched_entity *left = __pick_first_entity(cfs_rq);
	struct sched_entity *se;

	/*
	 * Safety gate: legacy Qualcomm/Samsung CFS + WALT remains the default.
	 * EEVDF selection is enabled only with sched_eevdf=1 on the kernel
	 * command line while we validate behavior on hardware.
	 */
	if (unlikely(sched_eevdf_enabled)) {
		se = eevdf_pick_entity(cfs_rq);
		if (se) {
			clear_buddies(cfs_rq, se);
			return se;
		}
	}
"""
    if fc.count(anchor) != 1:
        raise SystemExit("pick_next_entity anchor mismatch")
    fc = fc.replace(anchor, block, 1)

fair_c.write_text(fc)

text = fair_c.read_text()
for needle in [
    "EEVDF phase 5 picker integration",
    "sched_eevdf_enabled",
    "__setup(\"sched_eevdf=\"",
    "se = eevdf_pick_entity(cfs_rq);",
]:
    if needle not in text:
        raise SystemExit(f"missing phase 5 element: {needle}")

print("EEVDF phase 5 picker integration applied")
print("legacy_cfs_walt_default=yes")
print("eevdf_boot_toggle=sched_eevdf=1")
print("eevdf_picker_active_by_default=no")
