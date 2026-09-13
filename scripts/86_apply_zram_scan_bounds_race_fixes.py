#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 86_apply_zram_scan_bounds_race_fixes.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
p = root / "drivers/block/zram/zram_drv.c"
if not p.is_file():
    raise SystemExit(f"missing {p}")

s = p.read_text()
changes = []

# 2026 upstream fix: writeback_store() must derive nr_pages only after
# init_lock is held, otherwise reset+reinit to a smaller disksize can leave
# a stale upper bound and cause out-of-bounds table access.
old = """	unsigned long nr_pages = zram->disksize >> PAGE_SHIFT;
	unsigned long index;
"""
new = """	unsigned long nr_pages;
	unsigned long index;
"""
if old in s:
    s = s.replace(old, new, 1)
    changes.append("writeback_decl")
elif new in s:
    changes.append("writeback_decl_already")
else:
    raise SystemExit("writeback nr_pages declaration anchor changed")

anchor = """	if (!init_done(zram)) {
		ret = -EINVAL;
		goto release_init_lock;
	}

	if (!zram->backing_dev) {
"""
repl = """	if (!init_done(zram)) {
		ret = -EINVAL;
		goto release_init_lock;
	}

	nr_pages = zram->disksize >> PAGE_SHIFT;

	if (!zram->backing_dev) {
"""
if anchor in s:
    s = s.replace(anchor, repl, 1)
    changes.append("writeback_bound_under_lock")
elif repl in s:
    changes.append("writeback_bound_already")
else:
    raise SystemExit("writeback init_lock anchor changed")

# Companion fix: read_block_state() has the same stale-bound race.
old = """	struct zram *zram = file->private_data;
	unsigned long nr_pages = zram->disksize >> PAGE_SHIFT;
	struct timespec64 ts;
"""
new = """	struct zram *zram = file->private_data;
	unsigned long nr_pages;
	struct timespec64 ts;
"""
if old in s:
    s = s.replace(old, new, 1)
    changes.append("block_state_decl")
elif new in s:
    changes.append("block_state_decl_already")
else:
    raise SystemExit("read_block_state nr_pages declaration anchor changed")

anchor = """	if (!init_done(zram)) {
		up_read(&zram->init_lock);
		kvfree(kbuf);
		return -EINVAL;
	}

	for (index = *ppos; index < nr_pages; index++) {
"""
repl = """	if (!init_done(zram)) {
		up_read(&zram->init_lock);
		kvfree(kbuf);
		return -EINVAL;
	}

	nr_pages = zram->disksize >> PAGE_SHIFT;

	for (index = *ppos; index < nr_pages; index++) {
"""
if anchor in s:
    s = s.replace(anchor, repl, 1)
    changes.append("block_state_bound_under_lock")
elif repl in s:
    changes.append("block_state_bound_already")
else:
    raise SystemExit("read_block_state init_lock anchor changed")

p.write_text(s)

out = p.read_text()
if "unsigned long nr_pages = zram->disksize >> PAGE_SHIFT;" in out:
    # Other functions may legitimately use this pattern, so constrain checks.
    pass

# Function-local structural verification.
wb_start = out.find("static ssize_t writeback_store")
wb_end = out.find("\n}", wb_start)
wb = out[wb_start:wb_end+2]
if "unsigned long nr_pages;" not in wb:
    raise SystemExit("writeback nr_pages is not deferred")
if wb.find("down_read(&zram->init_lock);") > wb.find("nr_pages = zram->disksize >> PAGE_SHIFT;"):
    raise SystemExit("writeback nr_pages still calculated before init_lock")

rb_start = out.find("static ssize_t read_block_state")
rb_end = out.find("\n}", rb_start)
rb = out[rb_start:rb_end+2]
if "unsigned long nr_pages;" not in rb:
    raise SystemExit("read_block_state nr_pages is not deferred")
if rb.find("down_read(&zram->init_lock);") > rb.find("nr_pages = zram->disksize >> PAGE_SHIFT;"):
    raise SystemExit("read_block_state nr_pages still calculated before init_lock")

report = root.parent.parent / "artifacts" / "phase86-zram-scan-bounds.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=86-zram-scan-bounds-race-fixes\n"
    "base=phase84\n"
    "upstream_writeback_fix=894913e2d35c46ff19a77530907771ae57862b96\n"
    "upstream_block_state_fix=391f057f44a51cc9418da5cba78b014324174264\n"
    "zcomp_stream_model=samsung-original-get_cpu_ptr\n"
    "zram_slot_lock=wait-on-bit-sleepable\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
