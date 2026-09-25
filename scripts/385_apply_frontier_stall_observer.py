#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE385_FRONTIER_STALL_OBSERVER_V1"
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
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



def patch_header(text: str) -> str:
    if "A52_PHASE385_STICKY_FIXED_V1" in text:
        return text

    block = '''
/* A52_PHASE385_STICKY_FIXED_V1 */
void a52_ackfr_sticky_resync(void);
void a52_ackfr_sticky_heartbeat(unsigned int sample, u64 elapsed_ms);
void a52_ackfr_sticky_freeze(unsigned int active, u64 q, int depth,
                             unsigned int nr, unsigned int zero, u64 age_ms);
void a52_ackfr_sticky_usb_identity(const char *udc, const char *gadget,
                                   const char *parent, const char *driver,
                                   unsigned int pullup_capable);

'''
    return one(text, "\n#endif\n", block + "\n#endif\n", "sticky header API")


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

    if "A52_PHASE385_STICKY_FIXED_V1" not in text:
        text = one(
            text,
            "void a52_ackfr_usbdiag_set(unsigned int field, int value)\n",
            "static void a52_r385_sticky_usb_refresh(void);\n\n"
            "void a52_ackfr_usbdiag_set(unsigned int field, int value)\n",
            "sticky USB refresh forward declaration",
        )
        text = one(
            text,
            '''\tdefault:
\t\tbreak;
\t}
}
EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_set);
''',
            '''\tdefault:
\t\tbreak;
\t}
\ta52_r385_sticky_usb_refresh();
}
EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_set);
''',
            "sticky USB update hook",
        )

        sticky_block = '''
/* A52_PHASE385_STICKY_FIXED_V1
 *
 * RS48 is a high-rate circular recorder. Preserve the state that must survive
 * ring turnover in the otherwise-unused final 0x200 bytes of Phase380's
 * 0xB1BF8000..0xB1BFFFFF UFS sideband:
 *
 *   copy 0: 0xB1BFFE00..0xB1BFFEFF
 *   copy 1: 0xB1BFFF00..0xB1BFFFFF
 *
 * Each copy is a complete 256-byte CRC32C-protected snapshot. Updates preserve
 * earlier fields, so a later heartbeat cannot erase the USB identity/state.
 */
#define A52_R385_STICKY_PHYS       0xB1BFFE00ULL
#define A52_R385_STICKY_BYTES      0x200U
#define A52_R385_STICKY_COPY       0x100U
#define A52_R385_STICKY_MAGIC      0x353833594b434954ULL
#define A52_R385_STICKY_COMMIT     0x385c0de5U
#define A52_R385_STICKY_VERSION    1U

struct a52_r385_sticky_record {
\tu64 magic;
\tu64 seq;
\tu64 ns;
\tu64 frontier_ms;
\tu64 freeze_age_ms;
\tu64 freeze_q;
\tu32 version;
\tu32 event;
\tu32 heartbeat;
\tu32 freeze_active;
\ts32 freeze_depth;
\tu32 freeze_nr;
\tu32 freeze_zero;
\ts32 usb_mode_in;
\ts32 usb_hw_mode;
\ts32 usb_mode_out;
\ts32 usb_core_mode;
\ts32 usb_gadget_init;
\ts32 usb_gadget_add_rc;
\ts32 usb_gs_probe_rc;
\ts32 usb_udc_bind_rc;
\ts32 usb_pullup_rc;
\tchar udc[32];
\tchar gadget[32];
\tchar parent[40];
\tchar driver[24];
\tu32 pullup_capable;
\tu32 crc32c;
\tu32 commit;
\tu32 reserved;
};

static DEFINE_SPINLOCK(a52_r385_sticky_lock);
static struct a52_r385_sticky_record a52_r385_sticky_shadow;
static void *a52_r385_sticky_map;

static u32 a52_r385_sticky_crc32c(const void *buffer, size_t len)
{
\tconst u8 *bytes = buffer;
\tu32 crc = ~0U;
\tsize_t index;
\tunsigned int bit;

\tfor (index = 0; index < len; index++) {
\t\tcrc ^= bytes[index];
\t\tfor (bit = 0; bit < 8; bit++)
\t\t\tcrc = (crc >> 1) ^
\t\t\t\t((crc & 1) ? 0x82f63b78U : 0U);
\t}
\treturn ~crc;
}

static void a52_r385_sticky_refresh_usb_locked(void)
{
\tstruct a52_r385_sticky_record *r = &a52_r385_sticky_shadow;

\tr->usb_mode_in = atomic_read(&a52_r382_usb_mode_in);
\tr->usb_hw_mode = atomic_read(&a52_r382_usb_hw_mode);
\tr->usb_mode_out = atomic_read(&a52_r382_usb_mode_out);
\tr->usb_core_mode = atomic_read(&a52_r382_usb_core_mode);
\tr->usb_gadget_init = atomic_read(&a52_r382_usb_gadget_init);
\tr->usb_gadget_add_rc = atomic_read(&a52_r382_usb_gadget_add_rc);
\tr->usb_gs_probe_rc = atomic_read(&a52_r382_usb_gs_probe_rc);
\tr->usb_udc_bind_rc = atomic_read(&a52_r382_usb_udc_bind_rc);
\tr->usb_pullup_rc = atomic_read(&a52_r382_usb_pullup_rc);
}

static void a52_r385_sticky_flush_locked(void)
{
\tstruct a52_r385_sticky_record *r = &a52_r385_sticky_shadow;
\tvoid *dst0;
\tvoid *dst1;

\tr->magic = A52_R385_STICKY_MAGIC;
\tr->version = A52_R385_STICKY_VERSION;
\tr->seq++;
\tr->ns = ktime_get_boottime_ns();
\ta52_r385_sticky_refresh_usb_locked();
\tr->commit = A52_R385_STICKY_COMMIT;
\tr->crc32c = a52_r385_sticky_crc32c(
\t\tr, offsetof(struct a52_r385_sticky_record, crc32c));

\tif (!READ_ONCE(a52_r385_sticky_map))
\t\treturn;

\tdst0 = a52_r385_sticky_map;
\tdst1 = (u8 *)a52_r385_sticky_map + A52_R385_STICKY_COPY;
\tmemcpy(dst0, r, sizeof(*r));
\tmemcpy(dst1, r, sizeof(*r));
\twmb();
\t__flush_dcache_area(dst0, sizeof(*r));
\t__flush_dcache_area(dst1, sizeof(*r));
}

static void a52_r385_sticky_usb_refresh(void)
{
\tunsigned long flags;

\tspin_lock_irqsave(&a52_r385_sticky_lock, flags);
\ta52_r385_sticky_shadow.event = 4U;
\ta52_r385_sticky_flush_locked();
\tspin_unlock_irqrestore(&a52_r385_sticky_lock, flags);
}

void a52_ackfr_sticky_resync(void)
{
\tunsigned long flags;

\tspin_lock_irqsave(&a52_r385_sticky_lock, flags);
\ta52_r385_sticky_flush_locked();
\tspin_unlock_irqrestore(&a52_r385_sticky_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky_resync);

void a52_ackfr_sticky_heartbeat(unsigned int sample, u64 elapsed_ms)
{
\tunsigned long flags;

\tspin_lock_irqsave(&a52_r385_sticky_lock, flags);
\ta52_r385_sticky_shadow.event = 1U;
\ta52_r385_sticky_shadow.heartbeat = sample;
\ta52_r385_sticky_shadow.frontier_ms = elapsed_ms;
\ta52_r385_sticky_flush_locked();
\tspin_unlock_irqrestore(&a52_r385_sticky_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky_heartbeat);

void a52_ackfr_sticky_freeze(unsigned int active, u64 q, int depth,
\t\t\t     unsigned int nr, unsigned int zero, u64 age_ms)
{
\tunsigned long flags;

\tspin_lock_irqsave(&a52_r385_sticky_lock, flags);
\ta52_r385_sticky_shadow.event = active ? 2U : 3U;
\ta52_r385_sticky_shadow.freeze_active = active;
\ta52_r385_sticky_shadow.freeze_q = q;
\ta52_r385_sticky_shadow.freeze_depth = depth;
\ta52_r385_sticky_shadow.freeze_nr = nr;
\ta52_r385_sticky_shadow.freeze_zero = zero;
\ta52_r385_sticky_shadow.freeze_age_ms = age_ms;
\ta52_r385_sticky_flush_locked();
\tspin_unlock_irqrestore(&a52_r385_sticky_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky_freeze);

void a52_ackfr_sticky_usb_identity(const char *udc, const char *gadget,
\t\t\t\t   const char *parent, const char *driver,
\t\t\t\t   unsigned int pullup_capable)
{
\tunsigned long flags;

\tspin_lock_irqsave(&a52_r385_sticky_lock, flags);
\ta52_r385_sticky_shadow.event = 5U;
\tstrscpy(a52_r385_sticky_shadow.udc, udc ? udc : "-",
\t\tsizeof(a52_r385_sticky_shadow.udc));
\tstrscpy(a52_r385_sticky_shadow.gadget, gadget ? gadget : "-",
\t\tsizeof(a52_r385_sticky_shadow.gadget));
\tstrscpy(a52_r385_sticky_shadow.parent, parent ? parent : "-",
\t\tsizeof(a52_r385_sticky_shadow.parent));
\tstrscpy(a52_r385_sticky_shadow.driver, driver ? driver : "-",
\t\tsizeof(a52_r385_sticky_shadow.driver));
\ta52_r385_sticky_shadow.pullup_capable = pullup_capable;
\ta52_r385_sticky_flush_locked();
\tspin_unlock_irqrestore(&a52_r385_sticky_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_sticky_usb_identity);

static int __init a52_r385_sticky_init(void)
{
\tvoid *mapping;
\tunsigned long flags;

\tBUILD_BUG_ON(sizeof(struct a52_r385_sticky_record) !=
\t\t     A52_R385_STICKY_COPY);

\tmapping = memremap(A52_R385_STICKY_PHYS,
\t\tA52_R385_STICKY_BYTES, MEMREMAP_WB);
\tif (!mapping)
\t\treturn 0;

\tspin_lock_irqsave(&a52_r385_sticky_lock, flags);
\tWRITE_ONCE(a52_r385_sticky_map, mapping);
\ta52_r385_sticky_shadow.usb_mode_in = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_hw_mode = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_mode_out = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_core_mode = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_gadget_init = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_gadget_add_rc = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_gs_probe_rc = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_udc_bind_rc = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_shadow.usb_pullup_rc = A52_R382_USB_UNSEEN;
\ta52_r385_sticky_flush_locked();
\tspin_unlock_irqrestore(&a52_r385_sticky_lock, flags);
\treturn 0;
}
subsys_initcall(a52_r385_sticky_init);

'''
        text = one(
            text,
            "EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_emit);\n",
            "EXPORT_SYMBOL_GPL(a52_ackfr_usbdiag_emit);\n\n" + sticky_block,
            "sticky fixed-slot implementation",
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
    text = one(text, old, new, "dense UFS frontier")

    old = '''\tmemset(a52_r380_sideband, 0, A52_R380_SIDEBAND_BYTES);
\twmb();
\t__flush_dcache_area(a52_r380_sideband, A52_R380_SIDEBAND_BYTES);
\tWRITE_ONCE(a52_r380_hba, hba);
'''
    new = '''\tmemset(a52_r380_sideband, 0, A52_R380_SIDEBAND_BYTES);
\twmb();
\t__flush_dcache_area(a52_r380_sideband, A52_R380_SIDEBAND_BYTES);
\t/* Phase385 rewrites its mirrored fixed slot after the Phase380 clear. */
\ta52_ackfr_sticky_resync();
\tWRITE_ONCE(a52_r380_hba, hba);
'''
    return one(text, old, new, "sticky resync after UFS sideband clear")


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
        HDR: ("A52_PHASE385_STICKY_FIXED_V1", "a52_ackfr_sticky_resync", "a52_ackfr_sticky_usb_identity"),
        REC: (MARK, "A52_PHASE385_STICKY_FIXED_V1", "A52_R385_STICKY_PHYS", "A52_R385_STICKY_COPY", 'strncmp(fmt, "P385", 4)', 'strncmp(fmt, "B385", 4)', 'strncmp(fmt, "V385", 4)'),
        UFS: ("A52_PHASE385_DENSE_UFS_FRONTIER_V1", "230U, 240U", "300U, 350U, 425U", "a52_ackfr_sticky_resync();"),
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
        (HDR, patch_header),
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
