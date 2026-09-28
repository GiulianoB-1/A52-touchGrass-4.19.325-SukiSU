#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase409 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


HOT_BLOCK = r'''
/* A52_PHASE409_RAM1M_DEBUG2M_DSI_V1
 *
 * Focused two-tier DSI instrumentation:
 *
 *   HOT:  1 MiB non-wrapping binary event buffer in ordinary RAM.
 *         No block I/O, no printk, no R48 dependency in the Phase409 path.
 *
 *   COLD: one 2 MiB CRC32C-protected image written after the transaction to
 *         Samsung debug partition offset 0x800000..0x9fffff.
 *
 * The complete 1 MiB hot buffer is copied byte-for-byte into the cold image.
 * Phase345's microsecond samples are also copied into the cold summary area.
 */
#define A52_P409_RAM_BYTES          0x00100000U
#define A52_P409_DISK_BYTES         0x00200000U
#define A52_P409_DEBUG_OFFSET       0x00800000ULL
#define A52_P409_HEADER_BYTES       SZ_4K
#define A52_P409_HOT_DISK_OFFSET    A52_P409_HEADER_BYTES
#define A52_P409_COLD_OFFSET        (A52_P409_HOT_DISK_OFFSET + A52_P409_RAM_BYTES)
#define A52_P409_EVENT_BYTES        96U
#define A52_P409_EVENT_CAPACITY     (A52_P409_RAM_BYTES / A52_P409_EVENT_BYTES)
#define A52_P409_BIO_PAGES          128U
#define A52_P409_BIO_BYTES          (A52_P409_BIO_PAGES * PAGE_SIZE)
#define A52_P409_MAGIC              0x3930344953443241ULL /* A2DSI409 */
#define A52_P409_EVENT_COMMIT       0x409e0de5U
#define A52_P409_IMAGE_COMMIT       0x409c0de5U
#define A52_P409_VERSION            1U

enum a52_p409_stage {
	A52_P409_MATCH = 1,
	A52_P409_IRQ_PRE = 2,
	A52_P409_IRQ_POST = 3,
	A52_P409_DMA_PROGRAM = 4,
	A52_P409_TRIGGER_POST = 5,
	A52_P409_WAIT_ENTER = 6,
	A52_P409_ISR = 7,
	A52_P409_WAIT_EXIT = 8,
	A52_P409_FALLBACK = 9,
	A52_P409_FINAL = 10,
};

struct a52_p409_event {
	u64 ns;
	u32 seq;
	u16 stage;
	u16 cpu;
	u32 status;
	u32 fifo;
	u32 clk_ctrl;
	u32 clk_status;
	u32 int_ctrl;
	u32 lane_status;
	u32 dma_ctrl;
	u32 dma_offset;
	u32 dma_length;
	u32 sw_trigger;
	u32 trig_ctrl;
	u32 ack_err;
	u32 timeout;
	u32 phy_err;
	u32 axi2ahb;
	u32 dbg171;
	u32 aux0;
	u32 aux1;
	u32 reserved0;
	u32 commit;
} __packed;

struct a52_p409_header {
	u64 magic;
	u32 version;
	u32 phase;
	u64 capture_ns;
	u32 ram_bytes;
	u32 event_bytes;
	u32 event_capacity;
	u32 event_count;
	u32 event_dropped;
	u32 wait_ret;
	u32 dma_irq_trig;
	u32 phase345_count;
	u32 final_status;
	u32 final_fifo;
	u32 final_clk_ctrl;
	u32 final_clk_status;
	u32 final_int_ctrl;
	u32 final_lane_status;
	u32 final_dma_ctrl;
	u32 final_dma_offset;
	u32 final_dma_length;
	u32 final_sw_trigger;
	u32 final_trig_ctrl;
	u32 final_ack_err;
	u32 final_timeout;
	u32 final_phy_err;
	u32 final_axi2ahb;
	u32 final_dbg171;
	u32 writer_state;
	u32 reserved[31];
} __packed;

struct a52_p409_cold {
	u32 phase345_count;
	u32 reserved0;
	struct a52_p345_sample phase345[A52_P345_MAX];
} __packed;

static u32 a52_p409_crc32c(const void *buffer, size_t len)
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

static struct a52_p409_event *a52_p409_hot;
static u8 *a52_p409_disk;
static atomic_t a52_p409_index = ATOMIC_INIT(0);
static atomic_t a52_p409_dropped = ATOMIC_INIT(0);
static atomic_t a52_p409_frozen = ATOMIC_INIT(0);
static atomic_t a52_p409_writer_state = ATOMIC_INIT(0);
static u32 a52_p409_wait_ret;
static u32 a52_p409_dma_irq_trig;
static struct a52_p409_event a52_p409_final_snapshot;

void a52_p409_init_buffers(void)
{
	BUILD_BUG_ON(sizeof(struct a52_p409_event) != A52_P409_EVENT_BYTES);
	BUILD_BUG_ON(A52_P409_HOT_DISK_OFFSET + A52_P409_RAM_BYTES >
		     A52_P409_DISK_BYTES);
	BUILD_BUG_ON(A52_P409_COLD_OFFSET + sizeof(struct a52_p409_cold) >
		     A52_P409_DISK_BYTES - 8U);
	BUILD_BUG_ON(PAGE_SIZE != 4096);

	if (!READ_ONCE(a52_p409_hot))
		a52_p409_hot = vzalloc(A52_P409_RAM_BYTES);
	if (!READ_ONCE(a52_p409_disk))
		a52_p409_disk = vzalloc(A52_P409_DISK_BYTES);
}

void a52_p409_hot_record(struct dsi_ctrl_hw *ctrl, u16 stage,
			 u32 aux0, u32 aux1)
{
	struct a52_p409_event *e;
	int index;

	if (!READ_ONCE(a52_p409_hot) || atomic_read(&a52_p409_frozen))
		return;
	if (!ctrl || !ctrl->base)
		return;

	index = atomic_inc_return(&a52_p409_index) - 1;
	if (index < 0 || index >= A52_P409_EVENT_CAPACITY) {
		atomic_inc(&a52_p409_dropped);
		return;
	}

	e = &a52_p409_hot[index];
	memset(e, 0, sizeof(*e));
	e->ns = ktime_get_ns();
	e->seq = (u32)index + 1U;
	e->stage = stage;
	e->cpu = (u16)raw_smp_processor_id();
	e->status = DSI_R32(ctrl, DSI_STATUS);
	e->fifo = DSI_R32(ctrl, DSI_FIFO_STATUS);
	e->clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	e->clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	e->int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
	e->lane_status = DSI_R32(ctrl, DSI_LANE_STATUS);
	e->dma_ctrl = DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL);
	e->dma_offset = DSI_R32(ctrl, DSI_DMA_CMD_OFFSET);
	e->dma_length = DSI_R32(ctrl, DSI_DMA_CMD_LENGTH);
	e->sw_trigger = DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER);
	e->trig_ctrl = DSI_R32(ctrl, DSI_TRIG_CTRL);
	e->ack_err = DSI_R32(ctrl, DSI_ACK_ERR_STATUS);
	e->timeout = DSI_R32(ctrl, DSI_TIMEOUT_STATUS);
	e->phy_err = DSI_R32(ctrl, DSI_DLN0_PHY_ERR);
	e->axi2ahb = DSI_R32(ctrl, DSI_AXI2AHB_CTRL);
	e->dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	e->aux0 = aux0;
	e->aux1 = aux1;
	wmb();
	e->commit = A52_P409_EVENT_COMMIT;
}

static int a52_p409_submit_chunk(struct block_device *bdev,
				 size_t offset, size_t bytes)
{
	struct bio *bio;
	unsigned int pages;
	unsigned int i;
	int added;
	int rc;

	if (!bdev || !a52_p409_disk || !bytes || (bytes & (PAGE_SIZE - 1U)))
		return -EINVAL;

	pages = bytes / PAGE_SIZE;
	if (pages > A52_P409_BIO_PAGES)
		return -EINVAL;

	bio = bio_alloc(GFP_KERNEL, pages);
	if (!bio)
		return -ENOMEM;

	bio_set_dev(bio, bdev);
	bio->bi_iter.bi_sector =
		(sector_t)((A52_P409_DEBUG_OFFSET + offset) >> 9);
	bio_set_op_attrs(bio, REQ_OP_WRITE, REQ_SYNC | REQ_FUA);

	for (i = 0; i < pages; i++) {
		void *addr = a52_p409_disk + offset + (size_t)i * PAGE_SIZE;
		struct page *page = vmalloc_to_page(addr);

		if (!page) {
			bio_put(bio);
			return -EFAULT;
		}
		added = bio_add_page(bio, page, PAGE_SIZE, 0);
		if (added != PAGE_SIZE) {
			bio_put(bio);
			return -EIO;
		}
	}

	rc = submit_bio_wait(bio);
	bio_put(bio);
	return rc;
}

static void a52_p409_write_workfn(struct work_struct *work);
static DECLARE_WORK(a52_p409_write_work, a52_p409_write_workfn);

static void a52_p409_build_image(void)
{
	struct a52_p409_header *h;
	struct a52_p409_cold *cold;
	unsigned int n;
	u32 crc;
	u32 commit = A52_P409_IMAGE_COMMIT;

	memset(a52_p409_disk, 0, A52_P409_DISK_BYTES);

	h = (struct a52_p409_header *)a52_p409_disk;
	h->magic = A52_P409_MAGIC;
	h->version = A52_P409_VERSION;
	h->phase = 409U;
	h->capture_ns = ktime_get_boottime_ns();
	h->ram_bytes = A52_P409_RAM_BYTES;
	h->event_bytes = A52_P409_EVENT_BYTES;
	h->event_capacity = A52_P409_EVENT_CAPACITY;
	h->event_count = min_t(u32, (u32)atomic_read(&a52_p409_index),
			      A52_P409_EVENT_CAPACITY);
	h->event_dropped = (u32)atomic_read(&a52_p409_dropped);
	h->wait_ret = a52_p409_wait_ret;
	h->dma_irq_trig = a52_p409_dma_irq_trig;
	h->writer_state = (u32)atomic_read(&a52_p409_writer_state);

	h->final_status = a52_p409_final_snapshot.status;
	h->final_fifo = a52_p409_final_snapshot.fifo;
	h->final_clk_ctrl = a52_p409_final_snapshot.clk_ctrl;
	h->final_clk_status = a52_p409_final_snapshot.clk_status;
	h->final_int_ctrl = a52_p409_final_snapshot.int_ctrl;
	h->final_lane_status = a52_p409_final_snapshot.lane_status;
	h->final_dma_ctrl = a52_p409_final_snapshot.dma_ctrl;
	h->final_dma_offset = a52_p409_final_snapshot.dma_offset;
	h->final_dma_length = a52_p409_final_snapshot.dma_length;
	h->final_sw_trigger = a52_p409_final_snapshot.sw_trigger;
	h->final_trig_ctrl = a52_p409_final_snapshot.trig_ctrl;
	h->final_ack_err = a52_p409_final_snapshot.ack_err;
	h->final_timeout = a52_p409_final_snapshot.timeout;
	h->final_phy_err = a52_p409_final_snapshot.phy_err;
	h->final_axi2ahb = a52_p409_final_snapshot.axi2ahb;
	h->final_dbg171 = a52_p409_final_snapshot.dbg171;

	memcpy(a52_p409_disk + A52_P409_HOT_DISK_OFFSET,
	       a52_p409_hot, A52_P409_RAM_BYTES);

	cold = (struct a52_p409_cold *)(a52_p409_disk + A52_P409_COLD_OFFSET);
	n = (unsigned int)atomic_read(&a52_p345_count);
	if (n > A52_P345_MAX)
		n = A52_P345_MAX;
	cold->phase345_count = n;
	h->phase345_count = n;
	if (n)
		memcpy(cold->phase345, a52_p345_samples,
		       n * sizeof(cold->phase345[0]));

	crc = a52_p409_crc32c(a52_p409_disk, A52_P409_DISK_BYTES - 8U);
	memcpy(a52_p409_disk + A52_P409_DISK_BYTES - 8U, &crc, sizeof(crc));
	memcpy(a52_p409_disk + A52_P409_DISK_BYTES - 4U, &commit,
	       sizeof(commit));
	wmb();
}

static void a52_p409_write_workfn(struct work_struct *work)
{
	struct block_device *bdev;
	dev_t devt = MKDEV(SCSI_DISK0_MAJOR, 8);
	size_t offset;
	int rc = 0;

	(void)work;
	if (!a52_p409_hot || !a52_p409_disk) {
		atomic_set(&a52_p409_writer_state, 3);
		return;
	}

	atomic_set(&a52_p409_writer_state, 1);
	a52_p409_build_image();

	bdev = blkdev_get_by_dev(devt, FMODE_WRITE, NULL);
	if (IS_ERR(bdev)) {
		atomic_set(&a52_p409_writer_state, 3);
		return;
	}

	for (offset = 0; offset < A52_P409_DISK_BYTES;
	     offset += A52_P409_BIO_BYTES) {
		rc = a52_p409_submit_chunk(bdev, offset, A52_P409_BIO_BYTES);
		if (rc)
			break;
	}
	if (!rc)
		rc = blkdev_issue_flush(bdev, GFP_KERNEL);
	blkdev_put(bdev, FMODE_WRITE);

	if (!rc)
		atomic_set(&a52_p409_writer_state, 2);
	else
		atomic_set(&a52_p409_writer_state, 3);
}

void a52_p409_finalize_and_schedule(struct dsi_ctrl_hw *ctrl,
				    u32 wait_ret, u32 dma_irq_trig)
{
	int index;

	if (!a52_p409_hot || !a52_p409_disk || !ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p409_frozen, 0, 1) != 0)
		return;

	/* Final state is captured once; no further hot-buffer writes follow. */
	memset(&a52_p409_final_snapshot, 0, sizeof(a52_p409_final_snapshot));
	a52_p409_final_snapshot.ns = ktime_get_ns();
	a52_p409_final_snapshot.stage = A52_P409_FINAL;
	a52_p409_final_snapshot.cpu = (u16)raw_smp_processor_id();
	a52_p409_final_snapshot.status = DSI_R32(ctrl, DSI_STATUS);
	a52_p409_final_snapshot.fifo = DSI_R32(ctrl, DSI_FIFO_STATUS);
	a52_p409_final_snapshot.clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	a52_p409_final_snapshot.clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	a52_p409_final_snapshot.int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
	a52_p409_final_snapshot.lane_status = DSI_R32(ctrl, DSI_LANE_STATUS);
	a52_p409_final_snapshot.dma_ctrl = DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL);
	a52_p409_final_snapshot.dma_offset = DSI_R32(ctrl, DSI_DMA_CMD_OFFSET);
	a52_p409_final_snapshot.dma_length = DSI_R32(ctrl, DSI_DMA_CMD_LENGTH);
	a52_p409_final_snapshot.sw_trigger = DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER);
	a52_p409_final_snapshot.trig_ctrl = DSI_R32(ctrl, DSI_TRIG_CTRL);
	a52_p409_final_snapshot.ack_err = DSI_R32(ctrl, DSI_ACK_ERR_STATUS);
	a52_p409_final_snapshot.timeout = DSI_R32(ctrl, DSI_TIMEOUT_STATUS);
	a52_p409_final_snapshot.phy_err = DSI_R32(ctrl, DSI_DLN0_PHY_ERR);
	a52_p409_final_snapshot.axi2ahb = DSI_R32(ctrl, DSI_AXI2AHB_CTRL);
	a52_p409_final_snapshot.dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	a52_p409_final_snapshot.aux0 = wait_ret;
	a52_p409_final_snapshot.aux1 = dma_irq_trig;
	a52_p409_final_snapshot.commit = A52_P409_EVENT_COMMIT;

	index = atomic_read(&a52_p409_index);
	a52_p409_final_snapshot.seq =
		(index < A52_P409_EVENT_CAPACITY) ? (u32)index + 1U : 0U;
	a52_p409_wait_ret = wait_ret;
	a52_p409_dma_irq_trig = dma_irq_trig;
	schedule_work(&a52_p409_write_work);
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE408_DIRECT_BIO_DEBUG_DSI_V1",
        "A52_PHASE345_DMA_US_FRONTIER_V1",
        "static u32 a52_p407_crc32c(",
    ):
        if token not in text:
            raise SystemExit("Phase409 HWC prerequisite missing: " + token)

    inc = "#include <linux/mm.h>\n"
    text = one(text, inc, inc + "#include <linux/vmalloc.h>\n", "vmalloc include")

    anchor = "static u32 a52_p345_saved_dbg_ctl;\n"
    text = one(text, anchor, anchor + HOT_BLOCK, "hot/cold block")

    # DMA programmed snapshot, immediately before SW_TRIGGER path.
    old = '''	if (a52_p293_gdm_trace_active()) {
		a52_ackfr_record("P276 303 S05 dc=%x off=%x len=%x fc=%x",
'''
    new = '''	if (a52_p293_gdm_trace_active()) {
		a52_p409_hot_record(ctrl, A52_P409_DMA_PROGRAM, 0, 0);
		a52_ackfr_record("P276 303 S05 dc=%x off=%x len=%x fc=%x",
'''
    text = one(text, old, new, "DMA-program hot hook")

    # There are two production SW_TRIGGER paths. Record once after each
    # Phase345 burst has completed, without adding reads inside p0..p6.
    old = "		a52_p345_end(ctrl);\n"
    if text.count(old) != 2:
        raise SystemExit(f"Phase409 expected two Phase345 end hooks, found {text.count(old)}")
    text = text.replace(
        old,
        old + "		a52_p409_hot_record(ctrl, A52_P409_TRIGGER_POST, 0, 0);\n",
    )
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE405_DMA_DONE_DIRECT_V1",
        "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1",
        "a52_p407_capture_and_schedule(&dsi_ctrl->hw",
    ):
        if token not in text:
            raise SystemExit("Phase409 CTRL prerequisite missing: " + token)

    decl = "extern void a52_p407_capture_and_schedule(struct dsi_ctrl_hw *ctrl, u32 wait_ret, u32 dma_irq_trig); /* A52_PHASE407_SAMSUNG_DEBUG_DSI_V1 */\n"
    text = one(
        text, decl,
        decl +
        "extern void a52_p409_init_buffers(void); /* " + MARK + " */\n"
        "extern void a52_p409_hot_record(struct dsi_ctrl_hw *ctrl, u16 stage, u32 aux0, u32 aux1);\n"
        "extern void a52_p409_finalize_and_schedule(struct dsi_ctrl_hw *ctrl, u32 wait_ret, u32 dma_irq_trig);\n"
        "#define A52_P409_MATCH 1U\n"
        "#define A52_P409_IRQ_PRE 2U\n"
        "#define A52_P409_IRQ_POST 3U\n"
        "#define A52_P409_WAIT_ENTER 6U\n"
        "#define A52_P409_ISR 7U\n"
        "#define A52_P409_WAIT_EXIT 8U\n"
        "#define A52_P409_FALLBACK 9U\n",
        "Phase409 declarations",
    )

    old = '''	a52_ackfr_record("P276 303 S00p p=%02x%02x%02x", p[0], p[1], p[2]);
	a52_p314_ctrl_prestate(dsi_ctrl);
'''
    new = '''	a52_ackfr_record("P276 303 S00p p=%02x%02x%02x", p[0], p[1], p[2]);
	a52_p409_hot_record(&dsi_ctrl->hw, A52_P409_MATCH,
		((u32)p[0] << 16) | ((u32)p[1] << 8) | p[2], *flags);
	a52_p314_ctrl_prestate(dsi_ctrl);
'''
    text = one(text, old, new, "exact-match hot hook")

    old = '''	a52_ackfr_record(stage ? "P276 303 S04b ln=%x ck=%x" : "P276 303 S03b ln=%x ck=%x",
		DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
		DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS));
}
'''
    new = '''	a52_ackfr_record(stage ? "P276 303 S04b ln=%x ck=%x" : "P276 303 S03b ln=%x ck=%x",
		DSI_R32(&dsi_ctrl->hw, DSI_LANE_STATUS),
		DSI_R32(&dsi_ctrl->hw, DSI_CLK_STATUS));
	a52_p409_hot_record(&dsi_ctrl->hw,
		stage ? A52_P409_IRQ_POST : A52_P409_IRQ_PRE,
		(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
		DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
}
'''
    text = one(text, old, new, "IRQ arm hot hook")

    old = '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387D e trig=%d irqn=%d sw=%x ref=%u hw=%x",
			atomic_read(&dsi_ctrl->dma_irq_trig),
			dsi_ctrl->irq_info.irq_num,
			dsi_ctrl->irq_info.irq_stat_mask,
			dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE],
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
'''
    new = old + '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_p409_hot_record(&dsi_ctrl->hw, A52_P409_WAIT_ENTER,
			(u32)dsi_ctrl->irq_info.irq_num,
			dsi_ctrl->irq_info.irq_stat_mask);
'''
    text = one(text, old, new, "wait-enter hot hook")

    old = '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387D w ret=%d trig=%d hw=%x",
			ret, atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
'''
    new = old + '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_p409_hot_record(&dsi_ctrl->hw, A52_P409_WAIT_EXIT,
			(u32)ret, (u32)atomic_read(&dsi_ctrl->dma_irq_trig));
'''
    text = one(text, old, new, "wait-exit hot hook")

    old = '''			a52_ackfr_record("P276 387D f st=%x done=%u hw=%x",
				status, !!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
'''
    new = old + '''			a52_p409_hot_record(&dsi_ctrl->hw, A52_P409_FALLBACK,
				status, !!(status & DSI_CMD_MODE_DMA_DONE));
'''
    text = one(text, old, new, "fallback hot hook")

    old = '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387I irq=%d st=%x raw=%x err=%llx",
			irq, status, DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			(unsigned long long)errors);
'''
    new = old + '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_p409_hot_record(&dsi_ctrl->hw, A52_P409_ISR,
			status, (u32)errors);
'''
    text = one(text, old, new, "ISR hot hook")

    old = '''		a52_p407_capture_and_schedule(&dsi_ctrl->hw, (u32)ret,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
'''
    new = '''		a52_p409_finalize_and_schedule(&dsi_ctrl->hw, (u32)ret,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
'''
    text = one(text, old, new, "replace Phase407 writer")

    old = '''int dsi_ctrl_drv_init(struct dsi_ctrl *dsi_ctrl, struct dentry *parent)
{
	int rc = 0;

	a52_p346_sideband_init();
'''
    new = '''int dsi_ctrl_drv_init(struct dsi_ctrl *dsi_ctrl, struct dentry *parent)
{
	int rc = 0;

	a52_p409_init_buffers();
	a52_p346_sideband_init();
'''
    text = one(text, old, new, "buffer init")
    return text


def validate(root: Path) -> None:
    hwc = (root / HWC).read_text(errors="replace")
    ctrl = (root / CTRL).read_text(errors="replace")

    for token in (
        MARK,
        "#define A52_P409_RAM_BYTES          0x00100000U",
        "#define A52_P409_DISK_BYTES         0x00200000U",
        "#define A52_P409_DEBUG_OFFSET       0x00800000ULL",
        "#define A52_P409_EVENT_BYTES        96U",
        "a52_p409_hot = vzalloc(A52_P409_RAM_BYTES)",
        "a52_p409_disk = vzalloc(A52_P409_DISK_BYTES)",
        "A52_P409_EVENT_CAPACITY",
        "A52_P409_EVENT_COMMIT",
        "A52_P409_IMAGE_COMMIT",
        "vmalloc_to_page(addr)",
        "submit_bio_wait(bio)",
        "blkdev_issue_flush(bdev, GFP_KERNEL)",
        "memcpy(a52_p409_disk + A52_P409_HOT_DISK_OFFSET",
        "a52_p409_crc32c(a52_p409_disk, A52_P409_DISK_BYTES - 8U)",
        "schedule_work(&a52_p409_write_work)",
    ):
        if token not in hwc:
            raise SystemExit("Phase409 HWC token missing: " + token)

    for token in (
        MARK,
        "a52_p409_init_buffers();",
        "A52_P409_MATCH",
        "A52_P409_IRQ_PRE",
        "A52_P409_IRQ_POST",
        "A52_P409_WAIT_ENTER",
        "A52_P409_ISR",
        "A52_P409_WAIT_EXIT",
        "A52_P409_FALLBACK",
        "a52_p409_finalize_and_schedule(&dsi_ctrl->hw",
    ):
        if token not in ctrl:
            raise SystemExit("Phase409 CTRL token missing: " + token)

    if "a52_p407_capture_and_schedule(&dsi_ctrl->hw" in ctrl:
        raise SystemExit("Phase409 must not schedule old 4 KiB Samsung record")
    if hwc.count("submit_bio_wait(bio)") != 2:
        # one inherited, dormant Phase408 submit site + one Phase409 chunk helper
        raise SystemExit("Phase409 expected inherited + active BIO submit sites")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (HWC, CTRL):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase409 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))
        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase409 1MiB RAM + 2MiB Samsung debug DSI instrumentation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
