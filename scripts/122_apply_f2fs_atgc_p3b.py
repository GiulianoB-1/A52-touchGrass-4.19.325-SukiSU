#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys, re

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else 'workspace/touchgrass-a52xq').resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else 'artifacts/f2fs-atgc-p3b').resolve()
out.mkdir(parents=True, exist_ok=True)

commits = [
 ('093749e296e29a4b0162eb925a6701a01e8c9a98','ATGC original'),
 ('61461fc921b756ae16e64243f72af2bfc2e620db','checkpointed-data correctness'),
 ('89e53ff1651a61cf2abef9356e2f60d0086215be','default age threshold'),
 ('8939a8489ca64b56f49428b0d882709080a928d4','sysfs tunables'),
 ('7d19e3dab0002e527052b0aaf986e8c32e5537bf','gc_idle correctness'),
 ('80dc113aaa47c0d1dfd01f708d4d0c083022121b','reject LFS mode'),
]
report=[]
for sha,label in commits:
    pf=out/f'{sha}.patch'; lf=out/f'{sha}.apply.log'
    if not pf.exists():
        subprocess.run(['curl','-LfsS',f'https://github.com/torvalds/linux/commit/{sha}.patch','-o',str(pf)],check=True)
    with pf.open('rb') as inp, lf.open('wb') as log:
        rc=subprocess.run(['patch','-p1','--forward','--batch','--fuzz=0'],cwd=kernel,stdin=inp,stdout=log,stderr=subprocess.STDOUT).returncode
    report.append(f'{sha} {label}: patch_rc={rc}')

root=kernel/'fs'/'f2fs'
def rd(f): return (root/f).read_text()
def wr(f,s): (root/f).write_text(s)
def rep(f,old,new,count=1):
    s=rd(f); n=s.count(old)
    if n < count: raise RuntimeError(f'{f}: missing/changed anchor ({n}): {old[:120]!r}')
    wr(f,s.replace(old,new,count))
def between(f,start,end,new):
    s=rd(f); a=s.index(start); b=s.index(end,a); wr(f,s[:a]+new+s[b:])

rep('f2fs.h','#define F2FS_MOUNT_NORECOVERY\t\t0x04000000\n#define F2FS_MOUNT_GC_MERGE',
    '#define F2FS_MOUNT_NORECOVERY\t\t0x04000000\n#define F2FS_MOUNT_ATGC\t\t\t0x08000000\n#define F2FS_MOUNT_GC_MERGE')
rep('f2fs.h','#define NR_CURSEG_INMEM_TYPE\t(1)','#define NR_CURSEG_INMEM_TYPE\t(2)')
rep('f2fs.h','GC_IDLE_GREEDY,\n\tGC_URGENT,','GC_IDLE_GREEDY,\n\tGC_IDLE_AT,\n\tGC_URGENT,')
rep('f2fs.h',
'''int f2fs_npages_for_summary_flush(struct f2fs_sb_info *sbi, bool for_ra);
void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi, int type);
void f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi, int type);''',
'''int f2fs_npages_for_summary_flush(struct f2fs_sb_info *sbi, bool for_ra);
bool f2fs_segment_has_free_slot(struct f2fs_sb_info *sbi, int segno);
void f2fs_init_inmem_curseg(struct f2fs_sb_info *sbi);
void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi);
void f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi);''')

rep('gc.c','#include <trace/events/f2fs.h>\n\nstatic int gc_thread_func',
    '#include <trace/events/f2fs.h>\n\nstatic struct kmem_cache *victim_entry_slab;\n\nstatic int gc_thread_func')
between('gc.c','static int select_gc_type','static void select_policy',r'''static int select_gc_type(struct f2fs_sb_info *sbi, int gc_type)
{
	int gc_mode;
	if (gc_type == BG_GC)
		gc_mode = sbi->am.atgc_enabled ? GC_AT : GC_CB;
	else
		gc_mode = GC_GREEDY;
	switch (sbi->gc_mode) {
	case GC_IDLE_CB:
		gc_mode = GC_CB;
		break;
	case GC_IDLE_GREEDY:
	case GC_URGENT:
		gc_mode = GC_GREEDY;
		break;
	case GC_IDLE_AT:
		gc_mode = GC_AT;
		break;
	}
	return gc_mode;
}

''')
between('gc.c','static void select_policy','static unsigned int get_max_cost',r'''static void select_policy(struct f2fs_sb_info *sbi, int gc_type,
			int type, struct victim_sel_policy *p)
{
	struct dirty_seglist_info *dirty_i = DIRTY_I(sbi);
	if (p->alloc_mode == SSR || p->alloc_mode == AT_SSR) {
		p->gc_mode = GC_GREEDY;
		p->dirty_segmap = dirty_i->dirty_segmap[type];
		p->max_search = dirty_i->nr_dirty[type];
		p->ofs_unit = 1;
	} else {
		p->gc_mode = select_gc_type(sbi, gc_type);
		p->dirty_segmap = dirty_i->dirty_segmap[DIRTY];
		p->max_search = dirty_i->nr_dirty[DIRTY];
		p->ofs_unit = sbi->segs_per_sec;
	}
	if (gc_type != FG_GC && sbi->gc_mode != GC_URGENT &&
		p->gc_mode != GC_AT && p->alloc_mode != AT_SSR &&
		p->max_search > sbi->max_victim_search)
		p->max_search = sbi->max_victim_search;
	if (test_opt(sbi, NOHEAP) &&
		(type == CURSEG_HOT_DATA || IS_NODESEG(type)))
		p->offset = 0;
	else
		p->offset = SIT_I(sbi)->last_victim[p->gc_mode];
}

''')
between('gc.c','static int get_victim_by_default','static const struct victim_selection default_v_ops',r'''static int get_victim_by_default(struct f2fs_sb_info *sbi,
		unsigned int *result, int gc_type, int type, char alloc_mode,
		unsigned long long age)
{
	struct dirty_seglist_info *dirty_i = DIRTY_I(sbi);
	struct sit_info *sm = SIT_I(sbi);
	struct victim_sel_policy p;
	unsigned int secno, last_victim;
	unsigned int last_segment;
	unsigned int nsearched;
	bool is_atgc;

	mutex_lock(&dirty_i->seglist_lock);
	last_segment = MAIN_SECS(sbi) * sbi->segs_per_sec;
	p.alloc_mode = alloc_mode;
	p.age = age;
	p.age_threshold = sbi->am.age_threshold;
retry:
	select_policy(sbi, gc_type, type, &p);
	p.min_segno = NULL_SEGNO;
	p.oldest_age = 0;
	p.min_cost = get_max_cost(sbi, &p);
	is_atgc = (p.gc_mode == GC_AT || p.alloc_mode == AT_SSR);
	nsearched = 0;
	if (is_atgc)
		SIT_I(sbi)->dirty_min_mtime = ULLONG_MAX;
	if (*result != NULL_SEGNO) {
		if (get_valid_blocks(sbi, *result, false) &&
			!sec_usage_check(sbi, GET_SEC_FROM_SEG(sbi, *result)))
			p.min_segno = *result;
		goto out;
	}
	if (p.max_search == 0)
		goto out;
	if (__is_large_section(sbi) && p.alloc_mode == LFS) {
		if (sbi->next_victim_seg[BG_GC] != NULL_SEGNO) {
			p.min_segno = sbi->next_victim_seg[BG_GC];
			*result = p.min_segno;
			sbi->next_victim_seg[BG_GC] = NULL_SEGNO;
			goto got_result;
		}
		if (gc_type == FG_GC && sbi->next_victim_seg[FG_GC] != NULL_SEGNO) {
			p.min_segno = sbi->next_victim_seg[FG_GC];
			*result = p.min_segno;
			sbi->next_victim_seg[FG_GC] = NULL_SEGNO;
			goto got_result;
		}
	}
	last_victim = sm->last_victim[p.gc_mode];
	if (p.alloc_mode == LFS && gc_type == FG_GC) {
		p.min_segno = check_bg_victims(sbi);
		if (p.min_segno != NULL_SEGNO)
			goto got_it;
	}
	while (1) {
		unsigned long cost;
		unsigned int segno;
		segno = find_next_bit(p.dirty_segmap, last_segment, p.offset);
		if (segno >= last_segment) {
			if (sm->last_victim[p.gc_mode]) {
				last_segment = sm->last_victim[p.gc_mode];
				sm->last_victim[p.gc_mode] = 0;
				p.offset = 0;
				continue;
			}
			break;
		}
		p.offset = segno + p.ofs_unit;
		if (p.ofs_unit > 1) {
			p.offset -= segno % p.ofs_unit;
			nsearched += count_bits(p.dirty_segmap,
					p.offset - p.ofs_unit, p.ofs_unit);
		} else {
			nsearched++;
		}
#ifdef CONFIG_F2FS_CHECK_FS
		if (test_bit(segno, sm->invalid_segmap))
			goto next;
#endif
		secno = GET_SEC_FROM_SEG(sbi, segno);
		if (sec_usage_check(sbi, secno))
			goto next;
		if (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED))) {
			if (p.alloc_mode == LFS) {
				if (get_ckpt_valid_blocks(sbi, segno, true))
					goto next;
			} else if (!f2fs_segment_has_free_slot(sbi, segno)) {
				goto next;
			}
		}
		if (gc_type == BG_GC && test_bit(secno, dirty_i->victim_secmap))
			goto next;
		if (test_bit(secno, dirty_i->blacklist_victim_secmap))
			goto next;
		if (is_atgc) {
			add_victim_entry(sbi, &p, segno);
			goto next;
		}
		cost = get_gc_cost(sbi, segno, &p);
		if (p.min_cost > cost) {
			p.min_segno = segno;
			p.min_cost = cost;
		}
next:
		if (nsearched >= p.max_search) {
			if (!sm->last_victim[p.gc_mode] && segno <= last_victim)
				sm->last_victim[p.gc_mode] = last_victim + 1;
			else
				sm->last_victim[p.gc_mode] = segno + 1;
			sm->last_victim[p.gc_mode] %= MAIN_SECS(sbi) * sbi->segs_per_sec;
			break;
		}
	}
	if (is_atgc) {
		lookup_victim_by_age(sbi, &p);
		release_victim_entry(sbi);
	}
	if (is_atgc && p.min_segno == NULL_SEGNO &&
			sm->elapsed_time < p.age_threshold) {
		p.age_threshold = 0;
		goto retry;
	}
	if (p.min_segno != NULL_SEGNO) {
got_it:
		*result = (p.min_segno / p.ofs_unit) * p.ofs_unit;
got_result:
		if (p.alloc_mode == LFS) {
			secno = GET_SEC_FROM_SEG(sbi, p.min_segno);
			if (gc_type == FG_GC)
				sbi->cur_victim_sec = secno;
			else
				set_bit(secno, dirty_i->victim_secmap);
		}
	}
out:
	if (p.min_segno != NULL_SEGNO)
		trace_f2fs_get_victim(sbi->sb, type, gc_type, &p,
				sbi->cur_victim_sec, prefree_segments(sbi), free_segments(sbi));
	mutex_unlock(&dirty_i->seglist_lock);
	return (p.min_segno == NULL_SEGNO) ? 0 : 1;
}

''')
rep('gc.c','f2fs_allocate_data_block(fio.sbi, NULL, fio.old_blkaddr, &newaddr,\n\t\t\t\t\t&sum, CURSEG_COLD_DATA, NULL, false);',
    'f2fs_allocate_data_block(fio.sbi, NULL, fio.old_blkaddr, &newaddr,\n\t\t\t\t\t&sum, type, NULL, false);')
# ATGC uses a 64-bit timestamp key in the same generic rb_entry layout used
# by the newer F2FS extent-cache helpers. Backport that generalized rb-tree
# infrastructure instead of weakening ATGC's consistency checks.
rep('f2fs.h',
'''struct rb_entry {
\tstruct rb_node rb_node;\t\t/* rb node located in rb-tree */
\tunsigned int ofs;\t\t/* start offset of the entry */
\tunsigned int len;\t\t/* length of the entry */
};''',
'''struct rb_entry {
\tstruct rb_node rb_node;\t\t/* rb node located in rb-tree */
\tunion {
\t\tstruct {
\t\t\tunsigned int ofs;\t/* start offset of the entry */
\t\t\tunsigned int len;\t/* length of the entry */
\t\t};
\t\tunsigned long long key;\t\t/* 64-bits key */
\t};
};''')

rep('f2fs.h',
'''struct rb_entry *f2fs_lookup_rb_tree(struct rb_root_cached *root,
\t\t\t\tstruct rb_entry *cached_re, unsigned int ofs);
struct rb_node **f2fs_lookup_rb_tree_for_insert''',
'''struct rb_entry *f2fs_lookup_rb_tree(struct rb_root_cached *root,
\t\t\t\tstruct rb_entry *cached_re, unsigned int ofs);
struct rb_node **f2fs_lookup_rb_tree_ext(struct f2fs_sb_info *sbi,
\t\t\t\tstruct rb_root_cached *root,
\t\t\t\tstruct rb_node **parent,
\t\t\t\tunsigned long long key, bool *leftmost);
struct rb_node **f2fs_lookup_rb_tree_for_insert''')

rep('f2fs.h',
'''bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
\t\t\t\tstruct rb_root_cached *root);''',
'''bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
\t\t\t\tstruct rb_root_cached *root, bool check_key);''')

rep('extent_cache.c',
'''struct rb_node **f2fs_lookup_rb_tree_for_insert(struct f2fs_sb_info *sbi,''',
'''struct rb_node **f2fs_lookup_rb_tree_ext(struct f2fs_sb_info *sbi,
\t\t\t\t\tstruct rb_root_cached *root,
\t\t\t\t\tstruct rb_node **parent,
\t\t\t\t\tunsigned long long key, bool *leftmost)
{
\tstruct rb_node **p = &root->rb_root.rb_node;
\tstruct rb_entry *re;

\twhile (*p) {
\t\t*parent = *p;
\t\tre = rb_entry(*parent, struct rb_entry, rb_node);

\t\tif (key < re->key) {
\t\t\tp = &(*p)->rb_left;
\t\t} else {
\t\t\tp = &(*p)->rb_right;
\t\t\t*leftmost = false;
\t\t}
\t}

\treturn p;
}

struct rb_node **f2fs_lookup_rb_tree_for_insert(struct f2fs_sb_info *sbi,''')

between('extent_cache.c',
'''bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
''',
'''static struct kmem_cache *extent_tree_slab;''',
'''bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
\t\t\t\tstruct rb_root_cached *root, bool check_key)
{
#ifdef CONFIG_F2FS_CHECK_FS
\tstruct rb_node *cur = rb_first_cached(root), *next;
\tstruct rb_entry *cur_re, *next_re;

\tif (!cur)
\t\treturn true;

\twhile (cur) {
\t\tnext = rb_next(cur);
\t\tif (!next)
\t\t\treturn true;

\t\tcur_re = rb_entry(cur, struct rb_entry, rb_node);
\t\tnext_re = rb_entry(next, struct rb_entry, rb_node);

\t\tif (check_key) {
\t\t\tif (cur_re->key > next_re->key) {
\t\t\t\tf2fs_info(sbi, "inconsistent rbtree, cur(%llu) next(%llu)",
\t\t\t\t\tcur_re->key, next_re->key);
\t\t\t\treturn false;
\t\t\t}
\t\t\tgoto next;
\t\t}

\t\tif (cur_re->ofs + cur_re->len > next_re->ofs) {
\t\t\tf2fs_info(sbi, "inconsistent rbtree, cur(%u, %u) next(%u, %u)",
\t\t\t\t  cur_re->ofs, cur_re->len,
\t\t\t\t  next_re->ofs, next_re->len);
\t\t\treturn false;
\t\t}
next:
\t\tcur = next;
\t}
#endif
\treturn true;
}

''')

# Existing extent-cache call sites use offset/length ordering, not key ordering.
p = root/'extent_cache.c'
ec = p.read_text()
ec = ec.replace('f2fs_check_rb_tree_consistence(sbi, &et->root)',
                'f2fs_check_rb_tree_consistence(sbi, &et->root, false)')
p.write_text(ec)

# Samsung's discard-command rb-tree shares the extent-style offset/length
# ordering and therefore uses check_key=false. There are exactly two checks.
p = root/'segment.c'
seg = p.read_text()
seg, n = re.subn(r'f2fs_check_rb_tree_consistence\(sbi,\s*&dcc->root\)',
                 'f2fs_check_rb_tree_consistence(sbi, &dcc->root, false)', seg)
if n != 2:
    raise RuntimeError(f'unexpected discard rb-tree checker call count: {n}')
p.write_text(seg)

needle='\tif (__is_large_section(sbi))\n\t\tf2fs_ra_meta_pages(sbi, GET_SUM_BLOCK(sbi, segno),'
rep('gc.c',needle,'\tsanity_check_seg_type(sbi, get_seg_entry(sbi, segno)->type);\n\n'+needle)

s=rd('segment.c'); a=s.index('static void __f2fs_restore_inmem_curseg'); b=s.index('\nstatic int get_ssr_segment',a); block=s[a:b]
if 'void f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi)\n' not in block:
    block += '\nvoid f2fs_restore_inmem_curseg(struct f2fs_sb_info *sbi)\n{\n\t__f2fs_restore_inmem_curseg(sbi, CURSEG_COLD_DATA_PINNED);\n\n\tif (sbi->am.atgc_enabled)\n\t\t__f2fs_restore_inmem_curseg(sbi, CURSEG_ALL_DATA_ATGC);\n}\n'
    wr('segment.c',s[:a]+block+s[b:])
between('segment.c','static int get_ssr_segment(struct f2fs_sb_info *sbi, int type)\n','/*\n * flush out current segment',r'''static int get_ssr_segment(struct f2fs_sb_info *sbi, int type,
				int alloc_mode, unsigned long long age)
{
	struct curseg_info *curseg = CURSEG_I(sbi, type);
	const struct victim_selection *v_ops = DIRTY_I(sbi)->v_ops;
	unsigned segno = NULL_SEGNO;
	unsigned short seg_type = curseg->seg_type;
	int i, cnt;
	bool reversed = false;
	sanity_check_seg_type(sbi, seg_type);
	if (v_ops->get_victim(sbi, &segno, BG_GC, seg_type, alloc_mode, age)) {
		curseg->next_segno = segno;
		return 1;
	}
	if (IS_NODESEG(seg_type)) {
		if (seg_type >= CURSEG_WARM_NODE) { reversed = true; i = CURSEG_COLD_NODE; }
		else i = CURSEG_HOT_NODE;
		cnt = NR_CURSEG_NODE_TYPE;
	} else {
		if (seg_type >= CURSEG_WARM_DATA) { reversed = true; i = CURSEG_COLD_DATA; }
		else i = CURSEG_HOT_DATA;
		cnt = NR_CURSEG_DATA_TYPE;
	}
	for (; cnt-- > 0; reversed ? i-- : i++) {
		if (i == seg_type)
			continue;
		if (v_ops->get_victim(sbi, &segno, BG_GC, i, alloc_mode, age)) {
			curseg->next_segno = segno;
			return 1;
		}
	}
	if (unlikely(is_sbi_flag_set(sbi, SBI_CP_DISABLED))) {
		segno = get_free_segment(sbi);
		if (segno != NULL_SEGNO) {
			curseg->next_segno = segno;
			return 1;
		}
	}
	return 0;
}

''')
s=rd('segment.c').replace('get_ckpt_valid_blocks(sbi, segno);','get_ckpt_valid_blocks(sbi, segno, false);')
s=s.replace('get_ckpt_valid_blocks(sbi, curseg->segno))','get_ckpt_valid_blocks(sbi, curseg->segno, false))')
wr('segment.c',s)
rep('segment.c',r'''static bool __has_curseg_space(struct f2fs_sb_info *sbi, int type)
{
	struct curseg_info *curseg = CURSEG_I(sbi, type);
	if (curseg->next_blkoff < sbi->blocks_per_seg)
		return true;
	return false;
}
''',r'''static bool __has_curseg_space(struct f2fs_sb_info *sbi,
					struct curseg_info *curseg)
{
	if (curseg->next_blkoff < sbi->blocks_per_seg)
		return true;
	return false;
}
''')
rep('segment.c','\tstruct sit_info *sit_i = SIT_I(sbi);\n\tstruct curseg_info *curseg = CURSEG_I(sbi, type);\n\n\t/*\n\t * We need to wait for node_write',
    '\tstruct sit_info *sit_i = SIT_I(sbi);\n\tstruct curseg_info *curseg = CURSEG_I(sbi, type);\n\tbool from_gc = (type == CURSEG_ALL_DATA_ATGC);\n\tstruct seg_entry *se = NULL;\n\n\t/*\n\t * We need to wait for node_write')
rep('segment.c','\tmutex_lock(&curseg->curseg_mutex);\n\tdown_write(&sit_i->sentry_lock);\n\n\t*new_blkaddr = NEXT_FREE_BLKADDR(sbi, curseg);\n',
    '\tmutex_lock(&curseg->curseg_mutex);\n\tdown_write(&sit_i->sentry_lock);\n\n\tif (from_gc) {\n\t\tf2fs_bug_on(sbi, GET_SEGNO(sbi, old_blkaddr) == NULL_SEGNO);\n\t\tse = get_seg_entry(sbi, GET_SEGNO(sbi, old_blkaddr));\n\t\tsanity_check_seg_type(sbi, se->type);\n\t\tf2fs_bug_on(sbi, IS_NODESEG(se->type));\n\t}\n\n\t*new_blkaddr = NEXT_FREE_BLKADDR(sbi, curseg);\n\tf2fs_bug_on(sbi, curseg->next_blkoff >= sbi->blocks_per_seg);\n')
rep('segment.c','\tif (!__has_curseg_space(sbi, type))\n\t\tsit_i->s_ops->allocate_segment(sbi, type, false);\n',
    '\tif (!__has_curseg_space(sbi, curseg)) {\n\t\tif (from_gc)\n\t\t\tget_atssr_segment(sbi, type, se->type, AT_SSR, se->mtime);\n\t\telse\n\t\t\tsit_i->s_ops->allocate_segment(sbi, type, false);\n\t}\n')
rep('segment.c','\t\tif (f2fs_sb_has_readonly(sbi) &&\n\t\t\ti != CURSEG_HOT_DATA && i != CURSEG_HOT_NODE)\n\t\t\tcontinue;\n\n\t\tif (f2fs_test_bit',
    '\t\tif (f2fs_sb_has_readonly(sbi) &&\n\t\t\ti != CURSEG_HOT_DATA && i != CURSEG_HOT_NODE)\n\t\t\tcontinue;\n\n\t\tsanity_check_seg_type(sbi, curseg->seg_type);\n\n\t\tif (f2fs_test_bit')

rep('super.c','\tOpt_nogc_merge,\n\tOpt_err,','\tOpt_nogc_merge,\n\tOpt_atgc,\n\tOpt_err,')
rep('super.c','\t{Opt_nogc_merge, "nogc_merge"},\n\t{Opt_err, NULL},','\t{Opt_nogc_merge, "nogc_merge"},\n\t{Opt_atgc, "atgc"},\n\t{Opt_err, NULL},')
rep('super.c','\t\tcase Opt_nogc_merge:\n\t\t\tclear_opt(sbi, GC_MERGE);\n\t\t\tbreak;\n\t\tdefault:',
    '\t\tcase Opt_nogc_merge:\n\t\t\tclear_opt(sbi, GC_MERGE);\n\t\t\tbreak;\n\t\tcase Opt_atgc:\n\t\t\tset_opt(sbi, ATGC);\n\t\t\tbreak;\n\t\tdefault:')
rep('super.c','\tif (test_opt(sbi, GC_MERGE))\n\t\tseq_puts(seq, ",gc_merge");\n\telse\n\t\tseq_puts(seq, ",nogc_merge");\n',
    '\tif (test_opt(sbi, GC_MERGE))\n\t\tseq_puts(seq, ",gc_merge");\n\telse\n\t\tseq_puts(seq, ",nogc_merge");\n\tif (test_opt(sbi, ATGC))\n\t\tseq_puts(seq, ",atgc");\n')
rep('super.c','reset_checkpoint:\n\t/* f2fs_recover_fsync_data() cleared this already */',
    'reset_checkpoint:\n\tf2fs_init_inmem_curseg(sbi);\n\n\t/* f2fs_recover_fsync_data() cleared this already */')
s=rd('super.c'); anchor='\tif (f2fs_sb_has_readonly(sbi) && !f2fs_readonly(sbi->sb)) {'
if 'LFS not compatible with ATGC' not in s:
    if anchor not in s: raise RuntimeError('super.c LFS validation anchor changed')
    s=s.replace(anchor,'\tif (test_opt(sbi, ATGC) && f2fs_lfs_mode(sbi)) {\n\t\tf2fs_err(sbi, "LFS not compatible with ATGC");\n\t\treturn -EINVAL;\n\t}\n\n'+anchor,1)
wr('super.c',s)

rep('sysfs.c','\tRESERVED_BLOCKS,\t/* struct f2fs_sb_info */\n};','\tRESERVED_BLOCKS,\t/* struct f2fs_sb_info */\n\tATGC_INFO,\t/* struct atgc_management */\n};')
rep('sysfs.c','\telse if (struct_type == F2FS_SBI || struct_type == RESERVED_BLOCKS)\n\t\treturn (unsigned char *)sbi;',
    '\telse if (struct_type == F2FS_SBI || struct_type == RESERVED_BLOCKS)\n\t\treturn (unsigned char *)sbi;\n\telse if (struct_type == ATGC_INFO)\n\t\treturn (unsigned char *)&sbi->am;')
rep('sysfs.c','\tui = (unsigned int *)(ptr + a->offset);\n\n\treturn sprintf(buf, "%u\\n", *ui);\n}',
    '\tif (!strcmp(a->attr.name, "atgc_age_threshold"))\n\t\treturn sprintf(buf, "%llu\\n", sbi->am.age_threshold);\n\n\tui = (unsigned int *)(ptr + a->offset);\n\n\treturn sprintf(buf, "%u\\n", *ui);\n}')
s=rd('sysfs.c'); marker='\tif (!strcmp(a->attr.name, "iostat_enable")) {'
if 'atgc_candidate_ratio"))' not in s:
    if marker not in s: raise RuntimeError('sysfs store anchor changed')
    s=s.replace(marker,r'''	if (!strcmp(a->attr.name, "atgc_candidate_ratio")) {
		if (t > 100)
			return -EINVAL;
		sbi->am.candidate_ratio = t;
		return count;
	}
	if (!strcmp(a->attr.name, "atgc_age_weight")) {
		if (t > 100)
			return -EINVAL;
		sbi->am.age_weight = t;
		return count;
	}
	if (!strcmp(a->attr.name, "atgc_age_threshold")) {
		sbi->am.age_threshold = t;
		return count;
	}

'''+marker,1)
wr('sysfs.c',s)
rep('sysfs.c','F2FS_RW_ATTR(F2FS_SBI, f2fs_sb_info, sec_hqm_preserve, sec_hqm_preserve);\nF2FS_GENERAL_RO_ATTR(dirty_segments);',
    'F2FS_RW_ATTR(F2FS_SBI, f2fs_sb_info, sec_hqm_preserve, sec_hqm_preserve);\nF2FS_RW_ATTR(ATGC_INFO, atgc_management, atgc_candidate_ratio, candidate_ratio);\nF2FS_RW_ATTR(ATGC_INFO, atgc_management, atgc_candidate_count, max_candidate_count);\nF2FS_RW_ATTR(ATGC_INFO, atgc_management, atgc_age_weight, age_weight);\nF2FS_RW_ATTR(ATGC_INFO, atgc_management, atgc_age_threshold, age_threshold);\nF2FS_GENERAL_RO_ATTR(dirty_segments);')
rep('sysfs.c','\tATTR_LIST(gc_urgent),\n\tATTR_LIST(reclaim_segments),',
    '\tATTR_LIST(gc_urgent),\n\tATTR_LIST(atgc_candidate_ratio),\n\tATTR_LIST(atgc_candidate_count),\n\tATTR_LIST(atgc_age_weight),\n\tATTR_LIST(atgc_age_threshold),\n\tATTR_LIST(reclaim_segments),')

for p in kernel.rglob('*.rej'): p.unlink()
for p in kernel.rglob('*.orig'): p.unlink()

checks={
 'f2fs.h':['F2FS_MOUNT_ATGC','NR_CURSEG_INMEM_TYPE\t(2)','CURSEG_ALL_DATA_ATGC','GC_IDLE_AT','bool f2fs_segment_has_free_slot','void f2fs_init_inmem_curseg','unsigned long long key','f2fs_lookup_rb_tree_ext'],
 'segment.h':['AT_SSR','GC_AT','dirty_min_mtime','unsigned long long age_threshold'],
 'gc.c':['static struct kmem_cache *victim_entry_slab','p->alloc_mode == AT_SSR','init_atgc_management'],
 'segment.c':['CURSEG_ALL_DATA_ATGC','get_atssr_segment','bool from_gc = (type == CURSEG_ALL_DATA_ATGC)','get_ssr_segment(sbi, type, SSR, 0)'],
 'super.c':['Opt_atgc','"atgc"','LFS not compatible with ATGC','f2fs_init_inmem_curseg(sbi);'],
 'sysfs.c':['ATGC_INFO','atgc_candidate_ratio','atgc_candidate_count','atgc_age_weight','atgc_age_threshold'],
 'extent_cache.c':['f2fs_lookup_rb_tree_ext','bool check_key','cur_re->key > next_re->key'],
}
for f,needles in checks.items():
    data=rd(f)
    for n in needles:
        if n not in data: raise RuntimeError(f'{f}: missing sanity token {n}')
all_src='\n'.join(p.read_text(errors='ignore') for p in root.glob('*.[ch]'))
for bad in ['get_ckpt_valid_blocks(sbi, segno)', 'GC_URGENT_HIGH', 'GC_URGENT_LOW', 'dirty_bitmap']:
    if bad in all_src: raise RuntimeError(f'leftover incompatible token: {bad}')

subprocess.run(['git','diff','--check'],cwd=kernel,check=True)
(out/'report.txt').write_text('\n'.join(report)+"\nmanual Samsung ATGC adaptation=PASS\nATGC default=off\n")
with (out/'atgc-p3b.diff').open('wb') as f:
    subprocess.run(['git','diff','--','fs/f2fs','include/trace/events/f2fs.h'],cwd=kernel,stdout=f,check=True)
print('\n'.join(report))
print('P3B Samsung ATGC adaptation complete; ATGC remains opt-in')
