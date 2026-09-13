#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 84_apply_zram_sleepable_slotlock_only.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
p = root / "drivers/block/zram/zram_drv.c"
if not p.is_file():
    raise SystemExit(f"missing {p}")

s = p.read_text()

if "#include <linux/wait_bit.h>" not in s:
    anchor = "#include <linux/bitops.h>\n"
    if anchor not in s:
        raise SystemExit("bitops include anchor missing")
    s = s.replace(anchor, anchor + "#include <linux/wait_bit.h>\n", 1)

old = """static int zram_slot_trylock(struct zram *zram, u32 index)
{
	return bit_spin_trylock(ZRAM_LOCK, &zram->table[index].flags);
}

static void zram_slot_lock(struct zram *zram, u32 index)
{
	bit_spin_lock(ZRAM_LOCK, &zram->table[index].flags);
}

static void zram_slot_unlock(struct zram *zram, u32 index)
{
	bit_spin_unlock(ZRAM_LOCK, &zram->table[index].flags);
}
"""

new = """/*
 * Phase84 isolation test: convert only the per-slot lock primitive to a
 * wait-on-bit lock.  Samsung's zcomp get_cpu_ptr()/put_cpu_ptr() stream
 * ownership remains unchanged in this phase.
 */
static int zram_slot_trylock(struct zram *zram, u32 index)
{
	return !test_and_set_bit_lock(ZRAM_LOCK, &zram->table[index].flags);
}

static void zram_slot_lock(struct zram *zram, u32 index)
{
	wait_on_bit_lock(&zram->table[index].flags, ZRAM_LOCK,
			 TASK_UNINTERRUPTIBLE);
}

static void zram_slot_unlock(struct zram *zram, u32 index)
{
	clear_and_wake_up_bit(ZRAM_LOCK, &zram->table[index].flags);
}
"""

if old in s:
    s = s.replace(old, new, 1)
elif (
    "wait_on_bit_lock(&zram->table[index].flags" in s
    and "clear_and_wake_up_bit(ZRAM_LOCK" in s
    and "test_and_set_bit_lock(ZRAM_LOCK" in s
):
    pass
else:
    raise SystemExit("Samsung zram slot-lock block changed unexpectedly")

p.write_text(s)

out = p.read_text()
checks = [
    ("wait-on-bit lock missing", "wait_on_bit_lock(&zram->table[index].flags" in out),
    ("wake-up unlock missing", "clear_and_wake_up_bit(ZRAM_LOCK" in out),
    ("atomic trylock missing", "test_and_set_bit_lock(ZRAM_LOCK" in out),
    ("legacy bit spinlock remains", "bit_spin_lock(ZRAM_LOCK" not in out),
    ("legacy bit spin unlock remains", "bit_spin_unlock(ZRAM_LOCK" not in out),
]
for label, ok in checks:
    if not ok:
        raise SystemExit(label)

report = root.parent.parent / "artifacts" / "phase84-zram-slotlock-only.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=84-zram-sleepable-slotlock-only\n"
    "base=phase83\n"
    "zram_slot_lock=wait_on_bit_lock\n"
    "zram_slot_trylock=test_and_set_bit_lock\n"
    "zram_slot_unlock=clear_and_wake_up_bit\n"
    "zcomp_stream_model=unchanged-samsung-get_cpu_ptr\n"
    "zsmalloc_phase83_fixes=retained\n"
)
print(report.read_text(), end="")
