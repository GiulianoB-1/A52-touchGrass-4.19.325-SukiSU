#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq")
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-inmem-p3a")
out.mkdir(parents=True, exist_ok=True)
kernel = kernel.resolve()
out = out.resolve()

UPSTREAM = "d0b9e42ab6155dc05fc83f00af9f45d4dd02264d"
patchfile = out / f"{UPSTREAM}.patch"
logfile = out / f"{UPSTREAM}.apply.log"

subprocess.run([
    "curl", "-LfsS",
    f"https://github.com/torvalds/linux/commit/{UPSTREAM}.patch",
    "-o", str(patchfile)
], check=True)

# Strictly apply only exact upstream hunks. Samsung-specific rejects are
# resolved below. No fuzz is allowed.
with patchfile.open("rb") as src, logfile.open("wb") as log:
    rc = subprocess.run(
        ["patch", "-p1", "--forward", "--batch", "--fuzz=0"],
        cwd=kernel, stdin=src, stdout=log, stderr=subprocess.STDOUT
    ).returncode

print(logfile.read_text(errors="replace"))
print(f"upstream_patch_rc={rc}")

def path(name):
    return kernel / "fs" / "f2fs" / name

def replace_one(name, old, new):
    p = path(name)
    s = p.read_text()
    if old not in s:
        raise RuntimeError(f"missing anchor in {name}: {old[:100]!r}")
    p.write_text(s.replace(old, new, 1))

# Samsung carries NR_CURSEG_RO_TYPE; retain it while introducing upstream's
# persistent/in-memory curseg split.
replace_one("f2fs.h",
"""#define\tNR_CURSEG_DATA_TYPE\t(3)
#define NR_CURSEG_NODE_TYPE\t(3)
#define NR_CURSEG_RO_TYPE\t(2)
#define NR_CURSEG_TYPE\t(NR_CURSEG_DATA_TYPE + NR_CURSEG_NODE_TYPE)
""",
"""#define\tNR_CURSEG_DATA_TYPE\t(3)
#define NR_CURSEG_NODE_TYPE\t(3)
#define NR_CURSEG_RO_TYPE\t(2)
#define NR_CURSEG_INMEM_TYPE\t(1)
#define NR_CURSEG_PERSIST_TYPE\t(NR_CURSEG_DATA_TYPE + NR_CURSEG_NODE_TYPE)
#define NR_CURSEG_TYPE\t\t(NR_CURSEG_INMEM_TYPE + NR_CURSEG_PERSIST_TYPE)
""")

replace_one("f2fs.h",
"""int f2fs_npages_for_summary_flush(struct f2fs_sb_info *sbi, bool for_ra);
void allocate_segment_for_resize(struct f2fs_sb_info *sbi, int type,
""",
"""int f2fs_npages_for_summary_flush(struct f2fs_sb_info *sbi, bool for_ra);
void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi, int type);
void f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi, int type);
void allocate_segment_for_resize(struct f2fs_sb_info *sbi, int type,
""")

# Samsung's debug layout moved, so add the new pinned curseg explicitly.
replace_one("debug.c",
"""\t\tseq_printf(s, "  - Indir nodes: %d, %d, %d\\n",
\t\t\t   si->curseg[CURSEG_COLD_NODE],
\t\t\t   si->cursec[CURSEG_COLD_NODE],
\t\t\t   si->curzone[CURSEG_COLD_NODE]);
""",
"""\t\tseq_printf(s, "  - Indir nodes: %d, %d, %d\\n",
\t\t\t   si->curseg[CURSEG_COLD_NODE],
\t\t\t   si->cursec[CURSEG_COLD_NODE],
\t\t\t   si->curzone[CURSEG_COLD_NODE]);
\t\tseq_printf(s, "  - Pinned file: %d, %d, %d\\n",
\t\t\t   si->curseg[CURSEG_COLD_DATA_PINNED],
\t\t\t   si->cursec[CURSEG_COLD_DATA_PINNED],
\t\t\t   si->curzone[CURSEG_COLD_DATA_PINNED]);
""")

# Pinned files now allocate from their own real in-memory curseg.
replace_one("file.c",
"""\t\tdown_write(&sbi->pin_sem);
\t\tmap.m_seg_type = CURSEG_COLD_DATA_PINNED;

\t\tf2fs_lock_op(sbi);
\t\tf2fs_allocate_new_segments(sbi, CURSEG_COLD_DATA);
\t\tf2fs_unlock_op(sbi);

\t\terr = f2fs_map_blocks(inode, &map, 1, F2FS_GET_BLOCK_PRE_DIO);
""",
"""\t\tdown_write(&sbi->pin_sem);

\t\tf2fs_lock_op(sbi);
\t\tf2fs_allocate_new_segments(sbi, CURSEG_COLD_DATA_PINNED);
\t\tf2fs_unlock_op(sbi);

\t\tmap.m_seg_type = CURSEG_COLD_DATA_PINNED;
\t\terr = f2fs_map_blocks(inode, &map, 1, F2FS_GET_BLOCK_PRE_DIO);
""")

# Resize operates on persistent checkpoint logs only.
replace_one("gc.c",
"""\tfor (type = CURSEG_HOT_DATA; type < NR_CURSEG_TYPE; type++)
\t\tallocate_segment_for_resize(sbi, type, start, end);
""",
"""\tfor (type = CURSEG_HOT_DATA; type < NR_CURSEG_PERSIST_TYPE; type++)
\t\tallocate_segment_for_resize(sbi, type, start, end);
""")

# Samsung folded upstream's single-segment and multi-segment helpers into one
# function. Restore the helper semantics while keeping Samsung's public API.
replace_one("segment.c",
"""void f2fs_allocate_new_segments(struct f2fs_sb_info *sbi, int type)
{
\tstruct curseg_info *curseg;
\tunsigned int old_segno;
\tint i;

\tdown_write(&SIT_I(sbi)->sentry_lock);

\tfor (i = CURSEG_HOT_DATA; i <= CURSEG_COLD_DATA; i++) {
\t\tif (type != NO_CHECK_TYPE && i != type)
\t\t\tcontinue;

\t\tcurseg = CURSEG_I(sbi, i);
\t\tif (type == NO_CHECK_TYPE || curseg->next_blkoff ||
\t\t\t\tget_valid_blocks(sbi, curseg->segno, false) ||
\t\t\t\tget_ckpt_valid_blocks(sbi, curseg->segno)) {
\t\t\told_segno = curseg->segno;
\t\t\tSIT_I(sbi)->s_ops->allocate_segment(sbi, i, true);
\t\t\tlocate_dirty_segment(sbi, old_segno);
\t\t}
\t}

\tup_write(&SIT_I(sbi)->sentry_lock);
}
""",
"""static void __allocate_new_segment(struct f2fs_sb_info *sbi, int type)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
\tunsigned int old_segno;

\tif (!curseg->inited)
\t\tgoto alloc;

\tif (!curseg->next_blkoff &&
\t\t!get_valid_blocks(sbi, curseg->segno, false) &&
\t\t!get_ckpt_valid_blocks(sbi, curseg->segno))
\t\treturn;

alloc:
\told_segno = curseg->segno;
\tSIT_I(sbi)->s_ops->allocate_segment(sbi, type, true);
\tif (old_segno != NULL_SEGNO)
\t\tlocate_dirty_segment(sbi, old_segno);
}

void f2fs_allocate_new_segments(struct f2fs_sb_info *sbi, int type)
{
\tint i;

\tdown_write(&SIT_I(sbi)->sentry_lock);

\tif (type == NO_CHECK_TYPE) {
\t\tfor (i = CURSEG_HOT_DATA; i <= CURSEG_COLD_DATA; i++)
\t\t\t__allocate_new_segment(sbi, i);
\t} else {
\t\t__allocate_new_segment(sbi, type);
\t}

\tup_write(&SIT_I(sbi)->sentry_lock);
}
""")

# The old Samsung pin_sem read-side workaround existed because pinned writes
# shared CURSEG_COLD_DATA with GC. A real pinned curseg removes that alias.
replace_one("segment.c",
"""\tstruct sit_info *sit_i = SIT_I(sbi);
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
\tbool put_pin_sem = false;

\tif (type == CURSEG_COLD_DATA) {
\t\t/* GC during CURSEG_COLD_DATA_PINNED allocation */
\t\tif (down_read_trylock(&sbi->pin_sem)) {
\t\t\tput_pin_sem = true;
\t\t} else {
\t\t\ttype = CURSEG_WARM_DATA;
\t\t\tcurseg = CURSEG_I(sbi, type);
\t\t}
\t} else if (type == CURSEG_COLD_DATA_PINNED) {
\t\ttype = CURSEG_COLD_DATA;
\t}
""",
"""\tstruct sit_info *sit_i = SIT_I(sbi);
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
""")

# Preserve Samsung's node_write serialization for the underlying data segment
# even when the curseg index itself is an in-memory type.
replace_one("segment.c",
"""\tif (IS_DATASEG(type))
\t\tdown_write(&sbi->node_write);
""",
"""\tif (IS_DATASEG(curseg->seg_type))
\t\tdown_write(&sbi->node_write);
""")

replace_one("segment.c",
"""\tif (IS_DATASEG(type))
\t\tup_write(&sbi->node_write);

\tif (put_pin_sem)
\t\tup_read(&sbi->pin_sem);
""",
"""\tif (IS_DATASEG(curseg->seg_type))
\t\tup_write(&sbi->node_write);
""")

# 011e0868: after NR_CURSEG_TYPE becomes 7, write hints must still compare
# against the six persistent logs.
replace_one("super.c",
"""\tif (F2FS_OPTION(sbi).active_logs != NR_CURSEG_TYPE)
\t\tF2FS_OPTION(sbi).whint_mode = WHINT_MODE_OFF;
""",
"""\tif (F2FS_OPTION(sbi).active_logs != NR_CURSEG_PERSIST_TYPE)
\t\tF2FS_OPTION(sbi).whint_mode = WHINT_MODE_OFF;
""")

# Samsung has a read-only active-log mode; keep it and only change RW default.
replace_one("super.c",
"""\telse
\t\tF2FS_OPTION(sbi).active_logs = NR_CURSEG_TYPE;
""",
"""\telse
\t\tF2FS_OPTION(sbi).active_logs = NR_CURSEG_PERSIST_TYPE;
""")

# 632faca: an in-memory pinned curseg is legitimately unallocated after mount.
# Avoid converting NULL_SEGNO through section/zone arithmetic.
p = path("segment.h")
s = p.read_text()
s = s.replace(
    "((segno) / (sbi)->segs_per_sec)",
    "(((segno) == -1) ? -1: (segno) / (sbi)->segs_per_sec)",
    1,
)
s = s.replace(
    "((secno) / (sbi)->secs_per_zone)",
    "(((secno) == -1) ? -1: (secno) / (sbi)->secs_per_zone)",
    1,
)
p.write_text(s)

# Remove patch bookkeeping after all Samsung-specific rejects were handled.
for pat in ("*.rej", "*.orig"):
    for p in (kernel / "fs" / "f2fs").glob(pat):
        p.unlink()

# Structural assertions.
checks = {
    "fs/f2fs/f2fs.h": [
        "#define NR_CURSEG_INMEM_TYPE\t(1)",
        "#define NR_CURSEG_PERSIST_TYPE",
        "CURSEG_COLD_DATA_PINNED = NR_PERSISTENT_LOG",
        "void f2fs_save_inmem_curseg",
    ],
    "fs/f2fs/segment.h": [
        "unsigned short seg_type;",
        "bool inited;",
    ],
    "fs/f2fs/segment.c": [
        "void f2fs_save_inmem_curseg",
        "void f2fs_restore_inmem_curseg",
        "__allocate_new_segment",
        "IS_DATASEG(curseg->seg_type)",
    ],
    "fs/f2fs/checkpoint.c": [
        "f2fs_save_inmem_curseg(sbi, CURSEG_COLD_DATA_PINNED);",
        "NR_CURSEG_PERSIST_TYPE - __cp_payload(sbi)",
    ],
    "fs/f2fs/super.c": [
        "active_logs != NR_CURSEG_PERSIST_TYPE",
        "active_logs = NR_CURSEG_PERSIST_TYPE",
    ],
}
for rel, needles in checks.items():
    data = (kernel / rel).read_text()
    for needle in needles:
        if needle not in data:
            raise RuntimeError(f"sanity check failed: {rel}: {needle}")

subprocess.run(["git", "diff", "--check"], cwd=kernel, check=True)
(out / "report.txt").write_text(
    "F2FS P3A inmem curseg\n"
    f"upstream={UPSTREAM}\n"
    "followups=632faca72938,011e0868e0cf\n"
    "ATGC=not included\n"
)
subprocess.run(
    ["git", "diff", "--", "fs/f2fs"],
    cwd=kernel,
    stdout=(out / "inmem-curseg-p3a.diff").open("wb"),
    check=True,
)
print("P3A inmem curseg adaptation complete")
