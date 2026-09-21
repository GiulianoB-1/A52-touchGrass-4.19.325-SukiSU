#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 117_migrate_mglru_trace_to_proc.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
vmscan = root / "mm/vmscan.c"
if not vmscan.is_file():
    raise SystemExit(f"missing required file: {vmscan}")

s = vmscan.read_text()

required = (
    "/* A52 MGLRU power recorder P2 */",
    "static const struct file_operations mglru_rec_trace_fops",
    'debugfs_create_file("lru_gen_trace", 0444, NULL, NULL, &mglru_rec_trace_fops);',
)
for needle in required:
    if needle not in s:
        raise SystemExit(f"inherited MGLRU recorder marker missing: {needle}")

marker = "/* A52 MGLRU recorder procfs export */"
already_marked = marker in s

if "#include <linux/proc_fs.h>" not in s:
    anchors = (
        "#include <linux/debugfs.h>\n",
        "#include <linux/seq_file.h>\n",
        "#include <linux/mm.h>\n",
    )
    for anchor in anchors:
        pos = s.find(anchor)
        if pos >= 0:
            pos += len(anchor)
            s = s[:pos] + "#include <linux/proc_fs.h>\n" + s[pos:]
            break
    else:
        raise SystemExit("could not find safe include anchor for linux/proc_fs.h")

old_open = """static int mglru_rec_trace_open(struct inode *inode, struct file *file)
{
\treturn single_open(file, mglru_rec_trace_show, inode->i_private);
}
"""
new_open = """static int mglru_rec_trace_open(struct inode *inode, struct file *file)
{
\treturn single_open(file, mglru_rec_trace_show, NULL);
}
"""
if old_open in s:
    s = s.replace(old_open, new_open, 1)
elif new_open not in s:
    raise SystemExit("MGLRU trace open function shape not recognized")

dbg = 'debugfs_create_file("lru_gen_trace", 0444, NULL, NULL, &mglru_rec_trace_fops);'
proc = 'proc_create("lru_gen_trace", 0444, NULL, &mglru_rec_trace_fops);'
if proc not in s:
    if s.count(dbg) != 1:
        raise SystemExit(f"expected exactly one MGLRU debugfs trace registration, found {s.count(dbg)}")
    s = s.replace(
        dbg,
        dbg + "\n\t/* A52 MGLRU recorder procfs export */\n\t" + proc,
        1,
    )
elif marker not in s:
    # Repair a partially migrated tree: procfs registration is already present
    # but an earlier interrupted phase did not leave the audit marker.
    if s.count(proc) != 1:
        raise SystemExit(f"expected exactly one existing procfs trace registration, found {s.count(proc)}")
    s = s.replace(
        proc,
        marker + "\n\t" + proc,
        1,
    )

for needle in (
    marker,
    "#include <linux/proc_fs.h>",
    proc,
    "return single_open(file, mglru_rec_trace_show, NULL);",
):
    if needle not in s:
        raise SystemExit(f"MGLRU procfs migration audit missing: {needle}")

vmscan.write_text(s)

report_dir = root.parent.parent / "artifacts"
report_dir.mkdir(parents=True, exist_ok=True)
(report_dir / "mglru-procfs-migration.txt").write_text(
    "source=inherited-mglru-recorder-p2\n"
    "debugfs_config_dependency=removed-for-user-interface\n"
    "sysfs_record=/sys/kernel/mm/lru_gen/record\n"
    "sysfs_stats=/sys/kernel/mm/lru_gen/stats\n"
    "proc_trace=/proc/lru_gen_trace\n"
)

if already_marked:
    print("MGLRU procfs export already complete; audited idempotently")
else:
    print("MGLRU inherited recorder procfs export applied/repaired")
print("trace=/proc/lru_gen_trace")
