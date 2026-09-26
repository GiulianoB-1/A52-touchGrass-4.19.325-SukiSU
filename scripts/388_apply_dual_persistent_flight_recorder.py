#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE388_DUAL_PERSISTENT_FLIGHT_RECORDER_V1"
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
DRM = Path("drivers/gpu/drm/drm_atomic_helper.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase388 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


HEADER_BLOCK = r'''
/* A52_PHASE388_DUAL_PERSISTENT_FLIGHT_RECORDER_V1
 *
 * Existing ramoops/R48 remains the emergency/early-boot recorder.
 * Phase388 adds a second, independent high-volume binary recorder backed by
 * the final 2 MiB of Samsung's 10 MiB /dev/block/by-name/debug partition.
 * Trace call sites only append to a RAM ring; a kthread performs all UFS I/O.
 */
enum a52_uft_event {
	A52_UFT_DSI_WAIT_ENTER		= 0x1000,
	A52_UFT_DSI_WAIT_EXIT		= 0x1001,
	A52_UFT_DSI_FALLBACK_STATUS	= 0x1002,
	A52_UFT_DSI_FALLBACK_BRANCH	= 0x1003,
	A52_UFT_DSI_ISR			= 0x1004,
	A52_UFT_DRM_MODESET_FAIL		= 0x2000,
	A52_UFT_UFS_SAMPLE		= 0x3000,
	A52_UFT_USB_STAGE		= 0x4000,
	A52_UFT_FREEZE			= 0x5000,
};

void a52_uft_trace(u16 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3);

'''


RECORDER_BLOCK = r'''
/* A52_PHASE388_DUAL_PERSISTENT_FLIGHT_RECORDER_V1
 *
 * Layout:
 *   Samsung debug partition: require >= 10 MiB
 *   preserve:                first N-2 MiB (8 MiB on A52)
 *   Phase388 region:         final 2 MiB
 *   page size:               4096 bytes
 *   page count:              512
 *
 * Every page is self-describing and CRC32C protected. The last u32 of the
 * page is CRC32C over bytes 0..4091. A torn write invalidates only that page.
 *
 * The hot path never performs block I/O, allocation, sleeping, printk, or
 * synchronous waiting. It uses spin_trylock_irqsave(); if the worker owns the
 * ring lock, the event is deliberately dropped instead of perturbing DSI IRQ
 * timing.
 */
#define A52_UFT_PATH			"/dev/block/by-name/debug"
#define A52_UFT_MIN_PART_BYTES		(10ULL * 1024ULL * 1024ULL)
#define A52_UFT_REGION_BYTES		(2ULL * 1024ULL * 1024ULL)
#define A52_UFT_PAGE_BYTES		4096U
#define A52_UFT_PAGE_COUNT		512U
#define A52_UFT_HEADER_BYTES		64U
#define A52_UFT_RECORD_BYTES		32U
#define A52_UFT_RECORDS_PER_PAGE	125U
#define A52_UFT_RING_ENTRIES		4096U
#define A52_UFT_RING_MASK		(A52_UFT_RING_ENTRIES - 1U)
#define A52_UFT_MAGIC			0x3838544655323541ULL /* A52UFT88 */
#define A52_UFT_VERSION			1U

struct a52_uft_entry {
	u64 ts_ns;
	u32 seq;
	u16 event;
	u16 cpu;
	u32 arg0;
	u32 arg1;
	u32 arg2;
	u32 arg3;
};

struct a52_uft_page_header {
	u64 magic;
	u32 version;
	u32 header_bytes;
	u64 boot_id;
	u64 page_seq;
	u32 first_record_seq;
	u32 record_count;
	u64 dropped;
	u64 reserved0;
	u32 payload_bytes;
	u32 flags;
};

static const char a52_uft_marker[] __used =
	"A52_PHASE388_DUAL_PERSISTENT_FLIGHT_RECORDER_V1";

static struct a52_uft_entry a52_uft_ring[A52_UFT_RING_ENTRIES];
static DEFINE_SPINLOCK(a52_uft_lock);
static DECLARE_WAIT_QUEUE_HEAD(a52_uft_wq);
static u32 a52_uft_head;
static u32 a52_uft_tail;
static atomic_t a52_uft_seq = ATOMIC_INIT(0);
static atomic64_t a52_uft_dropped = ATOMIC64_INIT(0);
static struct task_struct *a52_uft_task;
static struct file *a52_uft_file;
static loff_t a52_uft_region_base;
static u32 a52_uft_page_index;
static u64 a52_uft_page_seq;
static u64 a52_uft_boot_id;

static u32 a52_uft_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				((crc & 1U) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

void a52_uft_trace(u16 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3)
{
	struct a52_uft_entry *e;
	unsigned long flags;
	u32 pending;

	BUILD_BUG_ON(sizeof(struct a52_uft_entry) != A52_UFT_RECORD_BYTES);

	if (!spin_trylock_irqsave(&a52_uft_lock, flags)) {
		atomic64_inc(&a52_uft_dropped);
		return;
	}

	if ((u32)(a52_uft_head - a52_uft_tail) >= A52_UFT_RING_ENTRIES) {
		a52_uft_tail++;
		atomic64_inc(&a52_uft_dropped);
	}

	e = &a52_uft_ring[a52_uft_head & A52_UFT_RING_MASK];
	e->ts_ns = ktime_get_boottime_ns();
	e->seq = (u32)atomic_inc_return(&a52_uft_seq);
	e->event = event;
	e->cpu = (u16)raw_smp_processor_id();
	e->arg0 = arg0;
	e->arg1 = arg1;
	e->arg2 = arg2;
	e->arg3 = arg3;
	a52_uft_head++;
	pending = a52_uft_head - a52_uft_tail;
	spin_unlock_irqrestore(&a52_uft_lock, flags);

	if (pending >= A52_UFT_RECORDS_PER_PAGE)
		wake_up_interruptible(&a52_uft_wq);
}
EXPORT_SYMBOL_GPL(a52_uft_trace);

static bool a52_uft_page_valid(const u8 *page,
			       const struct a52_uft_page_header *h)
{
	u32 stored;
	u32 calc;

	if (!page || !h || h->magic != A52_UFT_MAGIC ||
	    h->version != A52_UFT_VERSION ||
	    h->header_bytes != A52_UFT_HEADER_BYTES ||
	    h->record_count > A52_UFT_RECORDS_PER_PAGE ||
	    h->payload_bytes != h->record_count * A52_UFT_RECORD_BYTES)
		return false;

	memcpy(&stored, page + A52_UFT_PAGE_BYTES - sizeof(stored),
	       sizeof(stored));
	calc = a52_uft_crc32c(page, A52_UFT_PAGE_BYTES - sizeof(stored));
	return stored == calc;
}

static int a52_uft_open_and_scan(u8 *page)
{
	struct file *file;
	loff_t size;
	u64 max_page_seq = 0;
	u64 max_boot_id = 0;
	u32 next_index = 0;
	bool found = false;
	unsigned int i;

	file = filp_open(A52_UFT_PATH, O_RDWR | O_LARGEFILE, 0);
	if (IS_ERR(file))
		return PTR_ERR(file);

	size = i_size_read(file_inode(file));
	if (size < (loff_t)A52_UFT_MIN_PART_BYTES) {
		filp_close(file, NULL);
		return -ENOSPC;
	}

	a52_uft_region_base = size - (loff_t)A52_UFT_REGION_BYTES;

	for (i = 0; i < A52_UFT_PAGE_COUNT; i++) {
		struct a52_uft_page_header *h;
		loff_t pos = a52_uft_region_base +
			(loff_t)i * A52_UFT_PAGE_BYTES;
		ssize_t got;

		got = kernel_read(file, page, A52_UFT_PAGE_BYTES, &pos);
		if (got != A52_UFT_PAGE_BYTES)
			continue;
		h = (struct a52_uft_page_header *)page;
		if (!a52_uft_page_valid(page, h))
			continue;

		if (!found || h->page_seq > max_page_seq) {
			found = true;
			max_page_seq = h->page_seq;
			next_index = (i + 1U) % A52_UFT_PAGE_COUNT;
		}
		if (h->boot_id > max_boot_id)
			max_boot_id = h->boot_id;
	}

	a52_uft_file = file;
	a52_uft_page_index = found ? next_index : 0U;
	a52_uft_page_seq = found ? max_page_seq + 1ULL : 1ULL;
	a52_uft_boot_id = max_boot_id + 1ULL;
	if (!a52_uft_boot_id)
		a52_uft_boot_id = 1ULL;

	a52_ackfr_record("P388 UFT ready size=%lld base=%lld boot=%llu page=%llu idx=%u",
		(long long)size, (long long)a52_uft_region_base,
		(unsigned long long)a52_uft_boot_id,
		(unsigned long long)a52_uft_page_seq, a52_uft_page_index);
	return 0;
}

static unsigned int a52_uft_drain(u8 *page)
{
	struct a52_uft_page_header *h =
		(struct a52_uft_page_header *)page;
	struct a52_uft_entry *dst =
		(struct a52_uft_entry *)(page + A52_UFT_HEADER_BYTES);
	unsigned long flags;
	u32 available;
	u32 n;
	u32 i;

	memset(page, 0, A52_UFT_PAGE_BYTES);

	spin_lock_irqsave(&a52_uft_lock, flags);
	available = a52_uft_head - a52_uft_tail;
	n = min_t(u32, available, A52_UFT_RECORDS_PER_PAGE);
	for (i = 0; i < n; i++)
		dst[i] = a52_uft_ring[(a52_uft_tail + i) & A52_UFT_RING_MASK];
	a52_uft_tail += n;
	spin_unlock_irqrestore(&a52_uft_lock, flags);

	if (!n)
		return 0;

	h->magic = A52_UFT_MAGIC;
	h->version = A52_UFT_VERSION;
	h->header_bytes = A52_UFT_HEADER_BYTES;
	h->boot_id = a52_uft_boot_id;
	h->page_seq = a52_uft_page_seq;
	h->first_record_seq = dst[0].seq;
	h->record_count = n;
	h->dropped = (u64)atomic64_read(&a52_uft_dropped);
	h->payload_bytes = n * A52_UFT_RECORD_BYTES;
	h->flags = n == A52_UFT_RECORDS_PER_PAGE ? 1U : 0U;
	return n;
}

static int a52_uft_write_page(u8 *page)
{
	loff_t pos;
	ssize_t done;
	u32 crc;

	crc = a52_uft_crc32c(page, A52_UFT_PAGE_BYTES - sizeof(crc));
	memcpy(page + A52_UFT_PAGE_BYTES - sizeof(crc), &crc, sizeof(crc));

	pos = a52_uft_region_base +
		(loff_t)a52_uft_page_index * A52_UFT_PAGE_BYTES;
	done = kernel_write(a52_uft_file, page, A52_UFT_PAGE_BYTES, &pos);
	if (done != A52_UFT_PAGE_BYTES)
		return done < 0 ? (int)done : -EIO;

	a52_uft_page_seq++;
	a52_uft_page_index =
		(a52_uft_page_index + 1U) % A52_UFT_PAGE_COUNT;

	/* Bound persistence latency without forcing a synchronous write per page. */
	if (!(a52_uft_page_seq & 7ULL))
		vfs_fsync(a52_uft_file, 0);
	return 0;
}

static int a52_uft_thread(void *unused)
{
	u8 *page;
	unsigned int open_tries = 0;

	(void)unused;
	page = kmalloc(A52_UFT_PAGE_BYTES, GFP_KERNEL);
	if (!page)
		return -ENOMEM;

	while (!kthread_should_stop() && !a52_uft_file) {
		int ret = a52_uft_open_and_scan(page);

		if (!ret)
			break;
		open_tries++;
		if (open_tries == 1U || !(open_tries % 15U))
			a52_ackfr_record("P388 UFT open ret=%d try=%u",
					 ret, open_tries);
		if (msleep_interruptible(2000) && kthread_should_stop())
			goto out;
	}

	while (!kthread_should_stop() && a52_uft_file) {
		long w;

		w = wait_event_interruptible_timeout(
			a52_uft_wq,
			kthread_should_stop() ||
			READ_ONCE(a52_uft_head) - READ_ONCE(a52_uft_tail) >=
				A52_UFT_RECORDS_PER_PAGE,
			HZ);

		if (kthread_should_stop())
			break;

		/* On a 1 s timeout, also persist a partial page. */
		do {
			unsigned int n = a52_uft_drain(page);
			int ret;

			if (!n)
				break;
			ret = a52_uft_write_page(page);
			if (ret) {
				a52_ackfr_record("P388 UFT write ret=%d idx=%u",
					ret, a52_uft_page_index);
				break;
			}
			cond_resched();
		} while (READ_ONCE(a52_uft_head) != READ_ONCE(a52_uft_tail));

		if (w < 0 && w != -ERESTARTSYS)
			cond_resched();
	}

	if (a52_uft_file) {
		vfs_fsync(a52_uft_file, 0);
		filp_close(a52_uft_file, NULL);
		a52_uft_file = NULL;
	}
out:
	kfree(page);
	return 0;
}

static int __init a52_uft_init(void)
{
	BUILD_BUG_ON(sizeof(struct a52_uft_page_header) != A52_UFT_HEADER_BYTES);
	BUILD_BUG_ON(A52_UFT_PAGE_COUNT * A52_UFT_PAGE_BYTES !=
		     A52_UFT_REGION_BYTES);
	BUILD_BUG_ON(A52_UFT_HEADER_BYTES +
		     A52_UFT_RECORDS_PER_PAGE * A52_UFT_RECORD_BYTES + 4U >
		     A52_UFT_PAGE_BYTES);
	BUILD_BUG_ON(A52_UFT_RING_ENTRIES &
		     (A52_UFT_RING_ENTRIES - 1U));

	a52_uft_task = kthread_run(a52_uft_thread, NULL, "a52_uft388");
	if (IS_ERR(a52_uft_task)) {
		a52_ackfr_record("P388 UFT kthread ret=%ld",
				 PTR_ERR(a52_uft_task));
		a52_uft_task = NULL;
	}
	return 0;
}
late_initcall(a52_uft_init);

'''


def patch_header(text: str) -> str:
    if MARK in text:
        return text
    return one(text, "\n#endif\n", HEADER_BLOCK + "\n#endif\n",
               "header declarations")


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    includes = (
        "#include <linux/fs.h>\n",
        "#include <linux/file.h>\n",
        "#include <linux/kthread.h>\n",
        "#include <linux/delay.h>\n",
        "#include <linux/slab.h>\n",
        "#include <linux/wait.h>\n",
    )
    anchor = "#include <linux/kernel.h>\n"
    for inc in includes:
        if inc not in text:
            text = one(text, anchor, anchor + inc, "recorder include")

    text += "\n" + RECORDER_BLOCK

    # Mirror high-value existing fixed-state updates into the second backend.
    text = one(
        text,
        '''void a52_ackfr_sticky385_freeze(unsigned int event, int slot, u64 q,
                                int depth, unsigned int nr,
                                unsigned int zero, u64 age_ms)
{
	unsigned long flags;
''',
        '''void a52_ackfr_sticky385_freeze(unsigned int event, int slot, u64 q,
                                int depth, unsigned int nr,
                                unsigned int zero, u64 age_ms)
{
	unsigned long flags;

	a52_uft_trace(A52_UFT_FREEZE, event, (u32)slot,
		      ((u32)depth << 16) | (nr & 0xffffU),
		      ((u32)zero << 31) | ((u32)age_ms & 0x7fffffffU));
''',
        "freeze mirror",
    )

    text = one(
        text,
        '''void a52_ackfr_sticky385_ufs(unsigned int sample, u32 doorbell,
                             unsigned long outstanding, u64 irq_count)
{
	unsigned long flags;
''',
        '''void a52_ackfr_sticky385_ufs(unsigned int sample, u32 doorbell,
                             unsigned long outstanding, u64 irq_count)
{
	unsigned long flags;

	a52_uft_trace(A52_UFT_UFS_SAMPLE, sample, doorbell,
		      (u32)outstanding, (u32)irq_count);
''',
        "UFS mirror",
    )

    text = one(
        text,
        '''void a52_ackfr_sticky385_ofsimple(unsigned int stage, int ret)
{
	unsigned long flags;
''',
        '''void a52_ackfr_sticky385_ofsimple(unsigned int stage, int ret)
{
	unsigned long flags;

	a52_uft_trace(A52_UFT_USB_STAGE, stage, (u32)ret, 0U, 0U);
''',
        "USB stage mirror",
    )
    return text


def patch_dsi(text: str) -> str:
    if "A52_PHASE388_DSI_UFT_MIRROR_V1" in text:
        return text
    if "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1" not in text:
        raise SystemExit("Phase388 requires Phase387 DSI source")

    if "#include <linux/a52_ack_secure_flight_recorder.h>\n" not in text:
        text = one(
            text, '#include "dsi_ctrl_hw.h"\n',
            '#include "dsi_ctrl_hw.h"\n'
            '#include <linux/a52_ack_secure_flight_recorder.h>\n',
            "DSI recorder include",
        )

    text = one(
        text,
        '''			dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE],
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
''',
        '''			dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE],
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
		a52_uft_trace(A52_UFT_DSI_WAIT_ENTER,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
			(u32)dsi_ctrl->irq_info.irq_num,
			(u32)dsi_ctrl->irq_info.irq_stat_mask,
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL)); /* A52_PHASE388_DSI_UFT_MIRROR_V1 */
''',
        "DSI wait entry mirror",
    )

    text = one(
        text,
        '''		a52_ackfr_record("P276 387D w ret=%d trig=%d hw=%x",
			ret, atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
''',
        '''		a52_ackfr_record("P276 387D w ret=%d trig=%d hw=%x",
			ret, atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_uft_trace(A52_UFT_DSI_WAIT_EXIT, (u32)ret,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), 0U);
''',
        "DSI wait exit mirror",
    )

    text = one(
        text,
        '''			a52_ackfr_record("P276 387D f st=%x done=%u hw=%x",
				status, !!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
''',
        '''			a52_ackfr_record("P276 387D f st=%x done=%u hw=%x",
				status, !!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
			a52_uft_trace(A52_UFT_DSI_FALLBACK_STATUS, status,
				!!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), mask);
''',
        "DSI fallback status mirror",
    )

    text = one(
        text,
        '''			if (status & mask)
				a52_ackfr_record("P276 387D b=1 irq_lost=1");
			else
				a52_ackfr_record("P276 387D b=0 engine_done=0");
''',
        '''			if (status & mask) {
				a52_ackfr_record("P276 387D b=1 irq_lost=1");
				a52_uft_trace(A52_UFT_DSI_FALLBACK_BRANCH,
					1U, status, mask,
					(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
			} else {
				a52_ackfr_record("P276 387D b=0 engine_done=0");
				a52_uft_trace(A52_UFT_DSI_FALLBACK_BRANCH,
					0U, status, mask,
					(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
			}
''',
        "DSI fallback branch mirror",
    )

    text = one(
        text,
        '''		a52_ackfr_record("P276 387I irq=%d st=%x raw=%x err=%llx",
			irq, status, DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			(unsigned long long)errors);
''',
        '''		a52_ackfr_record("P276 387I irq=%d st=%x raw=%x err=%llx",
			irq, status, DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			(unsigned long long)errors);
	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_uft_trace(A52_UFT_DSI_ISR, (u32)irq, status,
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), (u32)errors);
''',
        "DSI ISR mirror",
    )
    return text


def patch_drm(text: str) -> str:
    if "A52_PHASE388_DRM_UFT_MIRROR_V1" in text:
        return text
    if "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1" not in text:
        raise SystemExit("Phase388 requires Phase387 DRM source")

    helper_old = '''static inline bool a52_p387_log(unsigned int n)
{
	return n <= 8U || !(n & 63U);
}

'''
    helper_new = '''static inline bool a52_p387_log(unsigned int n)
{
	return n <= 8U || !(n & 63U);
}

/* A52_PHASE388_DRM_UFT_MIRROR_V1 */
static inline void a52_p388_modeset_fail(unsigned int n, unsigned int stage,
					int ret, unsigned int object)
{
	a52_uft_trace(A52_UFT_DRM_MODESET_FAIL, n, stage, (u32)ret, object);
}

'''
    text = one(text, helper_old, helper_new, "DRM mirror helper")

    if "#include <linux/a52_ack_secure_flight_recorder.h>\n" not in text:
        # Phase387 injected the marker/helper immediately before the function;
        # add the public recorder include near the ordinary kernel includes.
        anchor = "#include <linux/export.h>\n"
        if anchor not in text:
            anchor = "#include <linux/kernel.h>\n"
        text = one(text, anchor,
                   anchor + "#include <linux/a52_ack_secure_flight_recorder.h>\n",
                   "DRM recorder include")

    # Every Phase387 failure record contains a unique stage tag. Mirror the
    # same stage/result into the binary backend immediately before returning.
    replacements = (
        (
'''				a52_ackfr_record("P276 387M n=%u st=0 r=-22 crtc=%d en=%u cm=%x",
					a52_p387_n, crtc->base.id,
					new_crtc_state->enable,
					new_crtc_state->connector_mask);
			return -EINVAL;
''',
'''				a52_ackfr_record("P276 387M n=%u st=0 r=-22 crtc=%d en=%u cm=%x",
					a52_p387_n, crtc->base.id,
					new_crtc_state->enable,
					new_crtc_state->connector_mask);
			a52_p388_modeset_fail(a52_p387_n, 0U, -EINVAL,
					      (u32)crtc->base.id);
			return -EINVAL;
'''),
        (
'''			a52_ackfr_record("P276 387M n=%u st=1 r=%d", a52_p387_n, ret);
		return ret;
''',
'''			a52_ackfr_record("P276 387M n=%u st=1 r=%d", a52_p387_n, ret);
		a52_p388_modeset_fail(a52_p387_n, 1U, ret, 0U);
		return ret;
'''),
        (
'''				a52_ackfr_record("P276 387M n=%u st=2 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			return ret;
''',
'''				a52_ackfr_record("P276 387M n=%u st=2 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			a52_p388_modeset_fail(a52_p387_n, 2U, ret,
					      (u32)connector->base.id);
			return ret;
'''),
        (
'''				a52_ackfr_record("P276 387M n=%u st=3 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			return ret;
''',
'''				a52_ackfr_record("P276 387M n=%u st=3 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			a52_p388_modeset_fail(a52_p387_n, 3U, ret,
					      (u32)connector->base.id);
			return ret;
'''),
        (
'''				a52_ackfr_record("P276 387M n=%u st=4 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			return ret;
''',
'''				a52_ackfr_record("P276 387M n=%u st=4 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			a52_p388_modeset_fail(a52_p387_n, 4U, ret,
					      (u32)crtc->base.id);
			return ret;
'''),
        (
'''				a52_ackfr_record("P276 387M n=%u st=5 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			return ret;
''',
'''				a52_ackfr_record("P276 387M n=%u st=5 r=%d crtc=%d",
					a52_p387_n, ret, crtc->base.id);
			a52_p388_modeset_fail(a52_p387_n, 5U, ret,
					      (u32)crtc->base.id);
			return ret;
'''),
        (
'''				a52_ackfr_record("P276 387M n=%u st=7 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			return ret;
''',
'''				a52_ackfr_record("P276 387M n=%u st=7 r=%d conn=%d",
					a52_p387_n, ret, connector->base.id);
			a52_p388_modeset_fail(a52_p387_n, 7U, ret,
					      (u32)connector->base.id);
			return ret;
'''),
        (
'''			a52_ackfr_record("P276 387M n=%u st=10 r=%d", a52_p387_n, ret);
		return ret;
''',
'''			a52_ackfr_record("P276 387M n=%u st=10 r=%d", a52_p387_n, ret);
		a52_p388_modeset_fail(a52_p387_n, 10U, ret, 0U);
		return ret;
'''),
    )
    for idx, (old, new) in enumerate(replacements):
        text = one(text, old, new, f"DRM failure mirror {idx}")
    return text


def validate(root: Path) -> None:
    h = (root / HDR).read_text()
    r = (root / REC).read_text()
    d = (root / DSI).read_text()
    m = (root / DRM).read_text()

    for token in (
        MARK, "enum a52_uft_event", "A52_UFT_DSI_WAIT_ENTER",
        "void a52_uft_trace(u16 event",
    ):
        if token not in h:
            raise SystemExit("Phase388 header token missing: " + token)

    for token in (
        MARK, '"A52_PHASE388_DUAL_PERSISTENT_FLIGHT_RECORDER_V1"',
        '"/dev/block/by-name/debug"', "A52_UFT_REGION_BYTES",
        "(2ULL * 1024ULL * 1024ULL)", "A52_UFT_PAGE_BYTES",
        "spin_trylock_irqsave", "kernel_write", "vfs_fsync",
        "late_initcall(a52_uft_init)", "A52_UFT_UFS_SAMPLE",
        "A52_UFT_USB_STAGE",
    ):
        if token not in r:
            raise SystemExit("Phase388 recorder token missing: " + token)

    for token in (
        "A52_PHASE388_DSI_UFT_MIRROR_V1",
        "A52_UFT_DSI_WAIT_ENTER", "A52_UFT_DSI_WAIT_EXIT",
        "A52_UFT_DSI_FALLBACK_STATUS", "A52_UFT_DSI_FALLBACK_BRANCH",
        "A52_UFT_DSI_ISR",
    ):
        if token not in d:
            raise SystemExit("Phase388 DSI token missing: " + token)

    for token in (
        "A52_PHASE388_DRM_UFT_MIRROR_V1",
        "a52_p388_modeset_fail", "A52_UFT_DRM_MODESET_FAIL",
    ):
        if token not in m:
            raise SystemExit("Phase388 DRM token missing: " + token)

    # The first 8 MiB of a 10 MiB A52 debug partition must remain untouched.
    if "size - (loff_t)A52_UFT_REGION_BYTES" not in r:
        raise SystemExit("Phase388 does not place recorder at partition tail")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    for rel in (HDR, REC, DSI, DRM):
        if not (root / rel).is_file():
            raise SystemExit("Phase388 missing source: " + str(rel))

    if not ns.check_only:
        p = root / HDR
        p.write_text(patch_header(p.read_text()))
        p = root / REC
        p.write_text(patch_recorder(p.read_text()))
        p = root / DSI
        p.write_text(patch_dsi(p.read_text()))
        p = root / DRM
        p.write_text(patch_drm(p.read_text()))

    validate(root)
    print("Phase388 dual persistent flight recorder: PASS")


if __name__ == "__main__":
    raise SystemExit(main())
