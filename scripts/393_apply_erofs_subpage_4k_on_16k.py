#!/usr/bin/env python3
from pathlib import Path
import hashlib
import re
import sys
import urllib.request

MARK = "A52_PHASE393_EROFS_SUBPAGE_4K_ON_16K_V1"
REF_REPO = "EndCredits/kernel_xiaomi_sm7250"
REF_COMMIT = "a264aed3fde518b43ab15ba530556e396502031f"
RAW = f"https://raw.githubusercontent.com/{REF_REPO}/{REF_COMMIT}/"

FILES = [
    "fs/erofs/Kconfig",
    "fs/erofs/Makefile",
    "fs/erofs/compress.h",
    "fs/erofs/data.c",
    "fs/erofs/decompressor.c",
    "fs/erofs/dir.c",
    "fs/erofs/erofs_fs.h",
    "fs/erofs/inode.c",
    "fs/erofs/internal.h",
    "fs/erofs/namei.c",
    "fs/erofs/super.c",
    "fs/erofs/sysfs.c",
    "fs/erofs/xattr.c",
    "fs/erofs/xattr.h",
    "fs/erofs/zdata.c",
    "fs/erofs/zmap.c",
    "fs/erofs/zutil.c",
    "include/trace/events/erofs.h",
]

def fetch(path: str) -> bytes:
    url = RAW + path
    with urllib.request.urlopen(url, timeout=60) as r:
        data = r.read()
    if not data:
        raise SystemExit(f"empty reference file: {url}")
    return data

def replace_once(path: Path, old: str, new: str, label: str):
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()

    manifest = []
    for rel in FILES:
        data = fetch(rel)
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        manifest.append((rel, hashlib.sha256(data).hexdigest(), len(data)))
        print(f"imported {rel} ({len(data)} bytes)")

    # TouchGrass 4.19 still exposes ->readpages rather than the newer
    # ->readahead address_space op carried by the Android-4.19 reference.
    # Keep the modern sub-page compressed backend, but adapt just its
    # compressed readahead wrapper back to the native TouchGrass API.
    zdata = root / "fs/erofs/zdata.c"
    text = zdata.read_text()

    start = text.find("/*\n * Since partial uptodate is still unimplemented for now")
    aops = text.find("const struct address_space_operations z_erofs_aops = {", start)
    if start < 0 or aops < 0:
        raise SystemExit("zdata: unable to locate modern readahead tail")
    end = text.find("};", aops)
    if end < 0:
        raise SystemExit("zdata: unable to locate z_erofs_aops end")
    end += 2

    compat_tail = r'''/*
 * A52_PHASE393_EROFS_SUBPAGE_4K_ON_16K_V1
 *
 * The reference Android-4.19 EROFS backport uses the post-5.x
 * ->readahead API. TouchGrass 4.19 still uses ->readpages.
 *
 * Preserve the modern compressed-subpage core and adapt only the
 * page-cache entrypoint. The readpages path intentionally uses the
 * reference backend's temporary-page/runqueue machinery and avoids
 * introducing generic MM/readahead API changes into this kernel.
 */
static void z_erofs_pcluster_readmore(struct z_erofs_decompress_frontend *f,
				      bool backmost)
{
	struct inode *inode = f->inode;
	struct erofs_map_blocks *map = &f->map;
	erofs_off_t cur, end, headoffset = f->headoffset;
	int err;

	if (backmost) {
		end = headoffset + PAGE_SIZE - 1;
		map->m_la = end;
		err = z_erofs_map_blocks_iter(inode, map,
					      EROFS_GET_BLOCKS_READMORE);
		if (err)
			return;
		end = round_up(end, PAGE_SIZE);
	} else {
		end = round_up(map->m_la, PAGE_SIZE);
		if (!map->m_llen)
			return;
	}

	cur = map->m_la + map->m_llen - 1;
	while ((cur >= end) && (cur < i_size_read(inode))) {
		pgoff_t index = cur >> PAGE_SHIFT;
		struct page *page;

		page = erofs_grab_cache_page_nowait(inode->i_mapping, index);
		if (page) {
			if (PageUptodate(page))
				unlock_page(page);
			else
				(void)z_erofs_do_read_page(f, page, false);
			put_page(page);
		}

		if (cur < PAGE_SIZE)
			break;
		cur = (index << PAGE_SHIFT) - 1;
	}
}

static int z_erofs_readpage(struct file *file, struct page *page)
{
	struct inode *const inode = page->mapping->host;
	struct z_erofs_decompress_frontend f = DECOMPRESS_FRONTEND_INIT(inode);
	int err;

	f.headoffset = (erofs_off_t)page->index << PAGE_SHIFT;

	z_erofs_pcluster_readmore(&f, true);
	err = z_erofs_do_read_page(&f, page, false);
	z_erofs_pcluster_readmore(&f, false);
	z_erofs_pcluster_end(&f);

	err = z_erofs_runqueue(&f, 0) ?: err;
	if (err)
		erofs_err(inode->i_sb, "failed to read, err [%d]", err);

	erofs_put_metabuf(&f.map.buf);
	erofs_release_pages(&f.pagepool);
	return err;
}

static int z_erofs_readpages(struct file *filp,
			     struct address_space *mapping,
			     struct list_head *pages,
			     unsigned int nr_pages)
{
	struct inode *const inode = mapping->host;
	struct z_erofs_decompress_frontend f = DECOMPRESS_FRONTEND_INIT(inode);
	gfp_t gfp = mapping_gfp_constraint(mapping, GFP_KERNEL);
	struct page *head = NULL;
	unsigned int ra_pages = nr_pages;
	LIST_HEAD(discard);

	if (!nr_pages)
		return 0;

	trace_erofs_readpages(mapping->host, lru_to_page(pages),
			      nr_pages, false);
	f.headoffset = (erofs_off_t)lru_to_page(pages)->index << PAGE_SHIFT;

	for (; nr_pages; --nr_pages) {
		struct page *page = lru_to_page(pages);

		prefetchw(&page->flags);
		list_del(&page->lru);

		if (add_to_page_cache_lru(page, mapping, page->index, gfp)) {
			list_add(&page->lru, &discard);
			continue;
		}

		set_page_private(page, (unsigned long)head);
		head = page;
	}

	while (head) {
		struct page *page = head;
		int err;

		head = (void *)page_private(page);
		err = z_erofs_do_read_page(&f, page, true);
		if (err && err != -EINTR)
			erofs_err(inode->i_sb,
				  "readpages error at page %lu @ nid %llu",
				  page->index, EROFS_I(inode)->nid);
		put_page(page);
	}

	z_erofs_pcluster_end(&f);
	(void)z_erofs_runqueue(&f, ra_pages);
	erofs_put_metabuf(&f.map.buf);
	erofs_release_pages(&f.pagepool);
	put_pages_list(&discard);
	return 0;
}

const struct address_space_operations z_erofs_aops = {
	.readpage = z_erofs_readpage,
	.readpages = z_erofs_readpages,
};'''
    zdata.write_text(text[:start] + compat_tail + text[end:])
    print("adapted compressed EROFS readahead to TouchGrass ->readpages")

    # Add dual-recorder proof at successful superblock parse.
    superc = root / "fs/erofs/super.c"
    replace_once(
        superc,
        '#include <linux/exportfs.h>\n',
        '#include <linux/exportfs.h>\n#include <linux/a52_p383_dual_recorder.h>\n',
        "Phase393 recorder include",
    )

    old = '''	/* handle multiple devices */
	ret = erofs_init_devices(sb, dsb);

	if (erofs_sb_has_fragments(sbi))
'''
    new = r'''	/* handle multiple devices */
	ret = erofs_init_devices(sb, dsb);
	if (!ret) {
		a52_p383_record("P393 EROFS_SB dev=%s blk=%u page=%lu compat=%x incompat=%x",
			sb->s_id, 1U << sbi->blkszbits, PAGE_SIZE,
			sbi->feature_compat, sbi->feature_incompat);
		pr_emerg("A52 P393 EROFS_SB dev=%s blk=%u page=%lu compat=%x incompat=%x\n",
			sb->s_id, 1U << sbi->blkszbits, PAGE_SIZE,
			sbi->feature_compat, sbi->feature_incompat);
	}

	if (erofs_sb_has_fragments(sbi))
'''
    replace_once(superc, old, new, "record accepted sub-page EROFS geometry")

    # Add a one-shot marker when the compressed path actually sees a
    # filesystem block smaller than PAGE_SIZE.
    zdata = root / "fs/erofs/zdata.c"
    text = zdata.read_text()
    include_anchor = '#include <trace/events/erofs.h>\n'
    if include_anchor not in text:
        raise SystemExit("zdata: trace include anchor missing")
    text = text.replace(
        include_anchor,
        include_anchor + '#include <linux/a52_p383_dual_recorder.h>\n',
        1,
    )
    struct_anchor = '#define Z_EROFS_INLINE_BVECS\t\t2\n'
    if struct_anchor not in text:
        raise SystemExit("zdata: inline bvec anchor missing")
    text = text.replace(
        struct_anchor,
        struct_anchor + '''
static atomic_t a52_p393_subpage_zip_once = ATOMIC_INIT(0);
''',
        1,
    )

    run_anchor = '''static int z_erofs_runqueue(struct z_erofs_decompress_frontend *f,
			    unsigned int ra_pages)
{
'''
    run_new = r'''static int z_erofs_runqueue(struct z_erofs_decompress_frontend *f,
			    unsigned int ra_pages)
{
	if (unlikely(f->inode->i_sb->s_blocksize < PAGE_SIZE) &&
	    atomic_cmpxchg(&a52_p393_subpage_zip_once, 0, 1) == 0) {
		a52_p383_record("P393 EROFS_ZIP blk=%lu page=%lu",
			(unsigned long)f->inode->i_sb->s_blocksize,
			(unsigned long)PAGE_SIZE);
		pr_emerg("A52 P393 EROFS_ZIP blk=%lu page=%lu\n",
			(unsigned long)f->inode->i_sb->s_blocksize,
			(unsigned long)PAGE_SIZE);
	}
'''
    if run_anchor not in text:
        raise SystemExit("zdata: runqueue anchor missing")
    text = text.replace(run_anchor, run_new, 1)
    zdata.write_text(text)
    print("added compressed sub-page runtime marker")

    # Remove obsolete old-driver objects/headers from the active source tree
    # to make accidental references obvious. They were not part of the pinned
    # modernized Android-4.19 EROFS tree.
    for rel in ("fs/erofs/utils.c", "fs/erofs/tagptr.h",
                "fs/erofs/zdata.h", "fs/erofs/zpvec.h"):
        p = root / rel
        if p.exists():
            p.unlink()
            print(f"removed obsolete {rel}")

    # Write provenance manifest into the kernel tree for artifacts/audits.
    manifest_path = root / "fs/erofs/A52_PHASE393_REFERENCE.txt"
    manifest_path.write_text(
        f"{MARK}\nreference={REF_REPO}@{REF_COMMIT}\n" +
        "".join(f"{rel} sha256={sha} bytes={size}\n"
                for rel, sha, size in manifest)
    )

    # Postconditions: the mount gate must allow 4K on a 16K PAGE_SIZE and
    # the compressed path must use byte pclusters rather than subtracting
    # PAGE_SHIFT from a 4K physical cluster.
    checks = {
        root / "fs/erofs/super.c": [
            MARK if False else "P393 EROFS_SB",
            "sbi->blkszbits < 9 || sbi->blkszbits > PAGE_SHIFT",
            "sb_set_blocksize(sb, 1 << sbi->blkszbits)",
        ],
        root / "fs/erofs/internal.h": [
            "unsigned char blkszbits",
            "#define erofs_blknr(sb, addr)",
            "#define erofs_blkoff(sb, addr)",
        ],
        root / "fs/erofs/zdata.c": [
            "unsigned int pclustersize",
            "PAGE_ALIGN(pcl->pclustersize) >> PAGE_SHIFT",
            "P393 EROFS_ZIP",
            ".readpages = z_erofs_readpages",
        ],
        root / "fs/erofs/zmap.c": [
            "sb->s_blocksize_bits",
            "erofs_blkoff(sb, map.m_pa)",
        ],
        manifest_path: [MARK, REF_COMMIT],
    }
    for path, toks in checks.items():
        data = path.read_text()
        for tok in toks:
            if tok not in data:
                raise SystemExit(f"{path}: missing Phase393 postcondition {tok!r}")

    # Ensure the known fatal old assumptions are gone from active code.
    bad = [
        ("fs/erofs/internal.h", "#define LOG_BLOCK_SIZE\t\tPAGE_SHIFT"),
        ("fs/erofs/super.c", "blkszbits != LOG_BLOCK_SIZE"),
        ("fs/erofs/zdata.c", "pcl->clusterbits -= PAGE_SHIFT"),
        ("fs/erofs/zdata.c", ".inputsize = PAGE_SIZE"),
        ("fs/erofs/zdata.c", ".readahead = z_erofs_readahead"),
    ]
    for rel, tok in bad:
        p = root / rel
        if p.exists() and tok in p.read_text():
            raise SystemExit(f"{rel}: stale unsafe assumption remains: {tok}")

    print("A52 Phase393 EROFS 4K-block-on-16K-page backport applied successfully")

if __name__ == "__main__":
    main()
