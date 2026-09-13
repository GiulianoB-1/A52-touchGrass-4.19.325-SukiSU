#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 89_apply_f2fs_rbtree64_support.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
extent = root / "fs/f2fs/extent_cache.c"
hdr = root / "fs/f2fs/f2fs.h"
segment = root / "fs/f2fs/segment.c"

for path in (extent, hdr, segment):
    if not path.is_file():
        raise SystemExit(f"missing {path}")

upstream_commit = "2e9b2bb250d5d1493eaf36215fbfe2cd76ce4f7c"

# ---------------------------------------------------------------------------
# fs/f2fs/extent_cache.c
# ---------------------------------------------------------------------------
text = extent.read_text()

helper = """struct rb_node **f2fs_lookup_rb_tree_ext(struct f2fs_sb_info *sbi,
					struct rb_root_cached *root,
					struct rb_node **parent,
					unsigned long long key, bool *leftmost)
{
	struct rb_node **p = &root->rb_root.rb_node;
	struct rb_entry *re;

	while (*p) {
		*parent = *p;
		re = rb_entry(*parent, struct rb_entry, rb_node);

		if (key < re->key) {
			p = &(*p)->rb_left;
		} else {
			p = &(*p)->rb_right;
			*leftmost = false;
		}
	}

	return p;
}

"""

insert_anchor = """struct rb_node **f2fs_lookup_rb_tree_for_insert(struct f2fs_sb_info *sbi,
"""

old_check = """bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
						struct rb_root_cached *root)
{
#ifdef CONFIG_F2FS_CHECK_FS
	struct rb_node *cur = rb_first_cached(root), *next;
	struct rb_entry *cur_re, *next_re;

	if (!cur)
		return true;

	while (cur) {
		next = rb_next(cur);
		if (!next)
			return true;

		cur_re = rb_entry(cur, struct rb_entry, rb_node);
		next_re = rb_entry(next, struct rb_entry, rb_node);

		if (cur_re->ofs + cur_re->len > next_re->ofs) {
			f2fs_info(sbi, "inconsistent rbtree, cur(%u, %u) next(%u, %u)",
				  cur_re->ofs, cur_re->len,
				  next_re->ofs, next_re->len);
			return false;
		}

		cur = next;
	}
#endif
	return true;
}
"""

new_check = """bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
				struct rb_root_cached *root, bool check_key)
{
#ifdef CONFIG_F2FS_CHECK_FS
	struct rb_node *cur = rb_first_cached(root), *next;
	struct rb_entry *cur_re, *next_re;

	if (!cur)
		return true;

	while (cur) {
		next = rb_next(cur);
		if (!next)
			return true;

		cur_re = rb_entry(cur, struct rb_entry, rb_node);
		next_re = rb_entry(next, struct rb_entry, rb_node);

		if (check_key) {
			if (cur_re->key > next_re->key) {
				f2fs_info(sbi, "inconsistent rbtree, "
					"cur(%llu) next(%llu)",
					cur_re->key, next_re->key);
				return false;
			}
			goto next;
		}

		if (cur_re->ofs + cur_re->len > next_re->ofs) {
			f2fs_info(sbi, "inconsistent rbtree, cur(%u, %u) next(%u, %u)",
				  cur_re->ofs, cur_re->len,
				  next_re->ofs, next_re->len);
			return false;
		}
next:
		cur = next;
	}
#endif
	return true;
}
"""

extent_already = (
    "f2fs_lookup_rb_tree_ext(" in text
    and "bool check_key" in text
    and "cur_re->key > next_re->key" in text
)

if not extent_already:
    if "f2fs_lookup_rb_tree_ext(" in text or "bool check_key" in text:
        raise SystemExit("partial Phase89 state in extent_cache.c")
    if text.count(insert_anchor) != 1:
        raise SystemExit(f"expected one rb-tree insert anchor, found {text.count(insert_anchor)}")
    if text.count(old_check) != 1:
        raise SystemExit(f"expected one legacy rb-tree consistency function, found {text.count(old_check)}")
    text = text.replace(insert_anchor, helper + insert_anchor, 1)
    text = text.replace(old_check, new_check, 1)
    extent.write_text(text)

# ---------------------------------------------------------------------------
# fs/f2fs/f2fs.h
# ---------------------------------------------------------------------------
text = hdr.read_text()

old_rb = """struct rb_entry {
	struct rb_node rb_node;		/* rb node located in rb-tree */
	unsigned int ofs;		/* start offset of the entry */
	unsigned int len;		/* length of the entry */
};
"""

new_rb = """struct rb_entry {
	struct rb_node rb_node;		/* rb node located in rb-tree */
	union {
		struct {
			unsigned int ofs;	/* start offset of the entry */
			unsigned int len;	/* length of the entry */
		};
		unsigned long long key;		/* 64-bits key */
	};
};
"""

old_proto_anchor = """struct rb_entry *f2fs_lookup_rb_tree(struct rb_root_cached *root,
				struct rb_entry *cached_re, unsigned int ofs);
"""

new_proto_anchor = old_proto_anchor + """struct rb_node **f2fs_lookup_rb_tree_ext(struct f2fs_sb_info *sbi,
				struct rb_root_cached *root,
				struct rb_node **parent,
				unsigned long long key, bool *left_most);
"""

old_check_proto = """bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
						struct rb_root_cached *root);
"""
new_check_proto = """bool f2fs_check_rb_tree_consistence(struct f2fs_sb_info *sbi,
				struct rb_root_cached *root, bool check_key);
"""

hdr_already = (
    "unsigned long long key;" in text
    and "f2fs_lookup_rb_tree_ext(" in text
    and "bool check_key);" in text
)

if not hdr_already:
    if "unsigned long long key;" in text or "f2fs_lookup_rb_tree_ext(" in text:
        raise SystemExit("partial Phase89 state in f2fs.h")
    if text.count(old_rb) != 1:
        raise SystemExit(f"expected one legacy rb_entry definition, found {text.count(old_rb)}")
    if text.count(old_proto_anchor) != 1:
        raise SystemExit(f"expected one rb-tree lookup prototype anchor, found {text.count(old_proto_anchor)}")
    if text.count(old_check_proto) != 1:
        raise SystemExit(f"expected one legacy consistency prototype, found {text.count(old_check_proto)}")
    text = text.replace(old_rb, new_rb, 1)
    text = text.replace(old_proto_anchor, new_proto_anchor, 1)
    text = text.replace(old_check_proto, new_check_proto, 1)
    hdr.write_text(text)

# ---------------------------------------------------------------------------
# fs/f2fs/segment.c
# Existing discard rb-tree uses offset/length ordering, not 64-bit age keys.
# Explicitly mark both callers as check_key=false.
# ---------------------------------------------------------------------------
text = segment.read_text()

old_tail = "&dcc->root));"
new_tail = "&dcc->root, false));"

if old_tail in text:
    count = text.count(old_tail)
    if count != 2:
        raise SystemExit(f"expected two legacy discard consistency calls, found {count}")
    text = text.replace(old_tail, new_tail)
elif text.count(new_tail) != 2:
    raise SystemExit("Phase89 discard consistency callers are neither legacy nor fully converted")

segment.write_text(text)

# ---------------------------------------------------------------------------
# Structural verification
# ---------------------------------------------------------------------------
extent_final = extent.read_text()
hdr_final = hdr.read_text()
segment_final = segment.read_text()

checks = {
    extent: [
        "struct rb_node **f2fs_lookup_rb_tree_ext(struct f2fs_sb_info *sbi,",
        "unsigned long long key, bool *leftmost)",
        "if (key < re->key)",
        "cur_re->key > next_re->key",
        "struct rb_root_cached *root, bool check_key)",
    ],
    hdr: [
        "unsigned long long key;",
        "struct rb_node **f2fs_lookup_rb_tree_ext(struct f2fs_sb_info *sbi,",
        "struct rb_root_cached *root, bool check_key);",
    ],
    segment: [
        "&dcc->root, false));",
    ],
}
for path, needles in checks.items():
    data = path.read_text()
    for needle in needles:
        if needle not in data:
            raise SystemExit(f"missing Phase89 postcondition in {path.name}: {needle}")

if segment_final.count("&dcc->root, false));") != 2:
    raise SystemExit("expected exactly two converted discard-tree consistency checks")

# Keep this prerequisite intentionally narrow. ATGC activation comes later.
for forbidden in (
    "struct atgc_management",
    "CURSEG_ALL_DATA_ATGC",
    "GC_IDLE_AT",
    "AT_SSR",
    "Opt_atgc",
):
    if forbidden in extent_final or forbidden in hdr_final or forbidden in segment_final:
        raise SystemExit(f"Phase89 unexpectedly contains later ATGC symbol: {forbidden}")

report = root.parent.parent / "artifacts" / "phase89-f2fs-rbtree64-support.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=89-f2fs-5.10-atgc-foundation2\n"
    "base=phase88-segment-age-accounting\n"
    f"upstream_commit={upstream_commit}\n"
    "scope=fs/f2fs/extent_cache.c,fs/f2fs/f2fs.h,fs/f2fs/segment.c\n"
    "behavior=64-bit-rbtree-key-support-for-future-segment-age-index\n"
    "discard_tree_mode=legacy-offset-range-check-explicit-false\n"
    "on_disk_format=unchanged\n"
    "mount_options=unchanged\n"
    "atgc=not-yet-enabled\n"
    "encryption=untouched\n"
    "compression=untouched\n"
)
print(report.read_text(), end="")
