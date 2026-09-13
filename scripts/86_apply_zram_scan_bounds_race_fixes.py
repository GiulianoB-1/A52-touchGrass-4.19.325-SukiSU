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

def get_chunk(text, start_marker, end_marker):
    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"missing function start: {start_marker}")
    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"missing function end marker after {start_marker}: {end_marker}")
    return start, end, text[start:end]

def replace_once_in_chunk(text, start_marker, end_marker, old, new, label):
    start, end, chunk = get_chunk(text, start_marker, end_marker)
    if old in chunk:
        if chunk.count(old) != 1:
            raise SystemExit(f"{label}: expected one old anchor, found {chunk.count(old)}")
        chunk = chunk.replace(old, new, 1)
        changes.append(label)
    elif new in chunk:
        changes.append(label + "_already")
    else:
        raise SystemExit(f"{label}: target anchor not found in target function")
    return text[:start] + chunk + text[end:]

# ---------------------------------------------------------------------------
# Upstream 2026 fix 894913e2d35c:
# writeback_store() must calculate nr_pages only while init_lock is held.
# The old Samsung code sampled disksize before the lock, allowing reset+
# reinitialization to a smaller table to leave a stale scan upper bound.
# ---------------------------------------------------------------------------
s = replace_once_in_chunk(
    s,
    "static ssize_t writeback_store",
    "\nstruct zram_work {",
    "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n\tunsigned long index;\n",
    "\tunsigned long nr_pages;\n\tunsigned long index;\n",
    "writeback_decl",
)

s = replace_once_in_chunk(
    s,
    "static ssize_t writeback_store",
    "\nstruct zram_work {",
    "\tif (!init_done(zram)) {\n"
    "\t\tret = -EINVAL;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}\n\n"
    "\tif (!zram->backing_dev) {\n",
    "\tif (!init_done(zram)) {\n"
    "\t\tret = -EINVAL;\n"
    "\t\tgoto release_init_lock;\n"
    "\t}\n\n"
    "\tnr_pages = zram->disksize >> PAGE_SHIFT;\n\n"
    "\tif (!zram->backing_dev) {\n",
    "writeback_bound_under_lock",
)

# ---------------------------------------------------------------------------
# Companion upstream 2026 fix 391f057f44a5:
# read_block_state() had the same stale disksize/table bound race.
# ---------------------------------------------------------------------------
s = replace_once_in_chunk(
    s,
    "static ssize_t read_block_state",
    "\nstatic const struct file_operations proc_zram_block_state_op",
    "\tstruct zram *zram = file->private_data;\n"
    "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n"
    "\tstruct timespec64 ts;\n",
    "\tstruct zram *zram = file->private_data;\n"
    "\tunsigned long nr_pages;\n"
    "\tstruct timespec64 ts;\n",
    "block_state_decl",
)

s = replace_once_in_chunk(
    s,
    "static ssize_t read_block_state",
    "\nstatic const struct file_operations proc_zram_block_state_op",
    "\tif (!init_done(zram)) {\n"
    "\t\tup_read(&zram->init_lock);\n"
    "\t\tkvfree(kbuf);\n"
    "\t\treturn -EINVAL;\n"
    "\t}\n\n"
    "\tfor (index = *ppos; index < nr_pages; index++) {\n",
    "\tif (!init_done(zram)) {\n"
    "\t\tup_read(&zram->init_lock);\n"
    "\t\tkvfree(kbuf);\n"
    "\t\treturn -EINVAL;\n"
    "\t}\n\n"
    "\tnr_pages = zram->disksize >> PAGE_SHIFT;\n\n"
    "\tfor (index = *ppos; index < nr_pages; index++) {\n",
    "block_state_bound_under_lock",
)

# Verify the exact target functions after all edits.
_, _, wb = get_chunk(s, "static ssize_t writeback_store", "\nstruct zram_work {")
if "\tunsigned long nr_pages;\n" not in wb:
    raise SystemExit("writeback_store: deferred nr_pages declaration missing")
if "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n" in wb:
    raise SystemExit("writeback_store: stale pre-lock nr_pages initializer remains")
wb_lock = wb.find("down_read(&zram->init_lock);")
wb_bound = wb.find("nr_pages = zram->disksize >> PAGE_SHIFT;")
wb_backing = wb.find("if (!zram->backing_dev)")
if min(wb_lock, wb_bound, wb_backing) < 0 or not (wb_lock < wb_bound < wb_backing):
    raise SystemExit("writeback_store: nr_pages is not calculated under init_lock before scan setup")

_, _, rb = get_chunk(
    s,
    "static ssize_t read_block_state",
    "\nstatic const struct file_operations proc_zram_block_state_op",
)
if "\tunsigned long nr_pages;\n" not in rb:
    raise SystemExit("read_block_state: deferred nr_pages declaration missing")
if "\tunsigned long nr_pages = zram->disksize >> PAGE_SHIFT;\n" in rb:
    raise SystemExit("read_block_state: stale pre-lock nr_pages initializer remains")
rb_lock = rb.find("down_read(&zram->init_lock);")
rb_bound = rb.find("nr_pages = zram->disksize >> PAGE_SHIFT;")
rb_loop = rb.find("for (index = *ppos; index < nr_pages; index++)")
if min(rb_lock, rb_bound, rb_loop) < 0 or not (rb_lock < rb_bound < rb_loop):
    raise SystemExit("read_block_state: nr_pages is not calculated under init_lock before scan")

p.write_text(s)

report = root.parent.parent / "artifacts" / "phase86-zram-scan-bounds.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=86-zram-scan-bounds-race-fixes\n"
    "base=phase84\n"
    "upstream_writeback_fix=894913e2d35c46ff19a77530907771ae57862b96\n"
    "upstream_block_state_fix=391f057f44a51cc9418da5cba78b014324174264\n"
    "scope=function-local-only\n"
    "zcomp_stream_model=samsung-original-get_cpu_ptr\n"
    "zram_slot_lock=wait-on-bit-sleepable\n"
    "changes=" + ",".join(changes) + "\n"
)
print(report.read_text(), end="")
