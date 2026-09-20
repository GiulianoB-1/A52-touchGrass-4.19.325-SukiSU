#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys, re

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-correctness-p7").resolve()
out.mkdir(parents=True, exist_ok=True)
root = kernel / "fs" / "f2fs"

def rd(f): return (root/f).read_text()
def wr(f,s): (root/f).write_text(s)
def rep(f,old,new,count=1):
    s=rd(f)
    n=s.count(old)
    if n < count:
        raise RuntimeError(f"{f}: missing/changed anchor ({n}): {old[:180]!r}")
    wr(f,s.replace(old,new,count))

# ---------------------------------------------------------------------------
# P7 - 2025/2026 correctness/data-integrity fixes, page-based 4.19 adaptation.
#
# 238e14eb7226 - serialize NAT flag inspection with node_write
# 019f9dda7f66 + c3e238bd1f56 - final fsync/dentry mark handling,
#                              preventing FGGC from preserving stale marks
# ce2739e482bc + 2d9c4a4ed4ee - final write_end_io ordering to avoid UAF
# 05872a167c2c - harden total_valid_block_count inconsistency
# 1773f63d108b - detect invalid NAT block addresses, including cached entries
#
# Deliberately excluded:
# 0f9af07ecc1a - requires newer f2fs_allocate_data_block() error-return API.
# 5eced87b7d19 - large post-EOF zeroing series depends on much newer filemap/
#                folio/invalidate architecture; should be isolated later.
# ---------------------------------------------------------------------------

# ---- node.c: NAT flag synchronization (238e14eb7226) ----------------------
s=rd("node.c")
old=r'''bool f2fs_need_inode_block_update(struct f2fs_sb_info *sbi, nid_t ino)
{
	struct f2fs_nm_info *nm_i = NM_I(sbi);
	struct nat_entry *e;
	bool need_update = true;

	down_read(&nm_i->nat_tree_lock);
	e = __lookup_nat_cache(nm_i, ino);
	if (e && get_nat_flag(e, HAS_LAST_FSYNC) &&
			(get_nat_flag(e, IS_CHECKPOINTED) ||
			 get_nat_flag(e, HAS_FSYNCED_INODE)))
		need_update = false;
	up_read(&nm_i->nat_tree_lock);
	return need_update;
}
'''
new=r'''bool f2fs_need_inode_block_update(struct f2fs_sb_info *sbi, nid_t ino)
{
	struct f2fs_nm_info *nm_i = NM_I(sbi);
	struct nat_entry *e;
	bool need_update = true;

	/*
	 * P7/238e14eb7226: NAT fsync/checkpoint flags are coupled to node
	 * write serialization. Reading them under nat_tree_lock alone can
	 * observe an inconsistent combination and skip a required inode write.
	 */
	down_read(&sbi->node_write);
	down_read(&nm_i->nat_tree_lock);
	e = __lookup_nat_cache(nm_i, ino);
	if (e && get_nat_flag(e, HAS_LAST_FSYNC) &&
			(get_nat_flag(e, IS_CHECKPOINTED) ||
			 get_nat_flag(e, HAS_FSYNCED_INODE)))
		need_update = false;
	up_read(&nm_i->nat_tree_lock);
	up_read(&sbi->node_write);
	return need_update;
}
'''
if old not in s:
    raise RuntimeError("node.c: f2fs_need_inode_block_update anchor changed")
s=s.replace(old,new,1)

# ---- node.c: NAT corruption detection (1773f63d108b adaptation) -----------
old=r'''	/* Check nat cache */
	down_read(&nm_i->nat_tree_lock);
	e = __lookup_nat_cache(nm_i, nid);
	if (e) {
		ni->ino = nat_get_ino(e);
		ni->blk_addr = nat_get_blkaddr(e);
		ni->version = nat_get_version(e);
		up_read(&nm_i->nat_tree_lock);
		return 0;
	}
'''
new=r'''	/* Check nat cache */
	down_read(&nm_i->nat_tree_lock);
	e = __lookup_nat_cache(nm_i, nid);
	if (e) {
		ni->ino = nat_get_ino(e);
		ni->blk_addr = nat_get_blkaddr(e);
		ni->version = nat_get_version(e);
		up_read(&nm_i->nat_tree_lock);

		/*
		 * P7/1773f63d108b: cached NAT entries must receive the same
		 * block-address sanity check as journal/on-disk NAT entries.
		 */
		if (__is_valid_data_blkaddr(ni->blk_addr) &&
		    !f2fs_is_valid_blkaddr(sbi, ni->blk_addr,
					   DATA_GENERIC_ENHANCE)) {
			f2fs_warn(sbi,
				"inconsistent cached NAT entry, ino:%u nid:%u blkaddr:%u ver:%u",
				ni->ino, ni->nid, ni->blk_addr, ni->version);
			set_sbi_flag(sbi, SBI_NEED_FSCK);
			return -EFSCORRUPTED;
		}
		return 0;
	}
'''
if old not in s:
    raise RuntimeError("node.c: cached NAT lookup anchor changed")
s=s.replace(old,new,1)

old=r'''cache:
	blkaddr = le32_to_cpu(ne.block_addr);
	if (__is_valid_data_blkaddr(blkaddr) &&
		!f2fs_is_valid_blkaddr(sbi, blkaddr, DATA_GENERIC_ENHANCE))
		return -EFAULT;

	/* cache nat entry */
	cache_nat_entry(sbi, nid, &ne);
	return 0;
'''
new=r'''cache:
	blkaddr = ni->blk_addr;
	if (__is_valid_data_blkaddr(blkaddr) &&
	    !f2fs_is_valid_blkaddr(sbi, blkaddr, DATA_GENERIC_ENHANCE)) {
		f2fs_warn(sbi,
			"inconsistent NAT entry, ino:%u nid:%u blkaddr:%u ver:%u",
			ni->ino, ni->nid, ni->blk_addr, ni->version);
		set_sbi_flag(sbi, SBI_NEED_FSCK);
		return -EFSCORRUPTED;
	}

	/* cache nat entry */
	cache_nat_entry(sbi, nid, &ne);
	return 0;
'''
if old not in s:
    raise RuntimeError("node.c: NAT disk/journal sanity anchor changed")
s=s.replace(old,new,1)

# ---- node.c: final fsync/dentry mark semantics -----------------------------
# 019f9dda7f66 + c3e238bd1f56, adapted from folios to pages.
old=r'''static int __write_node_page(struct page *page, bool atomic, bool *submitted,
				struct writeback_control *wbc, bool do_balance,
				enum iostat_type io_type, unsigned int *seq_id)
'''
new=r'''static int __write_node_page(struct page *page, bool atomic, bool do_fsync,
				bool *submitted, struct writeback_control *wbc,
				bool do_balance, enum iostat_type io_type,
				unsigned int *seq_id)
'''
if old not in s:
    raise RuntimeError("node.c: __write_node_page signature anchor changed")
s=s.replace(old,new,1)

anchor=r'''	if (atomic && !test_opt(sbi, NOBARRIER))
		fio.op_flags |= REQ_PREFLUSH | REQ_FUA;

	/* should add to global list before clearing PAGECACHE status */
'''
insert=r'''	if (atomic && !test_opt(sbi, NOBARRIER))
		fio.op_flags |= REQ_PREFLUSH | REQ_FUA;

	/*
	 * P7/019f9dda7f66+c3e238bd1f56:
	 * always rewrite footer recovery marks for this submission. In
	 * particular, FGGC node migration must not carry stale fsync/dentry
	 * marks from the source node page into the relocated node.
	 */
	set_dentry_mark(page, false);
	set_fsync_mark(page, do_fsync);
	if (IS_INODE(page) && (atomic || is_fsync_dnode(page)))
		set_dentry_mark(page,
				f2fs_need_dentry_mark(sbi, ino_of_node(page)));

	/* should add to global list before clearing PAGECACHE status */
'''
if anchor not in s:
    raise RuntimeError("node.c: node footer-mark insertion anchor changed")
s=s.replace(anchor,insert,1)

# Simple call sites: migration, generic writepage, sync_node_pages.
s=s.replace(r'''__write_node_page(node_page, false, NULL,
					&wbc, false, FS_GC_NODE_IO, NULL)''',
            r'''__write_node_page(node_page, false, false, NULL,
					&wbc, false, FS_GC_NODE_IO, NULL)''',1)
s=s.replace(r'''return __write_node_page(page, false, NULL, wbc, false,
						FS_NODE_IO, NULL);''',
            r'''return __write_node_page(page, false, false, NULL, wbc, false,
						FS_NODE_IO, NULL);''',1)
s=s.replace(r'''ret = __write_node_page(page, false, &submitted,
						wbc, do_balance, io_type, NULL);''',
            r'''ret = __write_node_page(page, false, false, &submitted,
						wbc, do_balance, io_type, NULL);''',1)

# fsync path: compute do_fsync, no longer mutate marks outside the write helper.
old=r'''		for (i = 0; i < nr_pages; i++) {
			struct page *page = pvec.pages[i];
			bool submitted = false;
'''
new=r'''		for (i = 0; i < nr_pages; i++) {
			struct page *page = pvec.pages[i];
			bool submitted = false;
			bool do_fsync = false;
'''
# This pattern occurs in both fsync and sync node loops; only replace first one
# after f2fs_fsync_node_pages.
pos=s.find("int f2fs_fsync_node_pages")
idx=s.find(old,pos)
if idx < 0:
    raise RuntimeError("node.c: fsync loop local anchor changed")
s=s[:idx]+s[idx:].replace(old,new,1)

old=r'''			set_fsync_mark(page, 0);
			set_dentry_mark(page, 0);

			if (!atomic || page == last_page) {
				set_fsync_mark(page, 1);
				if (IS_INODE(page)) {
					if (is_inode_flag_set(inode,
								FI_DIRTY_INODE))
						f2fs_update_inode(inode, page);
					set_dentry_mark(page,
						f2fs_need_dentry_mark(sbi, ino));
				}
'''
new=r'''			if (!atomic || page == last_page) {
				do_fsync = true;
				if (IS_INODE(page)) {
					if (is_inode_flag_set(inode,
								FI_DIRTY_INODE))
						f2fs_update_inode(inode, page);
				}
'''
if old not in s:
    raise RuntimeError("node.c: fsync footer-mark block changed")
s=s.replace(old,new,1)

old=r'''			ret = __write_node_page(page, atomic &&
						page == last_page,
						&submitted, wbc, true,
						FS_NODE_IO, seq_id);
'''
new=r'''			ret = __write_node_page(page, atomic &&
						page == last_page,
						do_fsync, &submitted, wbc, true,
						FS_NODE_IO, seq_id);
'''
if old not in s:
    raise RuntimeError("node.c: fsync __write_node_page call changed")
s=s.replace(old,new,1)

# Audit all call sites now have the new boolean.
if s.count("__write_node_page(") != 5:  # definition + 4 call sites
    raise RuntimeError(f"node.c: unexpected __write_node_page call count: {s.count('__write_node_page(')}")
if "__write_node_page(page, false, &submitted" in s or \
   "__write_node_page(node_page, false, NULL" in s:
    raise RuntimeError("node.c: stale pre-P7 __write_node_page call remains")

wr("node.c",s)

# ---- data.c: final write_end_io UAF-safe ordering --------------------------
s=rd("data.c")
old=r'''		f2fs_bug_on_endio(sbi, page->mapping == NODE_MAPPING(sbi) &&
					page->index != nid_of_node(page));

		dec_page_count(sbi, type);
		if (f2fs_in_warm_node_list(sbi, page))
			f2fs_del_fsync_node_entry(sbi, page);
		clear_cold_data(page);
		end_page_writeback(page);
	}
	if (!get_pages(sbi, F2FS_WB_CP_DATA) &&
				wq_has_sleeper(&sbi->cp_wait))
		wake_up(&sbi->cp_wait);

	bio_put(bio);
'''
new=r'''		f2fs_bug_on_endio(sbi, page->mapping == NODE_MAPPING(sbi) &&
					page->index != nid_of_node(page));

		/*
		 * P7/2d9c4a4ed4ee: warm-node bookkeeping can access sbi;
		 * perform it before decrementing the outstanding page count.
		 */
		if (f2fs_in_warm_node_list(sbi, page))
			f2fs_del_fsync_node_entry(sbi, page);

		dec_page_count(sbi, type);

		/*
		 * P7/ce2739e482bc: all sbi accesses tied to this writeback
		 * must happen before end_page_writeback(), which can release
		 * the final lifetime dependency during unmount.
		 */
		if (type == F2FS_WB_CP_DATA && !get_pages(sbi, type) &&
				wq_has_sleeper(&sbi->cp_wait))
			wake_up(&sbi->cp_wait);

		clear_cold_data(page);
		end_page_writeback(page);
	}

	bio_put(bio);
'''
if old not in s:
    raise RuntimeError("data.c: write_end_io ordering anchor changed")
s=s.replace(old,new,1)
wr("data.c",s)

# ---- f2fs.h: valid block count corruption hardening ------------------------
s=rd("f2fs.h")
old=r'''	spin_lock(&sbi->stat_lock);
	f2fs_bug_on(sbi, sbi->total_valid_block_count < (block_t) count);
	sbi->total_valid_block_count -= (block_t)count;
	if (sbi->reserved_blocks &&
'''
new=r'''	spin_lock(&sbi->stat_lock);
	/*
	 * P7/05872a167c2c: don't wrap total_valid_block_count if metadata
	 * is already inconsistent. Preserve service, flag fsck, and clamp.
	 */
	if (unlikely(sbi->total_valid_block_count < (block_t)count)) {
		f2fs_warn(sbi,
			"Inconsistent total_valid_block_count:%u, ino:%lu, count:%u",
			sbi->total_valid_block_count, inode->i_ino,
			(unsigned int)count);
		sbi->total_valid_block_count = 0;
		set_sbi_flag(sbi, SBI_NEED_FSCK);
	} else {
		sbi->total_valid_block_count -= (block_t)count;
	}
	if (sbi->reserved_blocks &&
'''
if old not in s:
    raise RuntimeError("f2fs.h: dec_valid_block_count anchor changed")
s=s.replace(old,new,1)
wr("f2fs.h",s)

# ---- final audits ----------------------------------------------------------
checks={
    "node.c":[
        "P7/238e14eb7226",
        "P7/1773f63d108b",
        "P7/019f9dda7f66+c3e238bd1f56",
        "bool do_fsync",
        "set_fsync_mark(page, do_fsync);",
        "set_dentry_mark(page, false);",
        "return -EFSCORRUPTED;",
    ],
    "data.c":[
        "P7/2d9c4a4ed4ee",
        "P7/ce2739e482bc",
        "if (type == F2FS_WB_CP_DATA && !get_pages(sbi, type)",
    ],
    "f2fs.h":[
        "P7/05872a167c2c",
        "sbi->total_valid_block_count = 0;",
        "set_sbi_flag(sbi, SBI_NEED_FSCK);",
    ],
}
for rel,needles in checks.items():
    data=rd(rel)
    for needle in needles:
        if needle not in data:
            raise RuntimeError(f"{rel}: missing P7 token: {needle}")

# No old unsafe CP wait after end_page_writeback loop.
data=rd("data.c")
we=data[data.find("static void f2fs_write_end_io"):data.find("struct block_device *f2fs_target_device")]
if we.rfind("wake_up(&sbi->cp_wait)") > we.rfind("end_page_writeback(page)"):
    raise RuntimeError("P7 invariant violated: cp_wait sbi access remains after end_page_writeback")

subprocess.run(["git","diff","--check"],cwd=kernel,check=True)

report="""F2FS P7 correctness/data-integrity
base=P6 runtime-validated
nat_flag_lock_fix=238e14eb7226 adapted
node_mark_fix=019f9dda7f66+c3e238bd1f56 adapted-to-pages
write_end_io_uaf_fix=ce2739e482bc+2d9c4a4ed4ee adapted-to-pages
valid_block_count_fix=05872a167c2c adapted
nat_corruption_fix=1773f63d108b adapted
excluded_alloc_failure_fix=0f9af07ecc1a needs newer allocate-data-block error API
excluded_post_eof_fix=5eced87b7d19 needs dedicated newer-filemap port
"""
(out/"report.txt").write_text(report)
with (out/"p7.diff").open("wb") as f:
    subprocess.run(["git","diff","--","fs/f2fs"],cwd=kernel,stdout=f,check=True)

print(report)
print("P7 correctness/data-integrity adaptation complete")
