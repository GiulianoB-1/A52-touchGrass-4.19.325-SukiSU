#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

FDR_HEADER = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_TG_FDR_H
#define _LINUX_TG_FDR_H

#include <linux/types.h>

enum tg_fdr_subsystem {
	TG_FDR_SUBSYS_CORE = 1,
	TG_FDR_SUBSYS_DRIVER = 2,
	TG_FDR_SUBSYS_IOMMU = 3,
	TG_FDR_SUBSYS_GPU = 4,
	TG_FDR_SUBSYS_DISPLAY = 5,
	TG_FDR_SUBSYS_STORAGE = 6,
	TG_FDR_SUBSYS_ANDROID = 7,
	TG_FDR_SUBSYS_POWER = 8,
	TG_FDR_SUBSYS_USB = 9,
	TG_FDR_SUBSYS_NET = 10,
	TG_FDR_SUBSYS_AUDIO = 11,
	TG_FDR_SUBSYS_CAMERA = 12,
	TG_FDR_SUBSYS_INPUT = 13,
	TG_FDR_SUBSYS_SENSOR = 14,
	TG_FDR_SUBSYS_THERMAL = 15,
	TG_FDR_SUBSYS_SECURITY = 16,
	TG_FDR_SUBSYS_PM = 17,
	TG_FDR_SUBSYS_FIRMWARE = 18,
	TG_FDR_SUBSYS_MEMORY = 19,
	TG_FDR_SUBSYS_IRQ = 20,
	TG_FDR_SUBSYS_TEST = 21,
	TG_FDR_SUBSYS_META = 22,
};

enum tg_fdr_object_type {
	TG_FDR_OBJ_NONE = 0,
	TG_FDR_OBJ_DEVICE = 1,
	TG_FDR_OBJ_DRIVER = 2,
	TG_FDR_OBJ_IOMMU_DOMAIN = 3,
	TG_FDR_OBJ_IOMMU_GROUP = 4,
	TG_FDR_OBJ_SMMU = 5,
	TG_FDR_OBJ_CONTEXT_BANK = 6,
	TG_FDR_OBJ_PAGETABLE = 7,
	TG_FDR_OBJ_DMABUF = 8,
	TG_FDR_OBJ_FENCE = 9,
	TG_FDR_OBJ_CLOCK = 10,
	TG_FDR_OBJ_REGULATOR = 11,
	TG_FDR_OBJ_GENPD = 12,
	TG_FDR_OBJ_IRQ = 13,
	TG_FDR_OBJ_FILE = 14,
	TG_FDR_OBJ_PROCESS = 15,
	TG_FDR_OBJ_OTHER = 63,
};

#define TG_FDR_FLAG_CRITICAL	(1U << 0)
#define TG_FDR_FLAG_MARKER	(1U << 1)
#define TG_FDR_FLAG_OBJECT_NEW	(1U << 2)

u32 tg_fdr_hash_tag(const char *tag);
u32 tg_fdr_object_id(u16 type, const void *ptr);
void tg_fdr_emit(u16 subsystem, u32 event, s32 rc, u32 object_id,
		 u64 a, u64 b, u64 c, u64 d, u32 flags);
void tg_fdr_emit_tag(u16 subsystem, const char *tag, s32 rc, u32 object_id,
		     u64 a, u64 b, u64 c, u64 d, u32 flags);

#define TG_FDR(_subsys, _event, _rc, _obj, _a, _b, _c, _d, _flags) \
	tg_fdr_emit((_subsys), (_event), (_rc), (_obj), \
		    (u64)(_a), (u64)(_b), (u64)(_c), (u64)(_d), (_flags))

#define TG_FDR_TAG(_subsys, _tag, _rc, _obj, _a, _b, _c, _d, _flags) \
	tg_fdr_emit_tag((_subsys), (_tag), (_rc), (_obj), \
			(u64)(_a), (u64)(_b), (u64)(_c), (u64)(_d), (_flags))

#endif
'''

FDR_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
#include <linux/atomic.h>
#include <linux/capability.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/miscdevice.h>
#include <linux/proc_fs.h>
#include <linux/sched.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/smp.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/tg_fdr.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#define TG_FDR_VERSION			1U
#define TG_FDR_BANK_COUNT		8U
#define TG_FDR_RECORDS_PER_BANK	32768U
#define TG_FDR_COMMIT			0xA52FU
#define TG_FDR_OBJECT_SLOTS		8192U
#define TG_FDR_MAX_OBJECT_TYPE		64U
#define TG_FDR_MARKER_BYTES		32U
#define TG_FDR_WAKE_GRANULARITY	128U

struct tg_fdr_record {
	u64 seq;
	u64 bank_seq;
	u64 ns;
	u64 object_id;
	u64 a;
	u64 b;
	u64 c;
	u64 d;
	s32 rc;
	u32 pid;
	u32 tid;
	u32 flags;
	u32 session;
	u32 event;
	u16 cpu;
	u16 subsystem;
	u16 bank;
	u16 commit;
} __packed;

struct tg_fdr_stream_header {
	char magic[8];
	u16 version;
	u16 header_size;
	u16 record_size;
	u16 bank_count;
	u32 session;
	u32 capacity_per_bank;
	u64 open_ns;
	u64 first_global;
	u64 current_global;
	u64 reader_lost;
	u8 reserved[72];
} __packed;

struct tg_fdr_bank {
	atomic64_t local_seq;
	struct tg_fdr_record records[TG_FDR_RECORDS_PER_BANK];
};

struct tg_fdr_object_slot {
	unsigned long ptr;
	u32 id;
	u16 type;
	u16 reserved;
};

struct tg_fdr_reader {
	u64 next_bank_seq[TG_FDR_BANK_COUNT];
	u64 lost;
	u64 seen_global;
	bool header_done;
};

static struct tg_fdr_bank tg_fdr_banks[TG_FDR_BANK_COUNT];
static struct tg_fdr_object_slot tg_fdr_objects[TG_FDR_OBJECT_SLOTS];
static atomic_t tg_fdr_object_next[TG_FDR_MAX_OBJECT_TYPE];
static DEFINE_SPINLOCK(tg_fdr_object_lock);
static atomic64_t tg_fdr_global_seq = ATOMIC64_INIT(0);
static atomic64_t tg_fdr_reader_lost = ATOMIC64_INIT(0);
static atomic_t tg_fdr_session = ATOMIC_INIT(0);
static DECLARE_WAIT_QUEUE_HEAD(tg_fdr_waitq);

static u32 tg_fdr_session_id(void)
{
	u32 id = (u32)atomic_read(&tg_fdr_session);

	if (likely(id))
		return id;

	{
		u64 now = ktime_get_ns();
		u32 candidate = (u32)now ^ (u32)(now >> 32) ^
				((u32)task_pid_nr(current) << 16) ^ 0xA52F4D52U;
		if (!candidate)
			candidate = 1;
		atomic_cmpxchg(&tg_fdr_session, 0, candidate);
	}
	return (u32)atomic_read(&tg_fdr_session);
}

u32 tg_fdr_hash_tag(const char *tag)
{
	u32 h = 2166136261U;
	const unsigned char *p = (const unsigned char *)tag;

	if (!p)
		return 0;
	while (*p) {
		h ^= *p++;
		h *= 16777619U;
	}
	return h;
}

static u16 tg_fdr_bank_for_subsystem(u16 subsystem)
{
	switch (subsystem) {
	case TG_FDR_SUBSYS_CORE:
	case TG_FDR_SUBSYS_TEST:
	case TG_FDR_SUBSYS_META:
		return 0;
	case TG_FDR_SUBSYS_DRIVER:
	case TG_FDR_SUBSYS_FIRMWARE:
		return 1;
	case TG_FDR_SUBSYS_IOMMU:
	case TG_FDR_SUBSYS_MEMORY:
		return 2;
	case TG_FDR_SUBSYS_GPU:
		return 3;
	case TG_FDR_SUBSYS_DISPLAY:
	case TG_FDR_SUBSYS_CAMERA:
		return 4;
	case TG_FDR_SUBSYS_STORAGE:
	case TG_FDR_SUBSYS_USB:
	case TG_FDR_SUBSYS_NET:
		return 5;
	case TG_FDR_SUBSYS_ANDROID:
	case TG_FDR_SUBSYS_SECURITY:
	case TG_FDR_SUBSYS_AUDIO:
	case TG_FDR_SUBSYS_INPUT:
	case TG_FDR_SUBSYS_SENSOR:
		return 6;
	case TG_FDR_SUBSYS_POWER:
	case TG_FDR_SUBSYS_PM:
	case TG_FDR_SUBSYS_THERMAL:
	case TG_FDR_SUBSYS_IRQ:
	default:
		return 7;
	}
}

void tg_fdr_emit(u16 subsystem, u32 event, s32 rc, u32 object_id,
		 u64 a, u64 b, u64 c, u64 d, u32 flags)
{
	struct tg_fdr_bank *bank;
	struct tg_fdr_record *r;
	u64 global_seq, local_seq;
	u16 bank_id;

	bank_id = tg_fdr_bank_for_subsystem(subsystem);
	bank = &tg_fdr_banks[bank_id];
	global_seq = (u64)atomic64_inc_return(&tg_fdr_global_seq);
	local_seq = (u64)atomic64_inc_return(&bank->local_seq);
	r = &bank->records[(local_seq - 1) & (TG_FDR_RECORDS_PER_BANK - 1)];

	WRITE_ONCE(r->commit, 0);
	smp_wmb();
	r->bank_seq = local_seq;
	r->ns = ktime_get_ns();
	r->object_id = object_id;
	r->a = a;
	r->b = b;
	r->c = c;
	r->d = d;
	r->rc = rc;
	r->pid = (u32)task_tgid_nr(current);
	r->tid = (u32)task_pid_nr(current);
	r->flags = flags;
	r->session = tg_fdr_session_id();
	r->event = event;
	r->cpu = (u16)raw_smp_processor_id();
	r->subsystem = subsystem;
	r->bank = bank_id;
	r->seq = global_seq;
	smp_wmb();
	WRITE_ONCE(r->commit, TG_FDR_COMMIT);

	if ((flags & TG_FDR_FLAG_CRITICAL) ||
	    !(global_seq & (TG_FDR_WAKE_GRANULARITY - 1)))
		wake_up_interruptible(&tg_fdr_waitq);
}

void tg_fdr_emit_tag(u16 subsystem, const char *tag, s32 rc, u32 object_id,
		     u64 a, u64 b, u64 c, u64 d, u32 flags)
{
	tg_fdr_emit(subsystem, tg_fdr_hash_tag(tag), rc, object_id,
		    a, b, c, d, flags);
}

u32 tg_fdr_object_id(u16 type, const void *ptr)
{
	unsigned long key = (unsigned long)ptr;
	unsigned long irqflags;
	u32 start, i, id = 0;
	bool created = false;

	if (!key || !type || type >= TG_FDR_MAX_OBJECT_TYPE)
		return 0;
	start = (u32)((key >> 4) ^ (key >> 17) ^ (key >> 31));
	start &= (TG_FDR_OBJECT_SLOTS - 1);

	spin_lock_irqsave(&tg_fdr_object_lock, irqflags);
	for (i = 0; i < TG_FDR_OBJECT_SLOTS; i++) {
		struct tg_fdr_object_slot *slot =
			&tg_fdr_objects[(start + i) & (TG_FDR_OBJECT_SLOTS - 1)];
		if (slot->ptr == key && slot->type == type) {
			id = slot->id;
			break;
		}
		if (!slot->ptr) {
			u32 n = (u32)atomic_inc_return(&tg_fdr_object_next[type]);
			id = ((u32)type << 24) | (n & 0x00FFFFFFU);
			slot->type = type;
			slot->id = id;
			smp_wmb();
			slot->ptr = key;
			created = true;
			break;
		}
	}
	spin_unlock_irqrestore(&tg_fdr_object_lock, irqflags);

	if (created)
		tg_fdr_emit(TG_FDR_SUBSYS_META, 0x4F424A01U, 0, id,
			    (u64)key, type, 0, 0, TG_FDR_FLAG_OBJECT_NEW);
	return id;
}

static bool tg_fdr_reader_candidate(struct tg_fdr_reader *rd, u16 bank_id,
				    struct tg_fdr_record *out)
{
	struct tg_fdr_bank *bank = &tg_fdr_banks[bank_id];
	u64 write = (u64)atomic64_read(&bank->local_seq);
	u64 next = rd->next_bank_seq[bank_id];
	u64 oldest;
	struct tg_fdr_record *src;
	u16 commit_before, commit_after;
	u64 seq_before, bank_seq_before;

	if (!next)
		next = 1;
	if (!write || next > write)
		return false;
	oldest = write > TG_FDR_RECORDS_PER_BANK ?
		 write - TG_FDR_RECORDS_PER_BANK + 1 : 1;
	if (next < oldest) {
		u64 lost = oldest - next;
		rd->lost += lost;
		atomic64_add((s64)lost, &tg_fdr_reader_lost);
		next = oldest;
		rd->next_bank_seq[bank_id] = next;
	}

	src = &bank->records[(next - 1) & (TG_FDR_RECORDS_PER_BANK - 1)];
	commit_before = READ_ONCE(src->commit);
	bank_seq_before = READ_ONCE(src->bank_seq);
	seq_before = READ_ONCE(src->seq);
	if (commit_before != TG_FDR_COMMIT || bank_seq_before != next || !seq_before)
		return false;
	smp_rmb();
	memcpy(out, src, sizeof(*out));
	smp_rmb();
	commit_after = READ_ONCE(src->commit);
	if (commit_after != TG_FDR_COMMIT ||
	    READ_ONCE(src->bank_seq) != bank_seq_before ||
	    READ_ONCE(src->seq) != seq_before)
		return false;
	return true;
}

static bool tg_fdr_reader_next(struct tg_fdr_reader *rd,
			       struct tg_fdr_record *out)
{
	struct tg_fdr_record candidate, best;
	u16 best_bank = 0;
	u16 i;
	bool have = false;

	for (i = 0; i < TG_FDR_BANK_COUNT; i++) {
		if (!tg_fdr_reader_candidate(rd, i, &candidate))
			continue;
		if (!have || candidate.seq < best.seq) {
			best = candidate;
			best_bank = i;
			have = true;
		}
	}
	if (!have)
		return false;
	*out = best;
	rd->next_bank_seq[best_bank] = best.bank_seq + 1;
	rd->seen_global = best.seq;
	return true;
}

static int tg_fdr_open(struct inode *inode, struct file *file)
{
	struct tg_fdr_reader *rd;
	u16 i;

	rd = kzalloc(sizeof(*rd), GFP_KERNEL);
	if (!rd)
		return -ENOMEM;
	for (i = 0; i < TG_FDR_BANK_COUNT; i++) {
		u64 write = (u64)atomic64_read(&tg_fdr_banks[i].local_seq);
		rd->next_bank_seq[i] = write > TG_FDR_RECORDS_PER_BANK ?
			write - TG_FDR_RECORDS_PER_BANK + 1 : 1;
	}
	rd->seen_global = (u64)atomic64_read(&tg_fdr_global_seq);
	file->private_data = rd;
	return 0;
}

static int tg_fdr_release(struct inode *inode, struct file *file)
{
	kfree(file->private_data);
	return 0;
}

static ssize_t tg_fdr_read(struct file *file, char __user *buf,
			   size_t len, loff_t *ppos)
{
	struct tg_fdr_reader *rd = file->private_data;
	size_t done = 0;
	int ret;

	if (!rd)
		return -EINVAL;
	if (!rd->header_done) {
		struct tg_fdr_stream_header h;
		u64 first = ~0ULL;
		u16 i;
		if (len < sizeof(h))
			return -EINVAL;
		memset(&h, 0, sizeof(h));
		memcpy(h.magic, "TGFDR1", 6);
		h.version = TG_FDR_VERSION;
		h.header_size = sizeof(h);
		h.record_size = sizeof(struct tg_fdr_record);
		h.bank_count = TG_FDR_BANK_COUNT;
		h.session = tg_fdr_session_id();
		h.capacity_per_bank = TG_FDR_RECORDS_PER_BANK;
		h.open_ns = ktime_get_ns();
		h.current_global = (u64)atomic64_read(&tg_fdr_global_seq);
		h.reader_lost = rd->lost;
		for (i = 0; i < TG_FDR_BANK_COUNT; i++) {
			struct tg_fdr_record c;
			if (tg_fdr_reader_candidate(rd, i, &c) && c.seq < first)
				first = c.seq;
		}
		h.first_global = first == ~0ULL ? h.current_global + 1 : first;
		if (copy_to_user(buf, &h, sizeof(h)))
			return -EFAULT;
		rd->header_done = true;
		return sizeof(h);
	}
	if (len < sizeof(struct tg_fdr_record))
		return -EINVAL;

	for (;;) {
		struct tg_fdr_record r;
		while (done + sizeof(r) <= len && tg_fdr_reader_next(rd, &r)) {
			if (copy_to_user(buf + done, &r, sizeof(r)))
				return done ? (ssize_t)done : -EFAULT;
			done += sizeof(r);
		}
		if (done)
			return done;
		if (file->f_flags & O_NONBLOCK)
			return -EAGAIN;
		ret = wait_event_interruptible(tg_fdr_waitq,
			(u64)atomic64_read(&tg_fdr_global_seq) != rd->seen_global);
		if (ret)
			return ret;
	}
}

static ssize_t tg_fdr_write(struct file *file, const char __user *buf,
			    size_t len, loff_t *ppos)
{
	char marker[TG_FDR_MARKER_BYTES];
	size_t n = len;
	u64 a = 0, b = 0, c = 0, d = 0;

	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;
	if (!n)
		return 0;
	if (n >= sizeof(marker))
		n = sizeof(marker) - 1;
	memset(marker, 0, sizeof(marker));
	if (copy_from_user(marker, buf, n))
		return -EFAULT;
	while (n && (marker[n - 1] == '\n' || marker[n - 1] == '\r'))
		marker[--n] = '\0';
	memcpy(&a, marker + 0, 8);
	memcpy(&b, marker + 8, 8);
	memcpy(&c, marker + 16, 8);
	memcpy(&d, marker + 24, 8);
	tg_fdr_emit(TG_FDR_SUBSYS_TEST, 0x4D41524BU, 0, 0,
		    a, b, c, d, TG_FDR_FLAG_MARKER | TG_FDR_FLAG_CRITICAL);
	return len;
}

static const struct file_operations tg_fdr_fops = {
	.owner = THIS_MODULE,
	.open = tg_fdr_open,
	.release = tg_fdr_release,
	.read = tg_fdr_read,
	.write = tg_fdr_write,
	.llseek = no_llseek,
};

static struct miscdevice tg_fdr_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "tg_fdr",
	.fops = &tg_fdr_fops,
	.mode = 0600,
};

static int tg_fdr_stats_show(struct seq_file *m, void *unused)
{
	u16 i;
	seq_puts(m, "# touchgrass_definitive_fdr_v1\n");
	seq_printf(m, "session=%u global_seq=%llu record_size=%zu banks=%u "
		   "capacity_per_bank=%u total_capacity=%u reader_lost=%llu\n",
		   tg_fdr_session_id(),
		   (unsigned long long)atomic64_read(&tg_fdr_global_seq),
		   sizeof(struct tg_fdr_record), TG_FDR_BANK_COUNT,
		   TG_FDR_RECORDS_PER_BANK,
		   TG_FDR_BANK_COUNT * TG_FDR_RECORDS_PER_BANK,
		   (unsigned long long)atomic64_read(&tg_fdr_reader_lost));
	for (i = 0; i < TG_FDR_BANK_COUNT; i++) {
		u64 n = (u64)atomic64_read(&tg_fdr_banks[i].local_seq);
		seq_printf(m,
			   "bank=%u attempted=%llu retained=%llu wraps=%llu high_water=%llu\n",
			   i, (unsigned long long)n,
			   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
						TG_FDR_RECORDS_PER_BANK : n),
			   (unsigned long long)(n / TG_FDR_RECORDS_PER_BANK),
			   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
						TG_FDR_RECORDS_PER_BANK : n));
	}
	return 0;
}

static int tg_fdr_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, tg_fdr_stats_show, NULL);
}

static const struct file_operations tg_fdr_stats_fops = {
	.owner = THIS_MODULE,
	.open = tg_fdr_stats_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init tg_fdr_init(void)
{
	int ret;
	tg_fdr_session_id();
	ret = misc_register(&tg_fdr_misc);
	if (ret)
		return ret;
	if (!proc_create("tg_fdr_stats", 0444, NULL, &tg_fdr_stats_fops)) {
		misc_deregister(&tg_fdr_misc);
		return -ENOMEM;
	}
	tg_fdr_emit(TG_FDR_SUBSYS_CORE, 0x46445201U, 0, 0,
		    TG_FDR_VERSION, sizeof(struct tg_fdr_record),
		    TG_FDR_BANK_COUNT, TG_FDR_RECORDS_PER_BANK,
		    TG_FDR_FLAG_CRITICAL);
	return 0;
}
late_initcall(tg_fdr_init);
'''

BOOT_ADAPTER = r'''// SPDX-License-Identifier: GPL-2.0-only
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/string.h>
#include <linux/tg_boot_reference.h>
#include <linux/tg_fdr.h>

static u16 tg_boot_ref_subsystem(const char *tag)
{
	if (!tag)
		return TG_FDR_SUBSYS_CORE;
	if (!strncmp(tag, "PROBE:", 6) || !strncmp(tag, "INITCALL:", 9))
		return TG_FDR_SUBSYS_DRIVER;
	if (!strncmp(tag, "IOMMU:", 6))
		return TG_FDR_SUBSYS_IOMMU;
	if (!strncmp(tag, "UFS", 3))
		return TG_FDR_SUBSYS_STORAGE;
	if (!strncmp(tag, "DISP:", 5))
		return TG_FDR_SUBSYS_DISPLAY;
	if (!strncmp(tag, "BINDER:", 7))
		return TG_FDR_SUBSYS_ANDROID;
	return TG_FDR_SUBSYS_CORE;
}

void tg_boot_ref_record(const char *tag, int rc, u64 a, u64 b, u64 c, u64 d)
{
	u16 subsys = tg_boot_ref_subsystem(tag);
	u32 flags = (!rc && tag && (!strncmp(tag, "USER:", 5) ||
		    !strncmp(tag, "MOUNT:", 6))) ? TG_FDR_FLAG_CRITICAL : 0;
	tg_fdr_emit_tag(subsys, tag, rc, 0, a, b, c, d, flags);
}

static int tg_boot_ref_show(struct seq_file *m, void *unused)
{
	seq_puts(m, "# touchgrass_final_boot_reference_v3\n");
	seq_puts(m, "# backend=touchgrass_definitive_fdr_v1\n");
	seq_puts(m, "# stream=/dev/tg_fdr stats=/proc/tg_fdr_stats\n");
	return 0;
}

static int tg_boot_ref_open(struct inode *inode, struct file *file)
{
	return single_open(file, tg_boot_ref_show, NULL);
}
static const struct file_operations tg_boot_ref_fops = {
	.owner = THIS_MODULE, .open = tg_boot_ref_open, .read = seq_read,
	.llseek = seq_lseek, .release = single_release,
};
static int __init tg_boot_ref_proc_init(void)
{
	return proc_create("tg_boot_reference", 0444, NULL, &tg_boot_ref_fops) ? 0 : -ENOMEM;
}
late_initcall(tg_boot_ref_proc_init);
'''

GPU_ADAPTER = r'''// SPDX-License-Identifier: GPL-2.0-only
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/string.h>
#include <linux/tg_fdr.h>
#include <linux/tg_gpu_reference.h>

static u16 tg_gpu_ref_subsystem(const char *tag)
{
	if (!tag)
		return TG_FDR_SUBSYS_GPU;
	if (!strncmp(tag, "SMMU:", 5) || !strncmp(tag, "KGSLI:", 6))
		return TG_FDR_SUBSYS_IOMMU;
	if (!strncmp(tag, "DISP:", 5))
		return TG_FDR_SUBSYS_DISPLAY;
	return TG_FDR_SUBSYS_GPU;
}

void tg_gpu_ref_record(const char *tag, const char *name, int rc,
		       u64 a, u64 b, u64 c, u64 d)
{
	tg_fdr_emit_tag(tg_gpu_ref_subsystem(tag), tag, rc, 0, a, b, c, d, 0);
}

static int tg_gpu_ref_show(struct seq_file *m, void *unused)
{
	seq_puts(m, "# touchgrass_gpu_reference_v2\n");
	seq_puts(m, "# backend=touchgrass_definitive_fdr_v1\n");
	seq_puts(m, "# stream=/dev/tg_fdr stats=/proc/tg_fdr_stats\n");
	return 0;
}

static int tg_gpu_ref_open(struct inode *inode, struct file *file)
{
	return single_open(file, tg_gpu_ref_show, NULL);
}
static const struct file_operations tg_gpu_ref_fops = {
	.owner = THIS_MODULE, .open = tg_gpu_ref_open, .read = seq_read,
	.llseek = seq_lseek, .release = single_release,
};
static int __init tg_gpu_ref_proc_init(void)
{
	return proc_create("tg_gpu_reference", 0444, NULL, &tg_gpu_ref_fops) ? 0 : -ENOMEM;
}
late_initcall(tg_gpu_ref_proc_init);
'''

def must(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"required file missing: {path}")
    return path.read_text()

def main(root: Path) -> None:
    checks = {
        "kernel/tg_boot_reference.c": "touchgrass_final_boot_reference_v2",
        "kernel/tg_gpu_reference.c": "touchgrass_gpu_reference_v1",
        "drivers/base/dd.c": "PROBE:BUS_POST",
        "drivers/iommu/arm-smmu.c": "SMMU:CEN_POST",
        "drivers/gpu/msm/kgsl_gmu.c": "GMU:ATT_POST",
    }
    for rel, token in checks.items():
        if token not in must(root / rel):
            raise RuntimeError(f"{rel}: proven recorder token missing: {token}")

    (root / "include/linux/tg_fdr.h").write_text(FDR_HEADER)
    (root / "kernel/tg_fdr.c").write_text(FDR_SOURCE)
    (root / "kernel/tg_boot_reference.c").write_text(BOOT_ADAPTER)
    (root / "kernel/tg_gpu_reference.c").write_text(GPU_ADAPTER)

    mk = root / "kernel/Makefile"
    text = must(mk)
    if "obj-y += tg_fdr.o\n" not in text:
        mk.write_text(text.rstrip() + "\nobj-y += tg_fdr.o\n")

    for rel, token in (
        ("kernel/tg_fdr.c", "TG_FDR_RECORDS_PER_BANK"),
        ("kernel/tg_fdr.c", 'name = "tg_fdr"'),
        ("kernel/tg_boot_reference.c", "backend=touchgrass_definitive_fdr_v1"),
        ("kernel/tg_gpu_reference.c", "backend=touchgrass_definitive_fdr_v1"),
    ):
        if token not in must(root / rel):
            raise RuntimeError(f"{rel}: FDR verification token missing: {token}")

    print("TouchGrass definitive FDR v1 backend staged")
    print("banks=8 records_per_bank=32768 record_bytes=96 total_bytes=25165824")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: touchgrass_definitive_fdr_overlay.py <kernel-root>")
    main(Path(sys.argv[1]).resolve())
