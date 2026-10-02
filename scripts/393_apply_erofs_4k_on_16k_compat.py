#!/usr/bin/env python3
from pathlib import Path
import sys

MARK = "A52_PHASE393_EROFS_4K_ON_16K_COMPAT_V1"

def one(path, old, new, label):
    t = path.read_text()
    if new in t:
        print(f"{path}: {label} already applied")
        return
    n = t.count(old)
    if n != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(t.replace(old, new, 1))
    print(f"{path}: applied {label}")

def many(path, old, new, label, minimum=1):
    t = path.read_text()
    n = t.count(old)
    if n < minimum:
        if new in t:
            print(f"{path}: {label} already applied")
            return
        raise SystemExit(f"{path}: {label}: expected >= {minimum} anchors, found {n}")
    path.write_text(t.replace(old, new))
    print(f"{path}: applied {label} ({n} replacements)")

def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()
    erofs = root / "fs/erofs"
    if not erofs.is_dir():
        raise SystemExit(f"missing {erofs}")

    internal = erofs / "internal.h"
    superc = erofs / "super.c"
    data = erofs / "data.c"
    inode = erofs / "inode.c"
    dirc = erofs / "dir.c"
    xattr = erofs / "xattr.c"
    zdata = erofs / "zdata.c"
    zmap = erofs / "zmap.c"
    decomp = erofs / "decompressor.c"
    erofs_fs = erofs / "erofs_fs.h"

    # ------------------------------------------------------------------
    # Foundation: keep the on-disk EROFS block at 4 KiB while Linux pages
    # are 16 KiB. The original Android 4.19 EROFS hard-wired these together.
    # ------------------------------------------------------------------
    old = r'''/* we strictly follow PAGE_SIZE and no buffer head yet */
#define LOG_BLOCK_SIZE		PAGE_SHIFT

#undef LOG_SECTORS_PER_BLOCK
#define LOG_SECTORS_PER_BLOCK	(PAGE_SHIFT - 9)

#undef SECTORS_PER_BLOCK
#define SECTORS_PER_BLOCK	(1 << SECTORS_PER_BLOCK)

#define EROFS_BLKSIZ		(1 << LOG_BLOCK_SIZE)

#if (EROFS_BLKSIZ % 4096 || !EROFS_BLKSIZ)
#error erofs cannot be used in this platform
#endif
'''
    new = r'''/*
 * A52_PHASE393_EROFS_4K_ON_16K_COMPAT_V1
 *
 * Android system/vendor EROFS images on this device use 4 KiB filesystem
 * blocks.  The legacy EROFS driver tied filesystem block size to PAGE_SIZE,
 * which makes a native 16 KiB kernel reject the on-disk superblock.
 *
 * Keep logical EROFS blocks at 4 KiB when PAGE_SIZE is larger.  Native Linux
 * pages remain PAGE_SIZE and can therefore contain multiple EROFS blocks.
 */
#define A52_EROFS_DISK_BLKSZ_BITS	12
#if PAGE_SHIFT > A52_EROFS_DISK_BLKSZ_BITS
#define LOG_BLOCK_SIZE			A52_EROFS_DISK_BLKSZ_BITS
#else
#define LOG_BLOCK_SIZE			PAGE_SHIFT
#endif

#undef LOG_SECTORS_PER_BLOCK
#define LOG_SECTORS_PER_BLOCK		(LOG_BLOCK_SIZE - 9)

#undef SECTORS_PER_BLOCK
#define SECTORS_PER_BLOCK		(1 << LOG_SECTORS_PER_BLOCK)

#define EROFS_BLKSIZ			(1 << LOG_BLOCK_SIZE)
#define EROFS_BLOCKS_PER_PAGE		(PAGE_SIZE >> LOG_BLOCK_SIZE)

#if (EROFS_BLKSIZ % 4096 || !EROFS_BLKSIZ)
#error erofs cannot be used in this platform
#endif
'''
    one(internal, old, new, "decouple 4K EROFS block size from native PAGE_SIZE")

    one(
        erofs_fs,
        "\t__u8 blkszbits;         /* support block_size == PAGE_SIZE only */\n",
        "\t__u8 blkszbits;         /* filesystem block size in bit shift */\n",
        "update on-disk blocksize comment",
    )

    # ------------------------------------------------------------------
    # Superblock: accept the 4K disk format and persist geometry evidence.
    # ------------------------------------------------------------------
    one(
        superc,
        '#include "xattr.h"\n',
        '#include "xattr.h"\n#include <linux/a52_p383_dual_recorder.h>\n',
        "dual-recorder include",
    )
    old = r'''	blkszbits = dsb->blkszbits;
	/* 9(512 bytes) + LOG_SECTORS_PER_BLOCK == LOG_BLOCK_SIZE */
	if (blkszbits != LOG_BLOCK_SIZE) {
		erofs_err(sb, "blksize %u isn't supported on this platform",
			  1 << blkszbits);
		goto out;
	}
'''
    new = r'''	blkszbits = dsb->blkszbits;
	if (blkszbits != LOG_BLOCK_SIZE || blkszbits > PAGE_SHIFT) {
		erofs_err(sb, "blksize %u isn't supported on this platform",
			  1 << blkszbits);
		goto out;
	}
	if (PAGE_SIZE > EROFS_BLKSIZ)
		a52_p383_record("P393 EROFSBLK disk=%u page=%lu",
				1U << blkszbits, (unsigned long)PAGE_SIZE);
'''
    one(superc, old, new, "accept 4K EROFS blocks on 16K PAGE_SIZE")

    # ------------------------------------------------------------------
    # Metadata: one old logical metadata "page" is one 4K EROFS block.
    # Allocate a native page container and synchronously read exactly 4K at
    # offset zero. This preserves all old metadata callers' expectations.
    # ------------------------------------------------------------------
    old = r'''struct page *erofs_get_meta_page(struct super_block *sb, erofs_blk_t blkaddr)
{
	struct address_space *const mapping = sb->s_bdev->bd_inode->i_mapping;
	struct page *page;

	page = read_cache_page_gfp(mapping, blkaddr,
				   mapping_gfp_constraint(mapping, ~__GFP_FS));
	/* should already be PageUptodate */
	if (!IS_ERR(page))
		lock_page(page);
	return page;
}
'''
    new = r'''struct page *erofs_get_meta_page(struct super_block *sb, erofs_blk_t blkaddr)
{
	struct address_space *const mapping = sb->s_bdev->bd_inode->i_mapping;
	struct page *page;

	if (EROFS_BLKSIZ < PAGE_SIZE) {
		struct bio *bio;
		int err;

		page = alloc_page(GFP_NOFS);
		if (!page)
			return ERR_PTR(-ENOMEM);
		lock_page(page);

		bio = bio_alloc(GFP_NOIO, 1);
		if (!bio) {
			unlock_page(page);
			put_page(page);
			return ERR_PTR(-ENOMEM);
		}
		bio_set_dev(bio, sb->s_bdev);
		bio->bi_iter.bi_sector = (sector_t)blkaddr <<
					 LOG_SECTORS_PER_BLOCK;
		bio->bi_opf = REQ_OP_READ;
		if (bio_add_page(bio, page, EROFS_BLKSIZ, 0) != EROFS_BLKSIZ) {
			bio_put(bio);
			unlock_page(page);
			put_page(page);
			return ERR_PTR(-EIO);
		}

		err = submit_bio_wait(bio);
		bio_put(bio);
		if (err) {
			SetPageError(page);
			unlock_page(page);
			put_page(page);
			return ERR_PTR(err);
		}
		SetPageUptodate(page);
		return page;
	}

	page = read_cache_page_gfp(mapping, blkaddr,
				   mapping_gfp_constraint(mapping, ~__GFP_FS));
	/* should already be PageUptodate */
	if (!IS_ERR(page))
		lock_page(page);
	return page;
}
'''
    one(data, old, new, "read one 4K metadata block into a native page container")

    # Flat/raw mapping uses filesystem blocks, not native pages.
    one(
        data,
        "\tnblocks = DIV_ROUND_UP(inode->i_size, PAGE_SIZE);\n",
        "\tnblocks = DIV_ROUND_UP(inode->i_size, EROFS_BLKSIZ);\n",
        "count flat file blocks in EROFS units",
    )
    one(
        data,
        "\t\tif (erofs_blkoff(map->m_pa) + map->m_plen > PAGE_SIZE) {\n",
        "\t\tif (erofs_blkoff(map->m_pa) + map->m_plen > EROFS_BLKSIZ) {\n",
        "bound inline data by EROFS block",
    )

    # Native-page raw reader for 4K filesystem blocks.
    raw_anchor = r'''static inline struct bio *erofs_read_raw_page(struct bio *bio,
					      struct address_space *mapping,
					      struct page *page,
					      erofs_off_t *last_block,
					      unsigned int nblocks,
					      bool ra)
{
'''
    raw_helper = r'''static struct bio *a52_erofs_read_raw_subpage(
					      struct address_space *mapping,
					      struct page *page,
					      bool ra)
{
	struct inode *const inode = mapping->host;
	struct super_block *const sb = inode->i_sb;
	struct erofs_map_blocks map = {
		.m_la = page_offset(page),
	};
	struct bio *bio;
	unsigned int readlen;
	int err;

	if (PageUptodate(page)) {
		unlock_page(page);
		return NULL;
	}

	err = erofs_map_blocks(inode, &map, EROFS_GET_BLOCKS_RAW);
	if (err)
		goto err_out;

	if (!(map.m_flags & EROFS_MAP_MAPPED)) {
		zero_user_segment(page, 0, PAGE_SIZE);
		SetPageUptodate(page);
		unlock_page(page);
		return NULL;
	}

	DBG_BUGON(map.m_plen != map.m_llen);

	if (map.m_flags & EROFS_MAP_META) {
		struct page *ipage;
		void *vsrc, *vto;
		erofs_blk_t blknr = erofs_blknr(map.m_pa);
		unsigned int blkoff = erofs_blkoff(map.m_pa);

		if (blkoff + map.m_plen > EROFS_BLKSIZ) {
			err = -EFSCORRUPTED;
			goto err_out;
		}

		ipage = erofs_get_meta_page(sb, blknr);
		if (IS_ERR(ipage)) {
			err = PTR_ERR(ipage);
			goto err_out;
		}

		vsrc = kmap_atomic(ipage);
		vto = kmap_atomic(page);
		memcpy(vto, vsrc + blkoff, map.m_plen);
		memset(vto + map.m_plen, 0, PAGE_SIZE - map.m_plen);
		kunmap_atomic(vto);
		kunmap_atomic(vsrc);
		flush_dcache_page(page);
		SetPageUptodate(page);
		unlock_page(ipage);
		put_page(ipage);
		unlock_page(page);
		return NULL;
	}

	if (erofs_blkoff(map.m_pa)) {
		err = -EFSCORRUPTED;
		goto err_out;
	}

	readlen = min_t(erofs_off_t, PAGE_SIZE, map.m_plen);
	if (!readlen) {
		err = -EIO;
		goto err_out;
	}
	if (readlen < PAGE_SIZE)
		zero_user_segment(page, readlen, PAGE_SIZE);

	bio = bio_alloc(GFP_NOIO, 1);
	if (!bio) {
		err = -ENOMEM;
		goto err_out;
	}
	bio->bi_end_io = erofs_readendio;
	bio_set_dev(bio, sb->s_bdev);
	bio->bi_iter.bi_sector = map.m_pa >> 9;
	bio->bi_opf = REQ_OP_READ;
	if (bio_add_page(bio, page, readlen, 0) != readlen) {
		bio_put(bio);
		err = -EIO;
		goto err_out;
	}
	submit_bio(bio);
	return NULL;

err_out:
	if (!ra) {
		SetPageError(page);
		ClearPageUptodate(page);
	}
	unlock_page(page);
	return ERR_PTR(err);
}

static inline struct bio *erofs_read_raw_page(struct bio *bio,
					      struct address_space *mapping,
					      struct page *page,
					      erofs_off_t *last_block,
					      unsigned int nblocks,
					      bool ra)
{
	if (EROFS_BLKSIZ < PAGE_SIZE)
		return a52_erofs_read_raw_subpage(mapping, page, ra);
'''
    one(data, raw_anchor, raw_helper, "native-page raw reader for 4K EROFS blocks")

    # Old full-page path remains intact for 4K PAGE_SIZE builds.
    one(
        data,
        "\terofs_off_t current_block = (erofs_off_t)page->index;\n",
        "\terofs_off_t current_block = page_offset(page) >> LOG_BLOCK_SIZE;\n",
        "derive current logical EROFS block from native page offset",
    )

    # ------------------------------------------------------------------
    # Inode/dir/xattr metadata boundaries must use the 4K filesystem block.
    # ------------------------------------------------------------------
    many(inode, "PAGE_SIZE", "EROFS_BLKSIZ",
         "use EROFS block for inline symlink boundaries", minimum=2)
    # Undo no PAGE_SIZE uses outside the two expected inline checks if replacement
    # touched an unrelated native-page use (the old file has exactly two).
    many(dirc, "PAGE_SIZE", "EROFS_BLKSIZ",
         "use EROFS block for directory record bounds", minimum=2)
    many(xattr, "PAGE_SIZE - it->ofs", "EROFS_BLKSIZ - it->ofs",
         "use EROFS block for xattr slices", minimum=2)

    # ------------------------------------------------------------------
    # Compressed mapping: workgroup identities and physical clusters are in
    # 4K filesystem-block units, while decompressed output remains native pages.
    # ------------------------------------------------------------------
    many(zdata, "map->m_pa >> PAGE_SHIFT", "map->m_pa >> LOG_BLOCK_SIZE",
         "index compressed workgroups by 4K EROFS blocks", minimum=2)
    one(
        zdata,
        "\tpcl->clusterbits -= PAGE_SHIFT;\n",
        "\tpcl->clusterbits -= LOG_BLOCK_SIZE;\n",
        "measure compressed cluster size in EROFS blocks",
    )
    one(
        zdata,
        "\tif (!PAGE_ALIGNED(map->m_pa)) {\n",
        "\tif (erofs_blkoff(map->m_pa)) {\n",
        "accept 4K-aligned compressed physical clusters",
    )

    old = r'''	/* give priority for inplaceio */
	if (clt->mode >= COLLECT_PRIMARY &&
	    type == Z_EROFS_PAGE_TYPE_EXCLUSIVE &&
	    z_erofs_try_inplace_io(clt, page))
		return 0;
'''
    new = r'''	/*
	 * In-place compressed I/O assumes one filesystem block consumes one
	 * whole Linux page. Disable it for 4K-block / 16K-page compatibility;
	 * staging/managed pages keep each compressed 4K block at offset zero.
	 */
	if (EROFS_BLKSIZ == PAGE_SIZE &&
	    clt->mode >= COLLECT_PRIMARY &&
	    type == Z_EROFS_PAGE_TYPE_EXCLUSIVE &&
	    z_erofs_try_inplace_io(clt, page))
		return 0;
'''
    one(zdata, old, new, "disable compressed inplace I/O for sub-page blocks")

    one(
        zdata,
        "\t\t\t\t\t.inputsize = PAGE_SIZE,\n",
        "\t\t\t\t\t.inputsize = clusterpages * EROFS_BLKSIZ,\n",
        "pass real compressed pcluster byte size",
    )

    # Read each compressed logical EROFS block into the start of its native
    # page container. One pcluster block therefore no longer consumes 16K I/O.
    one(
        zdata,
        "\t\terr = bio_add_page(bio, page, PAGE_SIZE, 0);\n\t\tif (err < PAGE_SIZE)\n",
        "\t\terr = bio_add_page(bio, page, EROFS_BLKSIZ, 0);\n\t\tif (err < EROFS_BLKSIZ)\n",
        "submit 4K compressed block I/O into native page containers",
    )

    # ------------------------------------------------------------------
    # LZ4 input: if a pcluster has multiple 4K blocks, gather those blocks
    # from separate native-page containers into one native-page scratch buffer.
    # The old implementation expected every compressed input block to occupy
    # a complete Linux page.
    # ------------------------------------------------------------------
    lz4_anchor = r'''static int z_erofs_lz4_decompress(struct z_erofs_decompress_req *rq, u8 *out)
{
	unsigned int inputmargin, inlen;
	u8 *src;
	bool copied, support_0padding;
	int ret;

	if (rq->inputsize > PAGE_SIZE)
		return -EOPNOTSUPP;

	src = kmap_atomic(*rq->in);
	inputmargin = 0;
	support_0padding = false;
'''
    lz4_new = r'''static int z_erofs_lz4_decompress(struct z_erofs_decompress_req *rq, u8 *out)
{
	unsigned int inputmargin, inlen;
	u8 *src;
	bool copied, support_0padding;
	int ret;

	if (rq->inputsize > PAGE_SIZE)
		return -EOPNOTSUPP;

	copied = false;
	if (EROFS_BLKSIZ < PAGE_SIZE && rq->inputsize > EROFS_BLKSIZ) {
		unsigned int off = 0, i = 0;

		src = erofs_get_pcpubuf(0);
		if (IS_ERR(src))
			return PTR_ERR(src);

		while (off < rq->inputsize) {
			unsigned int n = min_t(unsigned int, EROFS_BLKSIZ,
						 rq->inputsize - off);
			void *kaddr = kmap_atomic(rq->in[i++]);

			memcpy(src + off, kaddr, n);
			kunmap_atomic(kaddr);
			off += n;
		}
		copied = true;
	} else {
		src = kmap_atomic(*rq->in);
	}

	inputmargin = 0;
	support_0padding = false;
'''
    one(decomp, lz4_anchor, lz4_new, "gather multi-block compressed input")

    # There was an old copied=false assignment after zero-padding setup.
    one(
        decomp,
        "\tcopied = false;\n\tinlen = rq->inputsize - inputmargin;\n",
        "\tinlen = rq->inputsize - inputmargin;\n",
        "preserve packed-input cleanup state",
    )

    # Correct cleanup on the zero-padding all-zero error path.
    old = r'''		if (inputmargin >= rq->inputsize) {
			kunmap_atomic(src);
			return -EIO;
		}
'''
    new = r'''		if (inputmargin >= rq->inputsize) {
			if (copied)
				erofs_put_pcpubuf(src);
			else
				kunmap_atomic(src);
			return -EIO;
		}
'''
    one(decomp, old, new, "clean packed input on zero-padding error")

    # Shifted/plain physical blocks also need sub-page copy semantics.
    shifted_anchor = r'''static int z_erofs_shifted_transform(const struct z_erofs_decompress_req *rq,
				     struct list_head *pagepool)
{
	const unsigned int nrpages_out =
		PAGE_ALIGN(rq->pageofs_out + rq->outputsize) >> PAGE_SHIFT;
'''
    shifted_new = r'''static int z_erofs_shifted_transform(const struct z_erofs_decompress_req *rq,
				     struct list_head *pagepool)
{
	if (EROFS_BLKSIZ < PAGE_SIZE) {
		unsigned int done = 0;

		while (done < rq->outputsize) {
			unsigned int si = done >> LOG_BLOCK_SIZE;
			unsigned int so = done & (EROFS_BLKSIZ - 1);
			unsigned int dpos = rq->pageofs_out + done;
			unsigned int di = dpos >> PAGE_SHIFT;
			unsigned int dslot = dpos & ~PAGE_MASK;
			unsigned int n = min_t(unsigned int,
					      rq->outputsize - done,
					      EROFS_BLKSIZ - so);
			void *src;
			void *dst;

			n = min_t(unsigned int, n, PAGE_SIZE - dslot);
			if (!rq->out[di]) {
				done += n;
				continue;
			}

			src = kmap_atomic(rq->in[si]);
			dst = kmap_atomic(rq->out[di]);
			memcpy(dst + dslot, src + so, n);
			kunmap_atomic(dst);
			kunmap_atomic(src);
			done += n;
		}
		return 0;
	}

	const unsigned int nrpages_out =
		PAGE_ALIGN(rq->pageofs_out + rq->outputsize) >> PAGE_SHIFT;
'''
    one(decomp, shifted_anchor, shifted_new, "sub-page shifted/plain transform")

    # zmap automatically uses fixed LOG_BLOCK_SIZE=12 after the foundation.
    # Explicitly verify the physical-cluster checks still reference it.
    zmap_text = zmap.read_text()
    for token in (
        "vi->z_logical_clusterbits = LOG_BLOCK_SIZE",
        "vi->z_physical_clusterbits[0] != LOG_BLOCK_SIZE",
        "map->m_pa = blknr_to_addr(m.pblk)",
    ):
        if token not in zmap_text:
            raise SystemExit(f"{zmap}: expected legacy geometry anchor missing: {token}")

    # Final postconditions.
    checks = {
        internal: [
            MARK,
            "#define LOG_BLOCK_SIZE",
            "#define EROFS_BLOCKS_PER_PAGE",
        ],
        superc: [
            'P393 EROFSBLK disk=%u page=%lu',
            "blkszbits > PAGE_SHIFT",
        ],
        data: [
            "a52_erofs_read_raw_subpage",
            "bio_add_page(bio, page, EROFS_BLKSIZ, 0)",
            "nblocks = DIV_ROUND_UP(inode->i_size, EROFS_BLKSIZ)",
        ],
        zdata: [
            "map->m_pa >> LOG_BLOCK_SIZE",
            "pcl->clusterbits -= LOG_BLOCK_SIZE",
            "EROFS_BLKSIZ == PAGE_SIZE",
            ".inputsize = clusterpages * EROFS_BLKSIZ",
        ],
        decomp: [
            "rq->inputsize > EROFS_BLKSIZ",
            "z_erofs_shifted_transform",
            "EROFS_BLKSIZ < PAGE_SIZE",
        ],
    }
    for p, tokens in checks.items():
        txt = p.read_text()
        for token in tokens:
            if token not in txt:
                raise SystemExit(f"{p}: Phase393 postcondition missing: {token}")

    print("A52 Phase393 EROFS 4K-block / native-16K-page compatibility applied successfully")

if __name__ == "__main__":
    main()
