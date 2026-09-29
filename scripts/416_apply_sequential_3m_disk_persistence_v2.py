#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE416_SEQUENTIAL_3M_DISK_PERSIST_V2"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase416 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

STATUS_BLOCK = r'''
/* A52_PHASE416_SEQUENTIAL_3M_DISK_PERSIST_V2
 *
 * Phase414 correctly staged the chronological 2 MiB disk tier, but Phase415
 * recovery proved that the +0x800000 Samsung image remained stale Phase413.
 * Keep the Phase414 record format/capacity unchanged and harden only transport:
 *
 *  - core_initcall still allocates/stages the earliest records;
 *  - raw block I/O is not armed until late_initcall, after device init;
 *  - pre-late records remain in the 2 MiB vmalloc staging image;
 *  - late arm flushes the current generation, then normal append events nudge it;
 *  - B1A header is updated during the disk tier too, so logical count survives;
 *  - three redundant 128-byte status copies in B1A expose open/BIO/flush rc,
 *    retries, generations, and last page even when Samsung persistence fails.
 */
#define A52_P416_STATUS_MAGIC         0x3631345354415433ULL /* 3TATS416 */
#define A52_P416_STATUS_VERSION       1U
#define A52_P416_STATUS_COMMIT        0x416c0de5U
#define A52_P416_STATUS_OFFSET0       0x00000100U
#define A52_P416_STATUS_OFFSET1       0x00000180U
#define A52_P416_STATUS_OFFSET2       0x00000200U

struct a52_p416_status {
	u64 magic;
	u64 magic_inv;
	u32 version;
	u32 version_inv;
	u32 armed;
	u32 armed_inv;
	u32 worker_runs;
	u32 worker_runs_inv;
	s32 open_rc;
	u32 open_rc_inv;
	s32 submit_rc;
	u32 submit_rc_inv;
	s32 flush_rc;
	u32 flush_rc_inv;
	u32 retry_count;
	u32 retry_count_inv;
	u32 disk_gen;
	u32 disk_gen_inv;
	u32 written_gen;
	u32 written_gen_inv;
	u32 disk_count;
	u32 disk_count_inv;
	u32 last_page;
	u32 last_page_inv;
	u32 state;
	u32 state_inv;
	u32 commit;
	u32 commit_inv;
	u32 reserved[2];
} __packed;

static u32 a52_p416_disk_armed;
static u32 a52_p416_worker_runs;
static s32 a52_p416_open_rc = -EAGAIN;
static s32 a52_p416_submit_rc = -EAGAIN;
static s32 a52_p416_flush_rc = -EAGAIN;
static u32 a52_p416_last_page;

static void a52_p416_fill_status(struct a52_p416_status *s)
{
	u32 open_bits = (u32)a52_p416_open_rc;
	u32 submit_bits = (u32)a52_p416_submit_rc;
	u32 flush_bits = (u32)a52_p416_flush_rc;
	u32 retries = (u32)atomic_read(&a52_p414_disk_retry);
	u32 gen = (u32)atomic_read(&a52_p414_disk_gen);
	u32 written = (u32)atomic_read(&a52_p414_disk_written_gen);

	memset(s, 0, sizeof(*s));
	s->magic = A52_P416_STATUS_MAGIC;
	s->magic_inv = ~A52_P416_STATUS_MAGIC;
	s->version = A52_P416_STATUS_VERSION;
	s->version_inv = ~A52_P416_STATUS_VERSION;
	s->armed = a52_p416_disk_armed;
	s->armed_inv = ~s->armed;
	s->worker_runs = a52_p416_worker_runs;
	s->worker_runs_inv = ~s->worker_runs;
	s->open_rc = a52_p416_open_rc;
	s->open_rc_inv = ~open_bits;
	s->submit_rc = a52_p416_submit_rc;
	s->submit_rc_inv = ~submit_bits;
	s->flush_rc = a52_p416_flush_rc;
	s->flush_rc_inv = ~flush_bits;
	s->retry_count = retries;
	s->retry_count_inv = ~retries;
	s->disk_gen = gen;
	s->disk_gen_inv = ~gen;
	s->written_gen = written;
	s->written_gen_inv = ~written;
	s->disk_count = a52_p414_disk_count;
	s->disk_count_inv = ~s->disk_count;
	s->last_page = a52_p416_last_page;
	s->last_page_inv = ~s->last_page;
	s->state = a52_p414_state;
	s->state_inv = ~s->state;
	s->commit = A52_P416_STATUS_COMMIT;
	s->commit_inv = ~A52_P416_STATUS_COMMIT;
}

static void a52_p416_sync_status_locked(void)
{
	struct a52_p416_status s;
	const u32 offsets[] = {
		A52_P416_STATUS_OFFSET0,
		A52_P416_STATUS_OFFSET1,
		A52_P416_STATUS_OFFSET2,
	};
	unsigned int i;

	if (!a52_p414_ram)
		return;

	a52_p416_fill_status(&s);
	for (i = 0; i < ARRAY_SIZE(offsets); i++) {
		if (a52_p414_disk_stage)
			memcpy(a52_p414_disk_stage + offsets[i], &s, sizeof(s));
		memcpy_toio((u8 __iomem *)a52_p414_ram + offsets[i],
			    &s, sizeof(s));
	}
	a52_p406_persist((u8 __iomem *)a52_p414_ram + A52_P416_STATUS_OFFSET0,
			 A52_P416_STATUS_OFFSET2 - A52_P416_STATUS_OFFSET0 +
			 sizeof(s));
}
'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    for tok in (
        "A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1",
        "A52_PHASE415_MDSS_GDSC_HWCTRL_PARITY_V1",
        "static atomic_t a52_p414_disk_retry = ATOMIC_INIT(0);",
        "static void a52_p414_disk_workfn(struct work_struct *work)",
        "core_initcall_sync(a52_p414_init);",
    ):
        if tok not in text:
            raise SystemExit("Phase416 prerequisite missing: " + tok)

    # Phase416 uses EAGAIN/EINPROGRESS explicitly. Keep the dependency local.
    if "#include <linux/errno.h>\n" not in text:
        text = one(
            text,
            "#include <linux/kernel.h>\n",
            "#include <linux/kernel.h>\n#include <linux/errno.h>\n",
            "errno include",
        )

    # Put sidecar definitions after Phase414 state variables, before functions.
    anchor = "static atomic_t a52_p414_disk_retry = ATOMIC_INIT(0);\n"
    text = one(text, anchor, anchor + STATUS_BLOCK, "status block")

    # Do not attempt raw block I/O at core_init. queue_disk still increments the
    # generation, but only a late-armed transport can schedule the worker.
    old_queue = '''static void a52_p414_queue_disk(void)\n{\n\tatomic_inc(&a52_p414_disk_gen);\n\tmod_delayed_work(system_unbound_wq, &a52_p414_disk_work, 0);\n}\n'''
    new_queue = '''static void a52_p414_queue_disk(void)\n{\n\tatomic_inc(&a52_p414_disk_gen);\n\tif (READ_ONCE(a52_p416_disk_armed))\n\t\tmod_delayed_work(system_unbound_wq, &a52_p414_disk_work, 0);\n}\n'''
    text = one(text, old_queue, new_queue, "arm-gated queue")

    # Instrument the worker and persist each transition into B1A status copies.
    text = one(
        text,
        '''\tgeneration = (unsigned int)atomic_read(&a52_p414_disk_gen);\n\tbdev = blkdev_get_by_dev(devt, FMODE_WRITE, NULL);\n\tif (IS_ERR(bdev)) {\n\t\tretry = atomic_inc_return(&a52_p414_disk_retry);\n\t\tif (retry <= 240)\n\t\t\tmod_delayed_work(system_unbound_wq, &a52_p414_disk_work,\n\t\t\t\t\t msecs_to_jiffies(250));\n\t\treturn;\n\t}\n''',
        '''\tgeneration = (unsigned int)atomic_read(&a52_p414_disk_gen);\n\tspin_lock_irqsave(&a52_p414_lock, flags);\n\ta52_p416_worker_runs++;\n\ta52_p416_open_rc = -EINPROGRESS;\n\ta52_p416_submit_rc = -EAGAIN;\n\ta52_p416_flush_rc = -EAGAIN;\n\ta52_p416_sync_status_locked();\n\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\n\tbdev = blkdev_get_by_dev(devt, FMODE_WRITE, NULL);\n\tif (IS_ERR(bdev)) {\n\t\tint open_rc = PTR_ERR(bdev);\n\n\t\tretry = atomic_inc_return(&a52_p414_disk_retry);\n\t\tspin_lock_irqsave(&a52_p414_lock, flags);\n\t\ta52_p416_open_rc = open_rc;\n\t\ta52_p416_sync_status_locked();\n\t\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\t\tif (retry <= 240)\n\t\t\tmod_delayed_work(system_unbound_wq, &a52_p414_disk_work,\n\t\t\t\t\t msecs_to_jiffies(250));\n\t\treturn;\n\t}\n\tspin_lock_irqsave(&a52_p414_lock, flags);\n\ta52_p416_open_rc = 0;\n\ta52_p416_sync_status_locked();\n\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n''',
        "worker open diagnostics",
    )

    # Header submission status.
    text = one(
        text,
        '''\trc = a52_p414_submit_page(bdev, a52_p414_bounce, 0U);\n\tif (rc)\n\t\tgoto out;\n''',
        '''\ta52_p416_last_page = 0U;\n\trc = a52_p414_submit_page(bdev, a52_p414_bounce, 0U);\n\tspin_lock_irqsave(&a52_p414_lock, flags);\n\ta52_p416_submit_rc = rc;\n\ta52_p416_sync_status_locked();\n\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\tif (rc)\n\t\tgoto out;\n''',
        "header submit diagnostics",
    )

    # Full-page submission status.
    text = one(
        text,
        '''\t\trc = a52_p414_submit_page(\n\t\t\tbdev, a52_p414_disk_stage + page_index * PAGE_SIZE,\n\t\t\tpage_index);\n\t\tif (rc)\n\t\t\tgoto out;\n\t\ta52_p414_flushed_full_pages = page_index;\n''',
        '''\t\ta52_p416_last_page = page_index;\n\t\trc = a52_p414_submit_page(\n\t\t\tbdev, a52_p414_disk_stage + page_index * PAGE_SIZE,\n\t\t\tpage_index);\n\t\tspin_lock_irqsave(&a52_p414_lock, flags);\n\t\ta52_p416_submit_rc = rc;\n\t\ta52_p416_sync_status_locked();\n\t\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\t\tif (rc)\n\t\t\tgoto out;\n\t\ta52_p414_flushed_full_pages = page_index;\n''',
        "full-page diagnostics",
    )

    # Partial-page submission status.
    text = one(
        text,
        '''\t\trc = a52_p414_submit_page(bdev, a52_p414_bounce, page_index);\n\t\tif (rc)\n\t\t\tgoto out;\n\t}\n\n\trc = blkdev_issue_flush(bdev, GFP_KERNEL);\nout:\n''',
        '''\t\ta52_p416_last_page = page_index;\n\t\trc = a52_p414_submit_page(bdev, a52_p414_bounce, page_index);\n\t\tspin_lock_irqsave(&a52_p414_lock, flags);\n\t\ta52_p416_submit_rc = rc;\n\t\ta52_p416_sync_status_locked();\n\t\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\t\tif (rc)\n\t\t\tgoto out;\n\t}\n\n\trc = blkdev_issue_flush(bdev, GFP_KERNEL);\n\tspin_lock_irqsave(&a52_p414_lock, flags);\n\ta52_p416_flush_rc = rc;\n\ta52_p416_sync_status_locked();\n\tspin_unlock_irqrestore(&a52_p414_lock, flags);\nout:\n''',
        "partial and flush diagnostics",
    )

    # Persist success/failure generation and retries before scheduling again.
    text = one(
        text,
        '''\tif (!rc) {\n\t\tatomic_set(&a52_p414_disk_written_gen, generation);\n\t\tatomic_set(&a52_p414_disk_retry, 0);\n\t\tif ((unsigned int)atomic_read(&a52_p414_disk_gen) != generation)\n''',
        '''\tif (!rc) {\n\t\tatomic_set(&a52_p414_disk_written_gen, generation);\n\t\tatomic_set(&a52_p414_disk_retry, 0);\n\t\tspin_lock_irqsave(&a52_p414_lock, flags);\n\t\ta52_p416_sync_status_locked();\n\t\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\t\tif ((unsigned int)atomic_read(&a52_p414_disk_gen) != generation)\n''',
        "success status",
    )
    text = one(
        text,
        '''\tretry = atomic_inc_return(&a52_p414_disk_retry);\n\tif (retry <= 240)\n\t\tmod_delayed_work(system_unbound_wq, &a52_p414_disk_work,\n\t\t\t\t msecs_to_jiffies(250));\n}\n\nstatic void a52_p414_append_text''',
        '''\tretry = atomic_inc_return(&a52_p414_disk_retry);\n\tspin_lock_irqsave(&a52_p414_lock, flags);\n\ta52_p416_sync_status_locked();\n\tspin_unlock_irqrestore(&a52_p414_lock, flags);\n\tif (retry <= 240)\n\t\tmod_delayed_work(system_unbound_wq, &a52_p414_disk_work,\n\t\t\t\t msecs_to_jiffies(250));\n}\n\nstatic void a52_p414_append_text''',
        "failure status",
    )

    # During the Samsung tier, mirror logical count/header into persistent B1A.
    text = one(
        text,
        '''\t\ta52_p414_update_disk_header_locked();\n\t\tqueue_disk = true;\n''',
        '''\t\ta52_p414_update_disk_header_locked();\n\t\ta52_p414_update_ram_header_locked();\n\t\ta52_p416_sync_status_locked();\n\t\tqueue_disk = true;\n''',
        "persistent logical count",
    )

    # Keep the three status copies non-overlapping at 128-byte spacing.
    text = one(
        text,
        "\tBUILD_BUG_ON(sizeof(struct a52_p414_header) != 64U);\n",
        "\tBUILD_BUG_ON(sizeof(struct a52_p414_header) != 64U);\n"
        "\tBUILD_BUG_ON(sizeof(struct a52_p416_status) != 128U);\n",
        "status size guard",
    )

    # Initialize status but deliberately leave transport unarmed at core init.
    text = one(
        text,
        '''\ta52_p414_state = A52_P414_STATE_DISK;\n\ta52_p414_flushed_full_pages = 0;\n''',
        '''\ta52_p414_state = A52_P414_STATE_DISK;\n\ta52_p414_flushed_full_pages = 0;\n\ta52_p416_disk_armed = 0;\n\ta52_p416_worker_runs = 0;\n\ta52_p416_open_rc = -EAGAIN;\n\ta52_p416_submit_rc = -EAGAIN;\n\ta52_p416_flush_rc = -EAGAIN;\n\ta52_p416_last_page = 0;\n''',
        "status init",
    )
    text = one(
        text,
        '''\tmemcpy_toio(a52_p414_ram, &h, sizeof(h));\n\ta52_p406_persist(a52_p414_ram, sizeof(h));\n\n\tatomic_set(&a52_p414_ready, 1);\n\ta52_p414_queue_disk();\n''',
        '''\tmemcpy_toio(a52_p414_ram, &h, sizeof(h));\n\ta52_p406_persist(a52_p414_ram, sizeof(h));\n\ta52_p416_sync_status_locked();\n\n\tatomic_set(&a52_p414_ready, 1);\n\t/* Phase416: stage now, arm raw block I/O only from late_initcall. */\n''',
        "remove early disk arm",
    )

    # Add a late initcall that starts persistence after device init has run.
    late = r'''

static int __init a52_p416_disk_late_arm(void)
{
	unsigned long flags;

	if (!atomic_read(&a52_p414_ready))
		return 0;

	spin_lock_irqsave(&a52_p414_lock, flags);
	WRITE_ONCE(a52_p416_disk_armed, 1U);
	a52_p416_sync_status_locked();
	spin_unlock_irqrestore(&a52_p414_lock, flags);

	/* Flush everything staged since core_init, even if no later event arrives. */
	a52_p414_queue_disk();
	a52_ackfr_record("P416 DISKARM gen=%u count=%u",
		(unsigned int)atomic_read(&a52_p414_disk_gen),
		a52_p414_disk_count);
	return 0;
}
late_initcall_sync(a52_p416_disk_late_arm);
'''
    text = one(
        text,
        "core_initcall_sync(a52_p414_init);\n",
        "core_initcall_sync(a52_p414_init);\n" + late,
        "late arm",
    )

    # Admit P416 control breadcrumbs through both legacy admission gates.
    text = one(
        text,
        'if (strncmp(fmt, "P415", 4) &&\n    strncmp(fmt, "P414", 4) &&\n',
        'if (strncmp(fmt, "P416", 4) &&\n    strncmp(fmt, "P415", 4) &&\n    strncmp(fmt, "P414", 4) &&\n',
        "first P416 gate",
    )
    text = one(
        text,
        '\tif (!fmt || (\n\t    strncmp(fmt, "P415", 4) &&\n\t    strncmp(fmt, "P414", 4) &&\n',
        '\tif (!fmt || (\n\t    strncmp(fmt, "P416", 4) &&\n\t    strncmp(fmt, "P415", 4) &&\n\t    strncmp(fmt, "P414", 4) &&\n',
        "second P416 gate",
    )

    text += f'\n/* {MARK}: late-armed Samsung transport with persistent status sidecar. */\n'
    return text


def validate(text: str) -> None:
    required = (
        MARK,
        "A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1",
        "A52_PHASE415_MDSS_GDSC_HWCTRL_PARITY_V1",
        "A52_P416_STATUS_MAGIC",
        "A52_P416_STATUS_OFFSET0",
        "BUILD_BUG_ON(sizeof(struct a52_p416_status) != 128U);",
        "a52_p416_sync_status_locked",
        "a52_p414_update_ram_header_locked();",
        "static int __init a52_p416_disk_late_arm(void)",
        "late_initcall_sync(a52_p416_disk_late_arm);",
        "if (READ_ONCE(a52_p416_disk_armed))",
        "WRITE_ONCE(a52_p416_disk_armed, 1U);",
        "a52_p416_open_rc = open_rc;",
        "a52_p416_submit_rc = rc;",
        "a52_p416_flush_rc = rc;",
        'a52_ackfr_record("P416 DISKARM gen=%u count=%u"',
        'strncmp(fmt, "P416", 4)',
    )
    for tok in required:
        if tok not in text:
            raise SystemExit("Phase416 token missing: " + tok)
    if "\tatomic_set(&a52_p414_ready, 1);\n\ta52_p414_queue_disk();" in text:
        raise SystemExit("Phase416 early core-init disk queue remains")
    if text.count('strncmp(fmt, "P416", 4)') < 2:
        raise SystemExit("Phase416 must pass both ACK admission gates")
    if text.count("late_initcall_sync(a52_p416_disk_late_arm);") != 1:
        raise SystemExit("Phase416 late arm count wrong")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    p = ns.root / REC
    if not p.is_file():
        raise SystemExit("Phase416 recorder source missing")
    text = p.read_text(errors="replace")
    if not ns.check_only:
        text = patch(text)
        p.write_text(text)
    validate(p.read_text(errors="replace"))
    print("Phase416 sequential 3 MiB disk persistence v2: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
