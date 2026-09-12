#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * A52 touchGrass persistent crash recorder.
 *
 * Purpose: make every touchGrass diagnostic boot self-identifying in the
 * 0xB1B00000 1 MiB persistent region collected by recovery.  The recorder
 * deliberately uses its own fixed format so stale GKI RS48 data can never be
 * mistaken for this kernel.
 *
 * Layout:
 *   bank0 0x00000..0x3ffff
 *   bank1 0x40000..0x7ffff
 *   bank2 0x80000..0xbffff
 * Each bank has a 256-byte header followed by 1023 mirrored 256-byte records.
 * Records carry CRC32C plus a commit footer.  No allocation is performed in
 * the die/panic paths.
 */

#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/jiffies.h>
#include <linux/kdebug.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/notifier.h>
#include <linux/reboot.h>
#include <linux/sched.h>
#include <linux/sizes.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/workqueue.h>
#include <linux/utsname.h>
#include <linux/cpu.h>

#include <asm/ptrace.h>
#include <asm/sysreg.h>

#define TGREC_PHYS              0xB1B00000ULL
#define TGREC_SIZE              SZ_1M
#define TGREC_BANK_SIZE         0x00040000UL
#define TGREC_BANKS             3U
#define TGREC_RECORD_SIZE       256U
#define TGREC_HEADER_SIZE       256U
#define TGREC_SLOTS             ((TGREC_BANK_SIZE - TGREC_HEADER_SIZE) / TGREC_RECORD_SIZE)

#define TGREC_HEADER_MAGIC      0x48344754U /* "TG4H" */
#define TGREC_RECORD_MAGIC      0x52344754U /* "TG4R" */
#define TGREC_VERSION           1U
#define TGREC_COMMIT            0x5a52c476U

#define TGREC_TYPE_BOOT         1U
#define TGREC_TYPE_HEARTBEAT    2U
#define TGREC_TYPE_DIE          3U
#define TGREC_TYPE_PANIC        4U
#define TGREC_TYPE_REBOOT       5U

#define TGREC_BUILD_ID          "TG83F1"
#define TGREC_BUILD_DESC        "phase83 llvm17 fixed-eevdf subprog-call-fix"

struct tgrec_header {
	__le32 magic;
	__le16 version;
	__le16 bank;
	__le32 record_size;
	__le32 slot_count;
	__le64 boot_ns;
	__le64 seq;
	char build_id[16];
	char build_desc[128];
	__le32 magic_inv;
	u8 reserved[76];
} __packed;

struct tgrec_record {
	__le32 magic;
	__le16 version;
	__le16 type;
	__le64 seq;
	__le64 monotonic_ns;
	__le32 pid;
	__le32 tgid;
	__le16 cpu;
	__le16 message_len;
	char comm[TASK_COMM_LEN];
	char message[172];
	__le32 crc32c;
	__le32 commit;
	__le32 commit_inv;
	__le32 seq_low;
	__le32 seq_low_inv;
	char build_id[12];
} __packed;

static void *tgrec_mem;
static atomic64_t tgrec_seq = ATOMIC64_INIT(0);
static DEFINE_RAW_SPINLOCK(tgrec_lock);

extern struct atomic_notifier_head panic_notifier_list;

static u32 tgrec_crc32c(const void *buffer, size_t len)
{
	const u8 *p = buffer;
	u32 crc = ~0U;
	size_t i;
	unsigned int bit;

	for (i = 0; i < len; i++) {
		crc ^= p[i];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^ ((crc & 1U) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

static void tgrec_write_header(unsigned int bank)
{
	struct tgrec_header h;
	u8 *base;

	if (!tgrec_mem || bank >= TGREC_BANKS)
		return;

	memset(&h, 0, sizeof(h));
	h.magic = cpu_to_le32(TGREC_HEADER_MAGIC);
	h.version = cpu_to_le16(TGREC_VERSION);
	h.bank = cpu_to_le16((u16)bank);
	h.record_size = cpu_to_le32(TGREC_RECORD_SIZE);
	h.slot_count = cpu_to_le32(TGREC_SLOTS);
	h.boot_ns = cpu_to_le64(ktime_get_ns());
	h.seq = cpu_to_le64(0);
	strlcpy(h.build_id, TGREC_BUILD_ID, sizeof(h.build_id));
	strlcpy(h.build_desc, TGREC_BUILD_DESC, sizeof(h.build_desc));
	h.magic_inv = cpu_to_le32(~TGREC_HEADER_MAGIC);

	base = (u8 *)tgrec_mem + bank * TGREC_BANK_SIZE;
	memcpy(base, &h, sizeof(h));
}

static void tgrec_record(unsigned int type, const char *fmt, ...)
{
	struct tgrec_record r;
	char msg[sizeof(r.message)];
	unsigned long flags;
	unsigned int bank;
	unsigned int slot;
	u64 seq;
	size_t len;
	va_list ap;

	if (!tgrec_mem)
		return;

	memset(msg, 0, sizeof(msg));
	va_start(ap, fmt);
	vscnprintf(msg, sizeof(msg), fmt, ap);
	va_end(ap);

	seq = (u64)atomic64_inc_return(&tgrec_seq);
	slot = (unsigned int)((seq - 1) % TGREC_SLOTS);

	memset(&r, 0, sizeof(r));
	r.magic = cpu_to_le32(TGREC_RECORD_MAGIC);
	r.version = cpu_to_le16(TGREC_VERSION);
	r.type = cpu_to_le16((u16)type);
	r.seq = cpu_to_le64(seq);
	r.monotonic_ns = cpu_to_le64(ktime_get_ns());
	r.pid = cpu_to_le32((u32)current->pid);
	r.tgid = cpu_to_le32((u32)current->tgid);
	r.cpu = cpu_to_le16((u16)raw_smp_processor_id());
	get_task_comm(r.comm, current);
	len = strnlen(msg, sizeof(msg));
	r.message_len = cpu_to_le16((u16)len);
	memcpy(r.message, msg, len);
	r.crc32c = cpu_to_le32(tgrec_crc32c(&r,
					offsetof(struct tgrec_record, crc32c)));
	r.commit = cpu_to_le32(TGREC_COMMIT);
	r.commit_inv = cpu_to_le32(~TGREC_COMMIT);
	r.seq_low = cpu_to_le32((u32)seq);
	r.seq_low_inv = cpu_to_le32(~(u32)seq);
	strlcpy(r.build_id, TGREC_BUILD_ID, sizeof(r.build_id));

	raw_spin_lock_irqsave(&tgrec_lock, flags);
	for (bank = 0; bank < TGREC_BANKS; bank++) {
		u8 *dst = (u8 *)tgrec_mem + bank * TGREC_BANK_SIZE +
			  TGREC_HEADER_SIZE + slot * TGREC_RECORD_SIZE;

		/*
		 * Commit footer is written last.  A partially torn write is
		 * rejected by the decoder.
		 */
		memset(dst + offsetof(struct tgrec_record, commit), 0,
		       sizeof(r) - offsetof(struct tgrec_record, commit));
		memcpy(dst, &r, offsetof(struct tgrec_record, commit));
		wmb();
		memcpy(dst + offsetof(struct tgrec_record, commit),
		       (u8 *)&r + offsetof(struct tgrec_record, commit),
		       sizeof(r) - offsetof(struct tgrec_record, commit));
	}
	wmb();
	raw_spin_unlock_irqrestore(&tgrec_lock, flags);
}

static int __init tgrec_map_init(void)
{
	unsigned int bank;

	BUILD_BUG_ON(sizeof(struct tgrec_header) != TGREC_HEADER_SIZE);
	BUILD_BUG_ON(sizeof(struct tgrec_record) != TGREC_RECORD_SIZE);

	tgrec_mem = memremap(TGREC_PHYS, TGREC_SIZE, MEMREMAP_WB);
	if (!tgrec_mem) {
		pr_err("TGREC: memremap failed phys=0x%llx size=0x%x\n",
		       (unsigned long long)TGREC_PHYS, (unsigned int)TGREC_SIZE);
		return 0;
	}

	/* Destroy stale GKI RS48 data immediately on this kernel's boot. */
	memset(tgrec_mem, 0, TGREC_SIZE);
	wmb();

	for (bank = 0; bank < TGREC_BANKS; bank++)
		tgrec_write_header(bank);
	wmb();

	tgrec_record(TGREC_TYPE_BOOT,
		     "BOOT_READY id=%s desc=%s rel=%s",
		     TGREC_BUILD_ID, TGREC_BUILD_DESC, utsname()->release);
	pr_info("TGREC: persistent recorder ready id=%s slots=%u banks=%u\n",
		TGREC_BUILD_ID, TGREC_SLOTS, TGREC_BANKS);
	return 0;
}
pure_initcall(tgrec_map_init);

static int tgrec_die_notifier(struct notifier_block *nb,
			      unsigned long val, void *data)
{
	struct die_args *args = data;
	struct pt_regs *regs = args ? args->regs : NULL;
	u64 esr = read_sysreg(esr_el1);
	u64 far = read_sysreg(far_el1);

	if (!regs) {
		tgrec_record(TGREC_TYPE_DIE,
			     "DIE val=%lu str=%s err=%ld trap=%d sig=%d esr=%016llx far=%016llx",
			     val, args && args->str ? args->str : "?",
			     args ? args->err : 0L,
			     args ? args->trapnr : -1,
			     args ? args->signr : -1,
			     (unsigned long long)esr,
			     (unsigned long long)far);
		return NOTIFY_DONE;
	}

	tgrec_record(TGREC_TYPE_DIE,
		     "DIE val=%lu str=%s pc=%016llx lr=%016llx sp=%016llx pstate=%016llx",
		     val, args->str ? args->str : "?",
		     (unsigned long long)regs->pc,
		     (unsigned long long)regs->regs[30],
		     (unsigned long long)regs->sp,
		     (unsigned long long)regs->pstate);
	tgrec_record(TGREC_TYPE_DIE,
		     "FAULT esr=%016llx far=%016llx err=%ld trap=%d sig=%d x0=%016llx x1=%016llx x2=%016llx",
		     (unsigned long long)esr,
		     (unsigned long long)far,
		     args->err, args->trapnr, args->signr,
		     (unsigned long long)regs->regs[0],
		     (unsigned long long)regs->regs[1],
		     (unsigned long long)regs->regs[2]);
	tgrec_record(TGREC_TYPE_DIE,
		     "REGS x19=%016llx x20=%016llx x21=%016llx x22=%016llx x23=%016llx x24=%016llx x25=%016llx",
		     (unsigned long long)regs->regs[19],
		     (unsigned long long)regs->regs[20],
		     (unsigned long long)regs->regs[21],
		     (unsigned long long)regs->regs[22],
		     (unsigned long long)regs->regs[23],
		     (unsigned long long)regs->regs[24],
		     (unsigned long long)regs->regs[25]);
	return NOTIFY_DONE;
}

static struct notifier_block tgrec_die_nb = {
	.notifier_call = tgrec_die_notifier,
	.priority = INT_MAX,
};

static int tgrec_panic_notifier(struct notifier_block *nb,
				unsigned long val, void *data)
{
	const char *msg = data;

	tgrec_record(TGREC_TYPE_PANIC, "PANIC val=%lu msg=%s",
		     val, msg ? msg : "?");
	return NOTIFY_DONE;
}

static struct notifier_block tgrec_panic_nb = {
	.notifier_call = tgrec_panic_notifier,
	.priority = INT_MAX,
};

static int tgrec_reboot_notifier(struct notifier_block *nb,
				 unsigned long action, void *data)
{
	tgrec_record(TGREC_TYPE_REBOOT, "REBOOT action=%lu cmd=%s",
		     action, data ? (const char *)data : "?");
	return NOTIFY_DONE;
}

static struct notifier_block tgrec_reboot_nb = {
	.notifier_call = tgrec_reboot_notifier,
	.priority = INT_MAX,
};

static int __init tgrec_notifier_init(void)
{
	int ret;

	ret = register_die_notifier(&tgrec_die_nb);
	if (ret)
		pr_err("TGREC: register_die_notifier failed: %d\n", ret);

	ret = atomic_notifier_chain_register(&panic_notifier_list,
					     &tgrec_panic_nb);
	if (ret)
		pr_err("TGREC: panic notifier registration failed: %d\n", ret);

	ret = register_reboot_notifier(&tgrec_reboot_nb);
	if (ret)
		pr_err("TGREC: reboot notifier registration failed: %d\n", ret);

	tgrec_record(TGREC_TYPE_BOOT, "NOTIFIERS_READY");
	return 0;
}
core_initcall(tgrec_notifier_init);

static atomic_t tgrec_hb_count = ATOMIC_INIT(0);
static void tgrec_heartbeat_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(tgrec_heartbeat_work, tgrec_heartbeat_fn);

static void tgrec_heartbeat_fn(struct work_struct *work)
{
	int n = atomic_inc_return(&tgrec_hb_count);

	tgrec_record(TGREC_TYPE_HEARTBEAT,
		     "HB n=%d j=%lu online=%u comm=%s",
		     n, jiffies, num_online_cpus(), current->comm);
	if (n < 60)
		schedule_delayed_work(&tgrec_heartbeat_work, HZ);
}

static int __init tgrec_late_init(void)
{
	tgrec_record(TGREC_TYPE_BOOT, "LATE_INIT");
	schedule_delayed_work(&tgrec_heartbeat_work, HZ);
	return 0;
}
late_initcall(tgrec_late_init);
'''

MAKE_MARKER = "# A52 touchGrass persistent crash recorder"
MAKE_ENTRY = f"\n{MAKE_MARKER}\nobj-y += a52_touchgrass_recorder.o\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kernel", type=Path)
    args = ap.parse_args()

    root = args.kernel.resolve()
    src = root / "drivers/misc/a52_touchgrass_recorder.c"
    mk = root / "drivers/misc/Makefile"

    if not mk.is_file():
        raise SystemExit(f"missing {mk}")

    src.write_text(SOURCE, encoding="utf-8")
    text = mk.read_text(encoding="utf-8")
    if MAKE_MARKER not in text:
        mk.write_text(text.rstrip() + MAKE_ENTRY, encoding="utf-8")

    checks = {
        "build_identity": 'TGREC_BUILD_ID          "TG83F1"' in SOURCE,
        "phase83_identity": "phase83 llvm17 fixed-eevdf subprog-call-fix" in SOURCE,
        "fixed_phys": "0xB1B00000ULL" in SOURCE,
        "clear_stale_region": "memset(tgrec_mem, 0, TGREC_SIZE);" in SOURCE,
        "triple_bank": "TGREC_BANKS             3U" in SOURCE,
        "crc32c": "0x82f63b78U" in SOURCE,
        "die_notifier": "register_die_notifier" in SOURCE,
        "panic_notifier": "panic_notifier_list" in SOURCE,
        "heartbeat": "tgrec_heartbeat_fn" in SOURCE,
        "makefile": MAKE_MARKER in mk.read_text(encoding="utf-8"),
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise SystemExit("recorder staging audit failed: " + ", ".join(failed))

    print("touchgrass_persistent_recorder=staged")
    for k in sorted(checks):
        print(f"{k}=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
