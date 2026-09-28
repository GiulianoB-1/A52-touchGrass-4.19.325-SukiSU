#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE407_SAMSUNG_DEBUG_DSI_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase407 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE407_SAMSUNG_DEBUG_DSI_V1
 *
 * First Samsung debug-partition experiment:
 *   - ONE record only
 *   - ONE physical location only
 *   - CRC32C + commit marker
 *   - NO duplicate copy
 *   - NO R48 mirror
 *
 * The timing-sensitive exact-F0 burst remains RAM-only.  After the DMA wait
 * finishes, a compact binary snapshot is assembled in RAM and a workqueue
 * writes one 4 KiB page to the final page of Samsung's 10 MiB "debug"
 * partition (offset 0x9ff000).  The asynchronous block write therefore cannot
 * perturb SW_TRIGGER or the microsecond DMA_DONE observation window.
 */
#define A52_P407_RECORD_BYTES      4096U
#define A52_P407_DEBUG_OFFSET      0x009ff000ULL
#define A52_P407_MAGIC             0x3730344953443241ULL /* A2DSI407 */
#define A52_P407_COMMIT            0x407c0de5U
#define A52_P407_VERSION           1U

struct a52_p407_payload {
	u64 magic;
	u32 version;
	u32 phase;
	u64 capture_ns;
	u32 sample_count;
	u32 wait_ret;
	u32 dma_irq_trig;
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
	struct a52_p345_sample samples[A52_P345_MAX];
} __packed;

static u8 a52_p407_page[A52_P407_RECORD_BYTES] __aligned(64);
static atomic_t a52_p407_state = ATOMIC_INIT(0); /* 0 idle, 1 captured, 2 written, 3 failed */

static u32 a52_p407_crc32c(const void *buffer, size_t len)
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

static void a52_p407_write_workfn(struct work_struct *work);
static DECLARE_WORK(a52_p407_write_work, a52_p407_write_workfn);

static struct file *a52_p407_open_debug_partition(void)
{
	struct file *file;

	file = filp_open("/dev/block/by-name/debug",
		O_WRONLY | O_LARGEFILE | O_DSYNC, 0);
	if (!IS_ERR(file))
		return file;

	/* Observed A52 mapping in recovery: debug == /dev/block/sda8. */
	return filp_open("/dev/block/sda8",
		O_WRONLY | O_LARGEFILE | O_DSYNC, 0);
}

static void a52_p407_write_workfn(struct work_struct *work)
{
	struct file *file;
	loff_t pos = (loff_t)A52_P407_DEBUG_OFFSET;
	ssize_t written;
	int rc;

	(void)work;
	file = a52_p407_open_debug_partition();
	if (IS_ERR(file)) {
		atomic_set(&a52_p407_state, 3);
		return;
	}

	written = kernel_write(file, a52_p407_page,
		A52_P407_RECORD_BYTES, &pos);
	rc = (written == A52_P407_RECORD_BYTES) ? vfs_fsync(file, 0) : -EIO;
	filp_close(file, NULL);

	if (written == A52_P407_RECORD_BYTES && !rc)
		atomic_set(&a52_p407_state, 2);
	else
		atomic_set(&a52_p407_state, 3);
}

void a52_p407_capture_and_schedule(struct dsi_ctrl_hw *ctrl,
				   u32 wait_ret, u32 dma_irq_trig)
{
	struct a52_p407_payload payload;
	unsigned int n;
	u32 crc;
	u32 commit = A52_P407_COMMIT;

	BUILD_BUG_ON(sizeof(payload) + 8U > A52_P407_RECORD_BYTES);
	if (!ctrl || !ctrl->base)
		return;
	if (atomic_cmpxchg(&a52_p407_state, 0, 1) != 0)
		return;

	memset(&payload, 0, sizeof(payload));
	payload.magic = A52_P407_MAGIC;
	payload.version = A52_P407_VERSION;
	payload.phase = 407U;
	payload.capture_ns = ktime_get_boottime_ns();
	n = (unsigned int)atomic_read(&a52_p345_count);
	if (n > A52_P345_MAX)
		n = A52_P345_MAX;
	payload.sample_count = n;
	payload.wait_ret = wait_ret;
	payload.dma_irq_trig = dma_irq_trig;

	payload.final_status = DSI_R32(ctrl, DSI_STATUS);
	payload.final_fifo = DSI_R32(ctrl, DSI_FIFO_STATUS);
	payload.final_clk_ctrl = DSI_R32(ctrl, DSI_CLK_CTRL);
	payload.final_clk_status = DSI_R32(ctrl, DSI_CLK_STATUS);
	payload.final_int_ctrl = DSI_R32(ctrl, DSI_INT_CTRL);
	payload.final_lane_status = DSI_R32(ctrl, DSI_LANE_STATUS);
	payload.final_dma_ctrl = DSI_R32(ctrl, DSI_COMMAND_MODE_DMA_CTRL);
	payload.final_dma_offset = DSI_R32(ctrl, DSI_DMA_CMD_OFFSET);
	payload.final_dma_length = DSI_R32(ctrl, DSI_DMA_CMD_LENGTH);
	payload.final_sw_trigger = DSI_R32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER);
	payload.final_trig_ctrl = DSI_R32(ctrl, DSI_TRIG_CTRL);
	payload.final_ack_err = DSI_R32(ctrl, DSI_ACK_ERR_STATUS);
	payload.final_timeout = DSI_R32(ctrl, DSI_TIMEOUT_STATUS);
	payload.final_phy_err = DSI_R32(ctrl, DSI_DLN0_PHY_ERR);
	payload.final_axi2ahb = DSI_R32(ctrl, DSI_AXI2AHB_CTRL);
	payload.final_dbg171 = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);

	if (n)
		memcpy(payload.samples, a52_p345_samples,
		       n * sizeof(payload.samples[0]));

	memset(a52_p407_page, 0, sizeof(a52_p407_page));
	memcpy(a52_p407_page, &payload, sizeof(payload));
	crc = a52_p407_crc32c(a52_p407_page, A52_P407_RECORD_BYTES - 8U);
	memcpy(a52_p407_page + A52_P407_RECORD_BYTES - 8U, &crc, sizeof(crc));
	memcpy(a52_p407_page + A52_P407_RECORD_BYTES - 4U, &commit, sizeof(commit));
	wmb();

	schedule_work(&a52_p407_write_work);
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE345_DMA_US_FRONTIER_V1" not in text:
        raise SystemExit("Phase407 requires Phase345 microsecond samples")

    inc = "#include <asm/cacheflush.h>\n"
    repl = (
        inc +
        "#include <linux/fs.h>\n"
        "#include <linux/workqueue.h>\n"
        "#include <linux/err.h>\n"
        "#include <linux/errno.h>\n"
    )
    text = one(text, inc, repl, "I/O includes")

    anchor = "static u32 a52_p345_saved_dbg_ctl;\n"
    text = one(text, anchor, anchor + BLOCK, "Samsung debug writer")
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE405_DMA_DONE_DIRECT_V1" not in text:
        raise SystemExit("Phase407 requires Phase405 original DMA path")

    decl = "extern void a52_p345_flush(struct dsi_ctrl_hw *ctrl); /* A52_PHASE345_DMA_US_FRONTIER_V1 */\n"
    text = one(
        text,
        decl,
        decl +
        "extern void a52_p407_capture_and_schedule(struct dsi_ctrl_hw *ctrl, u32 wait_ret, u32 dma_irq_trig); /* " + MARK + " */\n",
        "capture declaration",
    )

    old = '''		a52_ackfr_record("P276 303 S08 ret=%d irq=%d in=%x st=%x", ret,
			atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			DSI_R32(&dsi_ctrl->hw, DSI_STATUS));
'''
    new = old + '''		a52_p407_capture_and_schedule(&dsi_ctrl->hw, (u32)ret,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
'''
    return one(text, old, new, "post-wait capture")


def validate(root: Path) -> None:
    hwc = (root / HWC).read_text(errors="replace")
    ctrl = (root / CTRL).read_text(errors="replace")

    for token in (
        MARK,
        "#define A52_P407_RECORD_BYTES      4096U",
        "#define A52_P407_DEBUG_OFFSET      0x009ff000ULL",
        'filp_open("/dev/block/by-name/debug"',
        'filp_open("/dev/block/sda8"',
        "kernel_write(file, a52_p407_page",
        "vfs_fsync(file, 0)",
        "a52_p407_crc32c(",
        "A52_P407_COMMIT",
        "schedule_work(&a52_p407_write_work)",
        "struct a52_p345_sample samples[A52_P345_MAX]",
    ):
        if token not in hwc:
            raise SystemExit("Phase407 HWC token missing: " + token)

    for token in (
        MARK,
        "a52_p407_capture_and_schedule(&dsi_ctrl->hw",
        "P276 303 S08 ret=%d irq=%d in=%x st=%x",
    ):
        if token not in ctrl:
            raise SystemExit("Phase407 CTRL token missing: " + token)

    if hwc.count("kernel_write(file, a52_p407_page") != 1:
        raise SystemExit("Phase407 must have exactly one partition write site")
    if "a52_ackfr_record(" in BLOCK:
        raise SystemExit("Phase407 Samsung writer must not mirror into R48")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (HWC, CTRL):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase407 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))
        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase407 single-copy Samsung debug DSI recorder: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
