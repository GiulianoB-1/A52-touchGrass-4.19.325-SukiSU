#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys, re

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-gc-cp-p6").resolve()
out.mkdir(parents=True, exist_ok=True)
root = kernel / "fs" / "f2fs"

def rd(f): return (root/f).read_text()
def wr(f,s): (root/f).write_text(s)
def rep(f,old,new,count=1):
    s=rd(f); n=s.count(old)
    if n < count:
        raise RuntimeError(f"{f}: missing/changed anchor ({n}): {old[:160]!r}")
    wr(f,s.replace(old,new,count))
def sub1(f,pat,repl,flags=0,label="regex"):
    s=rd(f); s,n=re.subn(pat,repl,s,count=1,flags=flags)
    if n != 1:
        raise RuntimeError(f"{f}: {label} expected 1 match, got {n}: {pat[:160]!r}")
    wr(f,s)

# ---------------------------------------------------------------------------
# P6 - mature Samsung checkpoint daemon + later GC/segment correctness.
#
# Samsung already has checkpoint_cmd_control / f2fs_issue_checkpoint(), which
# is functionally its checkpoint-merge implementation. Do NOT add upstream's
# separate cprc_info daemon. Instead, adapt the mature upstream fixes to the
# vendor ccc_info implementation and keep Samsung's timing/ioprio behavior.
#
# Upstream semantics represented:
#   d50dfc0c7df7 - don't enter superblock freeze write domain in ckpt thread;
#                  freeze must reject queued checkpoint requests
#   e9a844f6e487 - ioctl-triggered GC victim must enter normal victim handling
#   d26fecb03e1f - clear stale next_victim_seg when a section becomes free
#   c836d3b8d94e - don't balance while checkpoint is disabled
#   1005a3ca28e9 - pre-balance map_blocks create path in LFS mode
#
# Plus read-only vendor observability for checkpoint merge runtime validation.
# ---------------------------------------------------------------------------

# ---- checkpoint.c: upstream freeze-domain fix -----------------------------
s=rd("checkpoint.c")
old=r'''	sb_start_intwrite(sbi->sb);

	if (!llist_empty(&ccc->issue_list)) {
'''
new=r'''	/*
	 * P6/d50dfc0c7df7: checkpoint merge thread must not enter the VFS
	 * freeze write domain itself. f2fs_freeze() explicitly rejects a
	 * non-empty checkpoint queue instead.
	 */
	if (!llist_empty(&ccc->issue_list)) {
'''
if old not in s:
    raise RuntimeError("checkpoint.c: checkpoint-thread sb_start_intwrite anchor changed")
s=s.replace(old,new,1)
old2=r'''	sb_end_intwrite(sbi->sb);

	wait_event_interruptible(*q,
'''
if old2 not in s:
    raise RuntimeError("checkpoint.c: checkpoint-thread sb_end_intwrite anchor changed")
s=s.replace(old2,r'''	wait_event_interruptible(*q,
''',1)
wr("checkpoint.c",s)

# ---- super.c: freeze rejects queued Samsung checkpoint requests -----------
s=rd("super.c")
freeze_anchor=r'''	/* must be clean, since sync_filesystem() was already called */
	if (is_sbi_flag_set(F2FS_SB(sb), SBI_IS_DIRTY))
		return -EINVAL;
	return 0;
'''
freeze_new=r'''	/* must be clean, since sync_filesystem() was already called */
	if (is_sbi_flag_set(F2FS_SB(sb), SBI_IS_DIRTY))
		return -EINVAL;

	/*
	 * P6/d50dfc0c7df7: don't freeze while Samsung's checkpoint merge
	 * queue still contains work.
	 */
	if (F2FS_SB(sb)->ccc_info &&
	    !llist_empty(&F2FS_SB(sb)->ccc_info->issue_list))
		return -EINVAL;

	return 0;
'''
if freeze_anchor not in s:
    raise RuntimeError("super.c: f2fs_freeze clean anchor changed")
s=s.replace(freeze_anchor,freeze_new,1)
wr("super.c",s)

# ---- gc.c: ioctl-specified victim follows normal got_result path ----------
s=rd("gc.c")
old=r'''	if (*result != NULL_SEGNO) {
		if (get_valid_blocks(sbi, *result, false) &&
			!sec_usage_check(sbi, GET_SEC_FROM_SEG(sbi, *result)))
			p.min_segno = *result;
		goto out;
	}
'''
new=r'''	if (*result != NULL_SEGNO) {
		secno = GET_SEC_FROM_SEG(sbi, *result);
		if (!get_valid_blocks(sbi, *result, false))
			goto out;
		if (sec_usage_check(sbi, secno))
			goto out;

		/*
		 * P6/e9a844f6e487: explicit/ioctl GC victims need the same
		 * victim bookkeeping as automatically selected victims.
		 */
		if (gc_type == FG_GC)
			clear_bit(secno, dirty_i->victim_secmap);
		p.min_segno = *result;
		goto got_result;
	}
'''
if old not in s:
    raise RuntimeError("gc.c: explicit victim block changed")
s=s.replace(old,new,1)
wr("gc.c",s)

# ---- segment.h: clear stale cached next victim when section becomes free --
s=rd("segment.h")
old=r'''		if (next >= start_segno + sbi->segs_per_sec) {
			if (test_and_clear_bit(secno, free_i->free_secmap))
				free_i->free_sections++;
		}
'''
new=r'''		if (next >= start_segno + sbi->segs_per_sec) {
			if (test_and_clear_bit(secno, free_i->free_secmap)) {
				free_i->free_sections++;

				/*
				 * P6/d26fecb03e1f: cached next-victim hints must
				 * not keep pointing at a section that is free now.
				 */
				if (GET_SEC_FROM_SEG(sbi,
				    sbi->next_victim_seg[BG_GC]) == secno)
					sbi->next_victim_seg[BG_GC] = NULL_SEGNO;
				if (GET_SEC_FROM_SEG(sbi,
				    sbi->next_victim_seg[FG_GC]) == secno)
					sbi->next_victim_seg[FG_GC] = NULL_SEGNO;
			}
		}
'''
if old not in s:
    raise RuntimeError("segment.h: section-free anchor changed")
s=s.replace(old,new,1)
wr("segment.h",s)

# ---- segment.c: don't run balance path while checkpoint is disabled -------
s=rd("segment.c")
old="\tif (!f2fs_is_checkpoint_ready(sbi))\n\t\treturn;\n"
new=r'''	/*
	 * P6/c836d3b8d94e: checkpoint-disabled mode has its own space
	 * accounting/recovery path; don't enter normal balance/checkpoint GC.
	 */
	if (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED)))
		return;
'''
if old not in s:
    raise RuntimeError("segment.c: f2fs_balance_fs checkpoint-ready anchor changed")
s=s.replace(old,new,1)
wr("segment.c",s)

# ---- data.c: LFS create mapping pre-balances before taking map lock -------
s=rd("data.c")
old=r'''next_dnode:
	if (map->m_may_create)
		__do_map_lock(sbi, flag, true);
'''
new=r'''next_dnode:
	if (map->m_may_create) {
		/*
		 * P6/1005a3ca28e9: in LFS mode, make free-space progress
		 * before entering the map lock / allocation path.
		 */
		if (f2fs_lfs_mode(sbi))
			f2fs_balance_fs(sbi, true);
		__do_map_lock(sbi, flag, true);
	}
'''
if old not in s:
    raise RuntimeError("data.c: next_dnode map-lock anchor changed")
s=s.replace(old,new,1)
wr("data.c",s)

# ---- sysfs.c: read-only Samsung checkpoint-merge observability ------------
s=rd("sysfs.c")
if "ckpt_merge_active_show" not in s:
    macro=r'''#define F2FS_GENERAL_RO_ATTR(name) \
static struct f2fs_attr f2fs_attr_##name = __ATTR(name, 0444, name##_show, NULL)
'''
    if macro not in s:
        raise RuntimeError("sysfs.c: F2FS_GENERAL_RO_ATTR macro changed")
    funcs=r'''
static ssize_t ckpt_merge_active_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	struct f2fs_ckpt_cmd_control *ccc = sbi->ccc_info;

	return sprintf(buf, "%u\n", !!(ccc && ccc->ckpt_task));
}

static ssize_t ckpt_merge_issued_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	struct f2fs_ckpt_cmd_control *ccc = sbi->ccc_info;

	return sprintf(buf, "%d\n", ccc ? atomic_read(&ccc->issued_ckpt) : 0);
}

static ssize_t ckpt_merge_inflight_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	struct f2fs_ckpt_cmd_control *ccc = sbi->ccc_info;

	return sprintf(buf, "%d\n", ccc ? atomic_read(&ccc->issing_ckpt) : 0);
}

static ssize_t ckpt_merge_accumulated_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	struct f2fs_ckpt_cmd_control *ccc = sbi->ccc_info;

	return sprintf(buf, "%d\n", ccc ? atomic_read(&ccc->accum_ckpt) : 0);
}

static ssize_t ckpt_merge_queue_pending_show(struct f2fs_attr *a,
		struct f2fs_sb_info *sbi, char *buf)
{
	struct f2fs_ckpt_cmd_control *ccc = sbi->ccc_info;

	return sprintf(buf, "%u\n",
		!!(ccc && !llist_empty(&ccc->issue_list)));
}

'''
    s=s.replace(macro,funcs+macro,1)

if "F2FS_GENERAL_RO_ATTR(ckpt_merge_active);" not in s:
    anchor="F2FS_GENERAL_RO_ATTR(age_extent_cache_enabled);\n"
    if anchor not in s:
        raise RuntimeError("sysfs.c: P5B RO attr declaration anchor changed")
    s=s.replace(anchor,anchor+
        "F2FS_GENERAL_RO_ATTR(ckpt_merge_active);\n"
        "F2FS_GENERAL_RO_ATTR(ckpt_merge_issued);\n"
        "F2FS_GENERAL_RO_ATTR(ckpt_merge_inflight);\n"
        "F2FS_GENERAL_RO_ATTR(ckpt_merge_accumulated);\n"
        "F2FS_GENERAL_RO_ATTR(ckpt_merge_queue_pending);\n",1)

if "ATTR_LIST(ckpt_merge_active)," not in s:
    anchor="\tATTR_LIST(age_extent_cache_enabled),\n"
    if anchor not in s:
        raise RuntimeError("sysfs.c: P5B attr-list anchor changed")
    s=s.replace(anchor,anchor+
        "\tATTR_LIST(ckpt_merge_active),\n"
        "\tATTR_LIST(ckpt_merge_issued),\n"
        "\tATTR_LIST(ckpt_merge_inflight),\n"
        "\tATTR_LIST(ckpt_merge_accumulated),\n"
        "\tATTR_LIST(ckpt_merge_queue_pending),\n",1)
wr("sysfs.c",s)

# ---- structural validation ------------------------------------------------
checks={
    "checkpoint.c":[
        "P6/d50dfc0c7df7",
        "wait_event_interruptible(*q",
    ],
    "super.c":[
        "checkpoint merge",
        "!llist_empty(&F2FS_SB(sb)->ccc_info->issue_list)",
    ],
    "gc.c":[
        "P6/e9a844f6e487",
        "goto got_result;",
    ],
    "segment.h":[
        "P6/d26fecb03e1f",
        "sbi->next_victim_seg[BG_GC] = NULL_SEGNO;",
        "sbi->next_victim_seg[FG_GC] = NULL_SEGNO;",
    ],
    "segment.c":[
        "P6/c836d3b8d94e",
        "is_sbi_flag_set(sbi, SBI_CP_DISABLED)",
    ],
    "data.c":[
        "P6/1005a3ca28e9",
        "if (f2fs_lfs_mode(sbi))",
        "f2fs_balance_fs(sbi, true);",
    ],
    "sysfs.c":[
        "ckpt_merge_active_show",
        "ckpt_merge_issued_show",
        "ckpt_merge_inflight_show",
        "ckpt_merge_accumulated_show",
        "ckpt_merge_queue_pending_show",
    ],
}
for rel,needles in checks.items():
    data=rd(rel)
    for needle in needles:
        if needle not in data:
            raise RuntimeError(f"{rel}: missing P6 token: {needle}")

# Ensure the checkpoint issue thread itself no longer uses freeze-domain
# accounting. Samsung's flush thread may still contain these calls; only scope
# this invariant to issue_checkpoint_thread().
ck=rd("checkpoint.c")
a=ck.find("static int issue_checkpoint_thread")
b=ck.find("static inline bool nice_cpu_sched",a)
if a < 0 or b < 0:
    raise RuntimeError("checkpoint.c: issue_checkpoint_thread bounds changed")
thread=ck[a:b]
if "sb_start_intwrite" in thread or "sb_end_intwrite" in thread:
    raise RuntimeError("P6 invariant violated: checkpoint thread still enters freeze domain")

subprocess.run(["git","diff","--check"],cwd=kernel,check=True)

report="""F2FS P6 GC/segment/checkpoint modernization
base=P5 fully runtime-validated
checkpoint_vendor_path=Samsung checkpoint_cmd_control preserved
checkpoint_freeze_fix=d50dfc0c7df7 adapted
ioctl_gc_victim_fix=e9a844f6e487 adapted
stale_next_victim_fix=d26fecb03e1f adapted
cp_disabled_balance_fix=c836d3b8d94e
lfs_map_blocks_balance_fix=1005a3ca28e9
observability=ckpt_merge_active,issued,inflight,accumulated,queue_pending
"""
(out/"report.txt").write_text(report)
with (out/"p6.diff").open("wb") as f:
    subprocess.run(["git","diff","--","fs/f2fs"],cwd=kernel,stdout=f,check=True)

print(report)
print("P6 GC/segment/checkpoint modernization applied")
