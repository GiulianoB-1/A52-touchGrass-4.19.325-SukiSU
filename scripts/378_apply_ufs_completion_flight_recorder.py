#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

PATH = Path("drivers/scsi/ufs/ufshcd.c")
MARK = "A52_PHASE378_UFS_COMPLETION_FLIGHT_RECORDER_V1"

STATE_BLOCK = r'''
/* A52_PHASE378_UFS_COMPLETION_FLIGHT_RECORDER_V1
 *
 * Phase377 caught an apexd worker blocked in wait_on_page_read(). Keep the
 * UFS data path behavior unchanged and record enough state to distinguish:
 *   (1) hardware request still pending (doorbell/outstanding set),
 *   (2) completion observed but vendor hook short-circuited before scsi_done,
 *   (3) no UFS-side pending/ghost LRB, pushing the frontier below SCSI/UFS.
 *
 * IRQ-side code only updates lockless counters/last-event fields. Persistent
 * text is emitted by delayed work in process context around the ~19 s stall.
 */
#define A52_R378_MAX_TAGS 32U
#define A52_R378_SNAP_COUNT 5U

static struct ufs_hba *a52_r378_hba;
static struct delayed_work a52_r378_work;
static bool a52_r378_work_ready;
static unsigned int a52_r378_snap_index;
static atomic64_t a52_r378_issue_count = ATOMIC64_INIT(0);
static atomic64_t a52_r378_trc_count = ATOMIC64_INIT(0);
static atomic64_t a52_r378_seen_count = ATOMIC64_INIT(0);
static atomic64_t a52_r378_hook_done_count = ATOMIC64_INIT(0);
static atomic64_t a52_r378_scsi_done_count = ATOMIC64_INIT(0);
static u32 a52_r378_last_issue_tag = ~0U;
static u32 a52_r378_last_seen_tag = ~0U;
static u32 a52_r378_last_hook_tag = ~0U;
static u32 a52_r378_last_scsi_done_tag = ~0U;
static u32 a52_r378_last_doorbell;
static unsigned long a52_r378_last_outstanding;
static unsigned long a52_r378_last_completed;

static const u32 a52_r378_target_ms[A52_R378_SNAP_COUNT] = {
	18000U, 19000U, 20000U, 25000U, 35000U,
};

static void a52_r378_schedule_next(void)
{
	u64 now_ms;
	u64 delay_ms;

	if (!a52_r378_work_ready ||
	    a52_r378_snap_index >= A52_R378_SNAP_COUNT)
		return;

	now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	if (now_ms >= a52_r378_target_ms[a52_r378_snap_index])
		delay_ms = 1;
	else
		delay_ms = a52_r378_target_ms[a52_r378_snap_index] - now_ms;

	schedule_delayed_work(&a52_r378_work,
		msecs_to_jiffies((unsigned int)min_t(u64, delay_ms, ~0U)));
}

static void a52_r378_snapshot_work(struct work_struct *work)
{
	struct ufs_hba *hba = READ_ONCE(a52_r378_hba);
	unsigned long outstanding;
	u32 doorbell;
	u64 now_ms;
	u32 state, link, dev_pwr, clk_state;
	unsigned int i, limit;
	unsigned int ghost_count = 0;

	(void)work;
	if (!hba)
		return;

	now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	if (pm_runtime_active(hba->dev))
		doorbell = ufshcd_readl(hba, REG_UTP_TRANSFER_REQ_DOOR_BELL);
	else
		doorbell = 0;
	outstanding = READ_ONCE(hba->outstanding_reqs);
	state = READ_ONCE(hba->ufshcd_state);
	link = READ_ONCE(hba->uic_link_state);
	dev_pwr = READ_ONCE(hba->curr_dev_pwr_mode);
	clk_state = READ_ONCE(hba->clk_gating.state);

	limit = min_t(unsigned int, hba->nutrs, A52_R378_MAX_TAGS);
	for (i = 0; i < limit; i++) {
		struct ufshcd_lrb *lrbp = &hba->lrb[i];
		struct scsi_cmnd *cmd = READ_ONCE(lrbp->cmd);
		bool db = !!(doorbell & BIT(i));
		bool out = test_bit(i, &outstanding);

		if (cmd && !db && !out)
			ghost_count++;
	}

	a52_persistent_diag_mark(
		"A52UFS378 SNAP n=%u t_ms=%llu rpm=%u db=0x%08x out=0x%08lx ghost=%u state=%u link=%u devpwr=%u clk=%u\n",
		a52_r378_snap_index, (unsigned long long)now_ms,
		pm_runtime_active(hba->dev), doorbell, outstanding, ghost_count,
		state, link, dev_pwr, clk_state);
	a52_persistent_diag_mark(
		"A52UFS378 COUNT n=%u issue=%lld trc=%lld seen=%lld hook=%lld done=%lld last_i=%u last_s=%u last_h=%u last_d=%u trc_db=0x%08x trc_out=0x%08lx completed=0x%08lx\n",
		a52_r378_snap_index,
		(long long)atomic64_read(&a52_r378_issue_count),
		(long long)atomic64_read(&a52_r378_trc_count),
		(long long)atomic64_read(&a52_r378_seen_count),
		(long long)atomic64_read(&a52_r378_hook_done_count),
		(long long)atomic64_read(&a52_r378_scsi_done_count),
		READ_ONCE(a52_r378_last_issue_tag),
		READ_ONCE(a52_r378_last_seen_tag),
		READ_ONCE(a52_r378_last_hook_tag),
		READ_ONCE(a52_r378_last_scsi_done_tag),
		READ_ONCE(a52_r378_last_doorbell),
		READ_ONCE(a52_r378_last_outstanding),
		READ_ONCE(a52_r378_last_completed));

	for (i = 0; i < limit; i++) {
		struct ufshcd_lrb *lrbp = &hba->lrb[i];
		struct scsi_cmnd *cmd = READ_ONCE(lrbp->cmd);
		bool db = !!(doorbell & BIT(i));
		bool out = test_bit(i, &outstanding);
		s64 issue_ms;
		s64 age_ms;

		if (!cmd && !db && !out)
			continue;

		issue_ms = ktime_to_ms(READ_ONCE(lrbp->issue_time_stamp));
		age_ms = issue_ms > 0 ? (s64)now_ms - issue_ms : -1;
		a52_persistent_diag_mark(
			"A52UFS378 TAG n=%u tag=%u db=%u out=%u cmd=%px issue_ms=%lld age_ms=%lld ghost=%u\n",
			a52_r378_snap_index, i, db, out, cmd,
			(long long)issue_ms, (long long)age_ms,
			cmd && !db && !out);
	}

	a52_r378_snap_index++;
	a52_r378_schedule_next();
}

static void a52_r378_start_snapshots(struct ufs_hba *hba)
{
	if (a52_r378_work_ready)
		return;

	WRITE_ONCE(a52_r378_hba, hba);
	INIT_DELAYED_WORK(&a52_r378_work, a52_r378_snapshot_work);
	a52_r378_work_ready = true;
	a52_r378_snap_index = 0;
	a52_persistent_diag_mark(
		"A52UFS378 READY t_ms=%llu nutrs=%d ufs_version=0x%x\n",
		(unsigned long long)div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC),
		hba->nutrs, hba->ufs_version);
	a52_r378_schedule_next();
}

'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase378 {label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def validate(text: str) -> None:
    required = (
        MARK,
        "A52UFS378 SNAP",
        "A52UFS378 TAG",
        "A52UFS378 READY",
        "atomic64_inc(&a52_r378_issue_count);",
        "atomic64_inc(&a52_r378_trc_count);",
        "atomic64_inc(&a52_r378_seen_count);",
        "atomic64_inc(&a52_r378_hook_done_count);",
        "atomic64_inc(&a52_r378_scsi_done_count);",
        "a52_r378_start_snapshots(hba);",
        "trace_android_vh_ufs_compl_rsp_check_done(hba, lrbp, &done);",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase378 required token missing: " + token)


def apply(text: str) -> str:
    if MARK in text:
        validate(text)
        return text

    include_anchor = '#include <linux/blkdev.h>\n#include "ufshcd.h"\n'
    text = replace_once(
        text,
        include_anchor,
        '#include <linux/blkdev.h>\n#include <linux/timekeeping.h>\n#include "ufshcd.h"\n',
        "timekeeping include",
    )

    send_anchor = "/**\n * ufshcd_send_command - Send SCSI or device management commands\n"
    text = replace_once(text, send_anchor, STATE_BLOCK + send_anchor,
                        "diagnostic state block")

    issue_anchor = (
        "\tlrbp->issue_time_stamp = ktime_get();\n"
        "\tlrbp->compl_time_stamp = ktime_set(0, 0);\n"
    )
    issue_repl = issue_anchor + (
        "\tatomic64_inc(&a52_r378_issue_count);\n"
        "\tWRITE_ONCE(a52_r378_last_issue_tag, task_tag);\n"
    )
    text = replace_once(text, issue_anchor, issue_repl, "issue counter")

    clear_anchor = (
        "\tfor_each_set_bit(index, &completed_reqs, hba->nutrs) {\n"
        "\t\tif (!test_and_clear_bit(index, &hba->outstanding_reqs))\n"
        "\t\t\tcontinue;\n"
    )
    clear_repl = (
        "\tfor_each_set_bit(index, &completed_reqs, hba->nutrs) {\n"
        "\t\tif (!test_and_clear_bit(index, &hba->outstanding_reqs))\n"
        "\t\t\tcontinue;\n"
        "\t\tatomic64_inc(&a52_r378_seen_count);\n"
        "\t\tWRITE_ONCE(a52_r378_last_seen_tag, index);\n"
    )
    text = replace_once(text, clear_anchor, clear_repl, "completion seen counter")

    hook_anchor = (
        "\t\t\ttrace_android_vh_ufs_compl_rsp_check_done(hba, lrbp, &done);\n"
        "\t\t\tif (done)\n"
        "\t\t\t\treturn;\n"
    )
    hook_repl = (
        "\t\t\ttrace_android_vh_ufs_compl_rsp_check_done(hba, lrbp, &done);\n"
        "\t\t\tif (done) {\n"
        "\t\t\t\tatomic64_inc(&a52_r378_hook_done_count);\n"
        "\t\t\t\tWRITE_ONCE(a52_r378_last_hook_tag, index);\n"
        "\t\t\t\treturn;\n"
        "\t\t\t}\n"
    )
    text = replace_once(text, hook_anchor, hook_repl, "vendor hook result")

    done_anchor = (
        "\t\t\tufshcd_release_scsi_cmd(hba, lrbp);\n"
        "\t\t\t/* Do not touch lrbp after scsi done */\n"
        "\t\t\tcmd->scsi_done(cmd);\n"
    )
    done_repl = (
        "\t\t\tufshcd_release_scsi_cmd(hba, lrbp);\n"
        "\t\t\tatomic64_inc(&a52_r378_scsi_done_count);\n"
        "\t\t\tWRITE_ONCE(a52_r378_last_scsi_done_tag, index);\n"
        "\t\t\t/* Do not touch lrbp after scsi done */\n"
        "\t\t\tcmd->scsi_done(cmd);\n"
    )
    text = replace_once(text, done_anchor, done_repl, "scsi done counter")

    trc_anchor = (
        "\tif (completed_reqs) {\n"
        "\t\t__ufshcd_transfer_req_compl(hba, completed_reqs);\n"
    )
    trc_repl = (
        "\tif (completed_reqs) {\n"
        "\t\tatomic64_inc(&a52_r378_trc_count);\n"
        "\t\tWRITE_ONCE(a52_r378_last_completed, completed_reqs);\n"
        "\t\tWRITE_ONCE(a52_r378_last_doorbell,\n"
        "\t\t\tufshcd_readl(hba, REG_UTP_TRANSFER_REQ_DOOR_BELL));\n"
        "\t\tWRITE_ONCE(a52_r378_last_outstanding,\n"
        "\t\t\tREAD_ONCE(hba->outstanding_reqs));\n"
        "\t\t__ufshcd_transfer_req_compl(hba, completed_reqs);\n"
    )
    text = replace_once(text, trc_anchor, trc_repl, "trc snapshot")

    init_anchor = (
        "\tret = ufshcd_add_lus(hba);\n"
        "\tdo { } while (0);\n"
        "\tdo { } while (0);\n"
        "\tdo { } while (0);\n"
        "out:\n"
    )
    init_repl = (
        "\tret = ufshcd_add_lus(hba);\n"
        "\tdo { } while (0);\n"
        "\tdo { } while (0);\n"
        "\tdo { } while (0);\n"
        "\tif (!ret)\n"
        "\t\ta52_r378_start_snapshots(hba);\n"
        "out:\n"
    )
    text = replace_once(text, init_anchor, init_repl, "start snapshots")

    validate(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / PATH
    if not path.is_file():
        raise SystemExit("Phase378 ufshcd.c missing")

    text = path.read_text(encoding="utf-8")
    if ns.check_only:
        validate(text)
        print("Phase378 UFS completion flight recorder audit: PASS")
        return 0

    new = apply(text)
    path.write_text(new, encoding="utf-8")
    print("Phase378 UFS completion flight recorder applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
