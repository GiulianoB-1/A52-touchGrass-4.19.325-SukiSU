#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")
PANEL = Path("drivers/a52_display/msm/dsi/dsi_panel.c")
SSPANEL = Path("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c")

DISPLAY_TARGETS = {
    DISPLAY: (
        "dsi_display_register",
        "dsi_display_dev_probe",
        "dsi_display_init",
        "dsi_display_bind",
        "dsi_display_res_init",
        "dsi_display_drm_bridge_init",
    ),
    PANEL: (
        "dsi_panel_get",
        "dsi_panel_drv_init",
    ),
    SSPANEL: (
        "ss_panel_init",
        "ss_early_display_init",
    ),
}


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase414 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase414 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase414 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase414 closing brace missing: {sig}")


def mask_c(text: str) -> str:
    out = list(text)
    state = "normal"
    escaped = False
    i = 0
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "normal":
            if c == "/" and n == "/":
                out[i] = out[i + 1] = " "
                state = "line-comment"
                i += 2
                continue
            if c == "/" and n == "*":
                out[i] = out[i + 1] = " "
                state = "block-comment"
                i += 2
                continue
            if c == '"':
                out[i] = " "
                state = "string"
                escaped = False
            elif c == "'":
                out[i] = " "
                state = "char"
                escaped = False
        elif state == "line-comment":
            if c == "\n":
                state = "normal"
            else:
                out[i] = " "
        elif state == "block-comment":
            if c == "*" and n == "/":
                out[i] = out[i + 1] = " "
                state = "normal"
                i += 2
                continue
            if c != "\n":
                out[i] = " "
        else:
            quote = '"' if state == "string" else "'"
            if c == "\n":
                escaped = False
            else:
                out[i] = " "
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == quote:
                    state = "normal"
        i += 1
    return "".join(out)


def definition_openings(text: str, name: str) -> list[int]:
    masked = mask_c(text)
    openings: list[int] = []
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        paren = match.end() - 1
        depth = 0
        close_paren = -1
        for i in range(paren, len(masked)):
            c = masked[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close_paren = i
                    break
        if close_paren < 0:
            continue
        tail = masked[close_paren + 1: close_paren + 4097]
        brace_rel = tail.find("{")
        semi_rel = tail.find(";")
        if brace_rel < 0 or (semi_rel >= 0 and semi_rel < brace_rel):
            continue
        openings.append(close_paren + 1 + brace_rel)
    return openings


def add_recorder_include(text: str) -> str:
    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc in text:
        return text
    offset = 0
    seen = False
    insert_at = -1
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if line.startswith("#include"):
            seen = True
            insert_at = offset + len(line)
        elif seen and stripped and not stripped.startswith(("/*", "*", "//")):
            break
        offset += len(line)
    if insert_at < 0:
        raise SystemExit("Phase414 include block missing")
    return text[:insert_at] + inc + text[insert_at:]


P414_BLOCK = r'''
/* A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1
 *
 * One non-wrapping chronological recorder:
 *
 *   records 1 .. N1  -> Samsung debug partition +0x800000, 2 MiB
 *   records N1+1..N2 -> persistent reserved RAM 0xB1A00000, 1 MiB
 *   after N2          -> stop, never overwrite earlier evidence
 *
 * The Samsung region is the logical first tier. A 2 MiB vmalloc staging image
 * exists only so hot-path callers never wait for block I/O. A workqueue writes
 * completed pages and a bounce-copy of the current partial page in background.
 *
 * R48/RS48 remains completely separate and unchanged.
 *
 * No 2 MiB CRC is used. Persistence/identity use magic, phase, boot_id,
 * monotonic global sequence, record count, and commit words.
 */
#define A52_P414_DEBUG_OFFSET        0x00800000ULL
#define A52_P414_DISK_BYTES          (2U * SZ_1M)
#define A52_P414_RAM_PHYS            0xB1A00000ULL
#define A52_P414_RAM_BYTES           SZ_1M
#define A52_P414_HEADER_BYTES        SZ_4K
#define A52_P414_RECORD_BYTES        128U
#define A52_P414_DISK_CAPACITY       ((A52_P414_DISK_BYTES - A52_P414_HEADER_BYTES) / A52_P414_RECORD_BYTES)
#define A52_P414_RAM_CAPACITY        ((A52_P414_RAM_BYTES - A52_P414_HEADER_BYTES) / A52_P414_RECORD_BYTES)
#define A52_P414_MAGIC               0x3431344d33434553ULL
#define A52_P414_VERSION             1U
#define A52_P414_REC_COMMIT          0x414c0de5U
#define A52_P414_HDR_COMMIT          0x414b0071U
#define A52_P414_STATE_DISK          1U
#define A52_P414_STATE_RAM           2U
#define A52_P414_STATE_FULL          3U

struct a52_p414_header {
	u64 magic;
	u32 version;
	u32 phase;
	u64 boot_id;
	u64 global_seq;
	u32 disk_count;
	u32 ram_count;
	u32 disk_capacity;
	u32 ram_capacity;
	u32 record_bytes;
	u32 state;
	u32 dropped;
	u32 commit;
} __packed;

struct a52_p414_record {
	u64 seq;
	u64 ts_ns;
	u64 boot_id;
	u32 phase;
	u16 cpu;
	u16 len;
	char text[88];
	u32 commit;
	u32 reserved;
} __packed;

static DEFINE_SPINLOCK(a52_p414_lock);
static u8 *a52_p414_disk_stage;
static u8 *a52_p414_bounce;
static void __iomem *a52_p414_ram;
static u64 a52_p414_boot_id;
static u64 a52_p414_seq;
static u32 a52_p414_disk_count;
static u32 a52_p414_ram_count;
static u32 a52_p414_state;
static u32 a52_p414_dropped;
static u32 a52_p414_flushed_full_pages;
static atomic_t a52_p414_ready = ATOMIC_INIT(0);
static atomic_t a52_p414_disk_gen = ATOMIC_INIT(0);
static atomic_t a52_p414_disk_written_gen = ATOMIC_INIT(0);
static atomic_t a52_p414_disk_retry = ATOMIC_INIT(0);

static void a52_p414_fill_header(struct a52_p414_header *h)
{
	memset(h, 0, sizeof(*h));
	h->magic = A52_P414_MAGIC;
	h->version = A52_P414_VERSION;
	h->phase = 414U;
	h->boot_id = a52_p414_boot_id;
	h->global_seq = a52_p414_seq;
	h->disk_count = a52_p414_disk_count;
	h->ram_count = a52_p414_ram_count;
	h->disk_capacity = A52_P414_DISK_CAPACITY;
	h->ram_capacity = A52_P414_RAM_CAPACITY;
	h->record_bytes = A52_P414_RECORD_BYTES;
	h->state = a52_p414_state;
	h->dropped = a52_p414_dropped;
	h->commit = A52_P414_HDR_COMMIT;
}

static void a52_p414_update_disk_header_locked(void)
{
	struct a52_p414_header h;

	if (!a52_p414_disk_stage)
		return;
	a52_p414_fill_header(&h);
	memcpy(a52_p414_disk_stage, &h, sizeof(h));
	wmb();
}

static void a52_p414_update_ram_header_locked(void)
{
	struct a52_p414_header h;

	if (!a52_p414_ram)
		return;
	a52_p414_fill_header(&h);
	memcpy_toio(a52_p414_ram, &h, sizeof(h));
	a52_p406_persist(a52_p414_ram, sizeof(h));
}

static int a52_p414_submit_page(struct block_device *bdev, void *addr,
				unsigned int page_index)
{
	struct bio *bio;
	struct page *page;
	int added;
	int rc;

	if (!bdev || !addr)
		return -EINVAL;

	page = vmalloc_to_page(addr);
	if (!page)
		return -EFAULT;

	bio = bio_alloc(GFP_KERNEL, 1);
	if (!bio)
		return -ENOMEM;

	bio_set_dev(bio, bdev);
	bio->bi_iter.bi_sector =
		(sector_t)((A52_P414_DEBUG_OFFSET +
			    (u64)page_index * PAGE_SIZE) >> 9);
	bio_set_op_attrs(bio, REQ_OP_WRITE, REQ_SYNC | REQ_FUA);
	added = bio_add_page(bio, page, PAGE_SIZE, 0);
	if (added != PAGE_SIZE) {
		bio_put(bio);
		return -EIO;
	}

	rc = submit_bio_wait(bio);
	bio_put(bio);
	return rc;
}

static void a52_p414_disk_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_p414_disk_work, a52_p414_disk_workfn);

static void a52_p414_queue_disk(void)
{
	atomic_inc(&a52_p414_disk_gen);
	mod_delayed_work(system_unbound_wq, &a52_p414_disk_work, 0);
}

static void a52_p414_disk_workfn(struct work_struct *work)
{
	struct block_device *bdev;
	dev_t devt = MKDEV(SCSI_DISK0_MAJOR, 8);
	unsigned long flags;
	unsigned int count;
	unsigned int full_pages;
	unsigned int partial_bytes;
	unsigned int page_index;
	unsigned int generation;
	int rc = 0;
	int retry;

	(void)work;
	if (!READ_ONCE(a52_p414_disk_stage) || !READ_ONCE(a52_p414_bounce))
		return;

	generation = (unsigned int)atomic_read(&a52_p414_disk_gen);
	bdev = blkdev_get_by_dev(devt, FMODE_WRITE, NULL);
	if (IS_ERR(bdev)) {
		retry = atomic_inc_return(&a52_p414_disk_retry);
		if (retry <= 240)
			mod_delayed_work(system_unbound_wq, &a52_p414_disk_work,
					 msecs_to_jiffies(250));
		return;
	}

	/* Header is mutable, so always write it from a locked bounce snapshot. */
	spin_lock_irqsave(&a52_p414_lock, flags);
	memcpy(a52_p414_bounce, a52_p414_disk_stage, PAGE_SIZE);
	count = a52_p414_disk_count;
	spin_unlock_irqrestore(&a52_p414_lock, flags);

	rc = a52_p414_submit_page(bdev, a52_p414_bounce, 0U);
	if (rc)
		goto out;

	full_pages = (count * A52_P414_RECORD_BYTES) / PAGE_SIZE;
	partial_bytes = (count * A52_P414_RECORD_BYTES) % PAGE_SIZE;

	/* Completed data pages are immutable and can be submitted directly. */
	for (page_index = a52_p414_flushed_full_pages + 1U;
	     page_index <= full_pages; page_index++) {
		rc = a52_p414_submit_page(
			bdev, a52_p414_disk_stage + page_index * PAGE_SIZE,
			page_index);
		if (rc)
			goto out;
		a52_p414_flushed_full_pages = page_index;
	}

	/* Preserve the current partial page too, without racing hot-path writes. */
	if (partial_bytes) {
		page_index = 1U + full_pages;
		spin_lock_irqsave(&a52_p414_lock, flags);
		memcpy(a52_p414_bounce,
		       a52_p414_disk_stage + page_index * PAGE_SIZE,
		       PAGE_SIZE);
		spin_unlock_irqrestore(&a52_p414_lock, flags);
		rc = a52_p414_submit_page(bdev, a52_p414_bounce, page_index);
		if (rc)
			goto out;
	}

	rc = blkdev_issue_flush(bdev, GFP_KERNEL);
out:
	blkdev_put(bdev, FMODE_WRITE);

	if (!rc) {
		atomic_set(&a52_p414_disk_written_gen, generation);
		atomic_set(&a52_p414_disk_retry, 0);
		if ((unsigned int)atomic_read(&a52_p414_disk_gen) != generation)
			mod_delayed_work(system_unbound_wq,
				&a52_p414_disk_work, 0);
		return;
	}

	retry = atomic_inc_return(&a52_p414_disk_retry);
	if (retry <= 240)
		mod_delayed_work(system_unbound_wq, &a52_p414_disk_work,
				 msecs_to_jiffies(250));
}

static void a52_p414_append_text(const char *message)
{
	struct a52_p414_record rec;
	unsigned long flags;
	size_t len;
	bool queue_disk = false;

	if (!message || !atomic_read(&a52_p414_ready))
		return;

	if (!spin_trylock_irqsave(&a52_p414_lock, flags)) {
		a52_p414_dropped++;
		return;
	}

	memset(&rec, 0, sizeof(rec));
	rec.seq = ++a52_p414_seq;
	rec.ts_ns = ktime_get_boottime_ns();
	rec.boot_id = a52_p414_boot_id;
	rec.phase = 414U;
	rec.cpu = (u16)raw_smp_processor_id();
	len = strnlen(message, sizeof(rec.text) - 1U);
	rec.len = (u16)len;
	memcpy(rec.text, message, len);
	rec.text[len] = '\0';
	rec.commit = A52_P414_REC_COMMIT;

	if (a52_p414_disk_count < A52_P414_DISK_CAPACITY) {
		memcpy(a52_p414_disk_stage + A52_P414_HEADER_BYTES +
		       a52_p414_disk_count * A52_P414_RECORD_BYTES,
		       &rec, sizeof(rec));
		a52_p414_disk_count++;
		if (a52_p414_disk_count == A52_P414_DISK_CAPACITY) {
			a52_p414_state = A52_P414_STATE_RAM;
			a52_p414_update_ram_header_locked();
		}
		a52_p414_update_disk_header_locked();
		queue_disk = true;
	} else if (a52_p414_ram_count < A52_P414_RAM_CAPACITY) {
		void __iomem *dst =
			(u8 __iomem *)a52_p414_ram + A52_P414_HEADER_BYTES +
			a52_p414_ram_count * A52_P414_RECORD_BYTES;

		memcpy_toio(dst, &rec, sizeof(rec));
		a52_p406_persist(dst, sizeof(rec));
		a52_p414_ram_count++;
		if (a52_p414_ram_count == A52_P414_RAM_CAPACITY)
			a52_p414_state = A52_P414_STATE_FULL;
		a52_p414_update_ram_header_locked();
	} else {
		a52_p414_dropped++;
		a52_p414_state = A52_P414_STATE_FULL;
		a52_p414_update_ram_header_locked();
	}

	spin_unlock_irqrestore(&a52_p414_lock, flags);

	if (queue_disk)
		a52_p414_queue_disk();
}

static int __init a52_p414_init(void)
{
	struct a52_p414_header old = { 0 };
	struct a52_p414_header h;

	BUILD_BUG_ON(sizeof(struct a52_p414_header) != 64U);
	BUILD_BUG_ON(sizeof(struct a52_p414_record) != A52_P414_RECORD_BYTES);
	BUILD_BUG_ON(A52_P414_HEADER_BYTES +
		     A52_P414_DISK_CAPACITY * A52_P414_RECORD_BYTES !=
		     A52_P414_DISK_BYTES);
	BUILD_BUG_ON(A52_P414_HEADER_BYTES +
		     A52_P414_RAM_CAPACITY * A52_P414_RECORD_BYTES !=
		     A52_P414_RAM_BYTES);
	BUILD_BUG_ON(PAGE_SIZE != 4096);

	a52_p414_disk_stage = vzalloc(A52_P414_DISK_BYTES);
	a52_p414_bounce = vzalloc(PAGE_SIZE);
	a52_p414_ram = ioremap_cache(A52_P414_RAM_PHYS, A52_P414_RAM_BYTES);
	if (!a52_p414_disk_stage || !a52_p414_bounce || !a52_p414_ram)
		return 0;

	memcpy_fromio(&old, a52_p414_ram, sizeof(old));
	if (old.magic == A52_P414_MAGIC &&
	    old.version == A52_P414_VERSION &&
	    old.phase == 414U &&
	    old.boot_id && old.boot_id != ~0ULL)
		a52_p414_boot_id = old.boot_id + 1ULL;
	else
		a52_p414_boot_id = 1ULL;

	a52_p414_seq = 0;
	a52_p414_disk_count = 0;
	a52_p414_ram_count = 0;
	a52_p414_dropped = 0;
	a52_p414_state = A52_P414_STATE_DISK;
	a52_p414_flushed_full_pages = 0;

	memset(a52_p414_disk_stage, 0, A52_P414_DISK_BYTES);
	memset_io(a52_p414_ram, 0, A52_P414_HEADER_BYTES);

	a52_p414_fill_header(&h);
	memcpy(a52_p414_disk_stage, &h, sizeof(h));
	memcpy_toio(a52_p414_ram, &h, sizeof(h));
	a52_p406_persist(a52_p414_ram, sizeof(h));

	atomic_set(&a52_p414_ready, 1);
	a52_p414_queue_disk();

	/* This same record is admitted into R48, tying both channels to boot_id. */
	a52_ackfr_record("P414 BOOT id=%llu disk=%u ram=%u",
		(unsigned long long)a52_p414_boot_id,
		A52_P414_DISK_CAPACITY, A52_P414_RAM_CAPACITY);
	return 0;
}
core_initcall_sync(a52_p414_init);

'''


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE392_PERSISTENT_GAP_DUAL_BACKEND_V1",
        "A52_PHASE406_P392_CACHE_PERSIST_V1",
        "static void a52_p406_persist(void __iomem *addr, size_t len)",
        "core_initcall_sync(a52_p392_init);",
    ):
        if token not in text:
            raise SystemExit("Phase414 recorder prerequisite missing: " + token)

    anchor = "#include <linux/kernel.h>\n"
    for inc in (
        "#include <linux/bio.h>\n",
        "#include <linux/blkdev.h>\n",
        "#include <linux/major.h>\n",
        "#include <linux/mm.h>\n",
        "#include <linux/vmalloc.h>\n",
        "#include <linux/workqueue.h>\n",
    ):
        if inc not in text:
            text = one(text, anchor, anchor + inc, "recorder include")

    # Keep the lower 1 MiB of the old B1900000 backend alive for legacy
    # Phase392/405 evidence, freeing B1A00000..B1AFFFFF for Phase414.
    text = one(
        text,
        "#define A52_P392_BYTES\t\t(2U * SZ_1M)\n",
        "#define A52_P392_BYTES\t\tSZ_1M /* Phase414 leaves B1A00000 free */\n",
        "shrink Phase392 reserved backend",
    )

    text = one(
        text,
        "core_initcall_sync(a52_p392_init);\n",
        "core_initcall_sync(a52_p392_init);\n\n" + P414_BLOCK,
        "Phase414 recorder block",
    )

    # Mirror every formatted ACK recorder message into the new sequential
    # recorder. Phase392 stays as its independent legacy channel.
    pat = re.compile(
        r'(/\* A52_PHASE392_PERSISTENT_GAP_MIRROR_CALL_V1 \*/\n'
        r'\s*a52_p392_gap_write\(([^;]+)\);)'
    )
    m = pat.search(text)
    if not m:
        raise SystemExit("Phase414 Phase392 formatter mirror anchor missing")
    dst = m.group(2).strip()
    repl = m.group(1) + f"\n\ta52_p414_append_text({dst}); /* {MARK} */"
    text = text[:m.start()] + repl + text[m.end():]

    # Admit the Phase414 boot-generation record into the existing R48/RS48
    # transport so stale R48 generations can be rejected after recovery.
    if 'return !strncmp(message, "P414 ", 5) ||' not in text:
        text = one(
            text,
            'return !strncmp(message, "P276 ", 5) ||\n',
            'return !strncmp(message, "P414 ", 5) ||\n'
            '       !strncmp(message, "P276 ", 5) ||\n',
            "R48 message admission",
        )
    if 'strncmp(fmt, "P414", 4)' not in text:
        text = one(
            text,
            'if (strncmp(fmt, "P276", 4) &&\n',
            'if (strncmp(fmt, "P414", 4) &&\n'
            '    strncmp(fmt, "P276", 4) &&\n',
            "R48 format admission",
        )

    # Phase402 has a second, later admission gate immediately before formatting.
    # Phase414 records and the inherited display lifecycle scopes must pass this
    # gate too, otherwise they never reach a52_p414_append_text().
    phase402 = '''\tif (!fmt || (
\t    strncmp(fmt, "P402 ", 5) &&
'''
    phase414 = '''\tif (!fmt || (
\t    strncmp(fmt, "P414", 4) &&
\t    strcmp(fmt, "%s enter fn=%s") &&
\t    strcmp(fmt, "%s exit fn=%s us=%llu") &&
\t    strncmp(fmt, "P402 ", 5) &&
'''
    if phase414 not in text:
        text = one(text, phase402, phase414, "Phase402 Phase414 admission")

    return text


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE413_CONCURRENT_DEBUG2M_MIRROR_V1" not in text:
        raise SystemExit("Phase414 requires Phase413 HWC lineage")

    start = text.find("static atomic_t a52_p413_mirror_gen = ATOMIC_INIT(0);")
    if start < 0:
        raise SystemExit("Phase414 Phase413 mirror block start missing")
    _, end = function_bounds(
        text, "void a52_p413_persist_mode_event(u16 stage, u32 flags)")
    text = (
        text[:start] +
        "/* " + MARK + ": Phase413 debug2m mirror retired; Phase414 owns the partition. */\n" +
        text[end:]
    )
    text += (
        "\n/* " + MARK + ": Samsung debug2m is now tier 1 of the 3 MiB recorder. */\n"
        "static const char a52_p414_hwc_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


HOST_HELPER = r'''
/* A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1
 * First 128 generic DSI transfers, software arguments only.
 */
static atomic_t a52_p414_host_seq = ATOMIC_INIT(0);

static void a52_p414_host_note(struct dsi_ctrl *dsi_ctrl,
			       const struct mipi_dsi_msg *msg,
			       const u32 *flags)
{
	const u8 *p;
	u64 payload = 0;
	unsigned int i;
	unsigned int n;

	if (!dsi_ctrl || !msg || !flags)
		return;
	n = (unsigned int)atomic_inc_return(&a52_p414_host_seq);
	if (n > 128U)
		return;

	p = msg->tx_buf;
	if (p) {
		for (i = 0; i < min_t(unsigned int, (unsigned int)msg->tx_len, 8U); i++)
			payload |= (u64)p[i] << (i * 8U);
	}

	a52_ackfr_record("P414 H n=%u c=%d t=%x l=%u f=%x m=%x p=%llx",
		n, dsi_ctrl->cell_index, (unsigned int)msg->type,
		(unsigned int)msg->tx_len, *flags,
		(unsigned int)msg->flags, (unsigned long long)payload);
}
'''


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE413_CONCURRENT_DEBUG2M_MIRROR_V1" not in text:
        raise SystemExit("Phase414 requires Phase413 CTRL lineage")

    # Remove Phase413's old mirror API. Phase414 captures through ACKFR.
    text = one(
        text,
        "extern void a52_p413_persist_bootstrap(void); /* A52_PHASE413_CONCURRENT_DEBUG2M_MIRROR_V1 */\n"
        "extern void a52_p413_persist_mode_event(u16 stage, u32 flags);\n",
        "",
        "retire Phase413 declarations",
    )

    text = one(
        text,
        "\ta52_p409_init_buffers();\n"
        "\ta52_p413_persist_bootstrap();\n"
        "\t/* Phase413: hot RAM + concurrent Samsung debug2m mirror. */\n",
        "\t/* Phase414 recorder initializes independently at core_initcall. */\n",
        "retire Phase413 bootstrap",
    )

    anchor = "static void dsi_ctrl_flush_cmd_dma_queue(struct dsi_ctrl *dsi_ctrl)\n"
    text = one(text, anchor, HOST_HELPER + "\n" + anchor, "host helper insertion")

    old = (
        "\tif (a52_p411_exact_f0(dsi_ctrl, msg))\n"
        "\t\ta52_p413_persist_mode_event(11U, flags ? *flags : 0U);\n"
        "\ta52_p411_note_f0(dsi_ctrl, msg, flags, \"before-mode\");\n\n"
        "\t/* Select the tx mode to transfer the command */\n"
        "\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n"
        "\tif (a52_p411_exact_f0(dsi_ctrl, msg))\n"
        "\t\ta52_p413_persist_mode_event(12U, flags ? *flags : 0U);\n"
        "\ta52_p411_note_f0(dsi_ctrl, msg, flags, \"after-mode\");\n"
    )
    new = (
        "\ta52_p414_host_note(dsi_ctrl, msg, flags);\n"
        "\tif (a52_p411_exact_f0(dsi_ctrl, msg))\n"
        "\t\ta52_ackfr_record(\"P414 F0 pre f=%x\", flags ? *flags : 0U);\n"
        "\ta52_p411_note_f0(dsi_ctrl, msg, flags, \"before-mode\");\n\n"
        "\t/* Select the tx mode to transfer the command */\n"
        "\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n"
        "\tif (a52_p411_exact_f0(dsi_ctrl, msg))\n"
        "\t\ta52_ackfr_record(\"P414 F0 post f=%x\", flags ? *flags : 0U);\n"
        "\ta52_p411_note_f0(dsi_ctrl, msg, flags, \"after-mode\");\n"
    )
    text = one(text, old, new, "replace Phase413 exact-F0 persistence")

    text += "\n/* " + MARK + ": first-128 host transfer chronology enabled. */\n"
    return text


def patch_lifecycle(text: str, functions: tuple[str, ...], rel: Path) -> str:
    # The inherited Phase174 lifecycle scopes already emit ACKFR entry/exit
    # records. Phase414 mirrors ACKFR into the sequential 3 MiB recorder, so
    # adding a second statement here is redundant and can violate GNU89 when
    # a function has declarations following the scope macro.
    for function in functions:
        scope = f'A52_ACKFR_SCOPE("DISP", "a52.life.{function}");'
        if text.count(scope) != 1:
            raise SystemExit(
                f"Phase414 inherited lifecycle scope mismatch {rel}:{function}: "
                f"{text.count(scope)}")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    ctrl = (root / CTRL).read_text(errors="replace")
    hwc = (root / HWC).read_text(errors="replace")

    for token in (
        MARK,
        "A52_P414_DEBUG_OFFSET        0x00800000ULL",
        "A52_P414_DISK_BYTES          (2U * SZ_1M)",
        "A52_P414_RAM_PHYS            0xB1A00000ULL",
        "A52_P414_RAM_BYTES           SZ_1M",
        "A52_P414_RECORD_BYTES        128U",
        "A52_P414_DISK_CAPACITY",
        "A52_P414_RAM_CAPACITY",
        "a52_p414_append_text(",
        "a52_p414_submit_page(",
        "system_unbound_wq",
        "a52_p414_disk_count < A52_P414_DISK_CAPACITY",
        "a52_p414_ram_count < A52_P414_RAM_CAPACITY",
        "A52_P414_STATE_FULL",
        'a52_ackfr_record("P414 BOOT id=%llu disk=%u ram=%u"',
        'return !strncmp(message, "P414 ", 5) ||',
        'strncmp(fmt, "P414", 4)',
        'strcmp(fmt, "%s enter fn=%s")',
        'strcmp(fmt, "%s exit fn=%s us=%llu")',
        "SZ_1M /* Phase414 leaves B1A00000 free */",
    ):
        if token not in rec:
            raise SystemExit("Phase414 recorder token missing: " + token)

    if "a52_p414_crc" in rec or "crc32c(&rec" in P414_BLOCK:
        raise SystemExit("Phase414 must not add CRC to the 3 MiB recorder")

    for token in (
        MARK,
        "static atomic_t a52_p414_host_seq",
        "if (n > 128U)",
        'a52_ackfr_record("P414 H n=%u c=%d t=%x l=%u f=%x m=%x p=%llx"',
        'a52_ackfr_record("P414 F0 pre f=%x"',
        'a52_ackfr_record("P414 F0 post f=%x"',
        "(cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE",
        "*flags |= DSI_CTRL_CMD_FIFO_STORE",
    ):
        if token not in ctrl:
            raise SystemExit("Phase414 CTRL token missing: " + token)

    for forbidden in (
        "a52_p413_persist_bootstrap();",
        "a52_p413_persist_mode_event(11U",
        "a52_p413_persist_mode_event(12U",
        "a52_p409_init_buffers();",
    ):
        if forbidden in ctrl:
            raise SystemExit("Phase414 retired Phase413 token remains: " + forbidden)

    if "static atomic_t a52_p413_mirror_gen" in hwc:
        raise SystemExit("Phase414 Phase413 disk mirror remains active")
    if MARK not in hwc:
        raise SystemExit("Phase414 HWC retirement marker missing")

    for rel, functions in DISPLAY_TARGETS.items():
        text = (root / rel).read_text(errors="replace")
        for function in functions:
            token = f'A52_ACKFR_SCOPE("DISP", "a52.life.{function}");'
            if text.count(token) != 1:
                raise SystemExit(
                    f"Phase414 inherited lifecycle scope missing {rel}:{function}")

    # Preserve R48/RS48 and its three-copy transport unchanged.
    if "RS48" not in rec or "R48" not in rec:
        raise SystemExit("Phase414 R48/RS48 lineage missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REC, CTRL, HWC, DISPLAY, PANEL, SSPANEL):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase414 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / REC
        p.write_text(patch_recorder(p.read_text(errors="replace")))

        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))

        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))

        for rel, functions in DISPLAY_TARGETS.items():
            p = ns.root / rel
            p.write_text(patch_lifecycle(
                p.read_text(errors="replace"), functions, rel))

    validate(ns.root)
    print("Phase414 sequential 2MiB Samsung + 1MiB RAM recorder: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
