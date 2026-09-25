#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE383_EVENT_FRONTIER_LOOP_AIO_V1"

HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
SYSCALL = Path("arch/arm64/kernel/syscall.c")
BLK = Path("block/blk-mq.c")
LOOP = Path("drivers/block/loop.c")
UDC = Path("drivers/usb/gadget/udc/core.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase383 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_header(text: str) -> str:
    if "a52_ackfr_frontier_trigger" in text:
        return text
    block = r'''
/* A52_PHASE383_EVENT_FRONTIER_LOOP_AIO_V1 */
void a52_ackfr_frontier_trigger(unsigned int loop_no);
u64 a52_ackfr_frontier_trigger_ns(void);
u64 a52_ackfr_frontier_elapsed_ms(void);

'''
    return one(text, "\n#endif\n", block + "\n#endif\n", "header frontier API")


FRONTIER_BLOCK = r'''
/* A52_PHASE383_EVENT_FRONTIER_LOOP_AIO_V1
 *
 * Fixed boottime windows moved when diagnostics changed timing.  Anchor the
 * forensic window to the final APEX loop-device creation instead.
 */
static atomic64_t a52_r383_frontier_ns = ATOMIC64_INIT(0);
static atomic_t a52_r383_frontier_loop = ATOMIC_INIT(-1);

void a52_ackfr_frontier_trigger(unsigned int loop_no)
{
	u64 now = ktime_get_boottime_ns();

	if (atomic64_cmpxchg(&a52_r383_frontier_ns, 0, now) != 0)
		return;
	atomic_set(&a52_r383_frontier_loop, (int)loop_no);
	a52_ackfr_record("P383 trigger loop=%u t=%llu",
			 loop_no, (unsigned long long)now);
}
EXPORT_SYMBOL_GPL(a52_ackfr_frontier_trigger);

u64 a52_ackfr_frontier_trigger_ns(void)
{
	return (u64)atomic64_read(&a52_r383_frontier_ns);
}
EXPORT_SYMBOL_GPL(a52_ackfr_frontier_trigger_ns);

u64 a52_ackfr_frontier_elapsed_ms(void)
{
	u64 start = (u64)atomic64_read(&a52_r383_frontier_ns);
	u64 now;

	if (!start)
		return ~0ULL;
	now = ktime_get_boottime_ns();
	if (now < start)
		return ~0ULL;
	return div_u64(now - start, NSEC_PER_MSEC);
}
EXPORT_SYMBOL_GPL(a52_ackfr_frontier_elapsed_ms);

'''


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text
    if "#include <linux/ktime.h>\n" not in text:
        text = one(text, "#include <linux/kernel.h>\n",
                   "#include <linux/kernel.h>\n#include <linux/ktime.h>\n",
                   "recorder ktime include")

    anchor = "void a52_ackfr_record(const char *fmt, ...)\n"
    text = one(text, anchor, FRONTIER_BLOCK + anchor, "frontier block")

    old = '''	return !strncmp(fmt, "U380", 4) ||
	       !strncmp(fmt, "B380", 4) ||
	       !strncmp(fmt, "L380", 4) ||
	       !strncmp(fmt, "USB380", 6) ||
	       !strncmp(fmt, "V380", 4) ||
	       !strncmp(fmt, "V381", 4) ||
	       !strncmp(fmt, "V382", 4);
'''
    new = '''	return !strncmp(fmt, "U380", 4) ||
	       !strncmp(fmt, "B380", 4) ||
	       !strncmp(fmt, "L380", 4) ||
	       !strncmp(fmt, "USB380", 6) ||
	       !strncmp(fmt, "V380", 4) ||
	       !strncmp(fmt, "V381", 4) ||
	       !strncmp(fmt, "V382", 4) ||
	       !strncmp(fmt, "P383", 4) ||
	       !strncmp(fmt, "L383", 4);
'''
    text = one(text, old, new, "diagnostic admission helper")

    old = '''	    strncmp(fmt, "V380", 4) &&
	    strncmp(fmt, "V381", 4) &&
	    strncmp(fmt, "V382", 4))
		return;
'''
    new = '''	    strncmp(fmt, "V380", 4) &&
	    strncmp(fmt, "V381", 4) &&
	    strncmp(fmt, "V382", 4) &&
	    strncmp(fmt, "P383", 4) &&
	    strncmp(fmt, "L383", 4))
		return;
'''
    text = one(text, old, new, "Phase243 Phase383 admission")

    old = '''	       !strncmp(message, "V380 ", 5) ||
	       !strncmp(message, "V381 ", 5) ||
	       !strncmp(message, "V382 ", 5); /* A52_PHASE381_RETENTION_V1 */
'''
    new = '''	       !strncmp(message, "V380 ", 5) ||
	       !strncmp(message, "V381 ", 5) ||
	       !strncmp(message, "V382 ", 5) ||
	       !strncmp(message, "P383 ", 5) ||
	       !strncmp(message, "L383 ", 5); /* A52_PHASE381_RETENTION_V1 */
'''
    return one(text, old, new, "critical Phase383")


def patch_ufs(text: str) -> str:
    if "A52_PHASE383_EVENT_UFS_FRONTIER_V1" in text:
        return text

    old = '''static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
	18300U, 18400U, 18450U, 18475U, 18500U, 18525U,
	18550U, 18575U, 18600U, 18625U, 18650U,
};
'''
    new = '''/* A52_PHASE383_EVENT_UFS_FRONTIER_V1 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
	25U, 75U, 125U, 175U, 225U, 275U,
	300U, 325U, 350U, 375U, 425U,
};
'''
    text = one(text, old, new, "event-relative UFS offsets")

    old = '''static int a52_r380_sampler_fn(void *unused)
{
	unsigned int next = 0;
	u64 now_ms;

	(void)unused;
	while (!kthread_should_stop() && next < A52_R380_SNAPSHOT_COUNT) {
		now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
		if (now_ms >= a52_r380_target_ms[next]) {
			a52_r380_take_snapshot(next);
			a52_ackfr_usbdiag_emit(next);
			next++;
			continue;
		}
		if (msleep_interruptible(10) && kthread_should_stop())
			break;
	}
	return 0;
}
'''
    new = '''static int a52_r380_sampler_fn(void *unused)
{
	unsigned int next = 0;
	u64 start_ns;
	u64 elapsed_ms;

	(void)unused;
	while (!kthread_should_stop()) {
		start_ns = a52_ackfr_frontier_trigger_ns();
		if (start_ns)
			break;
		if (msleep_interruptible(5) && kthread_should_stop())
			return 0;
	}

	while (!kthread_should_stop() && next < A52_R380_SNAPSHOT_COUNT) {
		elapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
				    NSEC_PER_MSEC);
		if (elapsed_ms >= a52_r380_target_ms[next]) {
			a52_r380_take_snapshot(next);
			a52_ackfr_usbdiag_emit(next);
			next++;
			continue;
		}
		if (msleep_interruptible(5) && kthread_should_stop())
			break;
	}
	return 0;
}
'''
    return one(text, old, new, "event-relative UFS sampler")


def patch_census(text: str) -> str:
    if "A52_PHASE383_EVENT_CENSUS_V1" in text:
        return text

    text = one(text,
        "#define A52_R377_SNAPSHOT_COUNT     3U\n",
        "#define A52_R377_SNAPSHOT_COUNT     4U\n",
        "census snapshot count")
    text = one(text,
        "#define A52_R377_THREADS_PER_SNAP   12U\n",
        "#define A52_R377_THREADS_PER_SNAP   9U\n",
        "census thread count")
    text = one(text,
        "#define A52_R377_COMMIT             0x382c0de5U\n",
        "#define A52_R377_COMMIT             0x383c0de5U\n",
        "census commit")
    text = one(text,
        "#define A52_R377_VERSION            2U\n",
        "#define A52_R377_VERSION            3U\n",
        "census version")

    old = '''static int a52_r377_sampler_fn(void *unused)
{
	static const u32 target_ms[A52_R377_SNAPSHOT_COUNT] = {
		18350U, 18500U, 18550U
	};
	unsigned int next = 0;
	u64 now_ms;

	while (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
		now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
		if (now_ms >= target_ms[next]) {
			a52_r377_take_snapshot(next);
			next++;
			continue;
		}
		if (msleep_interruptible(10) && kthread_should_stop())
			break;
	}
	return 0;
}
'''
    new = '''/* A52_PHASE383_EVENT_CENSUS_V1 */
static int a52_r377_sampler_fn(void *unused)
{
	static const u32 target_ms[A52_R377_SNAPSHOT_COUNT] = {
		50U, 150U, 250U, 350U
	};
	unsigned int next = 0;
	u64 start_ns;
	u64 elapsed_ms;

	while (!kthread_should_stop()) {
		start_ns = a52_ackfr_frontier_trigger_ns();
		if (start_ns)
			break;
		if (msleep_interruptible(5) && kthread_should_stop())
			return 0;
	}

	while (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
		elapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
				    NSEC_PER_MSEC);
		if (elapsed_ms >= target_ms[next]) {
			a52_r377_take_snapshot(next);
			next++;
			continue;
		}
		if (msleep_interruptible(5) && kthread_should_stop())
			break;
	}
	return 0;
}
'''
    return one(text, old, new, "event-relative census sampler")


def patch_blk(text: str) -> str:
    if "A52_PHASE383_EVENT_BLOCK_WINDOW_V1" in text:
        return text

    old = '''static bool a52_r382_block_cliff_window(void)
{
	u64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);

	return ms >= 18000U && ms <= 19000U;
}
'''
    new = '''/* A52_PHASE383_EVENT_BLOCK_WINDOW_V1 */
static bool a52_r382_block_cliff_window(void)
{
	u64 ms = a52_ackfr_frontier_elapsed_ms();

	return ms <= 600U;
}
'''
    text = one(text, old, new, "event-relative block window")
    text = text.replace("if (a52_id <= 32)", "if (a52_id <= 64)")
    return text


def patch_loop(text: str) -> str:
    if "A52_PHASE383_LOOP_AIO_STAGE_V1" in text:
        return text

    old = '''static bool a52_r382_loop_cliff_window(void)
{
	u64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);

	return ms >= 18000U && ms <= 19000U;
}
'''
    new = '''/* A52_PHASE383_LOOP_AIO_STAGE_V1 */
static atomic_t a52_r383_loop_stage_id = ATOMIC_INIT(0);

static bool a52_r382_loop_cliff_window(void)
{
	u64 ms = a52_ackfr_frontier_elapsed_ms();

	return ms <= 600U;
}

static bool a52_r383_stage_log(void)
{
	int id;

	if (!a52_r382_loop_cliff_window())
		return false;
	id = atomic_inc_return(&a52_r383_loop_stage_id);
	return id <= 96;
}
'''
    text = one(text, old, new, "event-relative loop window")

    old = '''	a52_ackfr_record("L380 MAP n=%d q=%px depth=%u",
			 i, lo->lo_queue, lo->tag_set.queue_depth);

	blk_queue_max_hw_sectors(lo->lo_queue, BLK_DEF_MAX_SECTORS);
'''
    new = '''	a52_ackfr_record("L380 MAP n=%d q=%px depth=%u",
			 i, lo->lo_queue, lo->tag_set.queue_depth);
	if (i >= 49)
		a52_ackfr_frontier_trigger((unsigned int)i);

	blk_queue_max_hw_sectors(lo->lo_queue, BLK_DEF_MAX_SECTORS);
'''
    text = one(text, old, new, "loop49 frontier trigger")

    old = '''static void loop_handle_cmd(struct loop_cmd *cmd)
{
	struct request *rq = blk_mq_rq_from_pdu(cmd);
	const bool write = op_is_write(req_op(rq));
	struct loop_device *lo = rq->q->queuedata;
	int ret = 0;

'''
    new = '''static void loop_handle_cmd(struct loop_cmd *cmd)
{
	struct request *rq = blk_mq_rq_from_pdu(cmd);
	const bool write = op_is_write(req_op(rq));
	struct loop_device *lo = rq->q->queuedata;
	int ret = 0;

	if (a52_r383_stage_log())
		a52_ackfr_record("L383 W n=%d rq=%px aio=%u op=%u",
				 lo ? lo->lo_number : -1, rq,
				 cmd->use_aio, req_op(rq));

'''
    text = one(text, old, new, "loop worker stage")

    old = '''static void lo_rw_aio_do_completion(struct loop_cmd *cmd)
{
	struct request *rq = blk_mq_rq_from_pdu(cmd);

	if (!atomic_dec_and_test(&cmd->ref))
		return;
	kfree(cmd->bvec);
	cmd->bvec = NULL;
	if (likely(!blk_should_fake_timeout(rq->q)))
		blk_mq_complete_request(rq);
}
'''
    new = '''static void lo_rw_aio_do_completion(struct loop_cmd *cmd)
{
	struct request *rq = blk_mq_rq_from_pdu(cmd);
	bool final;

	final = atomic_dec_and_test(&cmd->ref);
	if (a52_r383_stage_log())
		a52_ackfr_record("L383 X rq=%px final=%u ref=%d ret=%d",
				 rq, final, atomic_read(&cmd->ref), cmd->ret);
	if (!final)
		return;
	kfree(cmd->bvec);
	cmd->bvec = NULL;
	if (likely(!blk_should_fake_timeout(rq->q)))
		blk_mq_complete_request(rq);
}
'''
    text = one(text, old, new, "AIO completion ref stage")

    old = '''static void lo_rw_aio_complete(struct kiocb *iocb, long ret, long ret2)
{
	struct loop_cmd *cmd = container_of(iocb, struct loop_cmd, iocb);

	if (cmd->css)
		css_put(cmd->css);
	cmd->ret = ret;
	lo_rw_aio_do_completion(cmd);
}
'''
    new = '''static void lo_rw_aio_complete(struct kiocb *iocb, long ret, long ret2)
{
	struct loop_cmd *cmd = container_of(iocb, struct loop_cmd, iocb);
	struct request *rq = blk_mq_rq_from_pdu(cmd);

	if (a52_r383_stage_log())
		a52_ackfr_record("L383 C rq=%px ret=%ld ret2=%ld",
				 rq, ret, ret2);
	if (cmd->css)
		css_put(cmd->css);
	cmd->ret = ret;
	lo_rw_aio_do_completion(cmd);
}
'''
    text = one(text, old, new, "AIO callback stage")

    old = '''	if (rw == WRITE)
		ret = call_write_iter(file, &cmd->iocb, &iter);
	else
		ret = call_read_iter(file, &cmd->iocb, &iter);

	lo_rw_aio_do_completion(cmd);
'''
    new = '''	if (a52_r383_stage_log())
		a52_ackfr_record("L383 A rq=%px n=%d rw=%u bytes=%u",
				 rq, lo->lo_number, rw, blk_rq_bytes(rq));
	if (rw == WRITE)
		ret = call_write_iter(file, &cmd->iocb, &iter);
	else
		ret = call_read_iter(file, &cmd->iocb, &iter);
	if (a52_r383_stage_log())
		a52_ackfr_record("L383 R rq=%px ret=%d", rq, ret);

	lo_rw_aio_do_completion(cmd);
'''
    return one(text, old, new, "AIO submit/return stage")


def patch_udc(text: str) -> str:
    if "A52_PHASE383_GSERIAL_BIND_FILTER_V1" in text:
        return text

    old = '''	a52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, -9998);
	dev_dbg(&udc->dev, "registering UDC driver [%s]\\n",
'''
    new = '''	/* A52_PHASE383_GSERIAL_BIND_FILTER_V1 */
	if (!strcmp(driver->function, "g_serial"))
		a52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, -9998);
	dev_dbg(&udc->dev, "registering UDC driver [%s]\\n",
'''
    text = one(text, old, new, "g_serial bind entry filter")

    old = '''	a52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, 0);
	a52_ackfr_record("V382 U bind drv=%s rc=0", driver->function);
'''
    new = '''	if (!strcmp(driver->function, "g_serial"))
		a52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, 0);
	a52_ackfr_record("V382 U bind drv=%s rc=0", driver->function);
'''
    text = one(text, old, new, "g_serial bind success filter")

    old = '''	a52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, ret);
	a52_ackfr_record("V382 U bind drv=%s rc=%d", driver->function, ret);
'''
    new = '''	if (!strcmp(driver->function, "g_serial"))
		a52_ackfr_usbdiag_set(A52_ACKFR_USB_UDC_BIND_RC, ret);
	a52_ackfr_record("V382 U bind drv=%s rc=%d", driver->function, ret);
'''
    return one(text, old, new, "g_serial bind failure filter")


def validate(root: Path) -> None:
    checks = {
        HDR: ("a52_ackfr_frontier_trigger", "a52_ackfr_frontier_elapsed_ms"),
        REC: (MARK, "P383 trigger loop=%u", "strncmp(fmt, \"L383\", 4)"),
        UFS: ("A52_PHASE383_EVENT_UFS_FRONTIER_V1", "425U", "a52_ackfr_frontier_trigger_ns"),
        SYSCALL: ("A52_PHASE383_EVENT_CENSUS_V1", "50U, 150U, 250U, 350U"),
        BLK: ("A52_PHASE383_EVENT_BLOCK_WINDOW_V1", "a52_ackfr_frontier_elapsed_ms"),
        LOOP: ("A52_PHASE383_LOOP_AIO_STAGE_V1", "L383 W", "L383 A", "L383 R", "L383 C", "L383 X"),
        UDC: ("A52_PHASE383_GSERIAL_BIND_FILTER_V1",),
    }
    for rel, toks in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for tok in toks:
            if tok not in data:
                raise SystemExit(f"Phase383 validation missing {tok} in {rel}")


def run(root: Path) -> None:
    paths = (HDR, REC, UFS, SYSCALL, BLK, LOOP, UDC)
    for rel in paths:
        if not (root / rel).is_file():
            raise SystemExit(f"Phase383 missing source: {rel}")

    patches = (
        (HDR, patch_header),
        (REC, patch_recorder),
        (UFS, patch_ufs),
        (SYSCALL, patch_census),
        (BLK, patch_blk),
        (LOOP, patch_loop),
        (UDC, patch_udc),
    )
    for rel, fn in patches:
        p = root / rel
        p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
        print("Phase383 event frontier + loop AIO audit: PASS")
        return 0
    run(ns.root)
    print("Phase383 event frontier + loop AIO applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
