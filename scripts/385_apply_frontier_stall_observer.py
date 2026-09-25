#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE385_FRONTIER_STALL_OBSERVER_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
BLK = Path("block/blk-mq.c")
CORE = Path("block/blk-core.c")
LOOP = Path("drivers/block/loop.c")
UDC = Path("drivers/usb/gadget/udc/core.c")


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit("Phase385 %s: expected 1 anchor, found %d" % (label, count))
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        '''\t       !strncmp(fmt, "U384", 4) ||
\t       !strncmp(fmt, "B384", 4) ||
\t       !strncmp(fmt, "L384", 4); /* A52_PHASE384_LOOP_UFS_STALL_PINPOINT_V1 */
''',
        '''\t       !strncmp(fmt, "U384", 4) ||
\t       !strncmp(fmt, "B384", 4) ||
\t       !strncmp(fmt, "L384", 4) ||
\t       !strncmp(fmt, "P385", 4) ||
\t       !strncmp(fmt, "B385", 4) ||
\t       !strncmp(fmt, "V385", 4); /* A52_PHASE385_FRONTIER_STALL_OBSERVER_V1 */
''',
        "recorder diagnostic admission",
    )
    text = one(
        text,
        '''\t    strncmp(fmt, "U384", 4) &&
\t    strncmp(fmt, "B384", 4) &&
\t    strncmp(fmt, "L384", 4))
\t\treturn;
''',
        '''\t    strncmp(fmt, "U384", 4) &&
\t    strncmp(fmt, "B384", 4) &&
\t    strncmp(fmt, "L384", 4) &&
\t    strncmp(fmt, "P385", 4) &&
\t    strncmp(fmt, "B385", 4) &&
\t    strncmp(fmt, "V385", 4))
\t\treturn;
''',
        "recorder Phase243 admission",
    )
    text = one(
        text,
        '''\t       !strncmp(message, "U384 ", 5) ||
\t       !strncmp(message, "B384 ", 5) ||
\t       !strncmp(message, "L384 ", 5); /* A52_PHASE381_RETENTION_V1 */
''',
        '''\t       !strncmp(message, "U384 ", 5) ||
\t       !strncmp(message, "B384 ", 5) ||
\t       !strncmp(message, "L384 ", 5) ||
\t       !strncmp(message, "P385 ", 5) ||
\t       !strncmp(message, "B385 ", 5) ||
\t       !strncmp(message, "V385 ", 5); /* A52_PHASE381_RETENTION_V1 */
''',
        "recorder critical retention",
    )
    return text


def patch_ufs(text: str) -> str:
    if "A52_PHASE385_DENSE_UFS_FRONTIER_V1" in text:
        return text

    old = '''/* A52_PHASE384_UFS_LONG_FRONTIER_V1 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t25U, 75U, 125U, 225U, 425U, 750U,
\t1000U, 2000U, 5000U, 10000U, 20000U,
};
'''
    new = '''/* A52_PHASE384_UFS_LONG_FRONTIER_V1 */
/* A52_PHASE385_DENSE_UFS_FRONTIER_V1 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t25U, 75U, 125U, 225U, 230U, 240U,
\t250U, 275U, 300U, 350U, 425U,
};
'''
    return one(text, old, new, "dense UFS frontier")


def patch_blk(text: str) -> str:
    if "A52_PHASE385_FREEZE_OBSERVER_V1" in text:
        return text

    inc_anchor = "#include <linux/ktime.h>\n"
    if "#include <linux/kthread.h>\n" not in text:
        text = one(
            text,
            inc_anchor,
            inc_anchor + "#include <linux/kthread.h>\n#include <linux/delay.h>\n#include <linux/init.h>\n",
            "blk observer includes",
        )

    old = '''/* A52_PHASE384_FREEZE_WAIT_HEARTBEAT_V1 */
void blk_mq_freeze_queue_wait(struct request_queue *q)
{
\tbool trace = a52_r382_block_cliff_window();
\tunsigned int a52_wait = 0;

\tif (trace)
\t\ta52_ackfr_record("B380 F enter q=%px dep=%d zero=%u",
\t\t\t\t  q, q->mq_freeze_depth,
\t\t\t\t  percpu_ref_is_zero(&q->q_usage_counter));

\tif (!trace) {
\t\twait_event(q->mq_freeze_wq,
\t\t\t   percpu_ref_is_zero(&q->q_usage_counter));
\t} else {
\t\twhile (!percpu_ref_is_zero(&q->q_usage_counter)) {
\t\t\twait_event_timeout(q->mq_freeze_wq,
\t\t\t\t\t   percpu_ref_is_zero(&q->q_usage_counter),
\t\t\t\t\t   msecs_to_jiffies(250));
\t\t\tif (percpu_ref_is_zero(&q->q_usage_counter))
\t\t\t\tbreak;
\t\t\ta52_wait++;
\t\t\tif (a52_wait <= 80)
\t\t\t\ta52_ackfr_record("B384 W q=%px dep=%d nr=%u n=%u zero=0",
\t\t\t\t\t\t q, q->mq_freeze_depth,
\t\t\t\t\t\t q->nr_requests, a52_wait);
\t\t}
\t}
\tif (trace)
\t\ta52_ackfr_record("B380 F exit q=%px dep=%d", q,
\t\t\t\t  q->mq_freeze_depth);
}
'''
    new = '''/* A52_PHASE384_FREEZE_WAIT_HEARTBEAT_V1 */
/* A52_PHASE385_FREEZE_OBSERVER_V1
 *
 * Restore the original wait_event semantics. Phase384's timeout wakeups could
 * hide an intermittent missing-wakeup race. A separate observer kthread samples
 * the waiter state without waking the queue.
 */
#define A52_R385_FREEZE_SLOTS 8U

struct a52_r385_freeze_slot {
\tstruct request_queue *q;
\tu64 start_ns;
\tbool active;
};

static struct a52_r385_freeze_slot a52_r385_freezes[A52_R385_FREEZE_SLOTS];
static DEFINE_SPINLOCK(a52_r385_freeze_lock);
static struct task_struct *a52_r385_observer_task;

static int a52_r385_freeze_enter(struct request_queue *q)
{
\tunsigned long flags;
\tunsigned int i;
\tint slot = -1;

\tspin_lock_irqsave(&a52_r385_freeze_lock, flags);
\tfor (i = 0; i < A52_R385_FREEZE_SLOTS; i++) {
\t\tif (a52_r385_freezes[i].active)
\t\t\tcontinue;
\t\ta52_r385_freezes[i].q = q;
\t\ta52_r385_freezes[i].start_ns = ktime_get_boottime_ns();
\t\ta52_r385_freezes[i].active = true;
\t\tslot = (int)i;
\t\tbreak;
\t}
\tspin_unlock_irqrestore(&a52_r385_freeze_lock, flags);

\tif (slot >= 0)
\t\ta52_ackfr_record("B385 E s=%d q=%px dep=%d nr=%u zero=%u",
\t\t\t\t slot, q, q->mq_freeze_depth, q->nr_requests,
\t\t\t\t percpu_ref_is_zero(&q->q_usage_counter));
\telse
\t\ta52_ackfr_record("B385 E s=-1 q=%px dep=%d nr=%u zero=%u",
\t\t\t\t q, q->mq_freeze_depth, q->nr_requests,
\t\t\t\t percpu_ref_is_zero(&q->q_usage_counter));
\treturn slot;
}

static void a52_r385_freeze_exit(int slot, struct request_queue *q)
{
\tunsigned long flags;
\tu64 age_ms = 0;

\tif (slot >= 0 && slot < A52_R385_FREEZE_SLOTS) {
\t\tspin_lock_irqsave(&a52_r385_freeze_lock, flags);
\t\tif (a52_r385_freezes[slot].active &&
\t\t    a52_r385_freezes[slot].q == q) {
\t\t\tage_ms = div_u64(ktime_get_boottime_ns() -
\t\t\t\t\t a52_r385_freezes[slot].start_ns,
\t\t\t\t\t NSEC_PER_MSEC);
\t\t\ta52_r385_freezes[slot].active = false;
\t\t\ta52_r385_freezes[slot].q = NULL;
\t\t}
\t\tspin_unlock_irqrestore(&a52_r385_freeze_lock, flags);
\t}

\ta52_ackfr_record("B385 X s=%d q=%px dep=%d age=%llu zero=%u",
\t\t\t slot, q, q->mq_freeze_depth,
\t\t\t (unsigned long long)age_ms,
\t\t\t percpu_ref_is_zero(&q->q_usage_counter));
}

static void a52_r385_sample_freezes(unsigned int sample, u64 elapsed_ms)
{
\tunsigned long flags;
\tunsigned int i;

\ta52_ackfr_record("P385 H s=%u ms=%llu",
\t\t\t sample, (unsigned long long)elapsed_ms);

\tfor (i = 0; i < A52_R385_FREEZE_SLOTS; i++) {
\t\tstruct request_queue *q = NULL;
\t\tu64 age_ms = 0;
\t\tunsigned int zero = 0;
\t\tunsigned int nr = 0;
\t\tint depth = 0;

\t\tspin_lock_irqsave(&a52_r385_freeze_lock, flags);
\t\tif (a52_r385_freezes[i].active) {
\t\t\tq = a52_r385_freezes[i].q;
\t\t\tif (q) {
\t\t\t\tage_ms = div_u64(ktime_get_boottime_ns() -
\t\t\t\t\t\t a52_r385_freezes[i].start_ns,
\t\t\t\t\t\t NSEC_PER_MSEC);
\t\t\t\tzero = percpu_ref_is_zero(&q->q_usage_counter);
\t\t\t\tnr = q->nr_requests;
\t\t\t\tdepth = q->mq_freeze_depth;
\t\t\t}
\t\t}
\t\tspin_unlock_irqrestore(&a52_r385_freeze_lock, flags);

\t\tif (q)
\t\t\ta52_ackfr_record("B385 W s=%u q=%px dep=%d nr=%u age=%llu zero=%u",
\t\t\t\t\t i, q, depth, nr,
\t\t\t\t\t (unsigned long long)age_ms, zero);
\t}
}

static int a52_r385_observer_fn(void *unused)
{
\tstatic const u32 target_ms[] = {
\t\t230U, 240U, 250U, 275U, 300U, 325U, 350U, 375U,
\t\t400U, 425U, 500U, 750U, 1000U, 1500U, 2000U,
\t};
\tunsigned int next = 0;
\tu64 start_ns;
\tu64 elapsed_ms;

\t(void)unused;
\twhile (!kthread_should_stop()) {
\t\tstart_ns = a52_ackfr_frontier_trigger_ns();
\t\tif (start_ns)
\t\t\tbreak;
\t\tif (msleep_interruptible(5) && kthread_should_stop())
\t\t\treturn 0;
\t}

\twhile (!kthread_should_stop() && next < ARRAY_SIZE(target_ms)) {
\t\telapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
\t\t\t\t    NSEC_PER_MSEC);
\t\tif (elapsed_ms >= target_ms[next]) {
\t\t\ta52_r385_sample_freezes(next, elapsed_ms);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(2) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
}

static int __init a52_r385_observer_init(void)
{
\ta52_r385_observer_task =
\t\tkthread_run(a52_r385_observer_fn, NULL, "a52_r385_obs");
\tif (IS_ERR(a52_r385_observer_task)) {
\t\ta52_ackfr_record("P385 observer_err=%ld",
\t\t\t\t PTR_ERR(a52_r385_observer_task));
\t\ta52_r385_observer_task = NULL;
\t}
\treturn 0;
}
late_initcall(a52_r385_observer_init);

void blk_mq_freeze_queue_wait(struct request_queue *q)
{
\tbool trace = a52_r382_block_cliff_window();
\tint a52_slot = -1;

\tif (trace) {
\t\ta52_ackfr_record("B380 F enter q=%px dep=%d zero=%u",
\t\t\t\t  q, q->mq_freeze_depth,
\t\t\t\t  percpu_ref_is_zero(&q->q_usage_counter));
\t\ta52_slot = a52_r385_freeze_enter(q);
\t}

\twait_event(q->mq_freeze_wq, percpu_ref_is_zero(&q->q_usage_counter));

\tif (trace) {
\t\ta52_r385_freeze_exit(a52_slot, q);
\t\ta52_ackfr_record("B380 F exit q=%px dep=%d", q,
\t\t\t\t  q->mq_freeze_depth);
\t}
}
'''
    return one(text, old, new, "restore wait and add freeze observer")


def patch_core(text: str) -> str:
    if "A52_PHASE385_LOW_PERTURBATION_QUEUE_V1" in text:
        return text

    old = '''\t\tif (a52_ms <= 2000U &&
\t\t    (a52_id = atomic_inc_return(&a52_r384_qget_id)) <= 192)
'''
    new = '''\t\t/* A52_PHASE385_LOW_PERTURBATION_QUEUE_V1 */
\t\tif (false && a52_ms <= 2000U &&
\t\t    (a52_id = atomic_inc_return(&a52_r384_qget_id)) <= 192)
'''
    text = one(text, old, new, "disable broad queue-get trace")

    old = '''\tif (a52_ms <= 2000U &&
\t    (a52_id = atomic_inc_return(&a52_r384_qput_id)) <= 192)
'''
    new = '''\tif (false && a52_ms <= 2000U &&
\t    (a52_id = atomic_inc_return(&a52_r384_qput_id)) <= 192)
'''
    return one(text, old, new, "disable broad queue-put trace")


def patch_loop(text: str) -> str:
    if "A52_PHASE385_LOW_PERTURBATION_LOOP_V1" in text:
        return text

    old = '''\tif (n < 46 || n > 49)
\t\treturn false;
\treturn atomic_inc_return(&a52_r384_loop_stage_id) <= 384;
}
'''
    new = '''\tif (n < 46 || n > 49)
\t\treturn false;
\t/* A52_PHASE385_LOW_PERTURBATION_LOOP_V1 */
\treturn atomic_read(&a52_r384_loop_stage_id) < 0;
}
'''
    return one(text, old, new, "disable high-volume L384 lifecycle")


def patch_udc(text: str) -> str:
    if "A52_PHASE385_UDC_IDENTITY_OBSERVER_V1" in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        raise SystemExit("Phase385 UDC requires inherited recorder include")
    if "#include <linux/kthread.h>\n" not in text:
        text = one(
            text,
            inc,
            inc + "#include <linux/kthread.h>\n#include <linux/delay.h>\n#include <linux/init.h>\n",
            "UDC observer includes",
        )

    anchor = "static DEFINE_MUTEX(udc_lock);\n"
    if anchor not in text:
        raise SystemExit("Phase385 UDC lock anchor missing")

    block = '''
/* A52_PHASE385_UDC_IDENTITY_OBSERVER_V1 */
static struct task_struct *a52_r385_usb_observer_task;

static void a52_r385_usb_snapshot(unsigned int sample)
{
\tstruct usb_udc *udc;
\tunsigned int seen = 0;

\tmutex_lock(&udc_lock);
\tlist_for_each_entry(udc, &udc_list, list) {
\t\tstruct usb_gadget *g = udc->gadget;
\t\tconst char *driver = udc->driver && udc->driver->function ?
\t\t\tudc->driver->function : "-";
\t\tconst char *gname = g && g->name ? g->name : "-";
\t\tconst char *parent = g && g->dev.parent ?
\t\t\tdev_name(g->dev.parent) : "-";
\t\tunsigned int pull = g && g->ops && g->ops->pullup ? 1U : 0U;

\t\tif (seen >= 4)
\t\t\tbreak;
\t\ta52_ackfr_record("V385 U s=%u udc=%s gad=%s par=%s drv=%s pull=%u",
\t\t\t\t sample, dev_name(&udc->dev), gname, parent,
\t\t\t\t driver, pull);
\t\tseen++;
\t}
\tmutex_unlock(&udc_lock);

\tif (!seen)
\t\ta52_ackfr_record("V385 U s=%u none", sample);
}

static int a52_r385_usb_observer_fn(void *unused)
{
\tstatic const u32 target_ms[] = { 230U, 300U, 425U, 750U };
\tunsigned int next = 0;
\tu64 start_ns;
\tu64 elapsed_ms;

\t(void)unused;
\twhile (!kthread_should_stop()) {
\t\tstart_ns = a52_ackfr_frontier_trigger_ns();
\t\tif (start_ns)
\t\t\tbreak;
\t\tif (msleep_interruptible(5) && kthread_should_stop())
\t\t\treturn 0;
\t}

\twhile (!kthread_should_stop() && next < ARRAY_SIZE(target_ms)) {
\t\telapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
\t\t\t\t    NSEC_PER_MSEC);
\t\tif (elapsed_ms >= target_ms[next]) {
\t\t\ta52_r385_usb_snapshot(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(5) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
}

static int __init a52_r385_usb_observer_init(void)
{
\ta52_r385_usb_observer_task =
\t\tkthread_run(a52_r385_usb_observer_fn, NULL, "a52_r385_usb");
\tif (IS_ERR(a52_r385_usb_observer_task)) {
\t\ta52_ackfr_record("V385 observer_err=%ld",
\t\t\t\t PTR_ERR(a52_r385_usb_observer_task));
\t\ta52_r385_usb_observer_task = NULL;
\t}
\treturn 0;
}
late_initcall(a52_r385_usb_observer_init);

'''
    return one(text, anchor, anchor + block, "UDC identity observer")


def validate(root: Path) -> None:
    checks = {
        REC: (MARK, 'strncmp(fmt, "P385", 4)', 'strncmp(fmt, "B385", 4)', 'strncmp(fmt, "V385", 4)'),
        UFS: ("A52_PHASE385_DENSE_UFS_FRONTIER_V1", "230U, 240U", "300U, 350U, 425U"),
        BLK: ("A52_PHASE385_FREEZE_OBSERVER_V1", "P385 H s=%u", "B385 W s=%u", "B385 X s=%d", "wait_event(q->mq_freeze_wq"),
        CORE: ("A52_PHASE385_LOW_PERTURBATION_QUEUE_V1", "false && a52_ms <= 2000U"),
        LOOP: ("A52_PHASE385_LOW_PERTURBATION_LOOP_V1", "atomic_read(&a52_r384_loop_stage_id) < 0"),
        UDC: ("A52_PHASE385_UDC_IDENTITY_OBSERVER_V1", "V385 U s=%u udc=%s", '"a52_r385_usb"'),
    }
    for rel, tokens in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in data:
                raise SystemExit("Phase385 validation missing %r in %s" % (token, rel))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()

    if args.check_only:
        validate(root)
        print("Phase385 frontier stall observer validation: PASS")
        return

    patches = (
        (REC, patch_recorder),
        (UFS, patch_ufs),
        (BLK, patch_blk),
        (CORE, patch_core),
        (LOOP, patch_loop),
        (UDC, patch_udc),
    )
    for rel, fn in patches:
        path = root / rel
        if not path.is_file():
            raise SystemExit("Phase385 missing source file: %s" % path)
        before = path.read_text(encoding="utf-8")
        after = fn(before)
        path.write_text(after, encoding="utf-8")
        print("Phase385 patched %s: %s" % (rel, before != after))

    validate(root)
    print("Phase385 frontier stall observer applied successfully")


if __name__ == "__main__":
    main()
