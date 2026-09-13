#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 90_apply_f2fs_inmem_curseg.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
paths = {name: root / "fs/f2fs" / name for name in (
    "f2fs.h", "segment.h", "segment.c", "checkpoint.c",
    "file.c", "gc.c", "super.c",
)}
for p in paths.values():
    if not p.is_file():
        raise SystemExit(f"missing {p}")

UPSTREAM = "d0b9e42ab6155dc05fc83f00af9f45d4dd02264d"

def replace_once(path, old, new, label):
    s = path.read_text()
    if new in s and old not in s:
        return
    if s.count(old) != 1:
        raise SystemExit(f"{label}: expected one legacy anchor, found {s.count(old)}")
    path.write_text(s.replace(old, new, 1))

# ---------------------------------------------------------------------------
# f2fs.h: split six persistent logs from one in-memory pinned-data log.
# Keep Samsung's readonly-log count.
# ---------------------------------------------------------------------------
p = paths["f2fs.h"]
replace_once(
    p,
"""#define\tNR_CURSEG_DATA_TYPE\t(3)
#define NR_CURSEG_NODE_TYPE\t(3)
#define NR_CURSEG_RO_TYPE\t(2)
#define NR_CURSEG_TYPE\t(NR_CURSEG_DATA_TYPE + NR_CURSEG_NODE_TYPE)

enum {
\tCURSEG_HOT_DATA\t= 0,\t/* directory entry blocks */
\tCURSEG_WARM_DATA,\t/* data blocks */
\tCURSEG_COLD_DATA,\t/* multimedia or GCed data blocks */
\tCURSEG_HOT_NODE,\t/* direct node blocks of directory files */
\tCURSEG_WARM_NODE,\t/* direct node blocks of normal files */
\tCURSEG_COLD_NODE,\t/* indirect node blocks */
\tNO_CHECK_TYPE,
\tCURSEG_COLD_DATA_PINNED,/* cold data for pinned file */
};
""",
"""#define\tNR_CURSEG_DATA_TYPE\t(3)
#define NR_CURSEG_NODE_TYPE\t(3)
#define NR_CURSEG_RO_TYPE\t(2)
#define NR_CURSEG_INMEM_TYPE\t(1)
#define NR_CURSEG_PERSIST_TYPE\t(NR_CURSEG_DATA_TYPE + NR_CURSEG_NODE_TYPE)
#define NR_CURSEG_TYPE\t\t(NR_CURSEG_INMEM_TYPE + NR_CURSEG_PERSIST_TYPE)

enum {
\tCURSEG_HOT_DATA\t= 0,\t/* directory entry blocks */
\tCURSEG_WARM_DATA,\t/* data blocks */
\tCURSEG_COLD_DATA,\t/* multimedia or GCed data blocks */
\tCURSEG_HOT_NODE,\t/* direct node blocks of directory files */
\tCURSEG_WARM_NODE,\t/* direct node blocks of normal files */
\tCURSEG_COLD_NODE,\t/* indirect node blocks */
\tNR_PERSISTENT_LOG,\t/* number of persistent logs */
\tCURSEG_COLD_DATA_PINNED = NR_PERSISTENT_LOG,
\t\t\t\t/* pinned file needing consecutive blocks */
\tNO_CHECK_TYPE,\t\t/* persistent + in-memory logs */
};
""",
    "f2fs.h curseg types",
)

replace_once(
    p,
"""int f2fs_npages_for_summary_flush(struct f2fs_sb_info *sbi, bool for_ra);
void allocate_segment_for_resize(struct f2fs_sb_info *sbi, int type,
\t\t\t\t\tunsigned int start, unsigned int end);
void f2fs_allocate_new_segments(struct f2fs_sb_info *sbi, int type);
""",
"""int f2fs_npages_for_summary_flush(struct f2fs_sb_info *sbi, bool for_ra);
void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi, int type);
void f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi, int type);
void allocate_segment_for_resize(struct f2fs_sb_info *sbi, int type,
\t\t\t\t\tunsigned int start, unsigned int end);
void f2fs_allocate_new_segment(struct f2fs_sb_info *sbi, int type);
void f2fs_allocate_new_segments(struct f2fs_sb_info *sbi);
""",
    "f2fs.h curseg prototypes",
)

# ---------------------------------------------------------------------------
# segment.h: real independent pinned curseg state.
# ---------------------------------------------------------------------------
p = paths["segment.h"]
replace_once(
    p,
"#define IS_NODESEG(t)\t((t) >= CURSEG_HOT_NODE)\n",
"#define IS_NODESEG(t)\t((t) >= CURSEG_HOT_NODE && (t) <= CURSEG_COLD_NODE)\n",
    "segment.h node predicate",
)

replace_once(
    p,
"""\t ((seg) == CURSEG_I(sbi, CURSEG_WARM_NODE)->segno) ||\t\\
\t ((seg) == CURSEG_I(sbi, CURSEG_COLD_NODE)->segno))
""",
"""\t ((seg) == CURSEG_I(sbi, CURSEG_WARM_NODE)->segno) ||\t\\
\t ((seg) == CURSEG_I(sbi, CURSEG_COLD_NODE)->segno) ||\t\\
\t ((seg) == CURSEG_I(sbi, CURSEG_COLD_DATA_PINNED)->segno))
""",
    "segment.h IS_CURSEG",
)

replace_once(
    p,
"""\t ((secno) == CURSEG_I(sbi, CURSEG_WARM_NODE)->segno /\t\t\\
\t  (sbi)->segs_per_sec) ||\t\\
\t ((secno) == CURSEG_I(sbi, CURSEG_COLD_NODE)->segno /\t\t\\
\t  (sbi)->segs_per_sec))\t\\
""",
"""\t ((secno) == CURSEG_I(sbi, CURSEG_WARM_NODE)->segno /\t\t\\
\t  (sbi)->segs_per_sec) ||\t\\
\t ((secno) == CURSEG_I(sbi, CURSEG_COLD_NODE)->segno /\t\t\\
\t  (sbi)->segs_per_sec) ||\t\\
\t ((secno) == CURSEG_I(sbi, CURSEG_COLD_DATA_PINNED)->segno /\t\\
\t  (sbi)->segs_per_sec))\n
""",
    "segment.h IS_CURSEC",
)

replace_once(
    p,
"""\tstruct f2fs_journal *journal;\t\t/* cached journal info */
\tunsigned char alloc_type;\t\t/* current allocation type */
\tunsigned int segno;\t\t\t/* current segment number */
\tunsigned short next_blkoff;\t\t/* next block offset to write */
\tunsigned int zone;\t\t\t/* current zone number */
\tunsigned int next_segno;\t\t/* preallocated segment */
};
""",
"""\tstruct f2fs_journal *journal;\t\t/* cached journal info */
\tunsigned char alloc_type;\t\t/* current allocation type */
\tunsigned short seg_type;\t\t/* underlying CURSEG_* data/node type */
\tunsigned int segno;\t\t\t/* current segment number */
\tunsigned short next_blkoff;\t\t/* next block offset to write */
\tunsigned int zone;\t\t\t/* current zone number */
\tunsigned int next_segno;\t\t/* preallocated segment */
\tbool inited;\t\t\t\t/* in-memory log owns a segment */
};
""",
    "segment.h curseg_info",
)

replace_once(
    p,
"""static inline struct curseg_info *CURSEG_I(struct f2fs_sb_info *sbi, int type)
{
\tif (type == CURSEG_COLD_DATA_PINNED)
\t\ttype = CURSEG_COLD_DATA;
\treturn (struct curseg_info *)(SM_I(sbi)->curseg_array + type);
}
""",
"""static inline struct curseg_info *CURSEG_I(struct f2fs_sb_info *sbi, int type)
{
\treturn (struct curseg_info *)(SM_I(sbi)->curseg_array + type);
}
""",
    "segment.h CURSEG_I",
)

replace_once(
    p,
"""static inline void __set_test_and_free(struct f2fs_sb_info *sbi,
\t\tunsigned int segno)
""",
"""static inline void __set_test_and_free(struct f2fs_sb_info *sbi,
\t\tunsigned int segno, bool inmem)
""",
    "segment.h free signature",
)
replace_once(
    p,
"""\t\tif (IS_CURSEC(sbi, secno))
\t\t\tgoto skip_free;
""",
"""\t\tif (!inmem && IS_CURSEC(sbi, secno))
\t\t\tgoto skip_free;
""",
    "segment.h free inmem behavior",
)

# ---------------------------------------------------------------------------
# checkpoint.c: in-memory curseg is not serialized as a seventh CP log.
# ---------------------------------------------------------------------------
p = paths["checkpoint.c"]
replace_once(
    p,
"""\tf2fs_flush_sit_entries(sbi, cpc);

\terr = do_checkpoint(sbi, cpc);
\tif (err)
\t\tf2fs_release_discard_addrs(sbi);
\telse
\t\tf2fs_clear_prefree_segments(sbi, cpc);
stop:
""",
"""\tf2fs_flush_sit_entries(sbi, cpc);

\t/* save in-memory pinned-log status around the persistent checkpoint */
\tf2fs_save_inmem_curseg(sbi, CURSEG_COLD_DATA_PINNED);

\terr = do_checkpoint(sbi, cpc);
\tif (err)
\t\tf2fs_release_discard_addrs(sbi);
\telse
\t\tf2fs_clear_prefree_segments(sbi, cpc);

\tf2fs_restore_inmem_curseg(sbi, CURSEG_COLD_DATA_PINNED);
stop:
""",
    "checkpoint save/restore inmem curseg",
)
replace_once(
    p,
"""\tsbi->max_orphans = (sbi->blocks_per_seg - F2FS_CP_PACKS -
\t\t\tNR_CURSEG_TYPE - __cp_payload(sbi)) *
""",
"""\tsbi->max_orphans = (sbi->blocks_per_seg - F2FS_CP_PACKS -
\t\t\tNR_CURSEG_PERSIST_TYPE - __cp_payload(sbi)) *
""",
    "checkpoint persistent curseg count",
)

# ---------------------------------------------------------------------------
# file.c: pinned allocation now explicitly opens its own in-memory curseg.
# ---------------------------------------------------------------------------
p = paths["file.c"]
replace_once(
    p,
"""\t\tdown_write(&sbi->pin_sem);
\t\tmap.m_seg_type = CURSEG_COLD_DATA_PINNED;

\t\tf2fs_lock_op(sbi);
\t\tf2fs_allocate_new_segments(sbi, CURSEG_COLD_DATA);
\t\tf2fs_unlock_op(sbi);

\t\terr = f2fs_map_blocks(inode, &map, 1, F2FS_GET_BLOCK_PRE_DIO);
\t\tup_write(&sbi->pin_sem);
""",
"""\t\tdown_write(&sbi->pin_sem);

\t\tf2fs_lock_op(sbi);
\t\tf2fs_allocate_new_segment(sbi, CURSEG_COLD_DATA_PINNED);
\t\tf2fs_unlock_op(sbi);

\t\tmap.m_seg_type = CURSEG_COLD_DATA_PINNED;
\t\terr = f2fs_map_blocks(inode, &map, 1, F2FS_GET_BLOCK_PRE_DIO);

\t\tup_write(&sbi->pin_sem);
""",
    "file.c pinned preallocation",
)

# ---------------------------------------------------------------------------
# gc.c: only persistent logs are relocated during resize.
# ---------------------------------------------------------------------------
p = paths["gc.c"]
replace_once(
    p,
"\tfor (type = CURSEG_HOT_DATA; type < NR_CURSEG_TYPE; type++)\n",
"\tfor (type = CURSEG_HOT_DATA; type < NR_CURSEG_PERSIST_TYPE; type++)\n",
    "gc.c resize curseg loop",
)

# ---------------------------------------------------------------------------
# segment.c core.
# ---------------------------------------------------------------------------
p = paths["segment.c"]
replace_once(
    p,
"\t\t__set_test_and_free(sbi, segno);\n",
"\t\t__set_test_and_free(sbi, segno, false);\n",
    "segment.c persistent free call",
)

replace_once(
    p,
"""static void reset_curseg(struct f2fs_sb_info *sbi, int type, int modified)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
\tstruct summary_footer *sum_footer;

\tcurseg->segno = curseg->next_segno;
\tcurseg->zone = GET_ZONE_FROM_SEG(sbi, curseg->segno);
\tcurseg->next_blkoff = 0;
\tcurseg->next_segno = NULL_SEGNO;

\tsum_footer = &(curseg->sum_blk->footer);
\tmemset(sum_footer, 0, sizeof(struct summary_footer));
\tif (IS_DATASEG(type))
\t\tSET_SUM_TYPE(sum_footer, SUM_TYPE_DATA);
\tif (IS_NODESEG(type))
\t\tSET_SUM_TYPE(sum_footer, SUM_TYPE_NODE);
\t__set_sit_entry_type(sbi, type, curseg->segno, modified);
}
""",
"""static void reset_curseg(struct f2fs_sb_info *sbi, int type, int modified)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
\tstruct summary_footer *sum_footer;

\tcurseg->inited = true;
\tcurseg->segno = curseg->next_segno;
\tcurseg->zone = GET_ZONE_FROM_SEG(sbi, curseg->segno);
\tcurseg->next_blkoff = 0;
\tcurseg->next_segno = NULL_SEGNO;

\tsum_footer = &(curseg->sum_blk->footer);
\tmemset(sum_footer, 0, sizeof(struct summary_footer));
\tif (IS_DATASEG(curseg->seg_type))
\t\tSET_SUM_TYPE(sum_footer, SUM_TYPE_DATA);
\tif (IS_NODESEG(curseg->seg_type))
\t\tSET_SUM_TYPE(sum_footer, SUM_TYPE_NODE);
\t__set_sit_entry_type(sbi, curseg->seg_type, curseg->segno, modified);
}
""",
    "segment.c reset_curseg",
)

replace_once(
    p,
"""static unsigned int __get_next_segno(struct f2fs_sb_info *sbi, int type)
{
\t/* if segs_per_sec is large than 1, we need to keep original policy. */
\tif (__is_large_section(sbi))
\t\treturn CURSEG_I(sbi, type)->segno;

\tif (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED)))
\t\treturn 0;

\tif (test_opt(sbi, NOHEAP) &&
\t\t(type == CURSEG_HOT_DATA || IS_NODESEG(type)))
\t\treturn 0;

\tif (SIT_I(sbi)->last_victim[ALLOC_NEXT])
\t\treturn SIT_I(sbi)->last_victim[ALLOC_NEXT];

\t/* find segments from 0 to reuse freed segments */
\tif (F2FS_OPTION(sbi).alloc_mode == ALLOC_MODE_REUSE)
\t\treturn 0;

\treturn CURSEG_I(sbi, type)->segno;
}
""",
"""static unsigned int __get_next_segno(struct f2fs_sb_info *sbi, int type)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);

\t/* if segs_per_sec is large than 1, we need to keep original policy. */
\tif (__is_large_section(sbi))
\t\treturn curseg->segno;

\t/* an in-memory log need not own a segment immediately after mount */
\tif (!curseg->inited)
\t\treturn 0;

\tif (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED)))
\t\treturn 0;

\tif (test_opt(sbi, NOHEAP) &&
\t\t(curseg->seg_type == CURSEG_HOT_DATA ||
\t\t IS_NODESEG(curseg->seg_type)))
\t\treturn 0;

\tif (SIT_I(sbi)->last_victim[ALLOC_NEXT])
\t\treturn SIT_I(sbi)->last_victim[ALLOC_NEXT];

\t/* find segments from 0 to reuse freed segments */
\tif (F2FS_OPTION(sbi).alloc_mode == ALLOC_MODE_REUSE)
\t\treturn 0;

\treturn curseg->segno;
}
""",
    "segment.c next segno",
)

replace_once(
    p,
"""static void new_curseg(struct f2fs_sb_info *sbi, int type, bool new_sec)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
\tunsigned int segno = curseg->segno;
\tint dir = ALLOC_LEFT;

\twrite_sum_page(sbi, curseg->sum_blk,
\t\t\t\tGET_SUM_BLOCK(sbi, segno));
\tif (type == CURSEG_WARM_DATA || type == CURSEG_COLD_DATA)
\t\tdir = ALLOC_RIGHT;
""",
"""static void new_curseg(struct f2fs_sb_info *sbi, int type, bool new_sec)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);
\tunsigned short seg_type = curseg->seg_type;
\tunsigned int segno = curseg->segno;
\tint dir = ALLOC_LEFT;

\tif (curseg->inited)
\t\twrite_sum_page(sbi, curseg->sum_blk,
\t\t\t\tGET_SUM_BLOCK(sbi, segno));
\tif (seg_type == CURSEG_WARM_DATA || seg_type == CURSEG_COLD_DATA)
\t\tdir = ALLOC_RIGHT;
""",
    "segment.c new_curseg",
)

# Insert save/restore helpers before get_ssr_segment.
s = p.read_text()
helper_anchor = "static int get_ssr_segment(struct f2fs_sb_info *sbi, int type)\n"
helper = """void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi, int type)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);

\tmutex_lock(&curseg->curseg_mutex);
\tif (!curseg->inited)
\t\tgoto out;

\tif (get_valid_blocks(sbi, curseg->segno, false)) {
\t\twrite_sum_page(sbi, curseg->sum_blk,
\t\t\t\tGET_SUM_BLOCK(sbi, curseg->segno));
\t} else {
\t\tmutex_lock(&DIRTY_I(sbi)->seglist_lock);
\t\t__set_test_and_free(sbi, curseg->segno, true);
\t\tmutex_unlock(&DIRTY_I(sbi)->seglist_lock);
\t}
out:
\tmutex_unlock(&curseg->curseg_mutex);
}

void f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi, int type)
{
\tstruct curseg_info *curseg = CURSEG_I(sbi, type);

\tmutex_lock(&curseg->curseg_mutex);
\tif (!curseg->inited)
\t\tgoto out;
\tif (get_valid_blocks(sbi, curseg->segno, false))
\t\tgoto out;

\tmutex_lock(&DIRTY_I(sbi)->seglist_lock);
\t__set_test_and_inuse(sbi, curseg->segno);
\tmutex_unlock(&DIRTY_I(sbi)->seglist_lock);
out:
\tmutex_unlock(&curseg->curseg_mutex);
}

"""
if helper not in s:
    if s.count(helper_anchor) != 1:
        raise SystemExit("segment.c save/restore insertion anchor missing")
    s = s.replace(helper_anchor, helper + helper_anchor, 1)
    p.write_text(s)

# Replace Samsung's type-filtered helper with the upstream split helpers.
replace_once(
    p,
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
\tlocate_dirty_segment(sbi, old_segno);
}

void f2fs_allocate_new_segment(struct f2fs_sb_info *sbi, int type)
{
\tdown_write(&SIT_I(sbi)->sentry_lock);
\t__allocate_new_segment(sbi, type);
\tup_write(&SIT_I(sbi)->sentry_lock);
}

void f2fs_allocate_new_segments(struct f2fs_sb_info *sbi)
{
\tint i;

\tdown_write(&SIT_I(sbi)->sentry_lock);
\tfor (i = CURSEG_HOT_DATA; i <= CURSEG_COLD_DATA; i++)
\t\t__allocate_new_segment(sbi, i);
\tup_write(&SIT_I(sbi)->sentry_lock);
}
""",
    "segment.c allocate-new helpers",
)

# Remove Samsung's pinned/cold alias lock workaround from allocation.
s = p.read_text()
old_alias = """\tbool put_pin_sem = false;

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

"""
if old_alias in s:
    s = s.replace(old_alias, "", 1)
else:
    if "bool put_pin_sem = false;" in s:
        raise SystemExit("segment.c partial pinned alias state")
old_tail = """\n\tif (put_pin_sem)
\t\tup_read(&sbi->pin_sem);
"""
if old_tail in s:
    s = s.replace(old_tail, "", 1)
elif "put_pin_sem" in s:
    raise SystemExit("segment.c leftover put_pin_sem")
p.write_text(s)

replace_once(
    p,
"sum_blk_addr(sbi, NR_CURSEG_TYPE, type)",
"sum_blk_addr(sbi, NR_CURSEG_PERSIST_TYPE, type)",
    "segment.c normal summary persistent count",
)
replace_once(
    p,
"""\tf2fs_ra_meta_pages(sbi, sum_blk_addr(sbi, NR_CURSEG_TYPE, type),
\t\t\t\t\tNR_CURSEG_TYPE - type, META_CP, true);
""",
"""\tf2fs_ra_meta_pages(sbi,
\t\t\t\tsum_blk_addr(sbi, NR_CURSEG_PERSIST_TYPE, type),
\t\t\t\tNR_CURSEG_PERSIST_TYPE - type, META_CP, true);
""",
    "segment.c summary readahead persistent count",
)

# build_curseg: initialize all 7 states, but only six are persistent log types.
replace_once(
    p,
"\tfor (i = 0; i < NR_CURSEG_TYPE; i++) {\n",
"\tfor (i = 0; i < NO_CHECK_TYPE; i++) {\n",
    "segment.c build curseg loop",
)
replace_once(
    p,
"""\t\tif (!array[i].journal)
\t\t\treturn -ENOMEM;
\t\tarray[i].segno = NULL_SEGNO;
\t\tarray[i].next_blkoff = 0;
""",
"""\t\tif (!array[i].journal)
\t\t\treturn -ENOMEM;
\t\tif (i < NR_PERSISTENT_LOG)
\t\t\tarray[i].seg_type = CURSEG_HOT_DATA + i;
\t\telse if (i == CURSEG_COLD_DATA_PINNED)
\t\t\tarray[i].seg_type = CURSEG_COLD_DATA;
\t\tarray[i].segno = NULL_SEGNO;
\t\tarray[i].next_blkoff = 0;
\t\tarray[i].inited = false;
""",
    "segment.c curseg initialization",
)

replace_once(
    p,
"\tfor (i = 0; i < NO_CHECK_TYPE; i++) {\n",
"\tfor (i = 0; i < NR_PERSISTENT_LOG; i++) {\n",
    "segment.c sanity persistent loop",
)

# ---------------------------------------------------------------------------
# super.c: mount/checkpoint ABI remains six persistent logs.
# ---------------------------------------------------------------------------
p = paths["super.c"]
replace_once(
    p,
"\t\t\tif (arg != 2 && arg != 4 && arg != NR_CURSEG_TYPE)\n",
"\t\t\tif (arg != 2 && arg != 4 && arg != NR_CURSEG_PERSIST_TYPE)\n",
    "super.c active_logs validation",
)
replace_once(
    p,
"""\t/* Not pass down write hints if the number of active logs is lesser
\t * than NR_CURSEG_TYPE.
\t */
\tif (F2FS_OPTION(sbi).active_logs != NR_CURSEG_TYPE)
""",
"""\t/* Not pass down write hints if active persistent logs are reduced. */
\tif (F2FS_OPTION(sbi).active_logs != NR_CURSEG_PERSIST_TYPE)
""",
    "super.c write hint log count",
)
replace_once(
    p,
"""\telse
\t\tF2FS_OPTION(sbi).active_logs = NR_CURSEG_TYPE;
""",
"""\telse
\t\tF2FS_OPTION(sbi).active_logs = NR_CURSEG_PERSIST_TYPE;
""",
    "super.c default active logs",
)
replace_once(
    p,
"""\tif (cp_pack_start_sum < cp_payload + 1 ||
\t\tcp_pack_start_sum > blocks_per_seg - 1 -
\t\t\tNR_CURSEG_TYPE) {
""",
"""\tif (cp_pack_start_sum < cp_payload + 1 ||
\t\tcp_pack_start_sum > blocks_per_seg - 1 -
\t\t\tNR_CURSEG_PERSIST_TYPE) {
""",
    "super.c checkpoint persistent count",
)

# ---------------------------------------------------------------------------
# Structural verification and regression guards.
# ---------------------------------------------------------------------------
checks = {
    "f2fs.h": [
        "#define NR_CURSEG_INMEM_TYPE\t(1)",
        "#define NR_CURSEG_PERSIST_TYPE",
        "CURSEG_COLD_DATA_PINNED = NR_PERSISTENT_LOG",
        "void f2fs_save_inmem_curseg",
        "void f2fs_allocate_new_segment",
    ],
    "segment.h": [
        "unsigned short seg_type;",
        "bool inited;",
        "CURSEG_COLD_DATA_PINNED)->segno",
        "unsigned int segno, bool inmem)",
    ],
    "segment.c": [
        "curseg->inited = true;",
        "void f2fs_save_inmem_curseg",
        "void f2fs_restore_inmem_curseg",
        "static void __allocate_new_segment",
        "f2fs_allocate_new_segment(struct f2fs_sb_info *sbi, int type)",
        "NR_CURSEG_PERSIST_TYPE - type",
        "array[i].seg_type = CURSEG_COLD_DATA;",
    ],
    "checkpoint.c": [
        "f2fs_save_inmem_curseg(sbi, CURSEG_COLD_DATA_PINNED);",
        "f2fs_restore_inmem_curseg(sbi, CURSEG_COLD_DATA_PINNED);",
        "NR_CURSEG_PERSIST_TYPE - __cp_payload(sbi)",
    ],
    "file.c": [
        "f2fs_allocate_new_segment(sbi, CURSEG_COLD_DATA_PINNED);",
        "map.m_seg_type = CURSEG_COLD_DATA_PINNED;",
    ],
    "gc.c": ["type < NR_CURSEG_PERSIST_TYPE"],
    "super.c": [
        "arg != NR_CURSEG_PERSIST_TYPE",
        "active_logs = NR_CURSEG_PERSIST_TYPE",
        "NR_CURSEG_PERSIST_TYPE) {",
    ],
}
for name, needles in checks.items():
    data = paths[name].read_text()
    for needle in needles:
        if needle not in data:
            raise SystemExit(f"{name}: missing Phase90 postcondition: {needle}")

all_text = "\n".join(p.read_text() for p in paths.values())
for forbidden in (
    "if (type == CURSEG_COLD_DATA_PINNED) {\n\t\ttype = CURSEG_COLD_DATA;",
    "bool put_pin_sem = false;",
    "f2fs_allocate_new_segments(sbi, CURSEG_COLD_DATA)",
):
    if forbidden in all_text:
        raise SystemExit(f"legacy pinned-curseg alias remains: {forbidden}")

# Phase90 must not enable ATGC yet.
for forbidden in ("struct atgc_management", "GC_IDLE_AT", "AT_SSR", "Opt_atgc"):
    if forbidden in all_text:
        raise SystemExit(f"Phase90 unexpectedly contains later ATGC symbol: {forbidden}")

report = root.parent.parent / "artifacts" / "phase90-f2fs-curseg.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=90-f2fs-curseg\n"
    "base=phase89\n"
    f"upstream_commit={UPSTREAM}\n"
    "feature=in-memory-pinned-curseg\n"
    "persistent_logs=6\n"
    "inmem_logs=1\n"
    "checkpoint_format=unchanged\n"
    "mount_active_logs_abi=2,4,6\n"
    "atgc=not-yet-enabled\n"
    "crash_diagnostics=preserved-by-workflow\n"
)
print(report.read_text(), end="")
