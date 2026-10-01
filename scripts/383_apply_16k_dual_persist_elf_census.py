#!/usr/bin/env python3
from pathlib import Path
import sys

MARK = "A52_PHASE383_16K_DUAL_ELF_CENSUS_V1"

RECORDER_C = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52 Phase383 native-16K dual persistent recorder
 *
 * Channel 1: normal printk -> ramoops/pstore.
 * Channel 2: non-wrapping structured records in the last 2 MiB of the
 *            Samsung debug partition (sda8, offset +0x800000).
 *
 * The transport is deliberately Linux-PAGE_SIZE agnostic.  On-disk metadata
 * stays fixed (4 KiB header, 128-byte records), while block I/O uses the
 * actual Linux PAGE_SIZE (16 KiB in the target experiment).
 */

#include <linux/atomic.h>
#include <linux/bio.h>
#include <linux/blkdev.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/export.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/major.h>
#include <linux/mm.h>
#include <linux/sizes.h>
#include <linux/spinlock.h>
#include <linux/stdarg.h>
#include <linux/string.h>
#include <linux/vmalloc.h>
#include <linux/workqueue.h>

#include <linux/a52_p383_dual_recorder.h>

#define A52_P383_DEBUG_DEVT          MKDEV(SCSI_DISK0_MAJOR, 8)
#define A52_P383_DEBUG_OFFSET        0x00800000ULL
#define A52_P383_DISK_BYTES          (2U * SZ_1M)
#define A52_P383_HEADER_BYTES        SZ_4K
#define A52_P383_RECORD_BYTES        128U
#define A52_P383_CAPACITY            ((A52_P383_DISK_BYTES - A52_P383_HEADER_BYTES) / A52_P383_RECORD_BYTES)

#define A52_P383_MAGIC               0x3833334d32434553ULL
#define A52_P383_VERSION             1U
#define A52_P383_PHASE               383U
#define A52_P383_REC_COMMIT          0x38330de5U
#define A52_P383_HDR_COMMIT          0x3833b007U

#define A52_P383_STATUS_MAGIC        0x3833535441545533ULL
#define A52_P383_STATUS_VERSION      1U
#define A52_P383_STATUS_COMMIT       0x3833c0deU
#define A52_P383_STATUS_OFFSET0      0x00000100U
#define A52_P383_STATUS_OFFSET1      0x00000180U
#define A52_P383_STATUS_OFFSET2      0x00000200U

#define A52_P383_STATE_STAGED        1U
#define A52_P383_STATE_ARMED         2U
#define A52_P383_STATE_FULL          3U

struct a52_p383_header {
	u64 magic;
	u32 version;
	u32 phase;
	u64 boot_id;
	u64 global_seq;
	u32 disk_count;
	u32 ram_count;
	u32 disk_capacity;
	u32 ram_capacity;
	u32 record_bytes;
	u32 state;
	u32 dropped;
	u32 commit;
} __packed;

struct a52_p383_record {
	u64 seq;
	u64 ts_ns;
	u64 boot_id;
	u32 phase;
	u16 cpu;
	u16 len;
	char text[88];
	u32 commit;
	u32 reserved;
} __packed;

struct a52_p383_pair {
	u32 value;
	u32 inverse;
} __packed;

struct a52_p383_status {
	u64 magic;
	u64 magic_inverse;
	struct a52_p383_pair version;
	struct a52_p383_pair armed;
	struct a52_p383_pair worker_runs;
	struct a52_p383_pair open_rc;
	struct a52_p383_pair submit_rc;
	struct a52_p383_pair flush_rc;
	struct a52_p383_pair retry_count;
	struct a52_p383_pair disk_gen;
	struct a52_p383_pair written_gen;
	struct a52_p383_pair disk_count;
	struct a52_p383_pair last_page;
	struct a52_p383_pair state;
	struct a52_p383_pair commit;
	u32 reserved0;
	u32 reserved1;
} __packed;

static DEFINE_SPINLOCK(a52_p383_lock);
static u8 *a52_p383_stage;
static u8 *a52_p383_bounce;
static u64 a52_p383_boot_id;
static u64 a52_p383_seq;
static u32 a52_p383_count;
static u32 a52_p383_dropped;
static u32 a52_p383_state;
static u32 a52_p383_worker_runs;
static int a52_p383_open_rc = -EAGAIN;
static int a52_p383_submit_rc = -EAGAIN;
static int a52_p383_flush_rc = -EAGAIN;
static u32 a52_p383_last_page;
static u32 a52_p383_dirty_min;
static u32 a52_p383_dirty_max;
static atomic_t a52_p383_ready = ATOMIC_INIT(0);
static atomic_t a52_p383_armed = ATOMIC_INIT(0);
static atomic_t a52_p383_disk_gen = ATOMIC_INIT(0);
static atomic_t a52_p383_written_gen = ATOMIC_INIT(0);
static atomic_t a52_p383_retry = ATOMIC_INIT(0);

static void a52_p383_disk_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_p383_disk_work, a52_p383_disk_workfn);

static inline struct a52_p383_pair a52_p383_pair(u32 v)
{
	struct a52_p383_pair p = {
		.value = v,
		.inverse = ~v,
	};
	return p;
}

static void a52_p383_mark_dirty_locked(u32 page)
{
	if (a52_p383_dirty_min == ~0U || page < a52_p383_dirty_min)
		a52_p383_dirty_min = page;
	if (page > a52_p383_dirty_max)
		a52_p383_dirty_max = page;
}

static void a52_p383_fill_header(struct a52_p383_header *h)
{
	memset(h, 0, sizeof(*h));
	h->magic = A52_P383_MAGIC;
	h->version = A52_P383_VERSION;
	h->phase = A52_P383_PHASE;
	h->boot_id = a52_p383_boot_id;
	h->global_seq = a52_p383_seq;
	h->disk_count = a52_p383_count;
	h->disk_capacity = A52_P383_CAPACITY;
	h->record_bytes = A52_P383_RECORD_BYTES;
	h->state = a52_p383_state;
	h->dropped = a52_p383_dropped;
	h->commit = A52_P383_HDR_COMMIT;
}

static void a52_p383_sync_header_locked(void)
{
	struct a52_p383_header h;

	if (!a52_p383_stage)
		return;
	a52_p383_fill_header(&h);
	memcpy(a52_p383_stage, &h, sizeof(h));
	a52_p383_mark_dirty_locked(0U);
}

static void a52_p383_fill_status(struct a52_p383_status *s)
{
	memset(s, 0, sizeof(*s));
	s->magic = A52_P383_STATUS_MAGIC;
	s->magic_inverse = ~A52_P383_STATUS_MAGIC;
	s->version = a52_p383_pair(A52_P383_STATUS_VERSION);
	s->armed = a52_p383_pair((u32)atomic_read(&a52_p383_armed));
	s->worker_runs = a52_p383_pair(a52_p383_worker_runs);
	s->open_rc = a52_p383_pair((u32)a52_p383_open_rc);
	s->submit_rc = a52_p383_pair((u32)a52_p383_submit_rc);
	s->flush_rc = a52_p383_pair((u32)a52_p383_flush_rc);
	s->retry_count = a52_p383_pair((u32)atomic_read(&a52_p383_retry));
	s->disk_gen = a52_p383_pair((u32)atomic_read(&a52_p383_disk_gen));
	s->written_gen = a52_p383_pair((u32)atomic_read(&a52_p383_written_gen));
	s->disk_count = a52_p383_pair(a52_p383_count);
	s->last_page = a52_p383_pair(a52_p383_last_page);
	s->state = a52_p383_pair(a52_p383_state);
	s->commit = a52_p383_pair(A52_P383_STATUS_COMMIT);
}

static void a52_p383_sync_status_locked(void)
{
	struct a52_p383_status s;

	if (!a52_p383_stage)
		return;
	a52_p383_fill_status(&s);
	memcpy(a52_p383_stage + A52_P383_STATUS_OFFSET0, &s, sizeof(s));
	memcpy(a52_p383_stage + A52_P383_STATUS_OFFSET1, &s, sizeof(s));
	memcpy(a52_p383_stage + A52_P383_STATUS_OFFSET2, &s, sizeof(s));
	a52_p383_mark_dirty_locked(0U);
}

static int a52_p383_submit_page(struct block_device *bdev, u32 page_index)
{
	struct bio *bio;
	struct page *page;
	unsigned long flags;
	int added;
	int rc;
	u8 *src;

	if (!bdev || !a52_p383_bounce || !a52_p383_stage)
		return -EINVAL;

	if ((u64)page_index * PAGE_SIZE >= A52_P383_DISK_BYTES)
		return -ERANGE;

	src = a52_p383_stage + (u64)page_index * PAGE_SIZE;
	spin_lock_irqsave(&a52_p383_lock, flags);
	memcpy(a52_p383_bounce, src, PAGE_SIZE);
	spin_unlock_irqrestore(&a52_p383_lock, flags);

	page = vmalloc_to_page(a52_p383_bounce);
	if (!page)
		return -EFAULT;

	bio = bio_alloc(GFP_KERNEL, 1);
	if (!bio)
		return -ENOMEM;

	bio_set_dev(bio, bdev);
	bio->bi_iter.bi_sector =
		(sector_t)((A52_P383_DEBUG_OFFSET +
			    (u64)page_index * PAGE_SIZE) >> 9);
	bio_set_op_attrs(bio, REQ_OP_WRITE, REQ_SYNC | REQ_FUA);
	added = bio_add_page(bio, page, PAGE_SIZE, 0);
	if (added != PAGE_SIZE) {
		bio_put(bio);
		return -EIO;
	}

	rc = submit_bio_wait(bio);
	bio_put(bio);
	return rc;
}

static void a52_p383_queue_disk(void)
{
	if (!atomic_read(&a52_p383_armed))
		return;
	atomic_inc(&a52_p383_disk_gen);
	mod_delayed_work(system_unbound_wq, &a52_p383_disk_work, 0);
}

static void a52_p383_disk_workfn(struct work_struct *work)
{
	struct block_device *bdev;
	unsigned long flags;
	u32 first, last, page;
	u32 generation;
	int rc = 0;
	int retry;

	(void)work;
	if (!atomic_read(&a52_p383_armed) || !READ_ONCE(a52_p383_stage))
		return;

	generation = (u32)atomic_read(&a52_p383_disk_gen);

	spin_lock_irqsave(&a52_p383_lock, flags);
	a52_p383_worker_runs++;
	a52_p383_open_rc = -EINPROGRESS;
	a52_p383_submit_rc = -EAGAIN;
	a52_p383_flush_rc = -EAGAIN;
	first = a52_p383_dirty_min;
	last = a52_p383_dirty_max;
	a52_p383_sync_status_locked();
	spin_unlock_irqrestore(&a52_p383_lock, flags);

	bdev = blkdev_get_by_dev(A52_P383_DEBUG_DEVT, FMODE_WRITE, NULL);
	if (IS_ERR(bdev)) {
		rc = PTR_ERR(bdev);
		spin_lock_irqsave(&a52_p383_lock, flags);
		a52_p383_open_rc = rc;
		a52_p383_sync_status_locked();
		spin_unlock_irqrestore(&a52_p383_lock, flags);
		goto retry;
	}

	spin_lock_irqsave(&a52_p383_lock, flags);
	a52_p383_open_rc = 0;
	a52_p383_sync_status_locked();
	spin_unlock_irqrestore(&a52_p383_lock, flags);

	if (first == ~0U)
		first = last = 0U;

	for (page = first; page <= last; page++) {
		a52_p383_last_page = page;
		rc = a52_p383_submit_page(bdev, page);
		spin_lock_irqsave(&a52_p383_lock, flags);
		a52_p383_submit_rc = rc;
		a52_p383_sync_status_locked();
		spin_unlock_irqrestore(&a52_p383_lock, flags);
		if (rc)
			break;
	}

	if (!rc) {
		rc = blkdev_issue_flush(bdev, GFP_KERNEL, NULL);
		spin_lock_irqsave(&a52_p383_lock, flags);
		a52_p383_flush_rc = rc;
		a52_p383_sync_status_locked();
		spin_unlock_irqrestore(&a52_p383_lock, flags);
	}
	blkdev_put(bdev, FMODE_WRITE);

	if (!rc) {
		atomic_set(&a52_p383_written_gen, generation);
		atomic_set(&a52_p383_retry, 0);

		spin_lock_irqsave(&a52_p383_lock, flags);
		if ((u32)atomic_read(&a52_p383_disk_gen) == generation) {
			a52_p383_dirty_min = ~0U;
			a52_p383_dirty_max = 0U;
		}
		a52_p383_sync_status_locked();
		spin_unlock_irqrestore(&a52_p383_lock, flags);

		/*
		 * Persist the final transport status/header page after written_gen
		 * and final rc fields change.
		 */
		bdev = blkdev_get_by_dev(A52_P383_DEBUG_DEVT, FMODE_WRITE, NULL);
		if (!IS_ERR(bdev)) {
			if (!a52_p383_submit_page(bdev, 0U))
				blkdev_issue_flush(bdev, GFP_KERNEL, NULL);
			blkdev_put(bdev, FMODE_WRITE);
		}

		if ((u32)atomic_read(&a52_p383_disk_gen) != generation)
			mod_delayed_work(system_unbound_wq,
				&a52_p383_disk_work, 0);
		return;
	}

retry:
	retry = atomic_inc_return(&a52_p383_retry);
	spin_lock_irqsave(&a52_p383_lock, flags);
	a52_p383_sync_status_locked();
	spin_unlock_irqrestore(&a52_p383_lock, flags);
	if (retry <= 240)
		mod_delayed_work(system_unbound_wq, &a52_p383_disk_work,
				 msecs_to_jiffies(250));
}

static void __a52_p383_append(const char *message)
{
	struct a52_p383_record rec;
	unsigned long flags;
	u32 off;
	u32 page;
	size_t len;

	if (!message || !atomic_read(&a52_p383_ready))
		return;

	spin_lock_irqsave(&a52_p383_lock, flags);

	if (a52_p383_count >= A52_P383_CAPACITY) {
		a52_p383_dropped++;
		a52_p383_state = A52_P383_STATE_FULL;
		a52_p383_sync_header_locked();
		a52_p383_sync_status_locked();
		spin_unlock_irqrestore(&a52_p383_lock, flags);
		return;
	}

	memset(&rec, 0, sizeof(rec));
	rec.seq = ++a52_p383_seq;
	rec.ts_ns = ktime_get_boot_ns();
	rec.boot_id = a52_p383_boot_id;
	rec.phase = A52_P383_PHASE;
	rec.cpu = (u16)raw_smp_processor_id();
	len = strnlen(message, sizeof(rec.text) - 1U);
	rec.len = (u16)len;
	memcpy(rec.text, message, len);
	rec.text[len] = '\0';
	rec.commit = A52_P383_REC_COMMIT;

	off = A52_P383_HEADER_BYTES +
	      a52_p383_count * A52_P383_RECORD_BYTES;
	memcpy(a52_p383_stage + off, &rec, sizeof(rec));
	page = off / PAGE_SIZE;
	a52_p383_mark_dirty_locked(page);
	a52_p383_count++;
	a52_p383_sync_header_locked();
	a52_p383_sync_status_locked();

	spin_unlock_irqrestore(&a52_p383_lock, flags);
	a52_p383_queue_disk();
}

void a52_p383_record(const char *fmt, ...)
{
	va_list ap;
	char message[88];

	if (!fmt)
		return;

	va_start(ap, fmt);
	vscnprintf(message, sizeof(message), fmt, ap);
	va_end(ap);

	pr_emerg("A52 P383 %s\n", message);
	__a52_p383_append(message);
}
EXPORT_SYMBOL_GPL(a52_p383_record);

void a52_p383_record_critical(const char *fmt, ...)
{
	va_list ap;
	char message[88];

	if (!fmt)
		return;

	va_start(ap, fmt);
	vscnprintf(message, sizeof(message), fmt, ap);
	va_end(ap);

	pr_emerg("A52 P383 %s\n", message);
	__a52_p383_append(message);

	if (atomic_read(&a52_p383_armed))
		flush_delayed_work(&a52_p383_disk_work);
}
EXPORT_SYMBOL_GPL(a52_p383_record_critical);

static int __init a52_p383_init(void)
{
	unsigned long flags;

	BUILD_BUG_ON(sizeof(struct a52_p383_record) != A52_P383_RECORD_BYTES);
	BUILD_BUG_ON(sizeof(struct a52_p383_status) != 128U);
	BUILD_BUG_ON((A52_P383_HEADER_BYTES % 512U) != 0U);
	BUILD_BUG_ON((A52_P383_DISK_BYTES % PAGE_SIZE) != 0U);

	a52_p383_stage = vzalloc(A52_P383_DISK_BYTES);
	a52_p383_bounce = vzalloc(PAGE_SIZE);
	if (!a52_p383_stage || !a52_p383_bounce)
		return 0;

	a52_p383_boot_id = ktime_get_real_ns();
	a52_p383_seq = 0;
	a52_p383_count = 0;
	a52_p383_dropped = 0;
	a52_p383_state = A52_P383_STATE_STAGED;
	a52_p383_dirty_min = ~0U;
	a52_p383_dirty_max = 0U;

	spin_lock_irqsave(&a52_p383_lock, flags);
	a52_p383_sync_header_locked();
	a52_p383_sync_status_locked();
	spin_unlock_irqrestore(&a52_p383_lock, flags);

	atomic_set(&a52_p383_ready, 1);
	a52_p383_record("BOOT id=%llu page=%lu hdr=%u cap=%u",
		(unsigned long long)a52_p383_boot_id,
		(unsigned long)PAGE_SIZE,
		A52_P383_HEADER_BYTES, A52_P383_CAPACITY);
	return 0;
}
core_initcall_sync(a52_p383_init);

static int __init a52_p383_late_arm(void)
{
	unsigned long flags;

	if (!atomic_read(&a52_p383_ready))
		return 0;

	atomic_set(&a52_p383_armed, 1);
	spin_lock_irqsave(&a52_p383_lock, flags);
	a52_p383_state = A52_P383_STATE_ARMED;
	a52_p383_sync_header_locked();
	a52_p383_sync_status_locked();
	spin_unlock_irqrestore(&a52_p383_lock, flags);

	a52_p383_record("DISKARM dev=sda8 off=0x%llx page=%lu",
		(unsigned long long)A52_P383_DEBUG_OFFSET,
		(unsigned long)PAGE_SIZE);
	flush_delayed_work(&a52_p383_disk_work);
	return 0;
}
late_initcall_sync(a52_p383_late_arm);
'''

RECORDER_H = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_A52_P383_DUAL_RECORDER_H
#define _LINUX_A52_P383_DUAL_RECORDER_H

void a52_p383_record(const char *fmt, ...);
void a52_p383_record_critical(const char *fmt, ...);

#endif
'''

DECODER = r'''#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import struct
from pathlib import Path

DEBUG_OFFSET = 0x800000
DISK_BYTES = 2 * 1024 * 1024
HEADER_BYTES = 4096
RECORD_BYTES = 128
CAPACITY = (DISK_BYTES - HEADER_BYTES) // RECORD_BYTES

MAGIC = 0x3833334D32434553
VERSION = 1
PHASE = 383
REC_COMMIT = 0x38330DE5
HDR_COMMIT = 0x3833B007

STATUS_MAGIC = 0x3833535441545533
STATUS_VERSION = 1
STATUS_COMMIT = 0x3833C0DE
STATUS_OFFSETS = (0x100, 0x180, 0x200)
STATUS_BYTES = 128

HEADER = struct.Struct("<QIIQQIIIIIIII")
RECORD = struct.Struct("<QQQIHH88sII")
STATUS = struct.Struct("<QQ" + "II" * 13 + "II")

def slice_debug(data: bytes) -> bytes:
    if len(data) == DISK_BYTES:
        return data
    if len(data) >= DEBUG_OFFSET + DISK_BYTES:
        return data[DEBUG_OFFSET:DEBUG_OFFSET + DISK_BYTES]
    raise SystemExit(f"debug input too small: {len(data)}")

def inv_ok(v: int, inv: int, bits: int = 32) -> bool:
    mask = (1 << bits) - 1
    return ((v ^ inv) & mask) == mask

def majority3(a: bytes, b: bytes, c: bytes) -> bytes:
    return bytes(((x & y) | (x & z) | (y & z)) for x, y, z in zip(a, b, c))

def parse_header(data: bytes) -> dict:
    vals = HEADER.unpack_from(data, 0)
    keys = ("magic","version","phase","boot_id","global_seq","disk_count","ram_count",
            "disk_capacity","ram_capacity","record_bytes","state","dropped","commit")
    out = dict(zip(keys, vals))
    out["valid"] = bool(
        out["magic"] == MAGIC and out["version"] == VERSION and
        out["phase"] == PHASE and out["disk_capacity"] == CAPACITY and
        out["record_bytes"] == RECORD_BYTES and out["commit"] == HDR_COMMIT
    )
    return out

def parse_status(raw: bytes) -> dict:
    v = STATUS.unpack(raw)
    magic, magic_inv = v[:2]
    pairs = list(zip(v[2:28:2], v[3:28:2]))
    names = ("version","armed","worker_runs","open_rc_u32","submit_rc_u32","flush_rc_u32",
             "retry_count","disk_gen","written_gen","disk_count","last_page","state","commit")
    out = {"magic": magic, "magic_inv": magic_inv}
    valid = {}
    for name, (x, xi) in zip(names, pairs):
        out[name] = x
        out[name+"_inv"] = xi
        valid[name] = inv_ok(x, xi)
    def s32(x): return x - (1 << 32) if x & 0x80000000 else x
    out["open_rc"] = s32(out["open_rc_u32"])
    out["submit_rc"] = s32(out["submit_rc_u32"])
    out["flush_rc"] = s32(out["flush_rc_u32"])
    out["pair_valid"] = valid
    out["valid_pair_count"] = sum(valid.values()) + int(inv_ok(magic, magic_inv, 64))
    out["valid"] = bool(
        magic == STATUS_MAGIC and inv_ok(magic, magic_inv, 64) and
        out["version"] == STATUS_VERSION and valid["version"] and
        out["commit"] == STATUS_COMMIT and valid["commit"]
    )
    return out

def recover_status(data: bytes) -> dict:
    copies = [data[o:o+STATUS_BYTES] for o in STATUS_OFFSETS]
    parsed = [parse_status(x) for x in copies]
    fused = majority3(*copies)
    return {"copies": parsed, "majority": parse_status(fused)}

def parse_records(data: bytes, count: int, boot_id: int) -> list[dict]:
    out = []
    for i in range(min(max(count, 0), CAPACITY)):
        off = HEADER_BYTES + i * RECORD_BYTES
        seq, ts, rec_boot, phase, cpu, length, raw, commit, reserved = RECORD.unpack_from(data, off)
        if commit != REC_COMMIT or phase != PHASE or rec_boot != boot_id:
            continue
        length = min(int(length), len(raw))
        text = raw[:length].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        out.append({"slot": i, "seq": seq, "ts_ns": ts, "cpu": cpu, "text": text})
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", type=Path, required=True)
    ap.add_argument("--json", type=Path)
    ns = ap.parse_args()

    data = slice_debug(ns.debug.read_bytes())
    hdr = parse_header(data)
    status = recover_status(data)
    records = parse_records(data, int(hdr["disk_count"]), int(hdr["boot_id"])) if hdr["valid"] else []
    result = {"header": hdr, "status": status, "records": records}

    st = status["majority"]
    print(f'Phase383 header_valid={hdr["valid"]} boot_id={hdr["boot_id"]} count={hdr["disk_count"]} records={len(records)}')
    print('status majority valid=%s pairs=%u/14 armed=%u runs=%u open_rc=%d submit_rc=%d flush_rc=%d retries=%u gen=%u written=%u count=%u page=%u state=%u' % (
        st["valid"], st["valid_pair_count"], st["armed"], st["worker_runs"],
        st["open_rc"], st["submit_rc"], st["flush_rc"], st["retry_count"],
        st["disk_gen"], st["written_gen"], st["disk_count"], st["last_page"], st["state"]))
    for r in records:
        print(f'{r["seq"]:06d} {r["ts_ns"]/1e9:12.6f}s cpu={r["cpu"]} {r["text"]}')

    if ns.json:
        ns.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if hdr["valid"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
'''

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    elf = root / "fs/binfmt_elf.c"
    makefile = root / "kernel/Makefile"
    rec = root / "kernel/a52_p383_dual_recorder.c"
    hdr = root / "include/linux/a52_p383_dual_recorder.h"

    for p in (elf, makefile):
        if not p.is_file():
            raise SystemExit(f"missing source file: {p}")

    rec.write_text(RECORDER_C)
    hdr.write_text(RECORDER_H)

    m = makefile.read_text()
    line = "obj-y += a52_p383_dual_recorder.o\n"
    if line not in m:
        makefile.write_text(m + "\n# " + MARK + "\n" + line)

    replace_once(
        elf,
        "#include <linux/sched/coredump.h>\n",
        "#include <linux/sched/coredump.h>\n#include <linux/a52_p383_dual_recorder.h>\n",
        "recorder include",
    )

    replace_once(
        elf,
        """\t/* Now we do a little grungy work by mmapping the ELF image into
\t   the correct location in memory. */
\tfor(i = 0, elf_ppnt = elf_phdata;
""",
        """\t/* A52 Phase383: persist the PID1 ELF geometry before the first map. */
\tif (task_pid_nr(current) == 1) {
\t\ta52_p383_record("ELFH file=%s type=%u phnum=%u align=%lu",
\t\t\tbprm->filename ? bprm->filename : "<null>",
\t\t\t(unsigned int)loc->elf_ex.e_type,
\t\t\t(unsigned int)loc->elf_ex.e_phnum,
\t\t\t(unsigned long)ELF_MIN_ALIGN);
\t}

\t/* Now we do a little grungy work by mmapping the ELF image into
\t   the correct location in memory. */
\tfor(i = 0, elf_ppnt = elf_phdata;
""",
        "ELF header census",
    )

    replace_once(
        elf,
        """\t\tif (elf_ppnt->p_type != PT_LOAD)
\t\t\tcontinue;

\t\tif (unlikely (elf_brk > elf_bss)) {
""",
        """\t\tif (elf_ppnt->p_type != PT_LOAD)
\t\t\tcontinue;

\t\tif (task_pid_nr(current) == 1) {
\t\t\ta52_p383_record("LOAD0 i=%d v=%llx off=%llx fl=%x",
\t\t\t\ti,
\t\t\t\t(unsigned long long)elf_ppnt->p_vaddr,
\t\t\t\t(unsigned long long)elf_ppnt->p_offset,
\t\t\t\t(unsigned int)elf_ppnt->p_flags);
\t\t\ta52_p383_record("LOAD1 i=%d fs=%llx ms=%llx al=%llx",
\t\t\t\ti,
\t\t\t\t(unsigned long long)elf_ppnt->p_filesz,
\t\t\t\t(unsigned long long)elf_ppnt->p_memsz,
\t\t\t\t(unsigned long long)elf_ppnt->p_align);
\t\t}

\t\tif (unlikely (elf_brk > elf_bss)) {
""",
        "PT_LOAD census",
    )

    replace_once(
        elf,
        """\t\terror = elf_map(bprm->file, load_bias + vaddr, elf_ppnt,
\t\t\t\telf_prot, elf_flags, total_size);
\t\tif (BAD_ADDR(error)) {
\t\t\tretval = IS_ERR((void *)error) ?
\t\t\t\tPTR_ERR((void*)error) : -EINVAL;
\t\t\tgoto out_free_dentry;
\t\t}
""",
        """\t\terror = elf_map(bprm->file, load_bias + vaddr, elf_ppnt,
\t\t\t\telf_prot, elf_flags, total_size);
\t\tif (BAD_ADDR(error)) {
\t\t\tretval = IS_ERR((void *)error) ?
\t\t\t\tPTR_ERR((void*)error) : -EINVAL;
\t\t\tif (task_pid_nr(current) == 1) {
\t\t\t\ta52_p383_record("MAPF0 i=%d rc=%d addr=%lx bias=%lx",
\t\t\t\t\ti, retval, load_bias + vaddr, load_bias);
\t\t\t\ta52_p383_record("MAPF1 i=%d v=%llx off=%llx fs=%llx",
\t\t\t\t\ti,
\t\t\t\t\t(unsigned long long)elf_ppnt->p_vaddr,
\t\t\t\t\t(unsigned long long)elf_ppnt->p_offset,
\t\t\t\t\t(unsigned long long)elf_ppnt->p_filesz);
\t\t\t\ta52_p383_record_critical("MAPF2 i=%d ms=%llx al=%llx prot=%x flags=%x",
\t\t\t\t\ti,
\t\t\t\t\t(unsigned long long)elf_ppnt->p_memsz,
\t\t\t\t\t(unsigned long long)elf_ppnt->p_align,
\t\t\t\t\telf_prot, elf_flags);
\t\t\t}
\t\t\tgoto out_free_dentry;
\t\t}
""",
        "critical map failure persistence",
    )

    for p in (rec, hdr, elf, makefile):
        txt = p.read_text()
        if p == rec and MARK not in txt:
            raise SystemExit("Phase383 recorder marker missing")
        if p == elf and "A52 Phase383" not in txt:
            raise SystemExit("Phase383 ELF census missing")
        if p == makefile and "a52_p383_dual_recorder.o" not in txt:
            raise SystemExit("Phase383 Makefile hook missing")

    decoder = root.parent.parent / "scripts" / "decode_phase383_16k_dual.py"
    decoder.parent.mkdir(parents=True, exist_ok=True)
    decoder.write_text(DECODER)

    print("A52 Phase383 native-16K dual persistent ELF census applied successfully")

if __name__ == "__main__":
    main()
