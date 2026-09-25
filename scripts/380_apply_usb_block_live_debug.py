#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE380_USB_BLOCK_LIVE_DEBUG_V1"

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
BLK = Path("block/blk-mq.c")
LOOP = Path("drivers/block/loop.c")
USB = Path("drivers/usb/gadget/function/u_serial.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase380 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if "A52_PHASE380_LEGACY_PROBES_RETIRED_V1" in text:
        return text

    for old, new, label in (
        ("static void a52_r340_start(void)\n",
         "static void __used a52_r340_start(void)\n", "r340 unused"),
        ("static void a52_r341_start(void)\n",
         "static void __used a52_r341_start(void)\n", "r341 unused"),
        ("static void a52_r343_start(void)\n",
         "static void __used a52_r343_start(void)\n", "r343 unused"),
    ):
        text = one(text, old, new, label)

    old = """\ta52_ackfr_record("HB start interval_ms=%u limit=%u",
\t\t\t  A52_R179_HEARTBEAT_INTERVAL_MS,
\t\t\t  A52_R179_HEARTBEAT_LIMIT);
\tschedule_delayed_work(&a52_r179_heartbeat_work,
\t\tmsecs_to_jiffies(A52_R179_HEARTBEAT_INTERVAL_MS));
\ta52_r274_frontier_start_jiffies = jiffies;
\tschedule_delayed_work(&a52_r273_frontier_work,
\t\tmsecs_to_jiffies(A52_R273_SCAN_FAST_MS));
\ta52_ackfr_record("P274 START tb=elapsed q=%u/%u s=%u",
\t\tA52_R273_SCAN_FAST_MS, A52_R273_SCAN_SLOW_MS,
\t\tA52_R273_SUMMARY_S);
\ta52_ackfr_record("P273 START h=%u q=%u/%u s=%u",
\t\tA52_R273_FRONTIER_END_S, A52_R273_SCAN_FAST_MS,
\t\tA52_R273_SCAN_SLOW_MS, A52_R273_SUMMARY_S);
\ta52_ackfr_record("P276 339A first=10 final=330 warm=1");
\ta52_r339_checkpoint_index = 0;
\tschedule_delayed_work(&a52_r339_preservation_work,
\t\tmsecs_to_jiffies(a52_r339_checkpoints_s[0] * 1000U));
\ta52_r340_start();
\ta52_r341_start();
\t/* Phase358 owns the Phase357 sideband at runtime. */
\ta52_r343_start();
"""
    new = """\t/*
\t * A52_PHASE380_LEGACY_PROBES_RETIRED_V1
\t *
\t * The old post-display frontier probes are no longer relevant to the
\t * UFS/loop/block stall and some actively perturb this boot:
\t *  - Phase273 + heartbeat consume system_wq
\t *  - Phase339 eventually forces a recovery restart
\t *  - Phase340 owns the old 0xB1BFF800 sideband
\t *  - Phase341 pins hardirq timers into 0xB1BFC000
\t *  - Phase343 runs a SCHED_FIFO busy loop on CPU5 and owns 0xB1BF8000
\t *
\t * Preserve their source for lineage, but do not start them at runtime.
\t * Phase380 owns 0xB1BF8000..0xB1BFFFFF exclusively.
\t */
\ta52_ackfr_record("P380 legacy probes retired hb=1 p273=1 p339=1 p340=1 p341=1 p343=1");
"""
    text = one(text, old, new, "retire legacy runtime probes")

    crit_old = """\t       !strncmp(message, "A52GDSC ", 8);
"""
    crit_new = """\t       !strncmp(message, "A52GDSC ", 8) ||
\t       !strncmp(message, "U380 ", 5) ||
\t       !strncmp(message, "B380 ", 5) ||
\t       !strncmp(message, "L380 ", 5) ||
\t       !strncmp(message, "USB380 ", 7);
"""
    text = one(text, crit_old, crit_new, "critical Phase380 prefixes")
    return text


UFS_BLOCK = r'''
/* A52_PHASE380_USB_BLOCK_LIVE_DEBUG_V1
 *
 * Phase379 reused 0xB1BF8000 while inherited Phase343 still owned that
 * address. Phase380 retires the old Phase273/339/340/341/343 runtime probes
 * and takes exclusive ownership of the final 32 KiB pmsg window.
 *
 * Raw records are fixed 256-byte records with CRC32C and THREE independent
 * physical copies. Compact copies are also sent through a52_ackfr_record(),
 * which retains the existing RS48 + CRC32C + three-RAMOOPS-bank transport.
 *
 * This sampler uses a dedicated kthread, not system_wq.
 */
#define A52_R380_SIDEBAND_PHYS       0xB1BF8000ULL
#define A52_R380_SIDEBAND_BYTES      0x8000U
#define A52_R380_COPY_BYTES          0x2A00U
#define A52_R380_SLOT_BYTES          256U
#define A52_R380_SLOTS_PER_COPY      42U
#define A52_R380_SNAPSHOT_COUNT      6U
#define A52_R380_TAGS_PER_SNAPSHOT   6U
#define A52_R380_MAGIC               0x3038335353465555ULL
#define A52_R380_COMMIT              0x380c0de5U
#define A52_R380_VERSION             1U
#define A52_R380_TYPE_SNAPSHOT       1U
#define A52_R380_TYPE_TAG            2U

struct a52_r380_record {
	u64 magic;
	u64 ns;
	u64 cmd;
	s64 issue_ns;
	s64 age_ms;
	u64 issue_count;
	u64 trc_count;
	u64 seen_count;
	u64 hook_count;
	u64 done_count;
	unsigned long outstanding;
	unsigned long completed;
	u32 snapshot_id;
	u32 type;
	u32 tag;
	u32 doorbell;
	u32 state;
	u32 link;
	u32 dev_pwr;
	u32 clk_state;
	u32 rpm;
	u32 ghost_count;
	u32 last_issue_tag;
	u32 last_seen_tag;
	u32 last_hook_tag;
	u32 last_done_tag;
	u32 db_bit;
	u32 out_bit;
	u32 command_type;
	u32 scsi_opcode;
	u32 nutrs;
	u32 active_tags;
	char label[32];
	u8 reserved[36];
	u32 crc32c;
	u32 commit;
	u32 version;
};

static const char a52_r380_marker[] __used =
	"A52_PHASE380_USB_BLOCK_LIVE_DEBUG_V1";
static void *a52_r380_sideband;
static struct ufs_hba *a52_r380_hba;
static struct task_struct *a52_r380_sampler_task;
static unsigned int a52_r380_slot;

static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
	15000U, 16000U, 16500U, 17000U, 18000U, 19000U,
};

static u32 a52_r380_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				((crc & 1) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

static void a52_r380_write_slot(struct a52_r380_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;
	void *dst2;

	if (!READ_ONCE(a52_r380_sideband) || !r ||
	    a52_r380_slot >= A52_R380_SLOTS_PER_COPY)
		return;

	r->crc32c = a52_r380_crc32c(r, offsetof(struct a52_r380_record, crc32c));
	r->commit = A52_R380_COMMIT;
	r->version = A52_R380_VERSION;

	pos = a52_r380_slot * A52_R380_SLOT_BYTES;
	dst0 = (u8 *)a52_r380_sideband + pos;
	dst1 = (u8 *)a52_r380_sideband + A52_R380_COPY_BYTES + pos;
	dst2 = (u8 *)a52_r380_sideband + 2U * A52_R380_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	memcpy(dst2, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
	__flush_dcache_area(dst2, sizeof(*r));
	a52_r380_slot++;
}

static void a52_r380_fill_common(struct a52_r380_record *r,
				 struct ufs_hba *hba,
				 unsigned int snapshot_id,
				 u32 doorbell,
				 unsigned long outstanding,
				 unsigned int ghost_count,
				 unsigned int active_tags)
{
	memset(r, 0, sizeof(*r));
	r->magic = A52_R380_MAGIC;
	r->ns = ktime_get_boottime_ns();
	r->issue_count = (u64)atomic64_read(&a52_r378_issue_count);
	r->trc_count = (u64)atomic64_read(&a52_r378_trc_count);
	r->seen_count = (u64)atomic64_read(&a52_r378_seen_count);
	r->hook_count = (u64)atomic64_read(&a52_r378_hook_done_count);
	r->done_count = (u64)atomic64_read(&a52_r378_scsi_done_count);
	r->outstanding = outstanding;
	r->completed = READ_ONCE(a52_r378_last_completed);
	r->snapshot_id = snapshot_id;
	r->doorbell = doorbell;
	r->state = READ_ONCE(hba->ufshcd_state);
	r->link = READ_ONCE(hba->uic_link_state);
	r->dev_pwr = READ_ONCE(hba->curr_dev_pwr_mode);
	r->clk_state = READ_ONCE(hba->clk_gating.state);
	r->rpm = pm_runtime_active(hba->dev);
	r->ghost_count = ghost_count;
	r->last_issue_tag = READ_ONCE(a52_r378_last_issue_tag);
	r->last_seen_tag = READ_ONCE(a52_r378_last_seen_tag);
	r->last_hook_tag = READ_ONCE(a52_r378_last_hook_tag);
	r->last_done_tag = READ_ONCE(a52_r378_last_scsi_done_tag);
	r->nutrs = hba->nutrs;
	r->active_tags = active_tags;
}

static void a52_r380_take_snapshot(unsigned int snapshot_id)
{
	struct ufs_hba *hba = READ_ONCE(a52_r380_hba);
	struct a52_r380_record r;
	unsigned long outstanding;
	u32 doorbell = 0;
	unsigned int i, limit;
	unsigned int ghost_count = 0;
	unsigned int active_tags = 0;
	unsigned int written_tags = 0;
	u64 now_ns;

	if (!hba)
		return;

	now_ns = ktime_get_boottime_ns();
	if (pm_runtime_active(hba->dev))
		doorbell = ufshcd_readl(hba, REG_UTP_TRANSFER_REQ_DOOR_BELL);
	outstanding = READ_ONCE(hba->outstanding_reqs);
	limit = min_t(unsigned int, hba->nutrs, A52_R378_MAX_TAGS);

	for (i = 0; i < limit; i++) {
		struct scsi_cmnd *cmd = READ_ONCE(hba->lrb[i].cmd);
		bool db = !!(doorbell & BIT(i));
		bool out = test_bit(i, &outstanding);

		if (cmd || db || out)
			active_tags++;
		if (cmd && !db && !out)
			ghost_count++;
	}

	a52_r380_fill_common(&r, hba, snapshot_id, doorbell, outstanding,
			      ghost_count, active_tags);
	r.type = A52_R380_TYPE_SNAPSHOT;
	r.tag = ~0U;
	strscpy(r.label, "UFS_SNAPSHOT", sizeof(r.label));
	a52_r380_write_slot(&r);
	a52_ackfr_record("U380 S%u db=%x out=%lx g=%u i=%lld h=%lld d=%lld",
			 snapshot_id, doorbell, outstanding, ghost_count,
			 (long long)r.issue_count, (long long)r.hook_count,
			 (long long)r.done_count);

	for (i = 0; i < limit && written_tags < A52_R380_TAGS_PER_SNAPSHOT; i++) {
		struct ufshcd_lrb *lrbp = &hba->lrb[i];
		struct scsi_cmnd *cmd = READ_ONCE(lrbp->cmd);
		bool db = !!(doorbell & BIT(i));
		bool out = test_bit(i, &outstanding);
		s64 issue_ns;

		if (!cmd && !db && !out)
			continue;

		a52_r380_fill_common(&r, hba, snapshot_id, doorbell, outstanding,
				      ghost_count, active_tags);
		r.type = A52_R380_TYPE_TAG;
		r.tag = i;
		r.cmd = (u64)(unsigned long)cmd;
		issue_ns = ktime_to_ns(READ_ONCE(lrbp->issue_time_stamp));
		r.issue_ns = issue_ns;
		r.age_ms = issue_ns > 0 ? div_s64((s64)now_ns - issue_ns,
						  NSEC_PER_MSEC) : -1;
		r.db_bit = db;
		r.out_bit = out;
		r.command_type = READ_ONCE(lrbp->command_type);
		if (cmd)
			r.scsi_opcode = READ_ONCE(cmd->cmnd[0]);
		strscpy(r.label, "UFS_TAG", sizeof(r.label));
		a52_r380_write_slot(&r);
		a52_ackfr_record("U380 T%u/%u db=%u o=%u age=%lld op=%02x",
				 snapshot_id, i, db, out,
				 (long long)r.age_ms, r.scsi_opcode);
		written_tags++;
	}
}

static int a52_r380_sampler_fn(void *unused)
{
	unsigned int next = 0;
	u64 now_ms;

	(void)unused;
	while (!kthread_should_stop() && next < A52_R380_SNAPSHOT_COUNT) {
		now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
		if (now_ms >= a52_r380_target_ms[next]) {
			a52_r380_take_snapshot(next);
			next++;
			continue;
		}
		if (msleep_interruptible(10) && kthread_should_stop())
			break;
	}
	return 0;
}

static void a52_r380_start_sampler(struct ufs_hba *hba)
{
	BUILD_BUG_ON(sizeof(struct a52_r380_record) != A52_R380_SLOT_BYTES);
	BUILD_BUG_ON(A52_R380_SLOTS_PER_COPY * A52_R380_SLOT_BYTES !=
		     A52_R380_COPY_BYTES);
	BUILD_BUG_ON(3U * A52_R380_COPY_BYTES > A52_R380_SIDEBAND_BYTES);

	if (READ_ONCE(a52_r380_sampler_task))
		return;

	a52_r380_sideband = memremap(A52_R380_SIDEBAND_PHYS,
		A52_R380_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r380_sideband) {
		a52_ackfr_record("U380 map_fail");
		return;
	}

	memset(a52_r380_sideband, 0, A52_R380_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_r380_sideband, A52_R380_SIDEBAND_BYTES);
	WRITE_ONCE(a52_r380_hba, hba);
	a52_r380_slot = 0;
	a52_ackfr_record("U380 READY nutrs=%u ver=%x copies=3 crc=crc32c",
			 hba->nutrs, hba->ufs_version);
	a52_r380_sampler_task =
		kthread_run(a52_r380_sampler_fn, NULL, "a52_r380_ufs");
	if (IS_ERR(a52_r380_sampler_task)) {
		a52_ackfr_record("U380 kthread_err=%ld",
				 PTR_ERR(a52_r380_sampler_task));
		a52_r380_sampler_task = NULL;
	}
}

'''


def patch_ufs(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE379_UFS_SIDEBAND_SAMPLER_V1" not in text:
        raise SystemExit("Phase380 requires Phase379 UFS lineage")

    text = one(text,
        "static void a52_r378_start_snapshots(struct ufs_hba *hba)\n",
        "static void __used a52_r378_start_snapshots(struct ufs_hba *hba)\n",
        "retire Phase378 scheduler")
    text = one(text,
        "static void a52_r379_start_sampler(struct ufs_hba *hba)\n",
        "static void __used a52_r379_start_sampler(struct ufs_hba *hba)\n",
        "retire Phase379 sampler")

    anchor = "/**\n * ufshcd_send_command - Send SCSI or device management commands\n"
    text = one(text, anchor, UFS_BLOCK + anchor, "Phase380 UFS block")

    old = """\tif (!ret) {
\t\ta52_r378_start_snapshots(hba);
\t\ta52_r379_start_sampler(hba);
\t}
out:
"""
    new = """\tif (!ret)
\t\ta52_r380_start_sampler(hba);
out:
"""
    text = one(text, old, new, "Phase380 sampler owner")
    return text


def patch_blk(text: str) -> str:
    if "A52_PHASE380_BLK_FREEZE_TRACE_V1" in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        anchor = "#include <linux/blkdev.h>\n"
        text = one(text, anchor, anchor + inc, "blk recorder include")

    fn = "int blk_mq_update_nr_requests(struct request_queue *q, unsigned int nr)\n"
    if fn not in text:
        raise SystemExit("Phase380 blk update function missing")

    text = one(text, fn,
        """/* A52_PHASE380_BLK_FREEZE_TRACE_V1 */
static atomic_t a52_r380_blk_resize_id = ATOMIC_INIT(0);

""" + fn, "blk trace state")

    old = """{
	struct blk_mq_tag_set *set = q->tag_set;
	struct blk_mq_hw_ctx *hctx;
	int i, ret;
"""
    new = """{
	struct blk_mq_tag_set *set = q->tag_set;
	struct blk_mq_hw_ctx *hctx;
	int i, ret;
	int a52_id = atomic_inc_return(&a52_r380_blk_resize_id);
	bool a52_trace = a52_id <= 24;

	if (a52_trace)
		a52_ackfr_record("B380 id=%d enter q=%px old=%u new=%u",
				 a52_id, q, q->nr_requests, nr);
"""
    text = one(text, old, new, "blk update entry")

    old = """	blk_mq_freeze_queue(q);
	blk_mq_quiesce_queue(q);

	ret = 0;
"""
    new = """	blk_mq_freeze_queue(q);
	if (a52_trace)
		a52_ackfr_record("B380 id=%d frozen q=%px", a52_id, q);
	blk_mq_quiesce_queue(q);
	if (a52_trace)
		a52_ackfr_record("B380 id=%d quiesced q=%px", a52_id, q);

	ret = 0;
"""
    text = one(text, old, new, "blk freeze milestones")

    old = """	blk_mq_unquiesce_queue(q);
	blk_mq_unfreeze_queue(q);

	return ret;
}
"""
    new = """	blk_mq_unquiesce_queue(q);
	blk_mq_unfreeze_queue(q);
	if (a52_trace)
		a52_ackfr_record("B380 id=%d exit q=%px ret=%d nr=%u",
				 a52_id, q, ret, q->nr_requests);

	return ret;
}
"""
    text = one(text, old, new, "blk update exit")
    return text


def patch_loop(text: str) -> str:
    if "A52_PHASE380_LOOP_TRACE_V1" in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        anchor = "#include <linux/blk-mq.h>\n"
        if anchor not in text:
            anchor = "#include <linux/blkdev.h>\n"
        text = one(text, anchor, anchor + inc, "loop recorder include")

    marker = "static void lo_complete_rq(struct request *rq)\n"
    text = one(text, marker,
        """/* A52_PHASE380_LOOP_TRACE_V1 */
static atomic_t a52_r380_loop_issue_id = ATOMIC_INIT(0);
static atomic_t a52_r380_loop_done_id = ATOMIC_INIT(0);

""" + marker, "loop trace state")

    old = """static void lo_complete_rq(struct request *rq)
{
	struct loop_cmd *cmd = blk_mq_rq_to_pdu(rq);
	blk_status_t ret = BLK_STS_OK;
"""
    new = """static void lo_complete_rq(struct request *rq)
{
	struct loop_cmd *cmd = blk_mq_rq_to_pdu(rq);
	struct loop_device *lo = rq->q->queuedata;
	blk_status_t ret = BLK_STS_OK;
	int a52_id = atomic_inc_return(&a52_r380_loop_done_id);

	if (a52_id <= 48)
		a52_ackfr_record("L380 D id=%d n=%d q=%px rq=%px aio=%u r=%d",
				 a52_id, lo ? lo->lo_number : -1, rq->q, rq,
				 cmd->use_aio, cmd->ret);
"""
    text = one(text, old, new, "loop completion entry")

    old = """	blk_mq_start_request(rq);

	if (lo->lo_state != Lo_bound)
"""
    new = """	blk_mq_start_request(rq);

	{
		int a52_id = atomic_inc_return(&a52_r380_loop_issue_id);
		if (a52_id <= 48)
			a52_ackfr_record("L380 I id=%d n=%d q=%px rq=%px op=%u",
					 a52_id, lo->lo_number, rq->q, rq,
					 req_op(rq));
	}

	if (lo->lo_state != Lo_bound)
"""
    text = one(text, old, new, "loop issue entry")

    old = """	lo->lo_queue->queuedata = lo;

	blk_queue_max_hw_sectors(lo->lo_queue, BLK_DEF_MAX_SECTORS);
"""
    new = """	lo->lo_queue->queuedata = lo;
	a52_ackfr_record("L380 MAP n=%d q=%px depth=%u",
			 i, lo->lo_queue, lo->tag_set.queue_depth);

	blk_queue_max_hw_sectors(lo->lo_queue, BLK_DEF_MAX_SECTORS);
"""
    text = one(text, old, new, "loop map")
    return text


def patch_usb(text: str) -> str:
    if "A52_PHASE380_USB_KTHREAD_CONSOLE_V1" in text:
        return text

    text = one(text,
        "#define GS_CONSOLE_BUF_SIZE\t8192\n",
        "#define GS_CONSOLE_BUF_SIZE\t(256 * 1024)\n",
        "USB console FIFO")

    old = """struct gs_console {
	struct console		console;
	struct work_struct	work;
	spinlock_t		lock;
	struct usb_request	*req;
	struct kfifo		buf;
	size_t			missed;
};
"""
    new = """/* A52_PHASE380_USB_KTHREAD_CONSOLE_V1 */
struct gs_console {
	struct console		console;
	struct task_struct	*task;
	wait_queue_head_t	wait;
	atomic_t		kick;
	spinlock_t		lock;
	struct usb_request	*req;
	struct kfifo		buf;
	size_t			missed;
};
"""
    text = one(text, old, new, "USB console state")

    complete_anchor = "static void gs_console_complete_out(struct usb_ep *ep, struct usb_request *req)\n"
    helper = r'''static void a52_gs_console_kick(struct gs_console *cons)
{
	atomic_set(&cons->kick, 1);
	wake_up_interruptible(&cons->wait);
}

'''
    text = one(text, complete_anchor, helper + complete_anchor,
               "USB console kick helper")

    # There are exactly three console-path schedule_work() calls in 5.10:
    # completion, console write and connect.
    n = text.count("schedule_work(&cons->work);")
    if n != 3:
        raise SystemExit(f"Phase380 USB expected 3 console schedule_work sites, found {n}")
    text = text.replace("schedule_work(&cons->work);", "a52_gs_console_kick(cons);")

    old = """static void gs_console_work(struct work_struct *work)
{
	struct gs_console *cons = container_of(work, struct gs_console, work);

	spin_lock_irq(&cons->lock);

	__gs_console_push(cons);

	spin_unlock_irq(&cons->lock);
}
"""
    new = """static int a52_gs_console_thread(void *data)
{
	struct gs_console *cons = data;

	while (!kthread_should_stop()) {
		wait_event_interruptible(cons->wait,
			kthread_should_stop() || atomic_xchg(&cons->kick, 0));
		if (kthread_should_stop())
			break;

		spin_lock_irq(&cons->lock);
		__gs_console_push(cons);
		spin_unlock_irq(&cons->lock);
	}
	return 0;
}
"""
    text = one(text, old, new, "USB dedicated console kthread")

    text = one(text,
        "\tcons->console.flags = CON_PRINTBUFFER;\n",
        "\tcons->console.flags = CON_PRINTBUFFER | CON_ANYTIME;\n",
        "USB console anytime")

    old = """	INIT_WORK(&cons->work, gs_console_work);
	spin_lock_init(&cons->lock);

	err = kfifo_alloc(&cons->buf, GS_CONSOLE_BUF_SIZE, GFP_KERNEL);
"""
    new = """	spin_lock_init(&cons->lock);
	init_waitqueue_head(&cons->wait);
	atomic_set(&cons->kick, 0);

	err = kfifo_alloc(&cons->buf, GS_CONSOLE_BUF_SIZE, GFP_KERNEL);
"""
    text = one(text, old, new, "USB console init primitives")

    old = """	port->console = cons;
	register_console(&cons->console);
"""
    new = """	cons->task = kthread_run(a52_gs_console_thread, cons,
				 "a52_usb_console");
	if (IS_ERR(cons->task)) {
		err = PTR_ERR(cons->task);
		cons->task = NULL;
		kfifo_free(&cons->buf);
		kfree(cons);
		return err;
	}

	port->console = cons;
	register_console(&cons->console);
	pr_info("USB380 ttyGS%d live console ready fifo=%u transport=kthread\\n",
		port->port_num, GS_CONSOLE_BUF_SIZE);
"""
    text = one(text, old, new, "USB console kthread start")

    old = """	cancel_work_sync(&cons->work);
	kfifo_free(&cons->buf);
"""
    new = """	if (cons->task)
		kthread_stop(cons->task);
	kfifo_free(&cons->buf);
"""
    text = one(text, old, new, "USB console kthread stop")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(encoding="utf-8")
    ufs = (root / UFS).read_text(encoding="utf-8")
    blk = (root / BLK).read_text(encoding="utf-8")
    loop = (root / LOOP).read_text(encoding="utf-8")
    usb = (root / USB).read_text(encoding="utf-8")

    checks = (
        ("recorder legacy retired", "A52_PHASE380_LEGACY_PROBES_RETIRED_V1" in rec),
        ("no runtime Phase343", "\ta52_r343_start();\n" not in rec),
        ("no runtime Phase341", "\ta52_r341_start();\n" not in rec),
        ("no runtime Phase340", "\ta52_r340_start();\n" not in rec),
        ("no Phase339 schedule",
         "schedule_delayed_work(&a52_r339_preservation_work" not in
         rec[rec.find("static int __init a52_r179_late_retry"):]),
        ("UFS marker", MARK in ufs),
        ("UFS triple raw copies", "dst2 = " in ufs),
        ("UFS CRC32C", "a52_r380_crc32c" in ufs),
        ("UFS R48 mirror", 'a52_ackfr_record("U380 S%u' in ufs),
        ("Phase378 delayed sampler retired", "\t\ta52_r378_start_snapshots(hba);" not in ufs),
        ("Phase379 sampler retired", "\t\ta52_r379_start_sampler(hba);" not in ufs),
        ("block freeze trace", "A52_PHASE380_BLK_FREEZE_TRACE_V1" in blk),
        ("loop trace", "A52_PHASE380_LOOP_TRACE_V1" in loop),
        ("USB kthread console", "A52_PHASE380_USB_KTHREAD_CONSOLE_V1" in usb),
        ("USB 256K FIFO", "#define GS_CONSOLE_BUF_SIZE\t(256 * 1024)" in usb),
        ("USB no console system_wq", "schedule_work(&cons->work);" not in usb),
        ("USB dedicated thread", '"a52_usb_console"' in usb),
    )
    bad = [name for name, ok in checks if not ok]
    if bad:
        raise SystemExit("Phase380 audit failed: " + ", ".join(bad))

    if "A52_R179_RS_ROOTS 48U" not in rec:
        raise SystemExit("Phase380 lost RS48 recorder")
    if "__le32 crc32c;" not in rec:
        raise SystemExit("Phase380 lost main recorder CRC32C")
    if "copies=3 crc=crc32c" not in rec:
        raise SystemExit("Phase380 lost main recorder triple-copy marker")


def apply(root: Path) -> None:
    paths = [REC, UFS, BLK, LOOP, USB]
    for p in paths:
        if not (root / p).is_file():
            raise SystemExit(f"Phase380 required source missing: {p}")

    (root / REC).write_text(patch_recorder((root / REC).read_text(encoding="utf-8")),
                            encoding="utf-8")
    (root / UFS).write_text(patch_ufs((root / UFS).read_text(encoding="utf-8")),
                            encoding="utf-8")
    (root / BLK).write_text(patch_blk((root / BLK).read_text(encoding="utf-8")),
                            encoding="utf-8")
    (root / LOOP).write_text(patch_loop((root / LOOP).read_text(encoding="utf-8")),
                             encoding="utf-8")
    (root / USB).write_text(patch_usb((root / USB).read_text(encoding="utf-8")),
                            encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    if ns.check_only:
        validate(ns.root)
        print("Phase380 combined USB/block/UFS debug audit: PASS")
        return 0

    apply(ns.root)
    print("Phase380 combined USB/block/UFS debug applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
