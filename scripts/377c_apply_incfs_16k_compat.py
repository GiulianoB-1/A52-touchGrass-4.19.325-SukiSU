#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace/touchgrass-a52xq")
path = root / "fs/incfs/vfs.c"
text = path.read_text()

UPSTREAM = "e302f3a21b6554722c72787fddc4780c0280e88b"

def replace_once(old: str, new: str, label: str):
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    text = text.replace(old, new, 1)

# 1) Keep the read-log scratch buffer tied to the IncFS on-disk block
# size, not the Linux VM page size.
replace_once(
    "ssize_t reads_per_page = PAGE_SIZE / sizeof(*reads_buf);",
    "ssize_t reads_per_page = INCFS_DATA_FILE_BLOCK_SIZE / sizeof(*reads_buf);",
    "log reads_per_page",
)

replace_once(
    "reads_buf = (struct incfs_pending_read_info *)__get_free_page(GFP_NOFS);",
    "reads_buf = kzalloc(INCFS_DATA_FILE_BLOCK_SIZE, GFP_NOFS);",
    "log scratch allocation",
)

old_log_free = """out:
	if (reads_buf)
		free_page((unsigned long)reads_buf);
	return result;
}

static __poll_t log_poll"""
new_log_free = """out:
	kfree(reads_buf);
	return result;
}

static __poll_t log_poll"""
replace_once(old_log_free, new_log_free, "log scratch free")

# 2) A Linux page can now contain multiple fixed 4 KiB IncFS data blocks.
# Read them one by one until the VM page is filled or EOF/short-read occurs.
old_read = """static int read_single_page(struct file *f, struct page *page)
{
	loff_t offset = 0;
	loff_t size = 0;
	ssize_t bytes_to_read = 0;
	ssize_t read_result = 0;
	struct data_file *df = get_incfs_data_file(f);
	int result = 0;
	void *page_start = kmap(page);
	int block_index;
	int timeout_ms;

	if (!df)
		return -EBADF;

	offset = page_offset(page);
	block_index = offset / INCFS_DATA_FILE_BLOCK_SIZE;
	size = df->df_size;
	timeout_ms = df->df_mount_info->mi_options.read_timeout_ms;

	if (offset < size) {
		struct mem_range tmp = {
			.len = 2 * INCFS_DATA_FILE_BLOCK_SIZE
		};

		tmp.data = (u8 *)__get_free_pages(GFP_NOFS, get_order(tmp.len));
		bytes_to_read = min_t(loff_t, size - offset, PAGE_SIZE);
		read_result = incfs_read_data_file_block(
			range(page_start, bytes_to_read), f, block_index,
			timeout_ms, tmp);

		free_pages((unsigned long)tmp.data, get_order(tmp.len));
	} else {
		bytes_to_read = 0;
		read_result = 0;
	}

	if (read_result < 0)
		result = read_result;
	else if (read_result < PAGE_SIZE)
		zero_user(page, read_result, PAGE_SIZE - read_result);

	if (result == 0)
		SetPageUptodate(page);
	else
		SetPageError(page);

	flush_dcache_page(page);
	kunmap(page);
	unlock_page(page);
	return result;
}
"""
new_read = """static int read_single_page(struct file *f, struct page *page)
{
	loff_t offset = 0;
	loff_t size = 0;
	ssize_t total_read = 0;
	struct data_file *df = get_incfs_data_file(f);
	int result = 0;
	void *page_start;
	int block_index;
	int timeout_ms;
	struct mem_range tmp = {
		.len = 2 * INCFS_DATA_FILE_BLOCK_SIZE
	};

	if (!df)
		return -EBADF;

	page_start = kmap(page);
	offset = page_offset(page);
	block_index = offset / INCFS_DATA_FILE_BLOCK_SIZE;
	size = df->df_size;
	timeout_ms = df->df_mount_info->mi_options.read_timeout_ms;

	tmp.data = kzalloc(tmp.len, GFP_NOFS);
	if (!tmp.data) {
		result = -ENOMEM;
		goto err;
	}

	while (offset + total_read < size) {
		ssize_t bytes_to_read = min_t(loff_t,
					      size - offset - total_read,
					      INCFS_DATA_FILE_BLOCK_SIZE);

		result = incfs_read_data_file_block(
			range(page_start + total_read, bytes_to_read), f,
			block_index, timeout_ms, tmp);
		if (result < 0)
			break;

		total_read += result;
		block_index++;

		if (result < INCFS_DATA_FILE_BLOCK_SIZE)
			break;
		if (total_read == PAGE_SIZE)
			break;
	}

	kfree(tmp.data);
err:
	if (result < 0)
		total_read = 0;
	else
		result = 0;

	if (total_read < PAGE_SIZE)
		zero_user(page, total_read, PAGE_SIZE - total_read);

	if (result == 0)
		SetPageUptodate(page);
	else
		SetPageError(page);

	flush_dcache_page(page);
	kunmap(page);
	unlock_page(page);
	return result;
}
"""
replace_once(old_read, new_read, "read_single_page 16K backport")

# 3) These buffers are fixed-size IncFS work buffers (8 KiB), not VM pages.
replace_once(
    """	data_buf = (u8 *)__get_free_pages(GFP_NOFS | __GFP_COMP,
					  get_order(data_buf_size));""",
    """	data_buf = kzalloc(data_buf_size, GFP_NOFS);""",
    "fill_blocks allocation",
)

replace_once(
    """	if (data_buf)
		free_pages((unsigned long)data_buf, get_order(data_buf_size));""",
    """	kfree(data_buf);""",
    "fill_blocks free",
)

# 4) The fixed IncFS file block stays 4 KiB. It no longer has to equal PAGE_SIZE.
replace_once(
    "	BUILD_BUG_ON(PAGE_SIZE != INCFS_DATA_FILE_BLOCK_SIZE);\n\n",
    "",
    "4K-only mount assertion",
)

marker = f"/* A52 P377C: IncFS 16K-page compatibility backport from {UPSTREAM} */\n"
anchor = "static int read_single_page(struct file *f, struct page *page)\n"
if marker not in text:
    if text.count(anchor) != 1:
        raise SystemExit("read_single_page marker anchor missing")
    text = text.replace(anchor, marker + anchor, 1)

path.write_text(text)

# Strict postconditions.
checks = [
    "ssize_t reads_per_page = INCFS_DATA_FILE_BLOCK_SIZE / sizeof(*reads_buf);",
    "reads_buf = kzalloc(INCFS_DATA_FILE_BLOCK_SIZE, GFP_NOFS);",
    "while (offset + total_read < size)",
    "if (total_read == PAGE_SIZE)",
    "data_buf = kzalloc(data_buf_size, GFP_NOFS);",
    marker.strip(),
]
for check in checks:
    if check not in text:
        raise SystemExit(f"missing postcondition: {check}")

if "BUILD_BUG_ON(PAGE_SIZE != INCFS_DATA_FILE_BLOCK_SIZE);" in text:
    raise SystemExit("4K-only BUILD_BUG_ON survived")

print("Applied A52 P377C IncFS 16K compatibility backport")
print(f"Upstream semantic source: {UPSTREAM}")
