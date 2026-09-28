#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE408_DIRECT_BIO_DEBUG_DSI_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase408 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase408 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase408 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase408 closing brace missing: {sig}")


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE407_SAMSUNG_DEBUG_DSI_V1" not in text:
        raise SystemExit("Phase408 requires Phase407 single-record format")

    include_anchor = "#include <linux/errno.h>\n"
    text = one(
        text, include_anchor,
        include_anchor +
        "#include <linux/blkdev.h>\n"
        "#include <linux/bio.h>\n"
        "#include <linux/major.h>\n"
        "#include <linux/mm.h>\n",
        "block includes",
    )

    text = one(
        text,
        "#define A52_P407_MAGIC             0x3730344953443241ULL /* A2DSI407 */\n",
        "#define A52_P407_MAGIC             0x3830344953443241ULL /* A2DSI408 */\n",
        "record magic",
    )
    text = one(
        text,
        "#define A52_P407_COMMIT            0x407c0de5U\n",
        "#define A52_P407_COMMIT            0x408c0de5U\n",
        "commit marker",
    )
    text = one(
        text,
        "static u8 a52_p407_page[A52_P407_RECORD_BYTES] __aligned(64);\n",
        "static u8 a52_p407_page[A52_P407_RECORD_BYTES] __aligned(PAGE_SIZE);\n",
        "page alignment",
    )
    text = one(
        text,
        "	payload.phase = 407U;\n",
        "	payload.phase = 408U;\n",
        "record phase",
    )

    # Remove filesystem-path opener completely. /dev nodes are userspace
    # artifacts and are not guaranteed to exist at the early DSI stage.
    start, end = function_bounds(text, "static struct file *a52_p407_open_debug_partition(void)")
    text = text[:start] + text[end:] + "\n"

    start, end = function_bounds(text, "static void a52_p407_write_workfn(struct work_struct *work)")
    newfn = r'''static void a52_p407_write_workfn(struct work_struct *work)
{
	struct block_device *bdev;
	struct bio *bio;
	dev_t devt = MKDEV(SCSI_DISK0_MAJOR, 8);
	int added;
	int rc;

	(void)work;

	/*
	 * A52 debug partition is sda8.  Address it by dev_t directly so this
	 * path has no dependency on ueventd-created /dev nodes or by-name
	 * symlinks.  The sector is relative to the partition bdev.
	 */
	bdev = blkdev_get_by_dev(devt, FMODE_WRITE, NULL);
	if (IS_ERR(bdev)) {
		atomic_set(&a52_p407_state, 3);
		return;
	}

	bio = bio_alloc(GFP_KERNEL, 1);
	if (!bio) {
		blkdev_put(bdev, FMODE_WRITE);
		atomic_set(&a52_p407_state, 3);
		return;
	}

	bio_set_dev(bio, bdev);
	bio->bi_iter.bi_sector = (sector_t)(A52_P407_DEBUG_OFFSET >> 9);
	bio_set_op_attrs(bio, REQ_OP_WRITE, REQ_SYNC | REQ_FUA);
	added = bio_add_page(bio, virt_to_page(a52_p407_page),
			     A52_P407_RECORD_BYTES,
			     offset_in_page(a52_p407_page));
	if (added != A52_P407_RECORD_BYTES) {
		bio_put(bio);
		blkdev_put(bdev, FMODE_WRITE);
		atomic_set(&a52_p407_state, 3);
		return;
	}

	rc = submit_bio_wait(bio);
	bio_put(bio);
	if (!rc)
		rc = blkdev_issue_flush(bdev, GFP_KERNEL, NULL);
	blkdev_put(bdev, FMODE_WRITE);

	if (!rc)
		atomic_set(&a52_p407_state, 2);
	else
		atomic_set(&a52_p407_state, 3);
}'''
    text = text[:start] + newfn + text[end:]

    text += (
        "\n/* " + MARK + "\n"
        " * Phase407 filesystem-path transport replaced by direct sda8 BIO.\n"
        " * One 4 KiB record, one CRC32C, one commit marker, no duplicate, no R48.\n"
        " */\n"
        "static const char a52_p408_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def validate(text: str) -> None:
    required = (
        MARK,
        "MKDEV(SCSI_DISK0_MAJOR, 8)",
        "blkdev_get_by_dev(devt, FMODE_WRITE, NULL)",
        "bio_alloc(GFP_KERNEL, 1)",
        "bio_set_dev(bio, bdev)",
        "A52_P407_DEBUG_OFFSET >> 9",
        "REQ_OP_WRITE, REQ_SYNC | REQ_FUA",
        "bio_add_page(bio, virt_to_page(a52_p407_page)",
        "submit_bio_wait(bio)",
        "blkdev_issue_flush(bdev, GFP_KERNEL, NULL)",
        "blkdev_put(bdev, FMODE_WRITE)",
        "__aligned(PAGE_SIZE)",
        "payload.phase = 408U;",
        "0x3830344953443241ULL",
        "0x408c0de5U",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase408 token missing: " + token)

    for forbidden in (
        'filp_open("/dev/block/by-name/debug"',
        'filp_open("/dev/block/sda8"',
        "kernel_write(file, a52_p407_page",
        "vfs_fsync(file, 0)",
    ):
        if forbidden in text:
            raise SystemExit("Phase408 stale filesystem transport remains: " + forbidden)

    if text.count("submit_bio_wait(bio)") != 1:
        raise SystemExit("Phase408 must have exactly one block write submit site")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    p = ns.root / HWC
    if not p.is_file():
        raise SystemExit("Phase408 HWC source missing")

    text = p.read_text(errors="replace")
    if not ns.check_only:
        text = patch(text)
        p.write_text(text)
    validate(p.read_text(errors="replace"))
    print("Phase408 direct-BIO Samsung debug DSI recorder: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
