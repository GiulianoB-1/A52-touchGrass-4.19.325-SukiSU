#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys, re

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-block-age-p5a").resolve()
out.mkdir(parents=True, exist_ok=True)
root = kernel / "fs" / "f2fs"

def rd(f): return (root/f).read_text()
def wr(f,s): (root/f).write_text(s)
def rep(f,old,new,count=1):
    s=rd(f)
    n=s.count(old)
    if n < count:
        raise RuntimeError(f"{f}: missing/changed anchor ({n}): {old[:160]!r}")
    wr(f,s.replace(old,new,count))
def sub1(f,pat,repl,flags=0,label="regex"):
    s=rd(f)
    s,n=re.subn(pat,repl,s,count=1,flags=flags)
    if n != 1:
        raise RuntimeError(f"{f}: {label} expected 1 match, got {n}: {pat[:160]!r}")
    wr(f,s)

# ---------------------------------------------------------------------------
# Samsung-native backport of the upstream block-age extent cache.
# Upstream prerequisite e7547daccd6a generalized all extent-cache structures.
# Instead of replacing Samsung's heavily modified read-extent cache, P5A keeps
# it intact and adds a parallel age-extent tree with the same semantics.
# Functional commits represented:
#   e7547daccd6a  extent-cache multi-type prerequisite (Samsung-native split)
#   71644dff4811  block_age-based extent cache
#   ed2724765e58  don't mix union values
#   8c0ed062ce27  correct truncation invalidation length
#   a84153f93980  zero-range invalidation
#   b03a41a495df  correct weighted block-age calculation
#   9b13a8662ea6  warm threshold validation
#   27bf6a637b76  allocated_data_blocks overflow handling
# P5A intentionally leaves AGE_EXTENT_CACHE disabled by default.
# ---------------------------------------------------------------------------

# ---- f2fs.h ---------------------------------------------------------------
s=rd("f2fs.h")
if "F2FS_MOUNT_AGE_EXTENT_CACHE" not in s:
    m=re.search(r"(^#define\s+F2FS_MOUNT_GC_MERGE\s+0x[0-9A-Fa-f]+\s*$)",s,re.M)
    if not m:
        raise RuntimeError("f2fs.h: GC_MERGE mount-bit anchor missing")
    s=s[:m.end()]+"\n#define F2FS_MOUNT_AGE_EXTENT_CACHE\t0x80000000"+s[m.end():]

if "AGE_EXTENT_CACHE_SHRINK_NUMBER" not in s:
    anchor="#define EXTENT_CACHE_SHRINK_NUMBER\t128"
    if anchor not in s:
        # some reconstructed trees may use READ_EXTENT_CACHE_SHRINK_NUMBER
        anchor="#define READ_EXTENT_CACHE_SHRINK_NUMBER\t128"
    if anchor not in s:
        raise RuntimeError("f2fs.h: extent shrink constant anchor missing")
    s=s.replace(anchor,anchor+r'''

/* block-age extent cache */
#define AGE_EXTENT_CACHE_SHRINK_NUMBER	128
#define LAST_AGE_WEIGHT			30
#define SAME_AGE_REGION			1024
#define DEF_HOT_DATA_AGE_THRESHOLD	262144
#define DEF_WARM_DATA_AGE_THRESHOLD	2621440
#define F2FS_EXTENT_AGE_INVALID		ULLONG_MAX
''',1)

if "struct age_extent_info" not in s:
    marker=r'''struct extent_tree {
	nid_t ino;			/* inode number */
'''
    a=s.find(marker)
    if a < 0:
        raise RuntimeError("f2fs.h: struct extent_tree start missing")
    b=s.find("\n};",a)
    if b < 0:
        raise RuntimeError("f2fs.h: struct extent_tree end missing")
    b += 3
    age_structs=r'''

struct age_extent_info {
	unsigned int fofs;
	unsigned int len;
	unsigned long long age;
	unsigned long long last_blocks;
};

struct age_extent_node {
	struct rb_node rb_node;
	struct age_extent_info ei;
	struct list_head list;
	struct age_extent_tree *et;
};

struct age_extent_tree {
	nid_t ino;
	struct rb_root_cached root;
	struct age_extent_node *cached_en;
	struct list_head list;
	rwlock_t lock;
	atomic_t node_cnt;
};
'''
    s=s[:b]+age_structs+s[b:]

if "struct age_extent_tree *age_extent_tree;" not in s:
    s=s.replace("struct extent_tree *extent_tree;\t/* cached extent_tree entry */",
                "struct extent_tree *extent_tree;\t/* cached read extent_tree entry */\n"
                "\tstruct age_extent_tree *age_extent_tree;\t/* cached block-age tree */",1)

if "age_extent_tree_root" not in s:
    anchor="\tatomic_t total_ext_node;\t\t/* extent info count */"
    if anchor not in s:
        raise RuntimeError("f2fs.h: sbi extent counter anchor missing")
    s=s.replace(anchor,anchor+r'''

	/* Samsung-native parallel block-age extent cache */
	struct radix_tree_root age_extent_tree_root;
	struct mutex age_extent_tree_lock;
	struct list_head age_extent_list;
	spinlock_t age_extent_lock;
	atomic_t total_age_ext_tree;
	struct list_head age_zombie_list;
	atomic_t total_age_zombie_tree;
	atomic_t total_age_ext_node;
	atomic64_t allocated_data_blocks;
	unsigned int hot_data_age_threshold;
	unsigned int warm_data_age_threshold;
''',1)

if "f2fs_init_age_extent_tree" not in s:
    anchor="void f2fs_init_extent_cache_info(struct f2fs_sb_info *sbi);"
    if anchor not in s:
        raise RuntimeError("f2fs.h: extent-cache prototype anchor missing")
    s=s.replace(anchor,r'''/* block-age extent cache ops */
void f2fs_init_age_extent_tree(struct inode *inode);
bool f2fs_lookup_age_extent_cache(struct inode *inode, pgoff_t pgofs,
			struct age_extent_info *ei);
void f2fs_update_age_extent_cache(struct dnode_of_data *dn);
void f2fs_update_age_extent_cache_range(struct dnode_of_data *dn,
			pgoff_t fofs, unsigned int len);
unsigned int f2fs_shrink_age_extent_tree(struct f2fs_sb_info *sbi,
			int nr_shrink);
unsigned long f2fs_count_age_extent_cache(struct f2fs_sb_info *sbi);

'''+anchor,1)

wr("f2fs.h",s)

# ---- extent_cache.c -------------------------------------------------------
s=rd("extent_cache.c")
if "age_extent_tree_slab" not in s:
    s=s.replace("static struct kmem_cache *extent_node_slab;",
                "static struct kmem_cache *extent_node_slab;\n"
                "static struct kmem_cache *age_extent_tree_slab;\n"
                "static struct kmem_cache *age_extent_node_slab;",1)

# Rename read-only public lifecycle functions so wrappers can service both
# caches without perturbing the existing read-cache internals.
if "static unsigned int f2fs_destroy_read_extent_node" not in s:
    s=s.replace("unsigned int f2fs_destroy_extent_node(struct inode *inode)",
                "static unsigned int f2fs_destroy_read_extent_node(struct inode *inode)",1)
    s=s.replace("node_cnt = f2fs_destroy_extent_node(inode);",
                "node_cnt = f2fs_destroy_read_extent_node(inode);",1)
if "static void f2fs_drop_read_extent_tree" not in s:
    s=s.replace("void f2fs_drop_extent_tree(struct inode *inode)",
                "static void f2fs_drop_read_extent_tree(struct inode *inode)",1)
if "static void f2fs_destroy_read_extent_tree" not in s:
    s=s.replace("void f2fs_destroy_extent_tree(struct inode *inode)",
                "static void f2fs_destroy_read_extent_tree(struct inode *inode)",1)

if "/* P5A block-age extent cache */" not in s:
    marker="void f2fs_init_extent_cache_info(struct f2fs_sb_info *sbi)"
    pos=s.find(marker)
    if pos < 0:
        raise RuntimeError("extent_cache.c: init cache info anchor missing")

    age_code=r'''
/* P5A block-age extent cache */
static bool f2fs_may_age_extent_tree(struct inode *inode)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);

	if (!test_opt(sbi, AGE_EXTENT_CACHE))
		return false;
	if (list_empty(&sbi->s_list))
		return false;
	if (is_inode_flag_set(inode, FI_COMPRESSED_FILE))
		return false;
	if (file_is_cold(inode))
		return false;

	return S_ISREG(inode->i_mode) || S_ISDIR(inode->i_mode);
}

static inline unsigned long long age_abs_diff(unsigned long long a,
					      unsigned long long b)
{
	return a >= b ? a - b : b - a;
}

static bool age_extent_mergeable(struct age_extent_info *back,
				 struct age_extent_info *front)
{
	return back->fofs + back->len == front->fofs &&
		age_abs_diff(back->age, front->age) <= SAME_AGE_REGION &&
		age_abs_diff(back->last_blocks, front->last_blocks) <=
							SAME_AGE_REGION;
}

static struct age_extent_node *age_attach_node(struct f2fs_sb_info *sbi,
		struct age_extent_tree *et, struct age_extent_info *ei,
		struct rb_node *parent, struct rb_node **p, bool leftmost)
{
	struct age_extent_node *en;

	en = kmem_cache_alloc(age_extent_node_slab, GFP_ATOMIC);
	if (!en)
		return NULL;

	en->ei = *ei;
	INIT_LIST_HEAD(&en->list);
	en->et = et;
	rb_link_node(&en->rb_node, parent, p);
	rb_insert_color_cached(&en->rb_node, &et->root, leftmost);
	atomic_inc(&et->node_cnt);
	atomic_inc(&sbi->total_age_ext_node);
	return en;
}

static void age_detach_node(struct f2fs_sb_info *sbi,
		struct age_extent_tree *et, struct age_extent_node *en)
{
	rb_erase_cached(&en->rb_node, &et->root);
	atomic_dec(&et->node_cnt);
	atomic_dec(&sbi->total_age_ext_node);
	if (et->cached_en == en)
		et->cached_en = NULL;
	kmem_cache_free(age_extent_node_slab, en);
}

static void age_release_node(struct f2fs_sb_info *sbi,
		struct age_extent_tree *et, struct age_extent_node *en)
{
	spin_lock(&sbi->age_extent_lock);
	if (!list_empty(&en->list))
		list_del_init(&en->list);
	spin_unlock(&sbi->age_extent_lock);
	age_detach_node(sbi, et, en);
}

static struct age_extent_tree *age_grab_tree(struct inode *inode)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_tree *et;
	nid_t ino = inode->i_ino;

	mutex_lock(&sbi->age_extent_tree_lock);
	et = radix_tree_lookup(&sbi->age_extent_tree_root, ino);
	if (!et) {
		et = f2fs_kmem_cache_alloc(age_extent_tree_slab, GFP_NOFS);
		f2fs_radix_tree_insert(&sbi->age_extent_tree_root, ino, et);
		memset(et, 0, sizeof(*et));
		et->ino = ino;
		et->root = RB_ROOT_CACHED;
		rwlock_init(&et->lock);
		INIT_LIST_HEAD(&et->list);
		atomic_set(&et->node_cnt, 0);
		atomic_inc(&sbi->total_age_ext_tree);
	} else if (!list_empty(&et->list)) {
		atomic_dec(&sbi->total_age_zombie_tree);
		list_del_init(&et->list);
	}
	mutex_unlock(&sbi->age_extent_tree_lock);

	F2FS_I(inode)->age_extent_tree = et;
	return et;
}

void f2fs_init_age_extent_tree(struct inode *inode)
{
	if (!f2fs_may_age_extent_tree(inode))
		return;
	age_grab_tree(inode);
}

static struct age_extent_node *age_lookup_node(struct inode *inode,
					       pgoff_t pgofs)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_tree *et = F2FS_I(inode)->age_extent_tree;
	struct age_extent_node *en;

	if (!et)
		return NULL;

	read_lock(&et->lock);
	en = (struct age_extent_node *)f2fs_lookup_rb_tree(&et->root,
			(struct rb_entry *)et->cached_en, pgofs);
	if (en) {
		spin_lock(&sbi->age_extent_lock);
		if (!list_empty(&en->list)) {
			list_move_tail(&en->list, &sbi->age_extent_list);
			et->cached_en = en;
		}
		spin_unlock(&sbi->age_extent_lock);
	}
	read_unlock(&et->lock);
	return en;
}

bool f2fs_lookup_age_extent_cache(struct inode *inode, pgoff_t pgofs,
				  struct age_extent_info *ei)
{
	struct age_extent_tree *et;
	struct age_extent_node *en;
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	bool found = false;

	if (!f2fs_may_age_extent_tree(inode))
		return false;
	et = F2FS_I(inode)->age_extent_tree;
	if (!et)
		return false;

	read_lock(&et->lock);
	en = (struct age_extent_node *)f2fs_lookup_rb_tree(&et->root,
			(struct rb_entry *)et->cached_en, pgofs);
	if (en) {
		*ei = en->ei;
		found = true;
		spin_lock(&sbi->age_extent_lock);
		if (!list_empty(&en->list)) {
			list_move_tail(&en->list, &sbi->age_extent_list);
			et->cached_en = en;
		}
		spin_unlock(&sbi->age_extent_lock);
	}
	read_unlock(&et->lock);
	return found;
}

static struct age_extent_node *age_insert_node(struct f2fs_sb_info *sbi,
		struct age_extent_tree *et, struct age_extent_info *ei,
		struct rb_node **insert_p, struct rb_node *insert_parent,
		bool leftmost)
{
	struct rb_node **p;
	struct rb_node *parent = NULL;
	struct age_extent_node *en;

	if (insert_p && insert_parent) {
		p = insert_p;
		parent = insert_parent;
	} else {
		leftmost = true;
		p = f2fs_lookup_rb_tree_for_insert(sbi, &et->root, &parent,
						  ei->fofs, &leftmost);
	}

	en = age_attach_node(sbi, et, ei, parent, p, leftmost);
	if (!en)
		return NULL;

	spin_lock(&sbi->age_extent_lock);
	list_add_tail(&en->list, &sbi->age_extent_list);
	et->cached_en = en;
	spin_unlock(&sbi->age_extent_lock);
	return en;
}

static struct age_extent_node *age_try_merge(struct f2fs_sb_info *sbi,
		struct age_extent_tree *et, struct age_extent_info *ei,
		struct age_extent_node *prev, struct age_extent_node *next)
{
	struct age_extent_node *en = NULL;

	if (prev && age_extent_mergeable(&prev->ei, ei)) {
		prev->ei.len += ei->len;
		*ei = prev->ei;
		en = prev;
	}
	if (next && age_extent_mergeable(ei, &next->ei)) {
		next->ei.fofs = ei->fofs;
		next->ei.len += ei->len;
		next->ei.age = ei->age;
		next->ei.last_blocks = ei->last_blocks;
		if (en)
			age_release_node(sbi, et, prev);
		en = next;
	}
	if (en) {
		spin_lock(&sbi->age_extent_lock);
		if (!list_empty(&en->list)) {
			list_move_tail(&en->list, &sbi->age_extent_list);
			et->cached_en = en;
		}
		spin_unlock(&sbi->age_extent_lock);
	}
	return en;
}

static void age_update_range(struct inode *inode,
			     struct age_extent_info *tei)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_tree *et = F2FS_I(inode)->age_extent_tree;
	struct age_extent_node *en = NULL, *en1 = NULL;
	struct age_extent_node *prev = NULL, *next = NULL;
	struct age_extent_info ei, old;
	struct rb_node **insert_p = NULL, *insert_parent = NULL;
	unsigned int end;
	bool leftmost = false;

	if (!et || !tei->len)
		return;
	end = tei->fofs + tei->len;

	write_lock(&et->lock);

	en = (struct age_extent_node *)f2fs_lookup_rb_tree_ret(&et->root,
			(struct rb_entry *)et->cached_en, tei->fofs,
			(struct rb_entry **)&prev, (struct rb_entry **)&next,
			&insert_p, &insert_parent, false, &leftmost);
	if (!en)
		en = next;

	while (en && en->ei.fofs < end) {
		unsigned int org_end;
		int parts = 0;

		en1 = next = NULL;
		old = en->ei;
		org_end = old.fofs + old.len;
		f2fs_bug_on(sbi, tei->fofs >= org_end);

		if (tei->fofs > old.fofs) {
			en->ei.len = tei->fofs - old.fofs;
			prev = en;
			parts = 1;
		}

		if (end < org_end) {
			if (parts) {
				ei = old;
				ei.fofs = end;
				ei.len = org_end - end;
				en1 = age_insert_node(sbi, et, &ei,
						     NULL, NULL, true);
				next = en1;
			} else {
				en->ei.fofs = end;
				en->ei.len = org_end - end;
				next = en;
			}
			parts++;
		}

		if (!next) {
			struct rb_node *node = rb_next(&en->rb_node);
			next = rb_entry_safe(node, struct age_extent_node,
					     rb_node);
		}

		if (!parts)
			age_release_node(sbi, et, en);

		if (parts != 1) {
			insert_p = NULL;
			insert_parent = NULL;
		}
		en = next;
	}

	if (tei->last_blocks != F2FS_EXTENT_AGE_INVALID) {
		ei = *tei;
		if (!age_try_merge(sbi, et, &ei, prev, next))
			age_insert_node(sbi, et, &ei, insert_p,
					insert_parent, leftmost);
	}

	write_unlock(&et->lock);
}

static unsigned long long age_weighted_average(unsigned long long new_age,
					       unsigned long long old_age)
{
	unsigned int rem_old, rem_new;
	unsigned long long res;

	res = div_u64_rem(new_age, 100, &rem_new) *
				(100 - LAST_AGE_WEIGHT) +
	      div_u64_rem(old_age, 100, &rem_old) * LAST_AGE_WEIGHT;
	if (rem_new)
		res += rem_new * (100 - LAST_AGE_WEIGHT) / 100;
	if (rem_old)
		res += rem_old * LAST_AGE_WEIGHT / 100;
	return res;
}

static int age_get_new(struct inode *inode, pgoff_t fofs, block_t blkaddr,
		       struct age_extent_info *ei)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_info old;
	loff_t f_size = i_size_read(inode);
	unsigned long long cur_blocks =
			(unsigned long long)atomic64_read(&sbi->allocated_data_blocks);

	ei->fofs = fofs;
	ei->len = 1;

	if ((f_size >> PAGE_SHIFT) == fofs &&
	    (f_size & (PAGE_SIZE - 1)) && blkaddr == NEW_ADDR)
		return -EINVAL;

	if (f2fs_lookup_age_extent_cache(inode, fofs, &old)) {
		unsigned long long cur_age;

		if (cur_blocks >= old.last_blocks)
			cur_age = cur_blocks - old.last_blocks;
		else
			cur_age = (ULLONG_MAX - 1) - old.last_blocks + cur_blocks;

		ei->age = old.age ? age_weighted_average(cur_age, old.age) :
				   cur_age;
		ei->last_blocks = cur_blocks;
		return 0;
	}

	f2fs_bug_on(sbi, blkaddr == NULL_ADDR);
	if (__is_valid_data_blkaddr(blkaddr) &&
	    !f2fs_is_valid_blkaddr(sbi, blkaddr, DATA_GENERIC_ENHANCE)) {
		f2fs_bug_on(sbi, 1);
		return -EINVAL;
	}

	ei->age = 0;
	ei->last_blocks = cur_blocks;
	return 0;
}

void f2fs_update_age_extent_cache(struct dnode_of_data *dn)
{
	struct age_extent_info ei;
	pgoff_t fofs;

	if (!f2fs_may_age_extent_tree(dn->inode))
		return;

	fofs = f2fs_start_bidx_of_node(ofs_of_node(dn->node_page), dn->inode) +
								dn->ofs_in_node;
	if (age_get_new(dn->inode, fofs, dn->data_blkaddr, &ei))
		return;
	age_update_range(dn->inode, &ei);
}

void f2fs_update_age_extent_cache_range(struct dnode_of_data *dn,
					pgoff_t fofs, unsigned int len)
{
	struct age_extent_info ei = {
		.fofs = fofs,
		.len = len,
		.last_blocks = F2FS_EXTENT_AGE_INVALID,
	};

	if (!f2fs_may_age_extent_tree(dn->inode))
		return;
	age_update_range(dn->inode, &ei);
}

static unsigned int age_free_tree(struct f2fs_sb_info *sbi,
				   struct age_extent_tree *et)
{
	struct rb_node *node, *next;
	struct age_extent_node *en;
	unsigned int count = atomic_read(&et->node_cnt);

	node = rb_first_cached(&et->root);
	while (node) {
		next = rb_next(node);
		en = rb_entry(node, struct age_extent_node, rb_node);
		age_release_node(sbi, et, en);
		node = next;
	}
	return count - atomic_read(&et->node_cnt);
}

unsigned long f2fs_count_age_extent_cache(struct f2fs_sb_info *sbi)
{
	return atomic_read(&sbi->total_age_zombie_tree) +
	       atomic_read(&sbi->total_age_ext_node);
}

unsigned int f2fs_shrink_age_extent_tree(struct f2fs_sb_info *sbi,
					 int nr_shrink)
{
	struct age_extent_tree *et, *next;
	struct age_extent_node *en;
	unsigned int node_cnt = 0, tree_cnt = 0;
	int remained;

	if (!test_opt(sbi, AGE_EXTENT_CACHE))
		return 0;

	if (!atomic_read(&sbi->total_age_zombie_tree))
		goto free_node;
	if (!mutex_trylock(&sbi->age_extent_tree_lock))
		goto out;

	list_for_each_entry_safe(et, next, &sbi->age_zombie_list, list) {
		if (atomic_read(&et->node_cnt)) {
			write_lock(&et->lock);
			node_cnt += age_free_tree(sbi, et);
			write_unlock(&et->lock);
		}
		f2fs_bug_on(sbi, atomic_read(&et->node_cnt));
		list_del_init(&et->list);
		radix_tree_delete(&sbi->age_extent_tree_root, et->ino);
		kmem_cache_free(age_extent_tree_slab, et);
		atomic_dec(&sbi->total_age_ext_tree);
		atomic_dec(&sbi->total_age_zombie_tree);
		tree_cnt++;
		if (node_cnt + tree_cnt >= nr_shrink)
			goto unlock_out;
		cond_resched();
	}
	mutex_unlock(&sbi->age_extent_tree_lock);

free_node:
	if (!mutex_trylock(&sbi->age_extent_tree_lock))
		goto out;
	remained = nr_shrink - (node_cnt + tree_cnt);

	spin_lock(&sbi->age_extent_lock);
	for (; remained > 0; remained--) {
		if (list_empty(&sbi->age_extent_list))
			break;
		en = list_first_entry(&sbi->age_extent_list,
				      struct age_extent_node, list);
		et = en->et;
		if (!write_trylock(&et->lock)) {
			list_move_tail(&en->list, &sbi->age_extent_list);
			continue;
		}
		list_del_init(&en->list);
		spin_unlock(&sbi->age_extent_lock);
		age_detach_node(sbi, et, en);
		write_unlock(&et->lock);
		node_cnt++;
		spin_lock(&sbi->age_extent_lock);
	}
	spin_unlock(&sbi->age_extent_lock);

unlock_out:
	mutex_unlock(&sbi->age_extent_tree_lock);
out:
	return node_cnt + tree_cnt;
}

static unsigned int f2fs_destroy_age_extent_node(struct inode *inode)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_tree *et = F2FS_I(inode)->age_extent_tree;
	unsigned int cnt = 0;

	if (!et || !atomic_read(&et->node_cnt))
		return 0;
	write_lock(&et->lock);
	cnt = age_free_tree(sbi, et);
	write_unlock(&et->lock);
	return cnt;
}

static void f2fs_drop_age_extent_tree(struct inode *inode)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_tree *et = F2FS_I(inode)->age_extent_tree;

	if (!et)
		return;
	write_lock(&et->lock);
	age_free_tree(sbi, et);
	write_unlock(&et->lock);
}

static void f2fs_destroy_age_extent_tree(struct inode *inode)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_tree *et = F2FS_I(inode)->age_extent_tree;

	if (!et)
		return;

	if (inode->i_nlink && !is_bad_inode(inode) &&
	    atomic_read(&et->node_cnt)) {
		mutex_lock(&sbi->age_extent_tree_lock);
		list_add_tail(&et->list, &sbi->age_zombie_list);
		atomic_inc(&sbi->total_age_zombie_tree);
		mutex_unlock(&sbi->age_extent_tree_lock);
		F2FS_I(inode)->age_extent_tree = NULL;
		return;
	}

	f2fs_destroy_age_extent_node(inode);
	mutex_lock(&sbi->age_extent_tree_lock);
	f2fs_bug_on(sbi, atomic_read(&et->node_cnt));
	radix_tree_delete(&sbi->age_extent_tree_root, inode->i_ino);
	kmem_cache_free(age_extent_tree_slab, et);
	atomic_dec(&sbi->total_age_ext_tree);
	mutex_unlock(&sbi->age_extent_tree_lock);
	F2FS_I(inode)->age_extent_tree = NULL;
}

unsigned int f2fs_destroy_extent_node(struct inode *inode)
{
	return f2fs_destroy_read_extent_node(inode) +
	       f2fs_destroy_age_extent_node(inode);
}

void f2fs_drop_extent_tree(struct inode *inode)
{
	f2fs_drop_read_extent_tree(inode);
	f2fs_drop_age_extent_tree(inode);
}

void f2fs_destroy_extent_tree(struct inode *inode)
{
	f2fs_destroy_read_extent_tree(inode);
	f2fs_destroy_age_extent_tree(inode);
}

'''
    s=s[:pos]+age_code+s[pos:]

# Extend init state.
if "INIT_RADIX_TREE(&sbi->age_extent_tree_root" not in s:
    anchor="\tatomic_set(&sbi->total_ext_node, 0);\n}"
    if anchor not in s:
        raise RuntimeError("extent_cache.c: read cache init tail missing")
    s=s.replace(anchor,r'''	atomic_set(&sbi->total_ext_node, 0);

	INIT_RADIX_TREE(&sbi->age_extent_tree_root, GFP_NOIO);
	mutex_init(&sbi->age_extent_tree_lock);
	INIT_LIST_HEAD(&sbi->age_extent_list);
	spin_lock_init(&sbi->age_extent_lock);
	atomic_set(&sbi->total_age_ext_tree, 0);
	INIT_LIST_HEAD(&sbi->age_zombie_list);
	atomic_set(&sbi->total_age_zombie_tree, 0);
	atomic_set(&sbi->total_age_ext_node, 0);
	atomic64_set(&sbi->allocated_data_blocks, 0);
	sbi->hot_data_age_threshold = DEF_HOT_DATA_AGE_THRESHOLD;
	sbi->warm_data_age_threshold = DEF_WARM_DATA_AGE_THRESHOLD;
}''',1)

# Extend slab create/destroy.
if '"f2fs_age_extent_tree"' not in s:
    old=r'''	extent_node_slab = f2fs_kmem_cache_create("f2fs_extent_node",
			sizeof(struct extent_node));
	if (!extent_node_slab) {
		kmem_cache_destroy(extent_tree_slab);
		return -ENOMEM;
	}
	return 0;
}'''
    new=r'''	extent_node_slab = f2fs_kmem_cache_create("f2fs_extent_node",
			sizeof(struct extent_node));
	if (!extent_node_slab) {
		kmem_cache_destroy(extent_tree_slab);
		return -ENOMEM;
	}

	age_extent_tree_slab = f2fs_kmem_cache_create("f2fs_age_extent_tree",
			sizeof(struct age_extent_tree));
	if (!age_extent_tree_slab)
		goto free_read_node;
	age_extent_node_slab = f2fs_kmem_cache_create("f2fs_age_extent_node",
			sizeof(struct age_extent_node));
	if (!age_extent_node_slab)
		goto free_age_tree;
	return 0;

free_age_tree:
	kmem_cache_destroy(age_extent_tree_slab);
free_read_node:
	kmem_cache_destroy(extent_node_slab);
	kmem_cache_destroy(extent_tree_slab);
	return -ENOMEM;
}'''
    if old not in s:
        raise RuntimeError("extent_cache.c: slab create block changed")
    s=s.replace(old,new,1)

if "kmem_cache_destroy(age_extent_node_slab);" not in s:
    old=r'''void f2fs_destroy_extent_cache(void)
{
	kmem_cache_destroy(extent_node_slab);
	kmem_cache_destroy(extent_tree_slab);
}'''
    new=r'''void f2fs_destroy_extent_cache(void)
{
	kmem_cache_destroy(age_extent_node_slab);
	kmem_cache_destroy(age_extent_tree_slab);
	kmem_cache_destroy(extent_node_slab);
	kmem_cache_destroy(extent_tree_slab);
}'''
    if old not in s:
        raise RuntimeError("extent_cache.c: destroy slab block changed")
    s=s.replace(old,new,1)

wr("extent_cache.c",s)

# ---- inode.c --------------------------------------------------------------
if "f2fs_init_age_extent_tree(inode);" not in rd("inode.c"):
    rep("inode.c",
        "if (f2fs_init_extent_tree(inode, &ri->i_ext))\n\t\tset_page_dirty(node_page);",
        "if (f2fs_init_extent_tree(inode, &ri->i_ext))\n"
        "\t\tset_page_dirty(node_page);\n"
        "\tf2fs_init_age_extent_tree(inode);")

# ---- file.c: corrected truncation and zero-range invalidation -------------
if "f2fs_update_age_extent_cache_range(dn, fofs, len);" not in rd("file.c"):
    rep("file.c",
        "\t\tf2fs_update_extent_cache_range(dn, fofs, 0, len);\n"
        "\t\tdec_valid_block_count(sbi, dn->inode, nr_free);",
        "\t\tf2fs_update_extent_cache_range(dn, fofs, 0, len);\n"
        "\t\tf2fs_update_age_extent_cache_range(dn, fofs, len);\n"
        "\t\tdec_valid_block_count(sbi, dn->inode, nr_free);")
if "f2fs_update_age_extent_cache_range(dn, start, index - start);" not in rd("file.c"):
    rep("file.c",
        "\tf2fs_update_extent_cache_range(dn, start, 0, index - start);\n\n\treturn ret;",
        "\tf2fs_update_extent_cache_range(dn, start, 0, index - start);\n"
        "\tf2fs_update_age_extent_cache_range(dn, start, index - start);\n\n"
        "\treturn ret;")

# ---- node.h / node.c memory accounting -----------------------------------
if "AGE_EXTENT_CACHE" not in rd("node.h"):
    rep("node.h",
        "\tEXTENT_CACHE,\t/* indicates extent cache */",
        "\tEXTENT_CACHE,\t/* indicates read extent cache */\n"
        "\tAGE_EXTENT_CACHE,\t/* indicates block-age extent cache */")

if "total_age_ext_tree" not in rd("node.c"):
    old=r'''} else if (type == EXTENT_CACHE) {
		mem_size = (atomic_read(&sbi->total_ext_tree) *
				sizeof(struct extent_tree) +
				atomic_read(&sbi->total_ext_node) *
				sizeof(struct extent_node)) >> PAGE_SHIFT;
		res = mem_size < ((avail_ram * nm_i->ram_thresh / 100) >> 1);
'''
    new=old+r'''	} else if (type == AGE_EXTENT_CACHE) {
		mem_size = (atomic_read(&sbi->total_age_ext_tree) *
				sizeof(struct age_extent_tree) +
				atomic_read(&sbi->total_age_ext_node) *
				sizeof(struct age_extent_node)) >> PAGE_SHIFT;
		res = mem_size < ((avail_ram * nm_i->ram_thresh / 100) >> 2);
'''
    if old not in rd("node.c"):
        raise RuntimeError("node.c: extent memory branch changed")
    rep("node.c",old,new)

# ---- shrinker.c -----------------------------------------------------------
s=rd("shrinker.c")
if "f2fs_count_age_extent_cache" not in s:
    s=s.replace("\t\t/* count extent cache entries */\n"
                "\t\tcount += __count_extent_cache(sbi);",
                "\t\t/* count read + block-age extent cache entries */\n"
                "\t\tcount += __count_extent_cache(sbi);\n"
                "\t\tcount += f2fs_count_age_extent_cache(sbi);",1)
    s=s.replace("\t\t/* shrink extent cache entries */\n"
                "\t\tfreed += f2fs_shrink_extent_tree(sbi, nr >> 1);",
                "\t\t/* shrink read + block-age extent cache entries */\n"
                "\t\tfreed += f2fs_shrink_age_extent_tree(sbi, nr >> 2);\n"
                "\t\tfreed += f2fs_shrink_extent_tree(sbi, nr >> 2);",1)
    s=s.replace("\tf2fs_shrink_extent_tree(sbi, __count_extent_cache(sbi));",
                "\tf2fs_shrink_extent_tree(sbi, __count_extent_cache(sbi));\n"
                "\tf2fs_shrink_age_extent_tree(sbi,\n"
                "\t\t\tf2fs_count_age_extent_cache(sbi));",1)
wr("shrinker.c",s)

# ---- super.c mount option, show, and remount protection -------------------
s=rd("super.c")
if "Opt_age_extent_cache" not in s:
    s=s.replace("\tOpt_atgc,\n\tOpt_err,",
                "\tOpt_atgc,\n\tOpt_age_extent_cache,\n\tOpt_err,",1)
    s=s.replace('\t{Opt_atgc, "atgc"},\n\t{Opt_err, NULL},',
                '\t{Opt_atgc, "atgc"},\n'
                '\t{Opt_age_extent_cache, "age_extent_cache"},\n'
                '\t{Opt_err, NULL},',1)
    s=s.replace("\t\tcase Opt_atgc:\n\t\t\tset_opt(sbi, ATGC);\n\t\t\tbreak;\n\t\tdefault:",
                "\t\tcase Opt_atgc:\n\t\t\tset_opt(sbi, ATGC);\n\t\t\tbreak;\n"
                "\t\tcase Opt_age_extent_cache:\n"
                "\t\t\tset_opt(sbi, AGE_EXTENT_CACHE);\n"
                "\t\t\tbreak;\n\t\tdefault:",1)
    show_anchor='\tif (test_opt(sbi, ATGC))\n\t\tseq_puts(seq, ",atgc");\n'
    if show_anchor not in s:
        raise RuntimeError("super.c: ATGC show-options anchor missing")
    s=s.replace(show_anchor,show_anchor+
                '\tif (test_opt(sbi, AGE_EXTENT_CACHE))\n'
                '\t\tseq_puts(seq, ",age_extent_cache");\n',1)

# Add remount state tracking + reject runtime toggling, mirroring upstream.
if "no_age_extent_cache" not in s:
    m=re.search(r"(\tbool no_atgc = !test_opt\(sbi, ATGC\);\n)",s)
    if not m:
        raise RuntimeError("super.c: remount no_atgc anchor missing")
    s=s[:m.end()]+"\tbool no_age_extent_cache = !test_opt(sbi, AGE_EXTENT_CACHE);\n"+s[m.end():]

    # Place next to ATGC/extent-cache remount restrictions.
    anchor='\t/* disallow enable/disable extent_cache dynamically */'
    pos=s.find(anchor)
    if pos < 0:
        raise RuntimeError("super.c: extent remount restriction anchor missing")
    # Find the end of that if block by locating following blank line.
    after=s.find("\n\n",pos)
    if after < 0:
        raise RuntimeError("super.c: extent remount block end missing")
    block=r'''

	/* disallow enable/disable age_extent_cache dynamically */
	if (no_age_extent_cache == !!test_opt(sbi, AGE_EXTENT_CACHE)) {
		err = -EINVAL;
		f2fs_warn(sbi, "switch age_extent_cache option is not allowed");
		goto restore_opts;
	}
'''
    s=s[:after]+block+s[after:]
wr("super.c",s)

# ---- sysfs.c thresholds ---------------------------------------------------
s=rd("sysfs.c")
if "hot_data_age_threshold" not in s:
    marker='\tif (!strcmp(a->attr.name, "iostat_enable")) {'
    if marker not in s:
        raise RuntimeError("sysfs.c: threshold store insertion anchor missing")
    block=r'''	if (!strcmp(a->attr.name, "hot_data_age_threshold")) {
		if (t == 0 || t >= sbi->warm_data_age_threshold)
			return -EINVAL;
		if (t == *ui)
			return count;
		*ui = (unsigned int)t;
		return count;
	}

	if (!strcmp(a->attr.name, "warm_data_age_threshold")) {
		if (t <= sbi->hot_data_age_threshold)
			return -EINVAL;
		if (t == *ui)
			return count;
		*ui = (unsigned int)t;
		return count;
	}

'''
    s=s.replace(marker,block+marker,1)

    decl='F2FS_RW_ATTR(F2FS_SBI, f2fs_sb_info, gc_urgent, gc_mode);\n'
    if decl not in s:
        raise RuntimeError("sysfs.c: attr declaration anchor missing")
    s=s.replace(decl,decl+
        'F2FS_RW_ATTR(F2FS_SBI, f2fs_sb_info, hot_data_age_threshold, hot_data_age_threshold);\n'
        'F2FS_RW_ATTR(F2FS_SBI, f2fs_sb_info, warm_data_age_threshold, warm_data_age_threshold);\n',1)

    alist='\tATTR_LIST(gc_urgent),\n'
    if alist not in s:
        raise RuntimeError("sysfs.c: attr list anchor missing")
    s=s.replace(alist,alist+
        '\tATTR_LIST(hot_data_age_threshold),\n'
        '\tATTR_LIST(warm_data_age_threshold),\n',1)
wr("sysfs.c",s)

# ---- segment.c ------------------------------------------------------------
s=rd("segment.c")

if "AGE_EXTENT_CACHE_SHRINK_NUMBER" not in s:
    # P3 tree still has vendor read-cache shrink name.
    old=r'''	if (!f2fs_available_free_memory(sbi, EXTENT_CACHE))
		f2fs_shrink_extent_tree(sbi, EXTENT_CACHE_SHRINK_NUMBER);
'''
    if old not in s:
        raise RuntimeError("segment.c: background extent shrink anchor missing")
    s=s.replace(old,old+
r'''
	if (!f2fs_available_free_memory(sbi, AGE_EXTENT_CACHE))
		f2fs_shrink_age_extent_tree(sbi,
				AGE_EXTENT_CACHE_SHRINK_NUMBER);
''',1)

if "static int __get_age_segment_type" not in s:
    marker="static int __get_segment_type_6(struct f2fs_io_info *fio)"
    pos=s.find(marker)
    if pos < 0:
        raise RuntimeError("segment.c: segment type 6 anchor missing")
    helper=r'''static int __get_age_segment_type(struct inode *inode, pgoff_t pgofs)
{
	struct f2fs_sb_info *sbi = F2FS_I_SB(inode);
	struct age_extent_info ei;

	if (f2fs_lookup_age_extent_cache(inode, pgofs, &ei)) {
		if (!ei.age)
			return NO_CHECK_TYPE;
		if (ei.age <= sbi->hot_data_age_threshold)
			return CURSEG_HOT_DATA;
		if (ei.age <= sbi->warm_data_age_threshold)
			return CURSEG_WARM_DATA;
		return CURSEG_COLD_DATA;
	}
	return NO_CHECK_TYPE;
}

'''
    s=s[:pos]+helper+s[pos:]

# Insert age classification after explicit cold/compressed handling and before
# explicit hot handling. Match P4's ATGC-aware cold-data block.
if "type = __get_age_segment_type(inode, fio->page->index);" not in s:
    # Ensure a local type variable exists.
    pat=r"(static int __get_segment_type_6\(struct f2fs_io_info \*fio\)\n\{\n\tif \(fio->type == DATA\) \{\n\t\tstruct inode \*inode = fio->page->mapping->host;\n)"
    repl=r"\1\t\tint type;\n"
    s,n=re.subn(pat,repl,s,count=1)
    if n != 1:
        raise RuntimeError("segment.c: couldn't add age type local")

    hot_anchor="\t\tif (file_is_hot(inode) ||"
    pos=s.find(hot_anchor,s.find("static int __get_segment_type_6"))
    if pos < 0:
        raise RuntimeError("segment.c: file_is_hot insertion anchor missing")
    age_call=r'''		type = __get_age_segment_type(inode, fio->page->index);
		if (type != NO_CHECK_TYPE)
			return type;

'''
    s=s[:pos]+age_call+s[pos:]

# Increment allocation counter using the real curseg segment type; include
# 2025 overflow handling.
if "atomic64_inc_return(&sbi->allocated_data_blocks)" not in s:
    anchor="\tlocate_dirty_segment(sbi, GET_SEGNO(sbi, *new_blkaddr));\n"
    pos=s.find(anchor,s.find("void f2fs_allocate_data_block"))
    if pos < 0:
        raise RuntimeError("segment.c: allocated block counter anchor missing")
    pos += len(anchor)
    counter=r'''
	if (IS_DATASEG(curseg->seg_type)) {
		unsigned long long new_val;

		new_val = atomic64_inc_return(&sbi->allocated_data_blocks);
		if (unlikely(new_val == ULLONG_MAX))
			atomic64_set(&sbi->allocated_data_blocks, 0);
	}
'''
    s=s[:pos]+counter+s[pos:]

# Capture/update age before old block address is replaced.
if "f2fs_update_age_extent_cache(dn);" not in s:
    anchor="\tf2fs_bug_on(sbi, dn->data_blkaddr == NULL_ADDR);\n"
    pos=s.find(anchor,s.find("void f2fs_outplace_write_data"))
    if pos < 0:
        raise RuntimeError("segment.c: outplace age update anchor missing")
    pos += len(anchor)
    call=r'''	if (fio->io_type == FS_DATA_IO || fio->io_type == FS_CP_DATA_IO)
		f2fs_update_age_extent_cache(dn);
'''
    s=s[:pos]+call+s[pos:]

wr("segment.c",s)

# ---- Structural safety checks --------------------------------------------
checks={
    "f2fs.h":[
        "F2FS_MOUNT_AGE_EXTENT_CACHE",
        "struct age_extent_info",
        "struct age_extent_tree *age_extent_tree;",
        "atomic64_t allocated_data_blocks",
        "DEF_HOT_DATA_AGE_THRESHOLD",
        "F2FS_EXTENT_AGE_INVALID",
        "f2fs_lookup_age_extent_cache",
    ],
    "extent_cache.c":[
        "P5A block-age extent cache",
        "age_weighted_average",
        "f2fs_update_age_extent_cache",
        "f2fs_shrink_age_extent_tree",
        "F2FS_EXTENT_AGE_INVALID",
        "f2fs_destroy_read_extent_node",
    ],
    "segment.c":[
        "__get_age_segment_type",
        "atomic64_inc_return(&sbi->allocated_data_blocks)",
        "f2fs_update_age_extent_cache(dn);",
        "AGE_EXTENT_CACHE_SHRINK_NUMBER",
    ],
    "super.c":[
        "Opt_age_extent_cache",
        '"age_extent_cache"',
        "switch age_extent_cache option is not allowed",
    ],
    "sysfs.c":[
        "hot_data_age_threshold",
        "warm_data_age_threshold",
    ],
    "file.c":[
        "f2fs_update_age_extent_cache_range(dn, fofs, len);",
        "f2fs_update_age_extent_cache_range(dn, start, index - start);",
    ],
    "inode.c":["f2fs_init_age_extent_tree(inode);"],
    "node.h":["AGE_EXTENT_CACHE"],
    "node.c":["total_age_ext_tree"],
    "shrinker.c":["f2fs_count_age_extent_cache"],
}
for rel,needles in checks.items():
    data=rd(rel)
    for needle in needles:
        if needle not in data:
            raise RuntimeError(f"{rel}: missing P5A token: {needle}")

# P5A must remain dormant by default. It may only be enabled by an explicit
# age_extent_cache mount option; do not silently set it in default_options.
superc=rd("super.c")
default_start=superc.find("static void default_options")
default_end=superc.find("\n}\n", default_start)
if default_start >= 0 and default_end >= 0:
    default_block=superc[default_start:default_end]
    if "set_opt(sbi, AGE_EXTENT_CACHE)" in default_block:
        raise RuntimeError("P5A invariant violated: age extent cache default-on")

subprocess.run(["git","diff","--check"],cwd=kernel,check=True)

report="""F2FS P5A block-age extent cache
base=P4 runtime-validated
prerequisite=e7547daccd6a semantics via Samsung-native parallel age tree
feature=71644dff4811
fix_union=ed2724765e58 semantics
fix_truncate=8c0ed062ce27
fix_zero_range=a84153f93980
fix_age_math=b03a41a495df
fix_warm_threshold=9b13a8662ea6
fix_counter_overflow=27bf6a637b76
activation=OFF by default (explicit age_extent_cache mount option only)
"""
(out/"report.txt").write_text(report)
with (out/"block-age-p5a.diff").open("wb") as f:
    subprocess.run(["git","diff","--","fs/f2fs"],cwd=kernel,stdout=f,check=True)

print(report)
print("P5A block-age extent cache adaptation complete")
