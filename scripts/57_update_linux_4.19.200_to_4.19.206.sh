#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.206
GENERATED_MERGE="$(dirname "$0")/.generated-merge-linux-$TARGET_VERSION.sh"
GENERATED_FIX="$(dirname "$0")/.generated-fix-linux-$TARGET_VERSION.sh"

cleanup() {
  rm -f "$GENERATED_MERGE" "$GENERATED_FIX"
}
trap cleanup EXIT

[[ "$(kernel_version)" == "4.19.200" ]] || fail "Expected Linux 4.19.200 before stable update"

info "Deriving the reviewed Linux 4.19.200 -> $TARGET_VERSION merge"
cp "$(dirname "$0")/checkpoint_merge_linux_4.19.210.sh" "$GENERATED_MERGE"
sed -i \
  -e 's/TO_TAG=v4.19.210/TO_TAG=v4.19.206/' \
  -e '/^TARGET_COMMIT=/d' \
  -e '/test "$to_sha" = "$TARGET_COMMIT"/d' \
  "$GENERATED_MERGE"
chmod +x "$GENERATED_MERGE"
"$GENERATED_MERGE"
[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Linux stable update did not reach $TARGET_VERSION"

info "Applying only conditional merge-shape repairs needed by $TARGET_VERSION"
cp "$(dirname "$0")/checkpoint_fix_linux_4.19.210_compile.sh" "$GENERATED_FIX"
sed -i 's/TARGET_VERSION=4.19.210/TARGET_VERSION=4.19.206/' "$GENERATED_FIX"
chmod +x "$GENERATED_FIX"
"$GENERATED_FIX"

python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/linux-4.19.206-extra-repairs.txt" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
rows = []

# Match the Qualcomm event timer initializer to the timerqueue API selected by
# the merged Linux 4.19.206 headers.
header = (root / "include/linux/timerqueue.h").read_text()
path = root / "drivers/soc/qcom/event_timer.c"
text = path.read_text()
newer = """static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.rb_root = RB_ROOT_CACHED,
};
"""
older = """static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.head = RB_ROOT,
\t.next = NULL,
};
"""
wants_newer = "struct rb_root_cached rb_root;" in header
desired = newer if wants_newer else older
obsolete = older if wants_newer else newer
if desired not in text:
    if obsolete not in text:
        raise SystemExit("event_timer initializer shape is unrecognized")
    path.write_text(text.replace(obsolete, desired, 1))
    rows.append("event_timer_initializer=repaired\n")
else:
    rows.append("event_timer_initializer=already-correct\n")

# Some 4.19.206 merge shapes contain the cleanup call but not the small helper.
path = root / "kernel/sched/cpufreq_schedutil.c"
text = path.read_text()
call = "sugov_clear_global_tunables();"
definition = "static void sugov_clear_global_tunables(void)"
anchor = "static void sugov_exit("
if call in text and definition not in text:
    if anchor not in text:
        raise SystemExit("schedutil exit anchor missing")
    helper = """static void sugov_clear_global_tunables(void)
{
\tif (!have_governor_per_policy())
\t\tglobal_tunables = NULL;
}

"""
    path.write_text(text.replace(anchor, helper + anchor, 1))
    rows.append("schedutil_cleanup_helper=repaired\n")
elif call not in text:
    raise SystemExit("schedutil cleanup call missing after generic repair")
else:
    rows.append("schedutil_cleanup_helper=already-present\n")

report.write_text("".join(rows))
PY

git -C "$KERNEL_DIR" diff --check
[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Post-repair tree no longer reports Linux $TARGET_VERSION"
cat "$ARTIFACTS_DIR/linux-4.19.206-extra-repairs.txt"
info "Linux stable update completed: 4.19.200 -> $TARGET_VERSION"
