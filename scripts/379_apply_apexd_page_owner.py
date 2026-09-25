#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

SYSCALL = Path("arch/arm64/kernel/syscall.c")
FILEMAP = Path("mm/filemap.c")
MARK = "A52_PHASE379_APEXD_PAGE_OWNER_V1"

BLOCK = r'''
/* A52_PHASE379_APEXD_PAGE_OWNER_V1
 *
 * Phase377 caught apexd worker PID 516 in TASK_UNINTERRUPTIBLE with
 * wchan wait_on_page_read+0x64/0x1f8.  Phase378 comparison showed that
 * wait_on_page_read(), EROFS metadata acquisition, blkdev_readpage() and
 * block_read_full_page() are effectively the same as TouchGrass 4.19.
 *
 * This recorder identifies the actual page object being waited on.
 * It records the page mapping owner, superblock magic, readpage callback,
 * page index/flags and up to four buffer_heads including backing block
 * device major/minor and buffer state.
 *
 * One 256-byte record per apexd thread.  The table resets when a newer
 * apexd TGID is observed so the later activation process owns the table.
 * Two mirrored 16 KiB copies at 0xB1BF0000.
 */
#define A52_R379_PHYS              0xB1BF0000ULL
#define A52_R379_BYTES             0x8000U
#define A52_R379_COPY_BYTES        0x4000U
#define A52_R379_SLOT_BYTES        256U
#define A52_R379_SLOTS             64U
#define A52_R379_MAGIC             0x3937335850474141ULL
#define A52_R379_COMMIT            0x379c0de5U
#define A52_R379_VERSION           1U
#define A52_R379_ENTER             1U
#define A52_R379_RETURN            2U

struct a52_r379_bh {
	u64 blocknr;
	u64 state;
	u32 major;
	u32 minor;
};

struct a52_r379_record {
	u64 magic;
	u64 entry_ns;
	u64 return_ns;
	u64 page;
	u64 mapping;
	u64 index;
	u64 page_flags;
	u64 readpage_fn;
	u64 host_ino;
	u32 pid;
	u32 tgid;
	u32 event;
	u32 task_state;
	u32 sb_magic;
	u32 host_mode;
	u32 host_rdev;
	u32 bh_count;
	struct a52_r379_bh bh[4];
	char comm[TASK_COMM_LEN];
	char readpage_symbol[40];
};

static const char a52_r379_marker[] __used =
	"A52_PHASE379_APEXD_PAGE_OWNER_V1";
static void *a52_r379_sideband;
static DEFINE_SPINLOCK(a52_r379_slot_lock);
static u32 a52_r379_tgid;
static u32 a52_r379_pids[A52_R379_SLOTS];
static struct a52_r379_record a52_r379_records[A52_R379_SLOTS];

static void a52_r379_write(unsigned int slot,
			   const struct a52_r379_record *r)
{
	unsigned int pos;
	void *a;
	void *b;

	if (!READ_ONCE(a52_r379_sideband) || slot >= A52_R379_SLOTS)
		return;
	pos = slot * A52_R379_SLOT_BYTES;
	a = (u8 *)a52_r379_sideband + pos;
	b = (u8 *)a52_r379_sideband + A52_R379_COPY_BYTES + pos;
	memcpy(a, r, sizeof(*r));
	memcpy(b, r, sizeof(*r));
	wmb();
	__flush_dcache_area(a, sizeof(*r));
	__flush_dcache_area(b, sizeof(*r));
}

static bool a52_r379_is_apexd(void)
{
	return !strncmp(current->comm, "apexd", TASK_COMM_LEN);
}

static int a52_r379_get_slot(void)
{
	unsigned long flags;
	u32 tgid = (u32)current->tgid;
	u32 pid = (u32)current->pid;
	unsigned int i;
	int free_slot = -1;

	if (!a52_r379_is_apexd())
		return -1;

	spin_lock_irqsave(&a52_r379_slot_lock, flags);

	if (tgid > a52_r379_tgid) {
		a52_r379_tgid = tgid;
		memset(a52_r379_pids, 0, sizeof(a52_r379_pids));
		memset(a52_r379_records, 0, sizeof(a52_r379_records));
		memset(a52_r379_sideband, 0, A52_R379_BYTES);
		wmb();
		__flush_dcache_area(a52_r379_sideband, A52_R379_BYTES);
	} else if (tgid < a52_r379_tgid) {
		spin_unlock_irqrestore(&a52_r379_slot_lock, flags);
		return -1;
	}

	for (i = 0; i < A52_R379_SLOTS; i++) {
		if (a52_r379_pids[i] == pid) {
			spin_unlock_irqrestore(&a52_r379_slot_lock, flags);
			return (int)i;
		}
		if (!a52_r379_pids[i] && free_slot < 0)
			free_slot = (int)i;
	}

	if (free_slot >= 0)
		a52_r379_pids[free_slot] = pid;

	spin_unlock_irqrestore(&a52_r379_slot_lock, flags);
	return free_slot;
}

static void a52_r379_fill(struct a52_r379_record *r, struct page *page,
			  unsigned int event)
{
	struct address_space *mapping;
	struct inode *host;
	struct buffer_head *head;
	struct buffer_head *bh;
	unsigned int n = 0;

	memset(r, 0, sizeof(*r));
	r->magic = A52_R379_MAGIC;
	r->pid = (u32)current->pid;
	r->tgid = (u32)current->tgid;
	r->event = event;
	r->task_state = (u32)READ_ONCE(current->state);
	r->page = (u64)(unsigned long)page;
	r->index = (u64)page->index;
	r->page_flags = READ_ONCE(page->flags);
	get_task_comm(r->comm, current);

	mapping = READ_ONCE(page->mapping);
	r->mapping = (u64)(unsigned long)mapping;
	if (!mapping)
		return;

	host = READ_ONCE(mapping->host);
	if (host) {
		r->host_ino = (u64)host->i_ino;
		r->host_mode = (u32)host->i_mode;
		r->host_rdev = (u32)new_encode_dev(host->i_rdev);
		if (host->i_sb)
			r->sb_magic = (u32)host->i_sb->s_magic;
	}

	if (mapping->a_ops && mapping->a_ops->readpage) {
		r->readpage_fn =
			(u64)(unsigned long)mapping->a_ops->readpage;
		sprint_symbol(r->readpage_symbol,
			      (unsigned long)mapping->a_ops->readpage);
	}

	if (!page_has_buffers(page))
		return;

	head = page_buffers(page);
	bh = head;
	do {
		if (n >= ARRAY_SIZE(r->bh))
			break;
		r->bh[n].blocknr = (u64)bh->b_blocknr;
		r->bh[n].state = (u64)READ_ONCE(bh->b_state);
		if (bh->b_bdev) {
			r->bh[n].major = (u32)MAJOR(bh->b_bdev->bd_dev);
			r->bh[n].minor = (u32)MINOR(bh->b_bdev->bd_dev);
		}
		n++;
		bh = bh->b_this_page;
	} while (bh && bh != head);
	r->bh_count = n;
}

static u64 a52_r379_page_wait_enter(struct page *page)
{
	struct a52_r379_record *r;
	int slot;

	if (!READ_ONCE(a52_r379_sideband) || !page || !a52_r379_is_apexd())
		return 0;

	slot = a52_r379_get_slot();
	if (slot < 0)
		return 0;

	r = &a52_r379_records[slot];
	a52_r379_fill(r, page, A52_R379_ENTER);
	r->entry_ns = ktime_get_ns();
	r->return_ns = 0;
	a52_r379_write((unsigned int)slot, r);
	return (u64)slot + 1ULL;
}

static void a52_r379_page_wait_return(struct page *page, u64 token)
{
	struct a52_r379_record *r;
	unsigned int slot;
	u64 entry_ns;

	if (!token || !READ_ONCE(a52_r379_sideband))
		return;
	slot = (unsigned int)(token - 1ULL);
	if (slot >= A52_R379_SLOTS)
		return;

	r = &a52_r379_records[slot];
	if (r->pid != (u32)current->pid || r->tgid != (u32)current->tgid)
		return;

	entry_ns = r->entry_ns;
	a52_r379_fill(r, page, A52_R379_RETURN);
	r->entry_ns = entry_ns;
	r->return_ns = ktime_get_ns();
	a52_r379_write(slot, r);
}

static int __init a52_r379_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_r379_record) != A52_R379_SLOT_BYTES);
	BUILD_BUG_ON(A52_R379_SLOTS * A52_R379_SLOT_BYTES !=
		     A52_R379_COPY_BYTES);

	a52_r379_sideband = memremap(A52_R379_PHYS, A52_R379_BYTES,
				     MEMREMAP_WB);
	if (!a52_r379_sideband)
		return 0;

	memset(a52_r379_sideband, 0, A52_R379_BYTES);
	memset(a52_r379_pids, 0, sizeof(a52_r379_pids));
	memset(a52_r379_records, 0, sizeof(a52_r379_records));
	a52_r379_tgid = 0;
	wmb();
	__flush_dcache_area(a52_r379_sideband, A52_R379_BYTES);
	return 0;
}
late_initcall(a52_r379_init);

'''

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase379 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)

def patch_syscall(text: str) -> str:
    if "static int __init __used a52_r377_init(void)" not in text:
        text = one(text,
            "static int __init a52_r377_init(void)\n",
            "static int __init __used a52_r377_init(void)\n",
            "mark Phase377 init dormant")
    if "late_initcall(a52_r377_init);" in text:
        text = one(text,
            "late_initcall(a52_r377_init);\n",
            "/* Phase379 owns 0xB1BF0000..0xB1BF7FFF at runtime. */\n",
            "disable Phase377 sideband")
    return text

def patch_filemap(text: str) -> str:
    if MARK in text:
        return text
    for inc in (
        "#include <linux/io.h>\n",
        "#include <linux/kallsyms.h>\n",
        "#include <linux/ktime.h>\n",
        "#include <linux/spinlock.h>\n",
        "#include <linux/kdev_t.h>\n",
        "#include <asm/cacheflush.h>\n",
    ):
        if inc not in text:
            text = inc + text

    anchor = "static struct page *wait_on_page_read(struct page *page)\n"
    if anchor not in text:
        raise SystemExit("Phase379 wait_on_page_read anchor missing")
    text = text.replace(anchor, BLOCK + anchor, 1)

    old = '''static struct page *wait_on_page_read(struct page *page)
{
	if (!IS_ERR(page)) {
		wait_on_page_locked(page);
		if (!PageUptodate(page)) {
'''
    new = '''static struct page *wait_on_page_read(struct page *page)
{
	u64 a52_r379_token = 0;

	if (!IS_ERR(page)) {
		a52_r379_token = a52_r379_page_wait_enter(page);
		wait_on_page_locked(page);
		a52_r379_page_wait_return(page, a52_r379_token);
		if (!PageUptodate(page)) {
'''
    text = one(text, old, new, "wait wrapper")
    return text

def validate(syscall: str, filemap: str) -> None:
    for token in (
        MARK,
        "A52_R379_SLOT_BYTES        256U",
        "A52_R379_SLOTS             64U",
        "A52_R379_COMMIT            0x379c0de5U",
        "mapping->a_ops->readpage",
        "page_has_buffers(page)",
        "MAJOR(bh->b_bdev->bd_dev)",
        "MINOR(bh->b_bdev->bd_dev)",
        "a52_r379_page_wait_enter(page)",
        "a52_r379_page_wait_return(page, a52_r379_token)",
        "late_initcall(a52_r379_init);",
    ):
        if token not in filemap:
            raise SystemExit("Phase379 filemap token missing: " + token)
    if "late_initcall(a52_r377_init);" in syscall:
        raise SystemExit("Phase379 must disable Phase377 sideband owner")
    if "static int __init __used a52_r377_init(void)" not in syscall:
        raise SystemExit("Phase379 dormant Phase377 marker missing")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    sp = ns.root / SYSCALL
    fp = ns.root / FILEMAP
    if not sp.is_file() or not fp.is_file():
        raise SystemExit("Phase379 source missing")

    s = sp.read_text(encoding="utf-8")
    f = fp.read_text(encoding="utf-8")

    if MARK in f:
        validate(s, f)
        print("Phase379 apexd page-owner audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase379 marker missing in check-only mode")

    s = patch_syscall(s)
    f = patch_filemap(f)
    validate(s, f)
    sp.write_text(s, encoding="utf-8")
    fp.write_text(f, encoding="utf-8")
    print("Phase379 apexd page-owner recorder applied")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
