#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE384_LOOP_UFS_STALL_PINPOINT_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
BLK = Path("block/blk-mq.c")
CORE = Path("block/blk-core.c")
LOOP = Path("drivers/block/loop.c")


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase384 {label}: expected 1 anchor, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        '''	       !strncmp(fmt, "P383", 4) ||
	       !strncmp(fmt, "L383", 4);
''',
        '''	       !strncmp(fmt, "P383", 4) ||
	       !strncmp(fmt, "L383", 4) ||
	       !strncmp(fmt, "U384", 4) ||
	       !strncmp(fmt, "B384", 4) ||
	       !strncmp(fmt, "L384", 4); /* A52_PHASE384_LOOP_UFS_STALL_PINPOINT_V1 */
''',
        "recorder admission helper",
    )
    text = one(
        text,
        '''	    strncmp(fmt, "P383", 4) &&
	    strncmp(fmt, "L383", 4))
		return;
''',
        '''	    strncmp(fmt, "P383", 4) &&
	    strncmp(fmt, "L383", 4) &&
	    strncmp(fmt, "U384", 4) &&
	    strncmp(fmt, "B384", 4) &&
	    strncmp(fmt, "L384", 4))
		return;
''',
        "recorder Phase243 admission",
    )
    text = one(
        text,
        '''	       !strncmp(message, "P383 ", 5) ||
	       !strncmp(message, "L383 ", 5); /* A52_PHASE381_RETENTION_V1 */
''',
        '''	       !strncmp(message, "P383 ", 5) ||
	       !strncmp(message, "L383 ", 5) ||
	       !strncmp(message, "U384 ", 5) ||
	       !strncmp(message, "B384 ", 5) ||
	       !strncmp(message, "L384 ", 5); /* A52_PHASE381_RETENTION_V1 */
''',
        "recorder critical retention",
    )
    return text


def patch_ufs(text: str) -> str:
    if "A52_PHASE384_UFS_LONG_FRONTIER_V1" in text:
        return text

    text = one(
        text,
        '''static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
	25U, 75U, 125U, 175U, 225U, 275U,
	300U, 325U, 350U, 375U, 425U,
};
''',
        '''/* A52_PHASE384_UFS_LONG_FRONTIER_V1 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
	25U, 75U, 125U, 225U, 425U, 750U,
	1000U, 2000U, 5000U, 10000U, 20000U,
};
''',
        "long UFS frontier offsets",
    )

    text = one(
        text,
        "static unsigned long a52_r378_last_completed;\n",
        '''static unsigned long a52_r378_last_completed;
/* A52_PHASE384_UFS_IRQ_CENSUS_V1 */
static atomic64_t a52_r384_irq_count = ATOMIC64_INIT(0);
static u32 a52_r384_last_irq_status;
''',
        "UFS IRQ census state",
    )

    text = one(
        text,
        '''	intr_status = ufshcd_readl(hba, REG_INTERRUPT_STATUS);
	hba->ufs_stats.last_intr_status = intr_status;
''',
        '''	intr_status = ufshcd_readl(hba, REG_INTERRUPT_STATUS);
	atomic64_inc(&a52_r384_irq_count);
	WRITE_ONCE(a52_r384_last_irq_status, intr_status);
	hba->ufs_stats.last_intr_status = intr_status;
''',
        "UFS IRQ entry census",
    )

    text = one(
        text,
        '''	a52_ackfr_record("U380 S%u db=%x out=%lx g=%u i=%lld h=%lld d=%lld",
			 snapshot_id, doorbell, outstanding, ghost_count,
			 (long long)r.issue_count, (long long)r.hook_count,
			 (long long)r.done_count);

	for (i = 0; i < limit && written_tags < A52_R380_TAGS_PER_SNAPSHOT; i++) {
''',
        '''	a52_ackfr_record("U380 S%u db=%x out=%lx g=%u i=%lld h=%lld d=%lld",
			 snapshot_id, doorbell, outstanding, ghost_count,
			 (long long)r.issue_count, (long long)r.hook_count,
			 (long long)r.done_count);
	a52_ackfr_record("U384 C%u tr=%lld se=%lld hk=%lld dn=%lld irq=%lld li=%x lc=%lx",
			 snapshot_id,
			 (long long)atomic64_read(&a52_r378_trc_count),
			 (long long)atomic64_read(&a52_r378_seen_count),
			 (long long)atomic64_read(&a52_r378_hook_done_count),
			 (long long)atomic64_read(&a52_r378_scsi_done_count),
			 (long long)atomic64_read(&a52_r384_irq_count),
			 READ_ONCE(a52_r384_last_irq_status),
			 READ_ONCE(a52_r378_last_completed));

	for (i = 0; i < limit && written_tags < A52_R380_TAGS_PER_SNAPSHOT; i++) {
''',
        "UFS compact completion census",
    )
    return text


def patch_blk(text: str) -> str:
    if "A52_PHASE384_FREEZE_WAIT_HEARTBEAT_V1" in text:
        return text

    old = '''void blk_mq_freeze_queue_wait(struct request_queue *q)
{
	bool trace = a52_r382_block_cliff_window();

	if (trace)
		a52_ackfr_record("B380 F enter q=%px dep=%d zero=%u",
				  q, q->mq_freeze_depth,
				  percpu_ref_is_zero(&q->q_usage_counter));
	wait_event(q->mq_freeze_wq, percpu_ref_is_zero(&q->q_usage_counter));
	if (trace)
		a52_ackfr_record("B380 F exit q=%px dep=%d", q,
				  q->mq_freeze_depth);
}
'''
    new = '''/* A52_PHASE384_FREEZE_WAIT_HEARTBEAT_V1 */
void blk_mq_freeze_queue_wait(struct request_queue *q)
{
	bool trace = a52_r382_block_cliff_window();
	unsigned int a52_wait = 0;

	if (trace)
		a52_ackfr_record("B380 F enter q=%px dep=%d zero=%u",
				  q, q->mq_freeze_depth,
				  percpu_ref_is_zero(&q->q_usage_counter));

	if (!trace) {
		wait_event(q->mq_freeze_wq,
			   percpu_ref_is_zero(&q->q_usage_counter));
	} else {
		while (!percpu_ref_is_zero(&q->q_usage_counter)) {
			wait_event_timeout(q->mq_freeze_wq,
					   percpu_ref_is_zero(&q->q_usage_counter),
					   msecs_to_jiffies(250));
			if (percpu_ref_is_zero(&q->q_usage_counter))
				break;
			a52_wait++;
			if (a52_wait <= 80)
				a52_ackfr_record("B384 W q=%px dep=%d nr=%u n=%u zero=0",
						 q, q->mq_freeze_depth,
						 q->nr_requests, a52_wait);
		}
	}
	if (trace)
		a52_ackfr_record("B380 F exit q=%px dep=%d", q,
				  q->mq_freeze_depth);
}
'''
    return one(text, old, new, "freeze wait heartbeat")


def patch_core(text: str) -> str:
    if "A52_PHASE384_QUEUE_REF_CENSUS_V1" in text:
        return text

    if "#include <linux/a52_ack_secure_flight_recorder.h>\n" not in text:
        anchor = "#include <linux/blkdev.h>\n"
        text = one(
            text, anchor,
            anchor + "#include <linux/a52_ack_secure_flight_recorder.h>\n",
            "blk-core recorder include",
        )

    text = one(
        text,
        '''int blk_queue_enter(struct request_queue *q, blk_mq_req_flags_t flags)
{
\tconst bool pm = flags & BLK_MQ_REQ_PM;
''',
        '''/* A52_PHASE384_QUEUE_REF_CENSUS_V1 */
static atomic_t a52_r384_qget_id = ATOMIC_INIT(0);
static atomic_t a52_r384_qput_id = ATOMIC_INIT(0);

int blk_queue_enter(struct request_queue *q, blk_mq_req_flags_t flags)
{
\tconst bool pm = flags & BLK_MQ_REQ_PM;
''',
        "queue ref census state",
    )

    fn_anchor = "int blk_queue_enter(struct request_queue *q, blk_mq_req_flags_t flags)"
    start = text.find(fn_anchor)
    if start < 0:
        raise SystemExit("Phase384 queue get census: blk_queue_enter missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit("Phase384 queue get census: opening brace missing")
    depth = 0
    end = -1
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end < 0:
        raise SystemExit("Phase384 queue get census: closing brace missing")

    body = text[start:end]
    ret = body.rfind("\treturn 0;")
    if ret < 0:
        raise SystemExit("Phase384 queue get census: successful return missing")
    body = body[:ret] + '''\t{
\t\tu64 a52_ms = a52_ackfr_frontier_elapsed_ms();
\t\tint a52_id;

\t\tif (a52_ms <= 2000U &&
\t\t    (a52_id = atomic_inc_return(&a52_r384_qget_id)) <= 192)
\t\t\ta52_ackfr_record("B384 G id=%d q=%px dep=%d",
\t\t\t\t\t a52_id, q, READ_ONCE(q->mq_freeze_depth));
\t}
''' + body[ret:]
    text = text[:start] + body + text[end:]

    text = one(
        text,
        '''void blk_queue_exit(struct request_queue *q)
{
\tpercpu_ref_put(&q->q_usage_counter);
}
''',
        '''void blk_queue_exit(struct request_queue *q)
{
\tu64 a52_ms = a52_ackfr_frontier_elapsed_ms();
\tint a52_id;

\tpercpu_ref_put(&q->q_usage_counter);
\tif (a52_ms <= 2000U &&
\t    (a52_id = atomic_inc_return(&a52_r384_qput_id)) <= 192)
\t\ta52_ackfr_record("B384 P id=%d q=%px dep=%d",
\t\t\t\t a52_id, q, READ_ONCE(q->mq_freeze_depth));
}
''',
        "queue put census",
    )
    return text


def patch_loop(text: str) -> str:
    if "A52_PHASE384_LOOP46_49_LIFECYCLE_V1" in text:
        return text

    helper = '''static bool a52_r383_stage_log(void)
{
	int id;

	if (!a52_r382_loop_cliff_window())
		return false;
	id = atomic_inc_return(&a52_r383_loop_stage_id);
	return id <= 96;
}
'''
    helper_new = helper + '''
/* A52_PHASE384_LOOP46_49_LIFECYCLE_V1 */
static atomic_t a52_r384_loop_stage_id = ATOMIC_INIT(0);

static int a52_r384_loop_no(struct request *rq)
{
	struct loop_device *lo;

	if (!rq || !rq->q)
		return -1;
	lo = rq->q->queuedata;
	return lo ? lo->lo_number : -1;
}

static bool a52_r384_loop_log(struct request *rq)
{
	int n = a52_r384_loop_no(rq);

	if (n < 46 || n > 49)
		return false;
	return atomic_inc_return(&a52_r384_loop_stage_id) <= 384;
}
'''
    text = one(text, helper, helper_new, "target loop helper")

    text = one(
        text,
        '''	if (a52_id <= 48 || (a52_late > 0 && a52_late <= 24))
		a52_ackfr_record("L380 D id=%d n=%d q=%px rq=%px aio=%u r=%d",
				 a52_id, lo ? lo->lo_number : -1, rq->q, rq,
				 cmd->use_aio, cmd->ret);
''',
        '''	if (a52_id <= 48 || (a52_late > 0 && a52_late <= 24))
		a52_ackfr_record("L380 D id=%d n=%d q=%px rq=%px aio=%u r=%d",
				 a52_id, lo ? lo->lo_number : -1, rq->q, rq,
				 cmd->use_aio, cmd->ret);
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 E n=%d q=%px rq=%px aio=%u ret=%d",
				 a52_r384_loop_no(rq), rq->q, rq,
				 cmd->use_aio, cmd->ret);
''',
        "target loop end-request entry",
    )

    text = one(
        text,
        '''	}

	if (lo->lo_state != Lo_bound)
''',
        '''	}
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 Q n=%d q=%px rq=%px op=%u b=%u",
				 lo->lo_number, rq->q, rq, req_op(rq),
				 blk_rq_bytes(rq));

	if (lo->lo_state != Lo_bound)
''',
        "target loop queue issue",
    )

    text = one(
        text,
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 W n=%d rq=%px aio=%u op=%u",
				 lo ? lo->lo_number : -1, rq,
				 cmd->use_aio, req_op(rq));

''',
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 W n=%d rq=%px aio=%u op=%u",
				 lo ? lo->lo_number : -1, rq,
				 cmd->use_aio, req_op(rq));
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 W n=%d rq=%px aio=%u op=%u b=%u",
				 a52_r384_loop_no(rq), rq, cmd->use_aio,
				 req_op(rq), blk_rq_bytes(rq));

''',
        "target loop worker",
    )

    text = one(
        text,
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 X rq=%px final=%u ref=%d ret=%d",
				 rq, final, atomic_read(&cmd->ref), cmd->ret);
''',
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 X rq=%px final=%u ref=%d ret=%d",
				 rq, final, atomic_read(&cmd->ref), cmd->ret);
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 X n=%d rq=%px f=%u ref=%d ret=%d",
				 a52_r384_loop_no(rq), rq, final,
				 atomic_read(&cmd->ref), cmd->ret);
''',
        "target AIO ref completion",
    )

    text = one(
        text,
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 C rq=%px ret=%ld ret2=%ld",
				 rq, ret, ret2);
''',
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 C rq=%px ret=%ld ret2=%ld",
				 rq, ret, ret2);
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 C n=%d rq=%px ret=%ld ret2=%ld",
				 a52_r384_loop_no(rq), rq, ret, ret2);
''',
        "target AIO callback",
    )

    text = one(
        text,
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 A rq=%px n=%d rw=%u bytes=%u",
				 rq, lo->lo_number, rw, blk_rq_bytes(rq));
''',
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 A rq=%px n=%d rw=%u bytes=%u",
				 rq, lo->lo_number, rw, blk_rq_bytes(rq));
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 A n=%d rq=%px rw=%u b=%u",
				 lo->lo_number, rq, rw, blk_rq_bytes(rq));
''',
        "target AIO submit",
    )

    text = one(
        text,
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 R rq=%px ret=%d", rq, ret);

	lo_rw_aio_do_completion(cmd);
''',
        '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 R rq=%px ret=%d", rq, ret);
	if (a52_r384_loop_log(rq))
		a52_ackfr_record("L384 R n=%d rq=%px ret=%d",
				 a52_r384_loop_no(rq), rq, ret);

	lo_rw_aio_do_completion(cmd);
''',
        "target AIO return",
    )

    text = one(
        text,
        '''	if (!cmd->use_aio || ret) {
		if (ret == -EOPNOTSUPP)
''',
        '''	if (!cmd->use_aio || ret) {
		if (a52_r384_loop_log(rq))
			a52_ackfr_record("L384 N n=%d rq=%px aio=%u ret=%d",
					 a52_r384_loop_no(rq), rq,
					 cmd->use_aio, ret);
		if (ret == -EOPNOTSUPP)
''',
        "target non-AIO completion",
    )
    return text


def validate(root: Path) -> None:
    checks = {
        REC: (MARK, 'strncmp(fmt, "U384", 4)', 'strncmp(fmt, "B384", 4)', 'strncmp(fmt, "L384", 4)'),
        UFS: ("A52_PHASE384_UFS_LONG_FRONTIER_V1", "A52_PHASE384_UFS_IRQ_CENSUS_V1", "U384 C%u"),
        BLK: ("A52_PHASE384_FREEZE_WAIT_HEARTBEAT_V1", "B384 W q=%px"),
        CORE: ("A52_PHASE384_QUEUE_REF_CENSUS_V1", "B384 G id=%d", "B384 P id=%d"),
        LOOP: ("A52_PHASE384_LOOP46_49_LIFECYCLE_V1", "L384 Q", "L384 W", "L384 A", "L384 R", "L384 C", "L384 X", "L384 E", "L384 N"),
    }
    for rel, tokens in checks.items():
        text = (root / rel).read_text()
        for token in tokens:
            if token not in text:
                raise SystemExit(f"Phase384 validation missing {token!r} in {rel}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()

    if args.check_only:
        validate(root)
        print("Phase384 loop/UFS stall pinpoint validation: PASS")
        return

    patches = (
        (REC, patch_recorder),
        (UFS, patch_ufs),
        (BLK, patch_blk),
        (CORE, patch_core),
        (LOOP, patch_loop),
    )
    for rel, fn in patches:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"Phase384 missing source file: {path}")
        before = path.read_text()
        after = fn(before)
        path.write_text(after)
        print(f"Phase384 patched {rel}: {before != after}")

    validate(root)
    print("Phase384 loop/UFS stall pinpoint applied successfully")


if __name__ == "__main__":
    main()
