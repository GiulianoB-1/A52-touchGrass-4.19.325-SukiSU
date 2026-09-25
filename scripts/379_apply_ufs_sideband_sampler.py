#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PATH = Path("drivers/scsi/ufs/ufshcd.c")
MARK = "A52_PHASE379_UFS_SIDEBAND_SAMPLER_V1"

BLOCK = r'''
/* A52_PHASE379_UFS_SIDEBAND_SAMPLER_V1
 *
 * Phase378 hardware result:
 * - the UFS recorder reached READY at ~1.35 s
 * - none of its default-system-workqueue snapshots at 18/19/20/25/35 s ran
 * - the independent Phase377 kthread did run at ~19.0 s
 * - that census caught apexd blocked in blk_mq_freeze_queue_wait(), multiple
 *   __wait_on_buffer() EXT4 paths, and __kthread_create_on_node().
 *
 * Therefore Phase379 removes the workqueue dependency from the UFS snapshot
 * path. A dedicated kthread writes fixed-size binary records directly into
 * the unused final 32 KiB of the pmsg ramoops quarter. Two 16 KiB mirrors are
 * written for clear-bit corruption recovery. No UFS behavior is changed.
 */
#define A52_R379_SIDEBAND_PHYS       0xB1BF8000ULL
#define A52_R379_SIDEBAND_BYTES      0x8000U
#define A52_R379_COPY_BYTES          0x4000U
#define A52_R379_SLOT_BYTES          256U
#define A52_R379_SLOTS_PER_COPY      64U
#define A52_R379_SNAPSHOT_COUNT      6U
#define A52_R379_TAGS_PER_SNAPSHOT   9U
#define A52_R379_MAGIC               0x3937335353465555ULL
#define A52_R379_COMMIT              0x379c0de5U
#define A52_R379_VERSION             1U
#define A52_R379_TYPE_SNAPSHOT       1U
#define A52_R379_TYPE_TAG            2U

struct a52_r379_record {
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
	u32 commit;
	u32 version;
	char label[32];
	u8 reserved[40];
};

static const char a52_r379_marker[] __used =
	"A52_PHASE379_UFS_SIDEBAND_SAMPLER_V1";
static void *a52_r379_sideband;
static struct ufs_hba *a52_r379_hba;
static struct task_struct *a52_r379_sampler_task;
static unsigned int a52_r379_slot;

static const u32 a52_r379_target_ms[A52_R379_SNAPSHOT_COUNT] = {
	17500U, 18000U, 18500U, 19000U, 19250U, 19500U,
};

static void a52_r379_write_slot(const struct a52_r379_record *r)
{
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r379_sideband) || !r ||
	    a52_r379_slot >= A52_R379_SLOTS_PER_COPY)
		return;

	pos = a52_r379_slot * A52_R379_SLOT_BYTES;
	dst0 = (u8 *)a52_r379_sideband + pos;
	dst1 = (u8 *)a52_r379_sideband + A52_R379_COPY_BYTES + pos;
	memcpy(dst0, r, sizeof(*r));
	memcpy(dst1, r, sizeof(*r));
	wmb();
	__flush_dcache_area(dst0, sizeof(*r));
	__flush_dcache_area(dst1, sizeof(*r));
	a52_r379_slot++;
}

static void a52_r379_fill_common(struct a52_r379_record *r,
				 struct ufs_hba *hba,
				 unsigned int snapshot_id,
				 u32 doorbell,
				 unsigned long outstanding,
				 unsigned int ghost_count,
				 unsigned int active_tags)
{
	memset(r, 0, sizeof(*r));
	r->magic = A52_R379_MAGIC;
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
	r->commit = A52_R379_COMMIT;
	r->version = A52_R379_VERSION;
}

static void a52_r379_take_snapshot(unsigned int snapshot_id)
{
	struct ufs_hba *hba = READ_ONCE(a52_r379_hba);
	struct a52_r379_record r;
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

	a52_r379_fill_common(&r, hba, snapshot_id, doorbell, outstanding,
			      ghost_count, active_tags);
	r.type = A52_R379_TYPE_SNAPSHOT;
	r.tag = ~0U;
	strscpy(r.label, "UFS_SNAPSHOT", sizeof(r.label));
	a52_r379_write_slot(&r);

	for (i = 0; i < limit && written_tags < A52_R379_TAGS_PER_SNAPSHOT; i++) {
		struct ufshcd_lrb *lrbp = &hba->lrb[i];
		struct scsi_cmnd *cmd = READ_ONCE(lrbp->cmd);
		bool db = !!(doorbell & BIT(i));
		bool out = test_bit(i, &outstanding);
		s64 issue_ns;

		if (!cmd && !db && !out)
			continue;

		a52_r379_fill_common(&r, hba, snapshot_id, doorbell, outstanding,
				      ghost_count, active_tags);
		r.type = A52_R379_TYPE_TAG;
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
		a52_r379_write_slot(&r);
		written_tags++;
	}
}

static int a52_r379_sampler_fn(void *unused)
{
	unsigned int next = 0;
	u64 now_ms;

	(void)unused;
	while (!kthread_should_stop() && next < A52_R379_SNAPSHOT_COUNT) {
		now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
		if (now_ms >= a52_r379_target_ms[next]) {
			a52_r379_take_snapshot(next);
			next++;
			continue;
		}
		if (msleep_interruptible(20) && kthread_should_stop())
			break;
	}
	return 0;
}

static void a52_r379_start_sampler(struct ufs_hba *hba)
{
	BUILD_BUG_ON(sizeof(struct a52_r379_record) != A52_R379_SLOT_BYTES);

	if (READ_ONCE(a52_r379_sampler_task))
		return;

	a52_r379_sideband = memremap(A52_R379_SIDEBAND_PHYS,
		A52_R379_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r379_sideband)
		return;

	memset(a52_r379_sideband, 0, A52_R379_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_r379_sideband, A52_R379_SIDEBAND_BYTES);
	WRITE_ONCE(a52_r379_hba, hba);
	a52_r379_slot = 0;
	a52_r379_sampler_task =
		kthread_run(a52_r379_sampler_fn, NULL, "a52_r379_ufs");
	if (IS_ERR(a52_r379_sampler_task))
		a52_r379_sampler_task = NULL;
}

'''

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase379 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)

def validate(text: str) -> None:
    for token in (
        MARK,
        "A52_R379_SIDEBAND_PHYS       0xB1BF8000ULL",
        "static const char a52_r379_marker[] __used =",
        "A52_R379_SLOTS_PER_COPY      64U",
        "17500U, 18000U, 18500U, 19000U, 19250U, 19500U",
        "a52_r379_take_snapshot(next);",
        "atomic64_read(&a52_r378_hook_done_count)",
        "a52_r379_start_sampler(hba);",
        'kthread_run(a52_r379_sampler_fn, NULL, "a52_r379_ufs")',
    ):
        if token not in text:
            raise SystemExit("Phase379 required token missing: " + token)

def apply(text: str) -> str:
    if MARK in text:
        validate(text)
        return text
    if "A52_PHASE378_UFS_COMPLETION_FLIGHT_RECORDER_V1" not in text:
        raise SystemExit("Phase379 requires Phase378 source lineage")

    include_anchor = '#include <linux/timekeeping.h>\n#include "ufshcd.h"\n'
    include_repl = (
        '#include <linux/timekeeping.h>\n'
        '#include <linux/kthread.h>\n'
        '#include <linux/delay.h>\n'
        '#include <linux/io.h>\n'
        '#include <asm/cacheflush.h>\n'
        '#include "ufshcd.h"\n'
    )
    text = one(text, include_anchor, include_repl, "includes")

    send_anchor = "/**\n * ufshcd_send_command - Send SCSI or device management commands\n"
    text = one(text, send_anchor, BLOCK + send_anchor, "sideband block")

    init_anchor = (
        "\tif (!ret)\n"
        "\t\ta52_r378_start_snapshots(hba);\n"
        "out:\n"
    )
    init_repl = (
        "\tif (!ret) {\n"
        "\t\ta52_r378_start_snapshots(hba);\n"
        "\t\ta52_r379_start_sampler(hba);\n"
        "\t}\n"
        "out:\n"
    )
    text = one(text, init_anchor, init_repl, "sampler start")
    validate(text)
    return text

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    path = ns.root / PATH
    if not path.is_file():
        raise SystemExit("Phase379 ufshcd.c missing")
    text = path.read_text(encoding="utf-8")
    if ns.check_only:
        validate(text)
        print("Phase379 UFS sideband sampler audit: PASS")
        return 0
    new = apply(text)
    path.write_text(new, encoding="utf-8")
    print("Phase379 UFS sideband sampler applied")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
