#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

PROFILE = "display-bindcore-fec-prz-v2"
REPORT_NAME = "phase34-a52-recorder-fec-report.json"
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
RAM_REL = Path("fs/pstore/ram.c")
MAIN_REL = Path("init/main.c")

NEW_RECORDER = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52 black-screen failure-window recorder v3.
 *
 * Records only boot, heartbeat, REFGEN, display, and watchdog metadata.
 * Each fixed 256-byte event is protected by CRC32C, 32 Reed-Solomon parity
 * symbols, a final commit footer, and three physically separated copies.
 */
#undef pr_fmt
#define pr_fmt(fmt) "A52REC3: " fmt

#include <linux/atomic.h>
#include <linux/byteorder/generic.h>
#include <linux/cpu.h>
#include <linux/export.h>
#include <linux/init.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/math64.h>
#include <linux/sched.h>
#include <linux/sched/stat.h>
#include <linux/string.h>
#include <linux/workqueue.h>

#include <linux/a52_ack_secure_flight_recorder.h>

#define A52_REC3_MAGIC 0x52323541U
#define A52_REC3_VERSION 3U
#define A52_REC3_HEADER_LEN 56U
#define A52_REC3_MESSAGE_LEN 128U
#define A52_REC3_PREPARED 0x31504552U
#define A52_REC3_PROFILE "display-bindcore-fec-prz-v2"
#define A52_REC3_FLAG_CRITICAL BIT(0)

#define A52_REC3_EVENT_BOOT 1U
#define A52_REC3_EVENT_HEARTBEAT 2U
#define A52_REC3_EVENT_REFGEN 3U
#define A52_REC3_EVENT_DISPLAY 4U
#define A52_REC3_EVENT_WATCHDOG 5U

struct a52_rec3_data {
	__le32 magic;
	u8 version;
	u8 header_len;
	__le16 flags;
	__le64 seq;
	__le64 monotonic_ns;
	__le32 pid;
	__le32 tgid;
	__le16 cpu;
	__le16 event_id;
	__le16 message_len;
	__le16 reserved;
	char comm[TASK_COMM_LEN];
	char message[A52_REC3_MESSAGE_LEN];
	__le32 crc32c;
	__le32 prepared;
	__le64 seq_inv;
	__le64 ns_inv;
} __packed;

static atomic64_t a52_rec3_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_rec3_write_failures = ATOMIC64_INIT(0);

extern unsigned int a52_ackfr_ramoops_write_record(const void *data,
						    size_t len, u64 seq);

static u32 a52_rec3_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^ ((crc & 1) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

static u16 a52_rec3_event_id(const char *message)
{
	if (!message)
		return 0;
	if (!strncmp(message, "BOOT ", 5))
		return A52_REC3_EVENT_BOOT;
	if (!strncmp(message, "HB ", 3))
		return A52_REC3_EVENT_HEARTBEAT;
	if (!strncmp(message, "REFGEN ", 7))
		return A52_REC3_EVENT_REFGEN;
	if (!strncmp(message, "DISP ", 5))
		return A52_REC3_EVENT_DISPLAY;
	if (!strncmp(message, "WDT ", 4))
		return A52_REC3_EVENT_WATCHDOG;
	return 0;
}

void a52_ackfr_record(const char *fmt, ...)
{
	struct a52_rec3_data record;
	char message[A52_REC3_MESSAGE_LEN];
	unsigned int written;
	va_list args;
	u64 seq;
	u64 ns;
	u16 event_id;
	size_t message_len;

	BUILD_BUG_ON(sizeof(struct a52_rec3_data) != 208);

	memset(message, 0, sizeof(message));
	va_start(args, fmt);
	vscnprintf(message, sizeof(message), fmt, args);
	va_end(args);

	event_id = a52_rec3_event_id(message);
	if (!event_id)
		return;

	seq = (u64)atomic64_inc_return(&a52_rec3_sequence);
	ns = ktime_get_ns();
	message_len = strnlen(message, sizeof(message));

	memset(&record, 0, sizeof(record));
	record.magic = cpu_to_le32(A52_REC3_MAGIC);
	record.version = A52_REC3_VERSION;
	record.header_len = A52_REC3_HEADER_LEN;
	record.flags = cpu_to_le16(A52_REC3_FLAG_CRITICAL);
	record.seq = cpu_to_le64(seq);
	record.monotonic_ns = cpu_to_le64(ns);
	record.pid = cpu_to_le32((u32)current->pid);
	record.tgid = cpu_to_le32((u32)current->tgid);
	record.cpu = cpu_to_le16((u16)task_cpu(current));
	record.event_id = cpu_to_le16(event_id);
	record.message_len = cpu_to_le16((u16)message_len);
	get_task_comm(record.comm, current);
	memcpy(record.message, message, message_len);
	record.crc32c = cpu_to_le32(a52_rec3_crc32c(&record,
					      offsetof(struct a52_rec3_data,
						       crc32c)));
	record.prepared = cpu_to_le32(A52_REC3_PREPARED);
	record.seq_inv = cpu_to_le64(~seq);
	record.ns_inv = cpu_to_le64(~ns);

	written = a52_ackfr_ramoops_write_record(&record, sizeof(record), seq);
	if (written != 0x7U)
		atomic64_inc(&a52_rec3_write_failures);
}
EXPORT_SYMBOL_GPL(a52_ackfr_record);

struct a52_ackfr_scope a52_ackfr_scope_begin(const char *domain,
					      const char *name)
{
	struct a52_ackfr_scope scope = {
		.domain = domain,
		.name = name,
		.start_ns = ktime_get_ns(),
	};

	a52_ackfr_record("%s enter fn=%s", domain, name);
	return scope;
}
EXPORT_SYMBOL_GPL(a52_ackfr_scope_begin);

void a52_ackfr_scope_cleanup(struct a52_ackfr_scope *scope)
{
	u64 duration_us;

	if (!scope || !scope->start_ns)
		return;
	duration_us = div_u64(ktime_get_ns() - scope->start_ns, 1000U);
	a52_ackfr_record("%s exit fn=%s us=%llu", scope->domain, scope->name,
			  (unsigned long long)duration_us);
}
EXPORT_SYMBOL_GPL(a52_ackfr_scope_cleanup);

#define A52_REC3_HEARTBEAT_INTERVAL_MS 1000U
#define A52_REC3_HEARTBEAT_LIMIT 180U

static atomic_t a52_rec3_heartbeat_count = ATOMIC_INIT(0);
static void a52_rec3_heartbeat_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_rec3_heartbeat_work,
			    a52_rec3_heartbeat_fn);

static void a52_rec3_heartbeat_fn(struct work_struct *work)
{
	unsigned int tick;

	tick = (unsigned int)atomic_inc_return(&a52_rec3_heartbeat_count);
	a52_ackfr_record("HB t=%u on=%u run=%lu j=%lu", tick,
			  num_online_cpus(), nr_running(), jiffies);
	if (tick < A52_REC3_HEARTBEAT_LIMIT)
		schedule_delayed_work(&a52_rec3_heartbeat_work,
			msecs_to_jiffies(A52_REC3_HEARTBEAT_INTERVAL_MS));
}

static int __init a52_rec3_early(void)
{
	a52_ackfr_record("BOOT phase=pre_smp");
	return 0;
}
early_initcall(a52_rec3_early);

static int __init a52_rec3_core(void)
{
	a52_ackfr_record("BOOT phase=core");
	return 0;
}
core_initcall(a52_rec3_core);

static int __init a52_rec3_subsys(void)
{
	a52_ackfr_record("BOOT phase=subsys");
	return 0;
}
subsys_initcall(a52_rec3_subsys);

static int __init a52_rec3_device(void)
{
	a52_ackfr_record("BOOT phase=device");
	return 0;
}
device_initcall(a52_rec3_device);

static int __init a52_rec3_late(void)
{
	a52_ackfr_record(
		"BOOT recorder=v3 profile=%s copies=3 rs=32 crc32c=1 slots=1023",
		A52_REC3_PROFILE);
	a52_ackfr_record("HB start ms=%u limit=%u",
			  A52_REC3_HEARTBEAT_INTERVAL_MS,
			  A52_REC3_HEARTBEAT_LIMIT);
	schedule_delayed_work(&a52_rec3_heartbeat_work,
		msecs_to_jiffies(A52_REC3_HEARTBEAT_INTERVAL_MS));
	pr_info("failure-window recorder v3 enabled failures=%llu\n",
		(unsigned long long)atomic64_read(&a52_rec3_write_failures));
	return 0;
}
late_initcall(a52_rec3_late);
'''

NEW_RAM_WRITER = r'''/* A52_RECORDER_V3_FEC */
#define A52_ACKFR_DATA_BYTES 208U
#define A52_ACKFR_PARITY_BYTES 32U
#define A52_ACKFR_CODEWORD_BYTES \
	(A52_ACKFR_DATA_BYTES + A52_ACKFR_PARITY_BYTES)
#define A52_ACKFR_RECORD_BYTES 256U
#define A52_ACKFR_COMMIT 0x5a52c0deU

struct a52_ackfr_footer {
	__le32 commit;
	__le32 commit_inv;
	__le32 seq_low;
	__le32 seq_low_inv;
} __packed;

extern unsigned int a52_ackfr_ramoops_write_record(const void *data,
						    size_t len, u64 seq);
'''

NEW_RAM_BACKEND = r'''#define A52_DIAG_BANK_SIZE 0x00040000UL
#define A52_DIAG_BANK_HEADER 256U
#define A52_DIAG_RECORD_SIZE 256U
#define A52_DIAG_SLOT_COUNT \
	((A52_DIAG_BANK_SIZE - A52_DIAG_BANK_HEADER) / A52_DIAG_RECORD_SIZE)
#define A52_DIAG_PERSISTENT_RAM_SIG 0x43474244U
#define A52_DIAG_SUPER_MAGIC 0x33425241U
#define A52_DIAG_BANK_COUNT 3U

static const phys_addr_t a52_diag_phys[A52_DIAG_BANK_COUNT] = {
	0xB1B00000ULL,
	0xB1B40000ULL,
	0xB1B80000ULL,
};

static const char * const a52_diag_labels[A52_DIAG_BANK_COUNT] = {
	"a52-rec3-record",
	"a52-rec3-console",
	"a52-rec3-ftrace",
};

static const enum pstore_type_id a52_diag_types[A52_DIAG_BANK_COUNT] = {
	PSTORE_TYPE_DMESG,
	PSTORE_TYPE_CONSOLE,
	PSTORE_TYPE_FTRACE,
};

struct a52_diag_super {
	__le32 magic;
	__le16 version;
	__le16 bytes;
	__le32 bank;
	__le32 copies;
	__le32 record_size;
	__le32 slot_count;
	__le32 data_bytes;
	__le32 parity_bytes;
	char profile[32];
	__le32 magic_inv;
	__le32 reserved;
} __packed;

static struct persistent_ram_zone *a52_diag_prz[A52_DIAG_BANK_COUNT];
static u8 __iomem *a52_diag_banks[A52_DIAG_BANK_COUNT];
static struct rs_control *a52_diag_rs;
static DEFINE_RAW_SPINLOCK(a52_diag_lock);

static void a52_diag_write_super(unsigned int bank)
{
	struct a52_diag_super super;
	unsigned int copy;

	memset(&super, 0, sizeof(super));
	super.magic = cpu_to_le32(A52_DIAG_SUPER_MAGIC);
	super.version = cpu_to_le16(3);
	super.bytes = cpu_to_le16(sizeof(super));
	super.bank = cpu_to_le32(bank);
	super.copies = cpu_to_le32(A52_DIAG_BANK_COUNT);
	super.record_size = cpu_to_le32(A52_DIAG_RECORD_SIZE);
	super.slot_count = cpu_to_le32(A52_DIAG_SLOT_COUNT);
	super.data_bytes = cpu_to_le32(A52_ACKFR_DATA_BYTES);
	super.parity_bytes = cpu_to_le32(A52_ACKFR_PARITY_BYTES);
	strscpy(super.profile, "display-bindcore-fec-prz-v2",
		sizeof(super.profile));
	super.magic_inv = cpu_to_le32(~A52_DIAG_SUPER_MAGIC);

	for (copy = 0; copy < 3; copy++)
		memcpy_toio(a52_diag_banks[bank] + 16 + copy * sizeof(super),
			    &super, sizeof(super));
}

static int a52_diag_map_bank(unsigned int bank)
{
	struct persistent_ram_ecc_info ecc = { };

	if (bank >= A52_DIAG_BANK_COUNT)
		return -EINVAL;
	if (a52_diag_prz[bank] && a52_diag_banks[bank])
		return 0;

	/*
	 * The 1 MiB RAMOOPS reservation is normal RAM on this device. Let
	 * persistent_ram_new() select vmap() for valid PFNs instead of forcing
	 * ioremap(), which cannot reliably map this reservation on the A52.
	 * Keep the exact fixed-slot format by writing through the PRZ mapping.
	 */
	a52_diag_prz[bank] = persistent_ram_new(a52_diag_phys[bank],
					       A52_DIAG_BANK_SIZE, 0, &ecc,
					       1, PRZ_FLAG_ZAP_OLD,
					       (char *)a52_diag_labels[bank]);
	if (IS_ERR(a52_diag_prz[bank])) {
		int ret = PTR_ERR(a52_diag_prz[bank]);

		a52_diag_prz[bank] = NULL;
		return ret;
	}

	a52_diag_prz[bank]->type = a52_diag_types[bank];
	a52_diag_banks[bank] = (u8 __iomem *)a52_diag_prz[bank]->vaddr;
	if (!a52_diag_banks[bank])
		return -ENOMEM;

	memset_io(a52_diag_banks[bank], 0, A52_DIAG_BANK_SIZE);
	writel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG, a52_diag_banks[bank]);
	writel_relaxed(0, a52_diag_banks[bank] + 4);
	writel_relaxed(0, a52_diag_banks[bank] + 8);
	a52_diag_write_super(bank);
	wmb();
	return 0;
}

unsigned int a52_ackfr_ramoops_write_record(const void *data, size_t len,
						    u64 seq)
{
	struct a52_ackfr_footer footer;
	u16 parity[A52_ACKFR_PARITY_BYTES];
	u8 record[A52_ACKFR_RECORD_BYTES];
	unsigned long irq_flags;
	unsigned int bank;
	unsigned int index;
	unsigned int slot;
	unsigned int written = 0;
	u32 valid_bytes;

	if (!data || len != A52_ACKFR_DATA_BYTES || !seq || !a52_diag_rs)
		return 0;

	memset(record, 0, sizeof(record));
	memcpy(record, data, len);
	memset(parity, 0, sizeof(parity));
	if (encode_rs8(a52_diag_rs, record, A52_ACKFR_DATA_BYTES,
		       parity, 0))
		return 0;
	for (index = 0; index < A52_ACKFR_PARITY_BYTES; index++)
		record[A52_ACKFR_DATA_BYTES + index] = (u8)parity[index];

	footer.commit = cpu_to_le32(A52_ACKFR_COMMIT);
	footer.commit_inv = cpu_to_le32(~A52_ACKFR_COMMIT);
	footer.seq_low = cpu_to_le32((u32)seq);
	footer.seq_low_inv = cpu_to_le32(~(u32)seq);
	slot = (unsigned int)((seq - 1) % A52_DIAG_SLOT_COUNT);

	raw_spin_lock_irqsave(&a52_diag_lock, irq_flags);
	for (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
		u8 __iomem *destination;

		if (!a52_diag_banks[bank])
			continue;
		destination = a52_diag_banks[bank] + A52_DIAG_BANK_HEADER +
			      slot * A52_DIAG_RECORD_SIZE;
		memset_io(destination + A52_ACKFR_CODEWORD_BYTES, 0,
			  sizeof(footer));
		memcpy_toio(destination, record, A52_ACKFR_CODEWORD_BYTES);
	}
	wmb();
	for (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
		u8 __iomem *destination;

		if (!a52_diag_banks[bank])
			continue;
		destination = a52_diag_banks[bank] + A52_DIAG_BANK_HEADER +
			      slot * A52_DIAG_RECORD_SIZE;
		memcpy_toio(destination + A52_ACKFR_CODEWORD_BYTES,
			    &footer, sizeof(footer));
		written |= BIT(bank);
	}
	wmb();
	valid_bytes = min_t(u64, seq, A52_DIAG_SLOT_COUNT) *
		      A52_DIAG_RECORD_SIZE;
	for (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
		if (!a52_diag_banks[bank])
			continue;
		writel_relaxed(((slot + 1) % A52_DIAG_SLOT_COUNT) *
			       A52_DIAG_RECORD_SIZE,
			       a52_diag_banks[bank] + 4);
		writel_relaxed(valid_bytes, a52_diag_banks[bank] + 8);
	}
	wmb();
	raw_spin_unlock_irqrestore(&a52_diag_lock, irq_flags);
	return written;
}
EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write_record);

int __init a52_persistent_diag_init(void)
{
	unsigned int bank;
	unsigned int mapped = 0;
	int ret;

	if (a52_diag_rs)
		return 0;
	a52_diag_rs = init_rs(8, 0x11d, 0, 1, A52_ACKFR_PARITY_BYTES);
	if (!a52_diag_rs)
		return -ENOMEM;

	for (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
		ret = a52_diag_map_bank(bank);
		if (!ret)
			mapped++;
		else
			pr_err("A52 recorder PRZ bank %u mapping failed: %d\n",
			       bank, ret);
	}
	if (mapped < 2) {
		free_rs(a52_diag_rs);
		a52_diag_rs = NULL;
		return -ENOMEM;
	}
	pr_info("A52 recorder v3 PRZ-mapped %u banks, slots=%u, RS parity=%u\n",
		mapped, A52_DIAG_SLOT_COUNT, A52_ACKFR_PARITY_BYTES);
	return 0;
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_ram(text: str) -> str:
    if "A52_RECORDER_V3_FEC" in text:
        return text
    if "#include <linux/rslib.h>" not in text:
        text = replace_once(
            text,
            "#include <linux/pstore_ram.h>\n",
            "#include <linux/pstore_ram.h>\n#include <linux/rslib.h>\n",
            "rslib include",
        )
    start = text.index("/* A52_ACK_UNIFIED_SECURE_RECORDER_V2 */")
    end = text.index("static void ramoops_free_przs", start)
    text = text[:start] + NEW_RAM_WRITER + "\n" + text[end:]
    start = text.index("#define A52_DIAG_CONSOLE_PHYS")
    end = text.index("static int __init __maybe_unused ramoops_init(void)", start)
    text = text[:start] + NEW_RAM_BACKEND + text[end:]
    return text


def patch_main(text: str) -> str:
    if 'a52_ackfr_record("BOOT phase=mm_init")' in text:
        return text
    text = text.replace(
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
        "extern void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n",
        "extern void a52_ackfr_record(const char *fmt, ...);\n",
        1,
    )
    text = text.replace(
        "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
        "static inline void a52_persistent_diag_mark_ftrace(const char *fmt, ...) { }\n",
        "static inline void a52_ackfr_record(const char *fmt, ...) { }\n",
        1,
    )
    pattern = re.compile(
        r'\telse \{\n'
        r'\t\ta52_persistent_diag_mark\(\n'
        r'\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "\n'
        r'\t\t\t"metadata_only=1 commit=5a52c0de\\n"\);\n'
        r'\t\ta52_persistent_diag_mark_ftrace\(\n'
        r'\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "\n'
        r'\t\t\t"metadata_only=1 commit=5a52c0de\\n"\);\n'
        r'\t\}',
    )
    text, count = pattern.subn(
        '\telse\n\t\ta52_ackfr_record("BOOT phase=mm_init");', text, count=1
    )
    if count != 1:
        raise SystemExit(f"main early record block: expected one anchor, found {count}")
    return text


def run(gki: Path, output: Path) -> dict[str, object]:
    recorder = gki / RECORDER_REL
    ram = gki / RAM_REL
    main = gki / MAIN_REL
    for path in (recorder, ram, main):
        if not path.is_file():
            raise SystemExit(f"required source missing: {path}")

    recorder.write_text(NEW_RECORDER, encoding="utf-8")
    ram.write_text(patch_ram(ram.read_text(encoding="utf-8")), encoding="utf-8")
    main.write_text(patch_main(main.read_text(encoding="utf-8")), encoding="utf-8")

    final_recorder = recorder.read_text(encoding="utf-8")
    final_ram = ram.read_text(encoding="utf-8")
    final_main = main.read_text(encoding="utf-8")
    required = {
        "recorder": [
            "A52 black-screen failure-window recorder v3",
            'A52_REC3_PROFILE "display-bindcore-fec-prz-v2"',
            "A52_REC3_MESSAGE_LEN 128U",
            "a52_rec3_crc32c",
            "a52_ackfr_ramoops_write_record",
            "A52_REC3_HEARTBEAT_INTERVAL_MS 1000U",
        ],
        "ram": [
            "A52_RECORDER_V3_FEC",
            "A52_ACKFR_PARITY_BYTES 32U",
            "A52_DIAG_BANK_COUNT 3U",
            "0xB1B00000ULL",
            "0xB1B40000ULL",
            "0xB1B80000ULL",
            "encode_rs8",
            "init_rs(8, 0x11d, 0, 1, A52_ACKFR_PARITY_BYTES)",
            "persistent_ram_new(a52_diag_phys[bank]",
            "a52_diag_prz[bank]->vaddr",
        ],
        "main": ['a52_ackfr_record("BOOT phase=mm_init")'],
    }
    for marker in required["recorder"]:
        if marker not in final_recorder:
            raise SystemExit(f"recorder audit marker missing: {marker}")
    for marker in required["ram"]:
        if marker not in final_ram:
            raise SystemExit(f"ram audit marker missing: {marker}")
    for marker in required["main"]:
        if marker not in final_main:
            raise SystemExit(f"main audit marker missing: {marker}")
    if "a52_persistent_diag_mark(" in final_main:
        raise SystemExit("legacy ASCII early recorder call remains")

    report = {
        "status": "a52-recorder-v3-fec-staged",
        "hardware_validated": False,
        "functional_change": "persistent-recorder-mapping-only",
        "display_control_flow_changed": False,
        "refgen_logic_changed": False,
        "secure_memory_logic_changed": False,
        "persistent_profile": PROFILE,
        "mapping_backend": "persistent_ram_new-vmap-owned-fixed-slots",
        "record_format": {
            "record_bytes": 256,
            "protected_data_bytes": 208,
            "reed_solomon_parity_bytes": 32,
            "unknown_symbol_correction_capacity_per_copy": 16,
            "crc": "CRC32C",
            "copies": 3,
            "commit_footer_bytes": 16,
            "slots_per_bank": 1023,
            "banks": ["record", "console", "ftrace"],
        },
        "scope_filter": ["BOOT", "HB", "REFGEN", "DISP", "WDT"],
        "heartbeat": {"interval_ms": 1000, "limit": 180},
        "recovery_order": [
            "byte-majority vote across three banks",
            "CRC32C validation",
            "Reed-Solomon correction when CRC fails",
            "final commit footer validation",
        ],
        "files": [str(RECORDER_REL), str(RAM_REL), str(MAIN_REL)],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-rec3-") as tmp:
        root = Path(tmp)
        recorder = root / RECORDER_REL
        ram = root / RAM_REL
        main = root / MAIN_REL
        recorder.parent.mkdir(parents=True, exist_ok=True)
        ram.parent.mkdir(parents=True, exist_ok=True)
        main.parent.mkdir(parents=True, exist_ok=True)
        recorder.write_text("legacy recorder\n", encoding="utf-8")
        ram.write_text(
            "#include <linux/pstore_ram.h>\n"
            "/* A52_ACK_UNIFIED_SECURE_RECORDER_V2 */\n"
            "old writer\n"
            "static void ramoops_free_przs(void) {}\n"
            "#define A52_DIAG_CONSOLE_PHYS 1\n"
            "old backend\n"
            "static int __init __maybe_unused ramoops_init(void) { return 0; }\n",
            encoding="utf-8",
        )
        main.write_text(
            "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
            "extern void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n"
            "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
            "static inline void a52_persistent_diag_mark_ftrace(const char *fmt, ...) { }\n"
            "void f(void) {\n"
            "\tif (a52_persistent_diag_init())\n"
            "\t\tpr_err(\"failed\\n\");\n"
            "\telse {\n"
            "\t\ta52_persistent_diag_mark(\n"
            "\t\t\t\"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored \"\n"
            "\t\t\t\"metadata_only=1 commit=5a52c0de\\n\");\n"
            "\t\ta52_persistent_diag_mark_ftrace(\n"
            "\t\t\t\"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored \"\n"
            "\t\t\t\"metadata_only=1 commit=5a52c0de\\n\");\n"
            "\t}\n}\n",
            encoding="utf-8",
        )
        first = run(root, root / "out")
        second = run(root, root / "out2")
        assert first["status"] == "a52-recorder-v3-fec-staged"
        assert second["status"] == first["status"]
        print(json.dumps({"status": "self-test-passed"}, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gki", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.gki is None or args.output is None:
        parser.error("--gki and --output are required unless --self-test is used")
    print(json.dumps(run(args.gki.resolve(), args.output.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
