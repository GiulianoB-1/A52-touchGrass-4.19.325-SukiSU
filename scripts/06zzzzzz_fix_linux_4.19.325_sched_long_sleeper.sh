#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/sched-long-sleeper-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before scheduler repair"

python3 - "$KERNEL_DIR/kernel/sched/fair.c" "$REPORT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = Path(sys.argv[2])
text = path.read_text()
repairs = []

sig = "static inline bool entity_is_long_sleeper(struct sched_entity *se)\n"
call = "if (entity_is_long_sleeper(se))"
place_anchor = """static void
place_entity(struct cfs_rq *cfs_rq, struct sched_entity *se, int initial)
{
"""

stable_helper = """static inline bool entity_is_long_sleeper(struct sched_entity *se)
{
	struct cfs_rq *cfs_rq;
	u64 sleep_time;

	if (se->exec_start == 0)
		return false;

	cfs_rq = cfs_rq_of(se);
	sleep_time = rq_clock_task(rq_of(cfs_rq));

	/* Happen while migrating because of clock task divergence */
	if (sleep_time <= se->exec_start)
		return false;

	sleep_time -= se->exec_start;
	if (sleep_time > ((1ULL << 63) / scale_load_down(NICE_0_LOAD)))
		return true;

	return false;
}

"""

if call in text and sig not in text:
    if text.count(place_anchor) != 1:
        raise SystemExit(
            f"place_entity anchor mismatch: {text.count(place_anchor)}"
        )
    text = text.replace(place_anchor, stable_helper + place_anchor, 1)
    path.write_text(text)
    repairs.append("kernel/sched/fair.c=restored-stable-long-sleeper-helper")
elif call in text and text.count(sig) != 1:
    raise SystemExit(
        f"entity_is_long_sleeper definition count is invalid: {text.count(sig)}"
    )
elif call not in text:
    raise SystemExit("4.19.325 long-sleeper placement guard is missing")

final = path.read_text()
if final.count(sig) != 1:
    raise SystemExit("entity_is_long_sleeper helper postcondition failed")
if final.count(call) != 1:
    raise SystemExit("entity_is_long_sleeper call postcondition failed")
if final.index(sig) > final.index(place_anchor):
    raise SystemExit("entity_is_long_sleeper helper must precede place_entity")

# Preserve the custom EEVDF gate: the stable guard belongs to the legacy CFS
# branch and must not replace eevdf_place_entity().
place_start = final.index(place_anchor)
place_end = final.index("\nstatic void check_enqueue_throttle", place_start)
place = final[place_start:place_end]
for required in (
    "if (unlikely(sched_eevdf_enabled))",
    "eevdf_place_entity(cfs_rq, se, initial);",
    "if (entity_is_long_sleeper(se))",
):
    if required not in place:
        raise SystemExit(f"scheduler placement postcondition missing: {required}")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- kernel/sched/fair.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'legacy_cfs=4.19.325-long-sleeper-vruntime-sanitization\n'
  printf 'eevdf=custom-placement-preserved\n'
  printf 'result=linux-4.19.325-scheduler-long-sleeper-helper-repaired\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION scheduler long-sleeper compatibility repaired"
