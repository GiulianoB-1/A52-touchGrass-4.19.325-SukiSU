#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/ext4-cpuhp-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying ext4 and CPU hotplug compatibility repairs for Linux $TARGET_VERSION"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: {count}")
    return text.replace(old, new, 1)


# ext4_check_dir_entry() gained the logical block argument before the byte
# offset. The first directory block is logical block zero in both checks.
path = root / "fs/ext4/namei.c"
text = path.read_text()
old = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, 0) ||\n"
)
new = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, 0, 0) ||\n"
)
if old in text:
    text = replace_once(text, old, new, "ext4 dot entry check")
    repairs.append("fs/ext4/namei.c=added-lblk-to-dot-entry-check")

old = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, offset) ||\n"
)
new = (
    "\t\tif (ext4_check_dir_entry(inode, NULL, de, bh, bh->b_data,\n"
    "\t\t\t\t\t bh->b_size, 0, offset) ||\n"
)
if old in text:
    text = replace_once(text, old, new, "ext4 dotdot entry check")
    repairs.append("fs/ext4/namei.c=added-lblk-to-dotdot-entry-check")

# ext4_find_entry() now returns the logical block containing the entry. Preserve
# it in the existing ext4_renament field so later delete/update paths use the
# same block that was re-read.
old = (
    "\told.bh = ext4_find_entry(old.dir, &old.dentry->d_name, &old.de,\n"
    "\t\t\t\t &old.inlined);\n"
)
new = (
    "\told.bh = ext4_find_entry(old.dir, &old.dentry->d_name, &old.de,\n"
    "\t\t\t\t &old.inlined, &old.lblk);\n"
)
if old in text:
    text = replace_once(text, old, new, "ext4 reset entry lookup")
    repairs.append("fs/ext4/namei.c=recorded-reset-entry-lblk")
path.write_text(text)

# The late CPU hotplug table references random and hrtimer callbacks directly.
# Restore the matching public headers that were dropped by the vendor merge.
path = root / "kernel/cpu.c"
text = path.read_text()
anchor = "#include <linux/cpuset.h>\n"
includes = "#include <linux/random.h>\n#include <linux/hrtimer.h>\n"
if "#include <linux/random.h>" not in text or "#include <linux/hrtimer.h>" not in text:
    if anchor not in text:
        raise SystemExit("kernel/cpu.c include anchor is missing")
    additions = ""
    if "#include <linux/random.h>" not in text:
        additions += "#include <linux/random.h>\n"
    if "#include <linux/hrtimer.h>" not in text:
        additions += "#include <linux/hrtimer.h>\n"
    text = replace_once(text, anchor, anchor + additions, "CPU hotplug includes")
    repairs.append("kernel/cpu.c=restored-random-and-hrtimer-includes")
path.write_text(text)

# Keep the Samsung dead-CPU and per-CPU tick interfaces, while declaring the
# upstream AP-dying callback used by the Linux 4.19.325 CPU hotplug state table.
path = root / "include/linux/hrtimer.h"
text = path.read_text()
if "int hrtimers_cpu_dying(unsigned int cpu);" not in text:
    anchor = "#ifdef CONFIG_HOTPLUG_CPU\n"
    text = replace_once(
        text,
        anchor,
        anchor + "int hrtimers_cpu_dying(unsigned int cpu);\n",
        "hrtimer CPU-dying declaration",
    )
    repairs.append("include/linux/hrtimer.h=declared-hrtimers-cpu-dying")
path.write_text(text)

# Linux 4.19.325 migrates hrtimers from the dying CPU in the AP hotplug stage.
# Recreate that implementation using the merged timer-list helper, while
# retaining Samsung's save_pcpu_tick() behavior before the scheduler tick is
# cancelled. The older hrtimers_dead_cpu() path remains available for vendor
# callers but is not used by the updated state table.
path = root / "kernel/time/hrtimer.c"
text = path.read_text()
if "int hrtimers_cpu_dying(unsigned int dying_cpu)" not in text:
    anchor = "int hrtimers_dead_cpu(unsigned int scpu)\n"
    if anchor not in text:
        raise SystemExit("hrtimers_dead_cpu anchor is missing")
    block = (
        "int hrtimers_cpu_dying(unsigned int dying_cpu)\n"
        "{\n"
        "\tstruct hrtimer_cpu_base *old_base, *new_base;\n"
        "\tint i, ncpu = cpumask_first(cpu_active_mask);\n"
        "\n"
        "\tsave_pcpu_tick(dying_cpu);\n"
        "\ttick_cancel_sched_timer(dying_cpu);\n"
        "\n"
        "\told_base = this_cpu_ptr(&hrtimer_bases);\n"
        "\tnew_base = &per_cpu(hrtimer_bases, ncpu);\n"
        "\n"
        "\t/* CPU hotplug is globally serialized; taking both locks is safe. */\n"
        "\traw_spin_lock(&old_base->lock);\n"
        "\traw_spin_lock_nested(&new_base->lock, SINGLE_DEPTH_NESTING);\n"
        "\n"
        "\tfor (i = 0; i < HRTIMER_MAX_CLOCK_BASES; i++)\n"
        "\t\tmigrate_hrtimer_list(&old_base->clock_base[i],\n"
        "\t\t\t\t     &new_base->clock_base[i], true);\n"
        "\n"
        "\t__hrtimer_get_next_event(new_base, HRTIMER_ACTIVE_SOFT);\n"
        "\tsmp_call_function_single(ncpu, retrigger_next_event, NULL, 0);\n"
        "\n"
        "\traw_spin_unlock(&new_base->lock);\n"
        "\told_base->online = 0;\n"
        "\traw_spin_unlock(&old_base->lock);\n"
        "\n"
        "\treturn 0;\n"
        "}\n"
        "\n"
    )
    text = replace_once(text, anchor, block + anchor, "hrtimer CPU-dying implementation")
    repairs.append("kernel/time/hrtimer.c=restored-upstream-cpu-dying-migration")
path.write_text(text)

# Exact postconditions.
ext4 = (root / "fs/ext4/namei.c").read_text()
if "bh->b_size, 0) ||" in ext4 or "bh->b_size, offset) ||" in ext4:
    raise SystemExit("old ext4_check_dir_entry signatures remain")
reset_start = ext4.index("static void ext4_resetent")
reset_end = ext4.index("\nstatic int ext4_find_delete_entry", reset_start)
if "&old.inlined, &old.lblk" not in ext4[reset_start:reset_end]:
    raise SystemExit("ext4 reset lookup did not retain lblk")

cpu = (root / "kernel/cpu.c").read_text()
for include in ("#include <linux/random.h>", "#include <linux/hrtimer.h>"):
    if cpu.count(include) != 1:
        raise SystemExit(f"CPU hotplug include validation failed: {include}")

header = (root / "include/linux/hrtimer.h").read_text()
if header.count("int hrtimers_cpu_dying(unsigned int cpu);") != 1:
    raise SystemExit("hrtimers_cpu_dying declaration validation failed")

hrtimer = (root / "kernel/time/hrtimer.c").read_text()
if hrtimer.count("int hrtimers_cpu_dying(unsigned int dying_cpu)") != 1:
    raise SystemExit("hrtimers_cpu_dying implementation validation failed")
if "save_pcpu_tick(dying_cpu);" not in hrtimer:
    raise SystemExit("Samsung per-CPU tick preservation is missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "ext4 and CPU hotplug compatibility repairs applied"
