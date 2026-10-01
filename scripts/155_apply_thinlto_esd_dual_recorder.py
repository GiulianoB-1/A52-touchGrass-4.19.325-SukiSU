#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_P155_THINLTO_ESD_DUAL_RECORDER_V1"
HDR = Path("include/linux/a52_p155_esd_recorder.h")
SRC = Path("drivers/misc/a52_p155_esd_recorder.c")
MAKE = Path("drivers/misc/Makefile")
SS = Path("techpack/display/msm/samsung/ss_dsi_panel_common.c")
SDE = Path("techpack/display/msm/sde/sde_connector.c")
DISPLAY = Path("techpack/display/msm/dsi/dsi_display.c")
PANEL = Path("techpack/display/msm/dsi/dsi_panel.c")

HEADER = r'''/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _LINUX_A52_P155_ESD_RECORDER_H
#define _LINUX_A52_P155_ESD_RECORDER_H

#include <linux/types.h>

struct dsi_display;

void a52_p155_recordf(const char *fmt, ...);
void a52_p155_snapshot_display(struct dsi_display *display, u32 tag);

#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * A52 P155 ThinLTO ESD dual persistent recorder.
 *
 * Every focused record is mirrored:
 *   1. synchronously to persistent RAM 0xB1B00000..0xB1BFFFFF
 *   2. asynchronously to Samsung /dev/block/by-name/debug + 0x800000
 *
 * Real A52 debug.bin captures show MiB 8 and 9 are empty. P155 uses only
 * the first 1 MiB of that empty tail, preserving Samsung's native RWC/crash
 * stream in the first 8 MiB. No block I/O occurs in display/IRQ hot paths.
 */
#include <linux/atomic.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/sizes.h>
#include <linux/spinlock.h>
#include <linux/stdarg.h>
#include <linux/string.h>
#include <linux/vmalloc.h>
#include <linux/workqueue.h>
#include <asm/barrier.h>
#include <asm/cacheflush.h>

#include <linux/a52_p155_esd_recorder.h>

#define A52_P155_DEBUG_OFFSET       0x00800000ULL
#define A52_P155_DEBUG_FREE_BYTES   (2U * SZ_1M)
#define A52_P155_RAM_PHYS           0xB1B00000ULL
#define A52_P155_RAM_BYTES          SZ_1M
#define A52_P155_HEADER_BYTES       SZ_4K
#define A52_P155_RECORD_BYTES       128U
#define A52_P155_CAPACITY           ((A52_P155_RAM_BYTES - A52_P155_HEADER_BYTES) / A52_P155_RECORD_BYTES)
#define A52_P155_MAGIC              0x3535314453453241ULL /* A2ESD155 */
#define A52_P155_VERSION            1U
#define A52_P155_RECORD_COMMIT      0x155c0de5U
#define A52_P155_HEADER_COMMIT      0x155b0071U
#define A52_P155_HEARTBEATS         40U

struct a52_p155_header {
	u64 magic;
	u32 version;
	u32 phase;
	u64 boot_id;
	u64 global_seq;
	u32 count;
	u32 capacity;
	u32 record_bytes;
	u32 disk_armed;
	s32 disk_last_rc;
	u32 disk_gen;
	u32 disk_written_gen;
	u32 disk_retry;
	u32 dropped;
	u32 commit;
} __packed;

struct a52_p155_record {
	u64 seq;
	u64 ts_ns;
	u64 boot_id;
	u32 phase;
	u16 cpu;
	u16 len;
	u32 pid;
	char comm[16];
	char text[68];
	u32 commit;
	u32 reserved;
} __packed;

static DEFINE_SPINLOCK(a52_p155_lock);
static void *a52_p155_ram;
static u8 *a52_p155_disk_stage;
static u64 a52_p155_boot_id;
static u64 a52_p155_seq;
static u32 a52_p155_count;
static u32 a52_p155_dropped;
static u32 a52_p155_disk_armed;
static s32 a52_p155_disk_last_rc = -EAGAIN;
static atomic_t a52_p155_ready = ATOMIC_INIT(0);
static atomic_t a52_p155_disk_gen = ATOMIC_INIT(0);
static atomic_t a52_p155_disk_written_gen = ATOMIC_INIT(0);
static atomic_t a52_p155_disk_retry = ATOMIC_INIT(0);
static atomic_t a52_p155_hb_count = ATOMIC_INIT(0);

static void a52_p155_persist(void *addr, size_t len)
{
	if (!addr || !len)
		return;
	__flush_dcache_area(addr, len);
	wmb();
	dsb(sy);
}

static void a52_p155_fill_header(struct a52_p155_header *h)
{
	memset(h, 0, sizeof(*h));
	h->magic = A52_P155_MAGIC;
	h->version = A52_P155_VERSION;
	h->phase = 155U;
	h->boot_id = a52_p155_boot_id;
	h->global_seq = a52_p155_seq;
	h->count = a52_p155_count;
	h->capacity = A52_P155_CAPACITY;
	h->record_bytes = A52_P155_RECORD_BYTES;
	h->disk_armed = a52_p155_disk_armed;
	h->disk_last_rc = a52_p155_disk_last_rc;
	h->disk_gen = (u32)atomic_read(&a52_p155_disk_gen);
	h->disk_written_gen = (u32)atomic_read(&a52_p155_disk_written_gen);
	h->disk_retry = (u32)atomic_read(&a52_p155_disk_retry);
	h->dropped = a52_p155_dropped;
	h->commit = A52_P155_HEADER_COMMIT;
}

static void a52_p155_sync_header_locked(void)
{
	struct a52_p155_header h;

	a52_p155_fill_header(&h);
	if (a52_p155_disk_stage)
		memcpy(a52_p155_disk_stage, &h, sizeof(h));
	if (a52_p155_ram) {
		memcpy(a52_p155_ram, &h, sizeof(h));
		a52_p155_persist(a52_p155_ram, sizeof(h));
	}
}

static void a52_p155_disk_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_p155_disk_work, a52_p155_disk_workfn);

static void a52_p155_queue_disk(unsigned long delay_ms)
{
	atomic_inc(&a52_p155_disk_gen);
	mod_delayed_work(system_unbound_wq, &a52_p155_disk_work,
			 msecs_to_jiffies(delay_ms));
}

void a52_p155_recordf(const char *fmt, ...)
{
	struct a52_p155_record r;
	unsigned long flags;
	va_list ap;
	u32 index;
	bool queue = false;

	if (!fmt || !atomic_read(&a52_p155_ready))
		return;

	memset(&r, 0, sizeof(r));
	r.ts_ns = ktime_get_boottime_ns();
	r.phase = 155U;
	r.cpu = (u16)raw_smp_processor_id();
	r.pid = (u32)task_pid_nr(current);
	strlcpy(r.comm, current->comm, sizeof(r.comm));
	va_start(ap, fmt);
	r.len = (u16)vscnprintf(r.text, sizeof(r.text), fmt, ap);
	va_end(ap);
	r.commit = A52_P155_RECORD_COMMIT;

	spin_lock_irqsave(&a52_p155_lock, flags);
	if (a52_p155_count >= A52_P155_CAPACITY) {
		a52_p155_dropped++;
		a52_p155_sync_header_locked();
		spin_unlock_irqrestore(&a52_p155_lock, flags);
		return;
	}

	index = a52_p155_count;
	r.seq = ++a52_p155_seq;
	r.boot_id = a52_p155_boot_id;

	if (a52_p155_disk_stage)
		memcpy(a52_p155_disk_stage + A52_P155_HEADER_BYTES +
		       (size_t)index * A52_P155_RECORD_BYTES, &r, sizeof(r));

	if (a52_p155_ram) {
		void *dst = (u8 *)a52_p155_ram + A52_P155_HEADER_BYTES +
			    (size_t)index * A52_P155_RECORD_BYTES;
		memcpy(dst, &r, sizeof(r));
		a52_p155_persist(dst, sizeof(r));
	}

	a52_p155_count++;
	a52_p155_sync_header_locked();
	queue = a52_p155_disk_armed && a52_p155_disk_stage;
	spin_unlock_irqrestore(&a52_p155_lock, flags);

	if (queue)
		a52_p155_queue_disk(0);
}
EXPORT_SYMBOL_GPL(a52_p155_recordf);

static struct file *a52_p155_open_debug(void)
{
	struct file *file;

	file = filp_open("/dev/block/by-name/debug",
			 O_WRONLY | O_LARGEFILE | O_DSYNC, 0);
	if (!IS_ERR(file))
		return file;

	return filp_open("/dev/block/sda8",
			 O_WRONLY | O_LARGEFILE | O_DSYNC, 0);
}

static void a52_p155_disk_workfn(struct work_struct *work)
{
	struct file *file;
	unsigned long flags;
	unsigned int generation;
	unsigned int count;
	size_t used;
	size_t bytes;
	loff_t pos = (loff_t)A52_P155_DEBUG_OFFSET;
	ssize_t written;
	int rc;
	int retry;

	(void)work;
	if (!READ_ONCE(a52_p155_disk_armed) || !READ_ONCE(a52_p155_disk_stage))
		return;

	generation = (unsigned int)atomic_read(&a52_p155_disk_gen);
	spin_lock_irqsave(&a52_p155_lock, flags);
	count = a52_p155_count;
	spin_unlock_irqrestore(&a52_p155_lock, flags);

	used = A52_P155_HEADER_BYTES + (size_t)count * A52_P155_RECORD_BYTES;
	bytes = ALIGN(used, SZ_4K);
	if (bytes > A52_P155_RAM_BYTES)
		bytes = A52_P155_RAM_BYTES;

	file = a52_p155_open_debug();
	if (IS_ERR(file)) {
		rc = PTR_ERR(file);
		goto retry;
	}

	written = kernel_write(file, a52_p155_disk_stage, bytes, &pos);
	rc = (written == (ssize_t)bytes) ? vfs_fsync(file, 0) : -EIO;
	filp_close(file, NULL);
	if (rc)
		goto retry;

	atomic_set(&a52_p155_disk_written_gen, generation);
	atomic_set(&a52_p155_disk_retry, 0);
	spin_lock_irqsave(&a52_p155_lock, flags);
	a52_p155_disk_last_rc = 0;
	a52_p155_sync_header_locked();
	spin_unlock_irqrestore(&a52_p155_lock, flags);

	if ((unsigned int)atomic_read(&a52_p155_disk_gen) != generation)
		mod_delayed_work(system_unbound_wq, &a52_p155_disk_work, 0);
	return;

retry:
	retry = atomic_inc_return(&a52_p155_disk_retry);
	spin_lock_irqsave(&a52_p155_lock, flags);
	a52_p155_disk_last_rc = rc;
	a52_p155_sync_header_locked();
	spin_unlock_irqrestore(&a52_p155_lock, flags);
	if (retry <= 240)
		mod_delayed_work(system_unbound_wq, &a52_p155_disk_work,
				 msecs_to_jiffies(250));
}

static void a52_p155_heartbeat_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_p155_heartbeat_work, a52_p155_heartbeat_workfn);

static void a52_p155_heartbeat_workfn(struct work_struct *work)
{
	unsigned int n;

	(void)work;
	n = (unsigned int)atomic_inc_return(&a52_p155_hb_count);
	a52_p155_recordf("P155 HB n=%u cnt=%u dg=%u dw=%u rc=%d",
		n, READ_ONCE(a52_p155_count),
		(unsigned int)atomic_read(&a52_p155_disk_gen),
		(unsigned int)atomic_read(&a52_p155_disk_written_gen),
		READ_ONCE(a52_p155_disk_last_rc));
	if (n < A52_P155_HEARTBEATS)
		mod_delayed_work(system_unbound_wq, &a52_p155_heartbeat_work,
				 msecs_to_jiffies(2000));
}

static int __init a52_p155_init(void)
{
	struct a52_p155_header old;
	unsigned long flags;

	BUILD_BUG_ON(sizeof(struct a52_p155_record) != A52_P155_RECORD_BYTES);
	a52_p155_disk_stage = vzalloc(A52_P155_RAM_BYTES);
	a52_p155_ram = memremap(A52_P155_RAM_PHYS, A52_P155_RAM_BYTES,
				MEMREMAP_WB);

	memset(&old, 0, sizeof(old));
	if (a52_p155_ram)
		memcpy(&old, a52_p155_ram, sizeof(old));
	if (old.magic == A52_P155_MAGIC &&
	    old.version == A52_P155_VERSION &&
	    old.phase == 155U &&
	    old.commit == A52_P155_HEADER_COMMIT)
		a52_p155_boot_id = old.boot_id + 1U;
	else
		a52_p155_boot_id = 1U;

	if (a52_p155_ram) {
		memset(a52_p155_ram, 0, A52_P155_RAM_BYTES);
		a52_p155_persist(a52_p155_ram, A52_P155_RAM_BYTES);
	}

	spin_lock_irqsave(&a52_p155_lock, flags);
	a52_p155_seq = 0;
	a52_p155_count = 0;
	a52_p155_dropped = 0;
	a52_p155_disk_armed = 0;
	a52_p155_disk_last_rc = -EAGAIN;
	a52_p155_sync_header_locked();
	spin_unlock_irqrestore(&a52_p155_lock, flags);

	if (a52_p155_ram || a52_p155_disk_stage) {
		atomic_set(&a52_p155_ready, 1);
		a52_p155_recordf("P155 BOOT ram=%d diskstage=%d cap=%u",
			!!a52_p155_ram, !!a52_p155_disk_stage,
			(unsigned int)A52_P155_CAPACITY);
	}
	return 0;
}
core_initcall_sync(a52_p155_init);

static int __init a52_p155_disk_late_arm(void)
{
	unsigned long flags;

	if (!atomic_read(&a52_p155_ready))
		return 0;

	spin_lock_irqsave(&a52_p155_lock, flags);
	a52_p155_disk_armed = 1U;
	a52_p155_sync_header_locked();
	spin_unlock_irqrestore(&a52_p155_lock, flags);

	a52_p155_recordf("P155 DISKARM off=%llx free=%u",
		(unsigned long long)A52_P155_DEBUG_OFFSET,
		(unsigned int)A52_P155_DEBUG_FREE_BYTES);
	a52_p155_queue_disk(0);
	mod_delayed_work(system_unbound_wq, &a52_p155_heartbeat_work,
			 msecs_to_jiffies(1000));
	return 0;
}
late_initcall_sync(a52_p155_disk_late_arm);

static const char a52_p155_image_marker[] __used =
	"A52_P155_THINLTO_ESD_DUAL_RECORDER_V1";

MODULE_DESCRIPTION("A52 P155 ThinLTO ESD dual persistent recorder");
MODULE_LICENSE("GPL v2");
'''

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"P155 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

def add_include(text: str, inc: str) -> str:
    if inc in text:
        return text
    lines = text.splitlines(keepends=True)
    pos = 0
    last = -1
    for line in lines:
        if line.startswith("#include"):
            last = pos + len(line)
        elif last >= 0 and line.strip() and not line.lstrip().startswith(("/*", "*", "//", "#if", "#endif")):
            break
        pos += len(line)
    if last < 0:
        raise SystemExit("P155 include block not found")
    return text[:last] + inc + text[last:]

def bounds(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise SystemExit("P155 function missing: " + signature)
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit("P155 function brace missing: " + signature)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise SystemExit("P155 function end missing: " + signature)

def patch_fn(text: str, signature: str, transform) -> str:
    a, b = bounds(text, signature)
    old = text[a:b]
    new = transform(old)
    if new == old:
        raise SystemExit("P155 function unchanged: " + signature)
    return text[:a] + new + text[b:]

SNAPSHOT = r'''
/* A52_P155_THINLTO_ESD_DUAL_RECORDER_V1: read-only DSI snapshot.
 * Call only while the display is expected to be powered.
 */
void a52_p155_snapshot_display(struct dsi_display *display, u32 tag)
{
	struct dsi_display_ctrl *dc;
	struct dsi_ctrl *ctrl;
	struct dsi_ctrl_hw *hw;

	if (!display || !display->ctrl_count ||
	    display->cmd_master_idx >= display->ctrl_count)
		return;
	dc = &display->ctrl[display->cmd_master_idx];
	ctrl = dc->ctrl;
	if (!ctrl || !ctrl->hw.base)
		return;
	hw = &ctrl->hw;

	a52_p155_recordf("P155 H0 t=%x irq=%d mask=%x ps=%u ce=%u ve=%u",
		tag, ctrl->irq_info.irq_num, ctrl->irq_info.irq_stat_mask,
		ctrl->current_state.power_state,
		ctrl->current_state.cmd_engine_state,
		ctrl->current_state.vid_engine_state);
	a52_p155_recordf("P155 H1 t=%x st=%x fi=%x cl=%x in=%x",
		tag, DSI_R32(hw, DSI_STATUS), DSI_R32(hw, DSI_FIFO_STATUS),
		DSI_R32(hw, DSI_CLK_STATUS), DSI_R32(hw, DSI_INT_CTRL));
	a52_p155_recordf("P155 H2 t=%x la=%x dm=%x of=%x ln=%x",
		tag, DSI_R32(hw, DSI_LANE_STATUS),
		DSI_R32(hw, DSI_COMMAND_MODE_DMA_CTRL),
		DSI_R32(hw, DSI_DMA_CMD_OFFSET),
		DSI_R32(hw, DSI_DMA_CMD_LENGTH));
	a52_p155_recordf("P155 H3 t=%x ae=%x to=%x dt=%d dl=%u",
		tag, DSI_R32(hw, DSI_ACK_ERR_STATUS),
		DSI_R32(hw, DSI_TIMEOUT_STATUS),
		atomic_read(&ctrl->dma_irq_trig), ctrl->cmd_len);
}
EXPORT_SYMBOL_GPL(a52_p155_snapshot_display);
'''

def patch_ss(text: str) -> str:
    if MARK in text:
        return text
    text = add_include(text, "#include <linux/a52_p155_esd_recorder.h>\n")

    def f(fn: str) -> str:
        anchor = '''\tint i;
\tint rc = 0;

\tif (!vdd->esd_recovery.is_enabled_esd_recovery) {'''
        repl = '''\tint i;
\tint rc = 0;

\ta52_p155_recordf("P155 E0 irq=%d st=%d cnt=%d en=%d g0=%d g1=%d",
\t\tirq, vdd->panel_state, vdd->panel_recovery_cnt,
\t\tvdd->display_enabled,
\t\tgpio_is_valid(vdd->esd_recovery.esd_gpio[0]) ?
\t\t\tgpio_get_value(vdd->esd_recovery.esd_gpio[0]) : -1,
\t\tgpio_is_valid(vdd->esd_recovery.esd_gpio[1]) ?
\t\t\tgpio_get_value(vdd->esd_recovery.esd_gpio[1]) : -1);
\ta52_p155_snapshot_display(GET_DSI_DISPLAY(vdd), 0xE0);

\tif (!vdd->esd_recovery.is_enabled_esd_recovery) {'''
        fn = one(fn, anchor, repl, "ESD entry")
        fn = one(fn,
            "\tesd_irq_enable(false, true, (void *)vdd);\n",
            "\tesd_irq_enable(false, true, (void *)vdd);\n"
            "\ta52_p155_recordf(\"P155 E1 irqoff cnt=%d\", vdd->panel_recovery_cnt);\n",
            "ESD irq off")
        fn = one(fn,
            "\tschedule_work(&conn->status_work.work);\n",
            "\tschedule_work(&conn->status_work.work);\n"
            "\ta52_p155_recordf(\"P155 E2 sched pend=%d dead=%d\",\n"
            "\t\tatomic_read(&GET_DSI_PANEL(vdd)->esd_recovery_pending),\n"
            "\t\tvdd->panel_dead);\n",
            "ESD schedule")
        fn = one(fn,
            "end:\n\treturn IRQ_HANDLED;\n",
            "end:\n\ta52_p155_recordf(\"P155 E3 exit irq=%d dead=%d cnt=%d\",\n"
            "\t\tirq, vdd->panel_dead, vdd->panel_recovery_cnt);\n"
            "\treturn IRQ_HANDLED;\n",
            "ESD exit")
        return fn

    text = patch_fn(text, "irqreturn_t esd_irq_handler(", f)
    return text + "\n/* " + MARK + ": Samsung ESD IRQ hooks. */\n"

def patch_sde(text: str) -> str:
    if MARK in text:
        return text
    text = add_include(text, "#include <linux/a52_p155_esd_recorder.h>\n")

    def dead(fn: str) -> str:
        fn = one(fn,
            "\tif (!conn)\n\t\treturn;\n",
            "\tif (!conn)\n\t\treturn;\n\n"
            "\ta52_p155_recordf(\"P155 PD0 dead=%d skip=%d cid=%d eid=%d\",\n"
            "\t\tconn->panel_dead, skip_pre_kickoff, conn->base.base.id,\n"
            "\t\tconn->encoder ? conn->encoder->base.id : -1);\n"
            "\tif (conn->display)\n"
            "\t\ta52_p155_snapshot_display(conn->display, 0xD0);\n",
            "panel-dead entry")
        fn = one(fn,
            "\tconn->panel_dead = true;\n",
            "\tconn->panel_dead = true;\n"
            "\ta52_p155_recordf(\"P155 PD1 setdead cid=%d\", conn->base.base.id);\n",
            "panel-dead set")
        return fn

    def esd(fn: str) -> str:
        fn = one(fn,
            "\tdisplay = sde_conn->display;\n",
            "\tdisplay = sde_conn->display;\n"
            "\ta52_p155_recordf(\"P155 ES0 pend=%d dead=%d\",\n"
            "\t\tatomic_read(&display->panel->esd_recovery_pending),\n"
            "\t\tsde_conn->panel_dead);\n",
            "ESD status entry")
        needle = "#endif\n\tmutex_unlock(&sde_conn->lock);\n"
        fn = one(fn, needle,
            "#endif\n"
            "\ta52_p155_recordf(\"P155 ES1 chk=%d pend=%d dead=%d\", ret,\n"
            "\t\tatomic_read(&display->panel->esd_recovery_pending),\n"
            "\t\tsde_conn->panel_dead);\n"
            "\tmutex_unlock(&sde_conn->lock);\n",
            "ESD status result")
        fn = one(fn,
            "\tSDE_EVT32(ret);\n\n\treturn ret;\n",
            "\tSDE_EVT32(ret);\n"
            "\ta52_p155_recordf(\"P155 ES2 ret=%d dead=%d\", ret, sde_conn->panel_dead);\n\n"
            "\treturn ret;\n",
            "ESD status exit")
        return fn

    text = patch_fn(text, "static void _sde_connector_report_panel_dead(", dead)
    text = patch_fn(text, "int sde_connector_esd_status(", esd)
    return text + "\n/* " + MARK + ": connector ESD/PANEL_DEAD hooks. */\n"

def patch_display(text: str) -> str:
    if MARK in text:
        return text
    text = add_include(text, "#include <linux/a52_p155_esd_recorder.h>\n")
    text = one(text, '#include "dsi_ctrl_hw.h"\n',
               '#include "dsi_ctrl_hw.h"\n#include "dsi_ctrl_reg.h"\n#include "dsi_hw.h"\n',
               "DSI register includes")
    text = one(text, "#define MAX_NAME_SIZE\t64\n",
               "#define MAX_NAME_SIZE\t64\n" + SNAPSHOT + "\n",
               "snapshot helper")

    def prep(fn: str) -> str:
        fn = one(fn,
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "\ta52_p155_recordf(\"P155 PR0 prep splash=%d poms=%d mode=%x\",\n"
            "\t\tdisplay->is_cont_splash_enabled, display->poms_pending,\n"
            "\t\tdisplay->config.panel_mode);\n"
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "prepare entry")
        replacements = [
            ("\tdsi_display_ctrl_isr_configure(display, true);\n",
             "\tdsi_display_ctrl_isr_configure(display, true);\n"
             "\ta52_p155_recordf(\"P155 PR1 isr-on\");\n", "prepare isr"),
            ("\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n\t\t\tDSI_CORE_CLK, DSI_CLK_ON);\n",
             "\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n\t\t\tDSI_CORE_CLK, DSI_CLK_ON);\n"
             "\ta52_p155_recordf(\"P155 PR2 coreclk rc=%d\", rc);\n", "prepare core"),
            ("\t\trc = dsi_display_phy_enable(display);\n",
             "\t\trc = dsi_display_phy_enable(display);\n"
             "\t\ta52_p155_recordf(\"P155 PR3 phy rc=%d\", rc);\n", "prepare phy"),
            ("\trc = dsi_display_ctrl_init(display);\n",
             "\trc = dsi_display_ctrl_init(display);\n"
             "\ta52_p155_recordf(\"P155 PR4 ctrl rc=%d\", rc);\n", "prepare ctrl"),
            ("\trc = dsi_display_ctrl_host_enable(display);\n",
             "\trc = dsi_display_ctrl_host_enable(display);\n"
             "\ta52_p155_recordf(\"P155 PR5 host rc=%d\", rc);\n", "prepare host"),
            ("\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n\t\t\tDSI_LINK_CLK, DSI_CLK_ON);\n",
             "\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n\t\t\tDSI_LINK_CLK, DSI_CLK_ON);\n"
             "\ta52_p155_recordf(\"P155 PR6 link rc=%d\", rc);\n"
             "\tif (!rc)\n\t\ta52_p155_snapshot_display(display, 0xA6);\n", "prepare link"),
            ("\t\t\trc = dsi_panel_prepare(display->panel);\n",
             "\t\t\ta52_p155_recordf(\"P155 PR7 panel-pre\");\n"
             "\t\t\trc = dsi_panel_prepare(display->panel);\n"
             "\t\t\ta52_p155_recordf(\"P155 PR8 panel rc=%d\", rc);\n", "prepare panel"),
            ("\treturn rc;\n",
             "\ta52_p155_recordf(\"P155 PR9 exit rc=%d\", rc);\n"
             "\treturn rc;\n", "prepare exit"),
        ]
        for old, new, label in replacements:
            fn = one(fn, old, new, label)
        return fn

    def disable(fn: str) -> str:
        fn = one(fn,
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "\ta52_p155_recordf(\"P155 DS0 disable mode=%x poms=%d\",\n"
            "\t\tdisplay->config.panel_mode, display->poms_pending);\n"
            "\ta52_p155_snapshot_display(display, 0xB0);\n"
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "disable entry")
        fn = one(fn,
            "\tif (!display->poms_pending) {\n\t\trc = dsi_panel_disable(display->panel);\n",
            "\tif (!display->poms_pending) {\n"
            "\t\ta52_p155_recordf(\"P155 DS1 panel-disable\");\n"
            "\t\trc = dsi_panel_disable(display->panel);\n"
            "\t\ta52_p155_recordf(\"P155 DS2 panel rc=%d\", rc);\n",
            "disable panel")
        fn = one(fn,
            "\treturn rc;\n",
            "\ta52_p155_recordf(\"P155 DS3 exit rc=%d\", rc);\n"
            "\treturn rc;\n",
            "disable exit")
        return fn

    def unprep(fn: str) -> str:
        fn = one(fn,
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "\ta52_p155_recordf(\"P155 UP0 unprep poms=%d ulps=%d\",\n"
            "\t\tdisplay->poms_pending, display->panel->ulps_suspend_enabled);\n"
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "unprepare entry")
        replacements = [
            ("\t\trc = dsi_panel_unprepare(display->panel);\n",
             "\t\ta52_p155_recordf(\"P155 UP1 panel-unprep\");\n"
             "\t\trc = dsi_panel_unprepare(display->panel);\n"
             "\t\ta52_p155_recordf(\"P155 UP2 panel rc=%d\", rc);\n", "unprepare panel"),
            ("\trc = dsi_display_ctrl_host_disable(display);\n",
             "\trc = dsi_display_ctrl_host_disable(display);\n"
             "\ta52_p155_recordf(\"P155 UP3 hostoff rc=%d\", rc);\n", "unprepare host"),
            ("\trc = dsi_display_ctrl_deinit(display);\n",
             "\trc = dsi_display_ctrl_deinit(display);\n"
             "\ta52_p155_recordf(\"P155 UP4 ctrloff rc=%d\", rc);\n", "unprepare ctrl"),
            ("\t\trc = dsi_display_phy_disable(display);\n",
             "\t\trc = dsi_display_phy_disable(display);\n"
             "\t\ta52_p155_recordf(\"P155 UP5 phyoff rc=%d\", rc);\n", "unprepare phy"),
            ("\tdsi_display_ctrl_isr_configure(display, false);\n",
             "\tdsi_display_ctrl_isr_configure(display, false);\n"
             "\ta52_p155_recordf(\"P155 UP6 isr-off\");\n", "unprepare isr"),
            ("\treturn rc;\n",
             "\ta52_p155_recordf(\"P155 UP7 exit rc=%d\", rc);\n"
             "\treturn rc;\n", "unprepare exit"),
        ]
        for old, new, label in replacements:
            fn = one(fn, old, new, label)
        return fn

    def enable(fn: str) -> str:
        fn = one(fn,
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "\ta52_p155_recordf(\"P155 EN0 enable splash=%d mode=%x\",\n"
            "\t\tdisplay->is_cont_splash_enabled, display->config.panel_mode);\n"
            "\tSDE_EVT32(SDE_EVTLOG_FUNC_ENTRY);\n",
            "enable entry")
        enable_anchor = "\t\trc = dsi_panel_enable(display->panel);\n"
        enable_count = fn.count(enable_anchor)
        if enable_count < 1:
            raise SystemExit("P155 enable panel: no matching enable site")
        fn = fn.replace(
            enable_anchor,
            "\t\ta52_p155_recordf(\"P155 EN1 panel-enable\");\n"
            "\t\trc = dsi_panel_enable(display->panel);\n"
            "\t\ta52_p155_recordf(\"P155 EN2 panel rc=%d\", rc);\n")
        final = "\n\treturn rc;\n}"
        if final not in fn:
            raise SystemExit("P155 enable final return missing")
        fn = fn.replace(final,
            "\n\ta52_p155_recordf(\"P155 EN3 exit rc=%d\", rc);\n"
            "\treturn rc;\n}", 1)
        return fn

    text = patch_fn(text, "int dsi_display_prepare(", prep)
    text = patch_fn(text, "int dsi_display_disable(", disable)
    text = patch_fn(text, "int dsi_display_unprepare(", unprep)
    text = patch_fn(text, "int dsi_display_enable(", enable)
    return text + "\n/* " + MARK + ": DSI recovery lifecycle + HW snapshot hooks. */\n"

def patch_panel(text: str) -> str:
    if MARK in text:
        return text
    text = add_include(text, "#include <linux/a52_p155_esd_recorder.h>\n")

    def on(fn: str) -> str:
        fn = one(fn,
            "\tstruct samsung_display_driver_data *vdd = panel->panel_private;\n",
            "\tstruct samsung_display_driver_data *vdd = panel->panel_private;\n"
            "\ta52_p155_recordf(\"P155 PO0 on st=%d dead=%d aot=%d regs=%d\",\n"
            "\t\tvdd->panel_state, vdd->panel_dead, vdd->aot_enable,\n"
            "\t\tpanel->power_info.count);\n",
            "power-on entry")
        fn = one(fn,
            '\t\tDSI_INFO("timing_check: panel power on\\n");\n'
            '\t\trc = dsi_pwr_enable_regulator(&panel->power_info, true);\n',
            '\t\tDSI_INFO("timing_check: panel power on\\n");\n'
            '\t\ta52_p155_recordf("P155 PO1 vreg-on begin");\n'
            '\t\trc = dsi_pwr_enable_regulator(&panel->power_info, true);\n'
            '\t\ta52_p155_recordf("P155 PO2 vreg-on rc=%d", rc);\n',
            "power-on regulator")
        fn = one(fn,
            "\tss_panel_power_ctrl(vdd, true);\n",
            "\ta52_p155_recordf(\"P155 PO3 ss-power begin\");\n"
            "\tss_panel_power_ctrl(vdd, true);\n"
            "\ta52_p155_recordf(\"P155 PO4 ss-power done\");\n",
            "power-on samsung power")
        fn = one(fn,
            "\trc = dsi_panel_set_pinctrl_state(panel, true);\n",
            "\ta52_p155_recordf(\"P155 PO5 pinctrl begin\");\n"
            "\trc = dsi_panel_set_pinctrl_state(panel, true);\n"
            "\ta52_p155_recordf(\"P155 PO6 pinctrl rc=%d\", rc);\n",
            "power-on pinctrl")
        fn = one(fn,
            "\t\trc = dsi_panel_reset(panel);\n",
            "\t\ta52_p155_recordf(\"P155 PO7 reset begin\");\n"
            "\t\trc = dsi_panel_reset(panel);\n"
            "\t\ta52_p155_recordf(\"P155 PO8 reset rc=%d\", rc);\n",
            "power-on reset")
        fn = one(fn,
            "exit:\n\treturn rc;\n",
            "exit:\n\ta52_p155_recordf(\"P155 PO9 exit rc=%d\", rc);\n"
            "\treturn rc;\n",
            "power-on exit")
        return fn

    def off(fn: str) -> str:
        fn = one(fn,
            "\tstruct samsung_display_driver_data *vdd = panel->panel_private;\n",
            "\tstruct samsung_display_driver_data *vdd = panel->panel_private;\n"
            "\ta52_p155_recordf(\"P155 PF0 off st=%d dead=%d\",\n"
            "\t\tvdd->panel_state, vdd->panel_dead);\n",
            "power-off entry")
        tail = "\treturn rc;\n"
        if fn.count(tail) < 1:
            raise SystemExit("P155 power-off: final return rc missing")
        pos = fn.rfind(tail)
        fn = (fn[:pos] +
              "\ta52_p155_recordf(\"P155 PF1 exit rc=%d\", rc);\n" +
              fn[pos:])
        return fn

    text = patch_fn(text, "int dsi_panel_power_on(", on)
    text = patch_fn(text, "int dsi_panel_power_off(", off)
    return text + "\n/* " + MARK + ": panel power sequencing hooks. */\n"

def stage(root: Path) -> None:
    for rel in (SS, SDE, DISPLAY, PANEL, MAKE):
        if not (root / rel).is_file():
            raise SystemExit("P155 source missing: " + str(rel))

    (root / HDR).write_text(HEADER)
    (root / SRC).write_text(SOURCE)

    mk = (root / MAKE).read_text()
    marker = "# A52 P155 ThinLTO ESD dual recorder"
    if marker not in mk:
        mk = mk.rstrip() + "\n\n" + marker + "\nobj-y += a52_p155_esd_recorder.o\n"
        (root / MAKE).write_text(mk + "\n")

    for rel, fn in ((SS, patch_ss), (SDE, patch_sde),
                    (DISPLAY, patch_display), (PANEL, patch_panel)):
        p = root / rel
        p.write_text(fn(p.read_text(errors="replace")))

def validate(root: Path) -> None:
    combined = "\n".join((root / p).read_text(errors="replace")
                           for p in (SRC, SS, SDE, DISPLAY, PANEL, MAKE))
    required = (
        MARK,
        "A52_P155_DEBUG_OFFSET       0x00800000ULL",
        "A52_P155_RAM_PHYS           0xB1B00000ULL",
        "A52_P155_RECORD_BYTES       128U",
        "memremap(A52_P155_RAM_PHYS",
        'filp_open("/dev/block/by-name/debug"',
        'filp_open("/dev/block/sda8"',
        "system_unbound_wq",
        "late_initcall_sync(a52_p155_disk_late_arm);",
        "a52_p155_snapshot_display",
        "P155 E0",
        "P155 PD0",
        "P155 ES1",
        "P155 PR6",
        "P155 PO1",
        "P155 PO2",
        "P155 UP6",
        "obj-y += a52_p155_esd_recorder.o",
    )
    for token in required:
        if token not in combined:
            raise SystemExit("P155 validation missing: " + token)

    if combined.count("obj-y += a52_p155_esd_recorder.o") != 1:
        raise SystemExit("P155 recorder Makefile entry count wrong")
    if "kernel_write(file, a52_p155_disk_stage" not in combined:
        raise SystemExit("P155 Samsung transport missing")
    if "memcpy(dst, &r, sizeof(r));" not in combined:
        raise SystemExit("P155 RAM mirror missing")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    if not ns.check_only:
        stage(ns.root)
    validate(ns.root)
    print("P155 ThinLTO ESD dual recorder: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
