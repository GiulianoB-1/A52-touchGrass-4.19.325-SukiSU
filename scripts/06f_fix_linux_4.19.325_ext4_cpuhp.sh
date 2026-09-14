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
# offset. ext4_get_first_dir_block() always reads logical block zero, so both
# dot and dotdot checks must explicitly pass lblk=0. Use function-scoped
# replacements because the Samsung/upstream merge changes indentation.
path = root / "fs/ext4/namei.c"
text = path.read_text()

fn_start = text.index("static struct buffer_head *ext4_get_first_dir_block")
fn_end = text.index("\nstruct ext4_renament", fn_start)
prefix, body, suffix = text[:fn_start], text[fn_start:fn_end], text[fn_end:]

old = "bh->b_size, 0) ||"
new = "bh->b_size, 0, 0) ||"
if old in body:
    if body.count(old) != 1:
        raise SystemExit(f"ext4 dot entry check anchor mismatch: {body.count(old)}")
    body = body.replace(old, new, 1)
    repairs.append("fs/ext4/namei.c=added-lblk-to-dot-entry-check")
elif new not in body:
    raise SystemExit("ext4 dot entry check is neither old nor repaired")

old = "bh->b_size, offset) ||"
new = "bh->b_size, 0, offset) ||"
if old in body:
    if body.count(old) != 1:
        raise SystemExit(f"ext4 dotdot entry check anchor mismatch: {body.count(old)}")
    body = body.replace(old, new, 1)
    repairs.append("fs/ext4/namei.c=added-lblk-to-dotdot-entry-check")
elif new not in body:
    raise SystemExit("ext4 dotdot entry check is neither old nor repaired")

text = prefix + body + suffix

# Samsung's ext4_find_entry() has a fifth lblk output argument. The upstream
# ext4_resetent() rereads the entry and must refresh BOTH old.inlined and
# old.lblk before ext4_setent() chooses the inline/non-inline update path.
fn_start = text.index("static void ext4_resetent")
fn_end = text.index("\nstatic int ext4_find_delete_entry", fn_start)
prefix, body, suffix = text[:fn_start], text[fn_start:fn_end], text[fn_end:]

merged_form = "&old.de, NULL, &old.lblk);"
fixed_form = "&old.de, &old.inlined, &old.lblk);"
if merged_form in body:
    if body.count(merged_form) != 1:
        raise SystemExit(
            f"ext4 reset lookup merged-form mismatch: {body.count(merged_form)}"
        )
    body = body.replace(merged_form, fixed_form, 1)
    repairs.append("fs/ext4/namei.c=refreshed-reset-entry-inline-state-and-lblk")
elif fixed_form not in body:
    # Handle the pure-upstream four-argument form if a future merge resolves
    # differently but still needs Samsung's lblk output.
    upstream_form = "&old.de,\n\t\t\t\t &old.inlined);"
    samsung_form = "&old.de,\n\t\t\t\t &old.inlined, &old.lblk);"
    if upstream_form in body:
        body = body.replace(upstream_form, samsung_form, 1)
        repairs.append("fs/ext4/namei.c=recorded-reset-entry-lblk")
    else:
        raise SystemExit("ext4 reset lookup form is unrecognized")

text = prefix + body + suffix
path.write_text(text)

# The late CPU hotplug table references random and hrtimer callbacks directly.
# Restore the matching public headers that were dropped by the vendor merge.
path = root / "kernel/cpu.c"
text = path.read_text()
anchor = "#include <trace/events/power.h>\n"
if "#include <linux/random.h>" not in text or "#include <linux/hrtimer.h>" not in text:
    if text.count(anchor) != 1:
        raise SystemExit(
            f"kernel/cpu.c trace include anchor mismatch: {text.count(anchor)}"
        )
    additions = ""
    if "#include <linux/random.h>" not in text:
        additions += "#include <linux/random.h>\n"
    if "#include <linux/hrtimer.h>" not in text:
        additions += "#include <linux/hrtimer.h>\n"
    text = text.replace(anchor, additions + "\n" + anchor, 1)
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
first_start = ext4.index("static struct buffer_head *ext4_get_first_dir_block")
first_end = ext4.index("\nstruct ext4_renament", first_start)
first_body = ext4[first_start:first_end]
if "bh->b_size, 0) ||" in first_body or "bh->b_size, offset) ||" in first_body:
    raise SystemExit("old ext4_check_dir_entry signatures remain")
if first_body.count("bh->b_size, 0, 0) ||") != 1:
    raise SystemExit("ext4 dot entry lblk repair validation failed")
if first_body.count("bh->b_size, 0, offset) ||") != 1:
    raise SystemExit("ext4 dotdot entry lblk repair validation failed")

reset_start = ext4.index("static void ext4_resetent")
reset_end = ext4.index("\nstatic int ext4_find_delete_entry", reset_start)
reset_body = ext4[reset_start:reset_end]
if "&old.de, &old.inlined, &old.lblk);" not in reset_body:
    raise SystemExit("ext4 reset lookup did not refresh inlined state and lblk")

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
