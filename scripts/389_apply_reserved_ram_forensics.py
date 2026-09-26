#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE389_RESERVED_RAM_FORENSICS_V1"
HDR = Path("include/linux/a52_ack_secure_flight_recorder.h")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
DRM = Path("drivers/gpu/drm/drm_atomic_helper.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase389 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


HEADER_BLOCK = r'''
/* A52_PHASE389_RESERVED_RAM_FORENSICS_V1
 *
 * Keep the existing 1 MiB ramoops/R48 recorder intact. Phase389 adds a second
 * recorder in Samsung-reserved DRAM 0xB1400000..0xB1AFFFFF (7 MiB). It never
 * writes /dev/block/by-name/debug. Recovery collects that partition read-only.
 */
enum a52_p389_event {
	A52_P389_DSI_WAIT_ENTER		= 0x1000,
	A52_P389_DSI_WAIT_EXIT		= 0x1001,
	A52_P389_DSI_FALLBACK_STATUS	= 0x1002,
	A52_P389_DSI_FALLBACK_BRANCH	= 0x1003,
	A52_P389_DSI_ISR		= 0x1004,
	A52_P389_DRM_MODESET_FAIL	= 0x2000,
	A52_P389_UFS_SAMPLE		= 0x3000,
	A52_P389_USB_STAGE		= 0x4000,
	A52_P389_FREEZE			= 0x5000,
};

void a52_p389_trace(u16 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3);

'''


RECORDER_BLOCK = r'''
/* A52_PHASE389_RESERVED_RAM_FORENSICS_V1
 *
 * Samsung/TouchGrass already treats this neighborhood as reserved debug DRAM.
 * The running A52 map supplied by the user shows System RAM resumes only at
 * 0xB1B00000, so 0xB1400000..0xB1AFFFFF is outside the page allocator.
 *
 * Layout:
 *   +0x000000..+0x000fff : metadata (4 KiB)
 *   +0x001000..+0x400fff : 4 MiB printk console ring
 *   +0x401000..+0x6fffff : structured trace ring (~3 MiB)
 *
 * The console mirrors TouchGrass sec_log_buf's safe model: ioremap_cache() +
 * CON_PRINTBUFFER. There is no UFS I/O. Structured events use a trylock and
 * per-record CRC32C; when contended, diagnostics are dropped rather than
 * delaying the DSI/IRQ path.
 */
#define A52_P389_PHYS			0xB1400000ULL
#define A52_P389_BYTES			(7U * SZ_1M)
#define A52_P389_HEADER_BYTES		SZ_4K
#define A52_P389_TEXT_OFF		A52_P389_HEADER_BYTES
#define A52_P389_TEXT_BYTES		(4U * SZ_1M)
#define A52_P389_TRACE_OFF		(A52_P389_TEXT_OFF + A52_P389_TEXT_BYTES)
#define A52_P389_TRACE_BYTES		(A52_P389_BYTES - A52_P389_TRACE_OFF)
#define A52_P389_MAGIC			0x393833524D415241ULL /* "ARAMR389" */
#define A52_P389_VERSION		1U

struct a52_p389_header {
	u64 magic;
	u32 version;
	u32 header_bytes;
	u64 phys;
	u64 bytes;
	u64 boot_id;
	u64 text_pos;
	u64 text_pos_inv;
	u64 trace_pos;
	u64 trace_pos_inv;
	u32 trace_seq;
	u32 trace_seq_inv;
	u64 dropped;
	u64 dropped_inv;
};

struct a52_p389_record {
	u64 ts_ns;
	u32 seq;
	u16 event;
	u16 cpu;
	u32 arg0;
	u32 arg1;
	u32 arg2;
	u32 arg3;
	u32 crc32c;
} __packed;

static const char a52_p389_marker[] __used =
	"A52_PHASE389_RESERVED_RAM_FORENSICS_V1";
static void __iomem *a52_p389_base;
static void __iomem *a52_p389_text;
static void __iomem *a52_p389_trace_base;
static DEFINE_SPINLOCK(a52_p389_text_lock);
static DEFINE_SPINLOCK(a52_p389_trace_lock);
static u64 a52_p389_text_pos;
static u64 a52_p389_trace_pos;
static u64 a52_p389_boot_id;
static u32 a52_p389_trace_seq;
static atomic64_t a52_p389_dropped = ATOMIC64_INIT(0);

static u32 a52_p389_crc32c(const void *buffer, size_t len)
{
	const u8 *bytes = buffer;
	u32 crc = ~0U;
	size_t index;
	unsigned int bit;

	for (index = 0; index < len; index++) {
		crc ^= bytes[index];
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^
				((crc & 1U) ? 0x82f63b78U : 0U);
	}
	return ~crc;
}

static void a52_p389_write_pair(size_t off, u64 value)
{
	u64 inv = ~value;

	memcpy_toio((u8 __iomem *)a52_p389_base + off, &value, sizeof(value));
	memcpy_toio((u8 __iomem *)a52_p389_base + off + sizeof(value),
		    &inv, sizeof(inv));
}

static void a52_p389_header_init(void)
{
	struct a52_p389_header old = { 0 };
	struct a52_p389_header h = { 0 };

	memcpy_fromio(&old, a52_p389_base, sizeof(old));
	if (old.magic == A52_P389_MAGIC &&
	    old.version == A52_P389_VERSION &&
	    old.boot_id && old.boot_id != ~0ULL)
		a52_p389_boot_id = old.boot_id + 1ULL;
	else
		a52_p389_boot_id = 1ULL;

	h.magic = A52_P389_MAGIC;
	h.version = A52_P389_VERSION;
	h.header_bytes = A52_P389_HEADER_BYTES;
	h.phys = A52_P389_PHYS;
	h.bytes = A52_P389_BYTES;
	h.boot_id = a52_p389_boot_id;
	h.text_pos = 0;
	h.text_pos_inv = ~0ULL;
	h.trace_pos = 0;
	h.trace_pos_inv = ~0ULL;
	h.trace_seq = 0;
	h.trace_seq_inv = ~0U;
	h.dropped = 0;
	h.dropped_inv = ~0ULL;

	memcpy_toio(a52_p389_base, &h, sizeof(h));
	a52_p389_text_pos = 0;
	a52_p389_trace_pos = 0;
	a52_p389_trace_seq = 0;
	atomic64_set(&a52_p389_dropped, 0);
}

static void a52_p389_console_write(struct console *con,
				   const char *s, unsigned int count)
{
	unsigned long flags;
	u64 pos;
	u32 first;

	(void)con;
	if (!a52_p389_text || !count)
		return;

	spin_lock_irqsave(&a52_p389_text_lock, flags);
	pos = a52_p389_text_pos % A52_P389_TEXT_BYTES;
	first = min_t(u32, count, A52_P389_TEXT_BYTES - (u32)pos);
	memcpy_toio((u8 __iomem *)a52_p389_text + pos, s, first);
	if (count > first)
		memcpy_toio(a52_p389_text, s + first, count - first);
	a52_p389_text_pos += count;
	a52_p389_write_pair(offsetof(struct a52_p389_header, text_pos),
			    a52_p389_text_pos);
	spin_unlock_irqrestore(&a52_p389_text_lock, flags);
}

static struct console a52_p389_console = {
	.name = "a52r389",
	.write = a52_p389_console_write,
	.flags = CON_PRINTBUFFER | CON_ENABLED | CON_ANYTIME,
	.index = -1,
};

void a52_p389_trace(u16 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3)
{
	struct a52_p389_record rec;
	unsigned long flags;
	u64 slots;
	u64 slot;
	u64 dropped;

	BUILD_BUG_ON(sizeof(struct a52_p389_record) != 36U);

	if (!a52_p389_trace_base)
		return;

	if (!spin_trylock_irqsave(&a52_p389_trace_lock, flags)) {
		atomic64_inc(&a52_p389_dropped);
		return;
	}

	rec.ts_ns = ktime_get_boottime_ns();
	rec.seq = ++a52_p389_trace_seq;
	rec.event = event;
	rec.cpu = (u16)raw_smp_processor_id();
	rec.arg0 = arg0;
	rec.arg1 = arg1;
	rec.arg2 = arg2;
	rec.arg3 = arg3;
	rec.crc32c = a52_p389_crc32c(&rec,
				     offsetof(struct a52_p389_record, crc32c));

	slots = A52_P389_TRACE_BYTES / sizeof(struct a52_p389_record);
	slot = a52_p389_trace_pos % slots;
	memcpy_toio((u8 __iomem *)a52_p389_trace_base +
		    slot * sizeof(struct a52_p389_record),
		    &rec, sizeof(rec));
	a52_p389_trace_pos++;

	a52_p389_write_pair(offsetof(struct a52_p389_header, trace_pos),
			    a52_p389_trace_pos);
	{
		u32 seq_pair[2] = { a52_p389_trace_seq, ~a52_p389_trace_seq };
		memcpy_toio((u8 __iomem *)a52_p389_base +
			    offsetof(struct a52_p389_header, trace_seq),
			    seq_pair, sizeof(seq_pair));
	}
	dropped = (u64)atomic64_read(&a52_p389_dropped);
	a52_p389_write_pair(offsetof(struct a52_p389_header, dropped), dropped);
	spin_unlock_irqrestore(&a52_p389_trace_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_p389_trace);

static int __init a52_p389_reserved_init(void)
{
	BUILD_BUG_ON(A52_P389_TRACE_OFF >= A52_P389_BYTES);
	BUILD_BUG_ON(sizeof(struct a52_p389_header) > A52_P389_HEADER_BYTES);

	a52_p389_base = ioremap_cache(A52_P389_PHYS, A52_P389_BYTES);
	if (!a52_p389_base) {
		a52_ackfr_record("P389 RAM map failed phys=%llx bytes=%x",
			(unsigned long long)A52_P389_PHYS, A52_P389_BYTES);
		return 0;
	}

	a52_p389_text = (u8 __iomem *)a52_p389_base + A52_P389_TEXT_OFF;
	a52_p389_trace_base =
		(u8 __iomem *)a52_p389_base + A52_P389_TRACE_OFF;
	a52_p389_header_init();
	register_console(&a52_p389_console);
	a52_ackfr_record("P389 RAM ready phys=%llx bytes=%x text=%x trace=%x boot=%llu",
		(unsigned long long)A52_P389_PHYS, A52_P389_BYTES,
		A52_P389_TEXT_BYTES, A52_P389_TRACE_BYTES,
		(unsigned long long)a52_p389_boot_id);
	return 0;
}
core_initcall_sync(a52_p389_reserved_init);

'''


def patch_header(text: str) -> str:
    if MARK in text:
        return text
    return one(text, "\n#endif\n", HEADER_BLOCK + "\n#endif\n",
               "header declarations")


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text

    needed = (
        "#include <linux/console.h>\n",
        "#include <linux/io.h>\n",
        "#include <linux/sizes.h>\n",
        "#include <linux/spinlock.h>\n",
        "#include <linux/ktime.h>\n",
    )
    anchor = "#include <linux/kernel.h>\n"
    for inc in needed:
        if inc not in text:
            text = one(text, anchor, anchor + inc, "recorder include")

    text += "\n" + RECORDER_BLOCK

    text = one(
        text,
        '''void a52_ackfr_sticky385_freeze(unsigned int event, int slot, u64 q,
                                int depth, unsigned int nr,
                                unsigned int zero, u64 age_ms)
{
	unsigned long flags;
''',
        '''void a52_ackfr_sticky385_freeze(unsigned int event, int slot, u64 q,
                                int depth, unsigned int nr,
                                unsigned int zero, u64 age_ms)
{
	unsigned long flags;

	a52_p389_trace(A52_P389_FREEZE, event, (u32)slot,
		       ((u32)depth << 16) | (nr & 0xffffU),
		       ((u32)zero << 31) | ((u32)age_ms & 0x7fffffffU));
''',
        "freeze mirror",
    )

    text = one(
        text,
        '''void a52_ackfr_sticky385_ufs(unsigned int sample, u32 doorbell,
                             unsigned long outstanding, u64 irq_count)
{
	unsigned long flags;
''',
        '''void a52_ackfr_sticky385_ufs(unsigned int sample, u32 doorbell,
                             unsigned long outstanding, u64 irq_count)
{
	unsigned long flags;

	a52_p389_trace(A52_P389_UFS_SAMPLE, sample, doorbell,
		       (u32)outstanding, (u32)irq_count);
''',
        "UFS mirror",
    )

    text = one(
        text,
        '''void a52_ackfr_sticky385_ofsimple(unsigned int stage, int ret)
{
	unsigned long flags;
''',
        '''void a52_ackfr_sticky385_ofsimple(unsigned int stage, int ret)
{
	unsigned long flags;

	a52_p389_trace(A52_P389_USB_STAGE, stage, (u32)ret, 0U, 0U);
''',
        "USB stage mirror",
    )
    return text


def patch_dsi(text: str) -> str:
    if "A52_PHASE389_DSI_RAM_MIRROR_V1" in text:
        return text
    if "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1" not in text:
        raise SystemExit("Phase389 requires Phase387 DSI source")

    if "#include <linux/a52_ack_secure_flight_recorder.h>\n" not in text:
        text = one(
            text, '#include "dsi_ctrl_hw.h"\n',
            '#include "dsi_ctrl_hw.h"\n'
            '#include <linux/a52_ack_secure_flight_recorder.h>\n',
            "DSI recorder include",
        )

    text = one(
        text,
        '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387D e trig=%d irqn=%d sw=%x ref=%u hw=%x",
			atomic_read(&dsi_ctrl->dma_irq_trig),
			dsi_ctrl->irq_info.irq_num,
			dsi_ctrl->irq_info.irq_stat_mask,
			dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE],
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
''',
        '''	if (a52_p293_gdm_armed(dsi_ctrl)) {
		a52_ackfr_record("P276 387D e trig=%d irqn=%d sw=%x ref=%u hw=%x",
			atomic_read(&dsi_ctrl->dma_irq_trig),
			dsi_ctrl->irq_info.irq_num,
			dsi_ctrl->irq_info.irq_stat_mask,
			dsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE],
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
		a52_p389_trace(A52_P389_DSI_WAIT_ENTER,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
			(u32)dsi_ctrl->irq_info.irq_num,
			(u32)dsi_ctrl->irq_info.irq_stat_mask,
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
	} /* A52_PHASE389_DSI_RAM_MIRROR_V1 */
''',
        "DSI wait entry",
    )

    text = one(
        text,
        '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387D w ret=%d trig=%d hw=%x",
			ret, atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
''',
        '''	if (a52_p293_gdm_armed(dsi_ctrl)) {
		a52_ackfr_record("P276 387D w ret=%d trig=%d hw=%x",
			ret, atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
		a52_p389_trace(A52_P389_DSI_WAIT_EXIT, (u32)ret,
			(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), 0U);
	}
''',
        "DSI wait exit",
    )

    text = one(
        text,
        '''			a52_ackfr_record("P276 387D f st=%x done=%u hw=%x",
				status, !!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
''',
        '''			a52_ackfr_record("P276 387D f st=%x done=%u hw=%x",
				status, !!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL));
			a52_p389_trace(A52_P389_DSI_FALLBACK_STATUS, status,
				!!(status & DSI_CMD_MODE_DMA_DONE),
				DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), mask);
''',
        "fallback status",
    )

    text = one(
        text,
        '''			if (status & mask)
				a52_ackfr_record("P276 387D b=1 irq_lost=1");
			else
				a52_ackfr_record("P276 387D b=0 engine_done=0");
''',
        '''			if (status & mask) {
				a52_ackfr_record("P276 387D b=1 irq_lost=1");
				a52_p389_trace(A52_P389_DSI_FALLBACK_BRANCH,
					1U, status, mask,
					(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
			} else {
				a52_ackfr_record("P276 387D b=0 engine_done=0");
				a52_p389_trace(A52_P389_DSI_FALLBACK_BRANCH,
					0U, status, mask,
					(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
			}
''',
        "fallback branch",
    )

    text = one(
        text,
        '''	if (a52_p293_gdm_armed(dsi_ctrl))
		a52_ackfr_record("P276 387I irq=%d st=%x raw=%x err=%llx",
			irq, status, DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			(unsigned long long)errors);
''',
        '''	if (a52_p293_gdm_armed(dsi_ctrl)) {
		a52_ackfr_record("P276 387I irq=%d st=%x raw=%x err=%llx",
			irq, status, DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),
			(unsigned long long)errors);
		a52_p389_trace(A52_P389_DSI_ISR, (u32)irq, status,
			DSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL), (u32)errors);
	}
''',
        "ISR mirror",
    )
    return text


def patch_drm(text: str) -> str:
    if "A52_PHASE389_DRM_RAM_MIRROR_V1" in text:
        return text
    if "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1" not in text:
        raise SystemExit("Phase389 requires Phase387 DRM source")

    if "#include <linux/a52_ack_secure_flight_recorder.h>\n" not in text:
        anchor = "#include <linux/export.h>\n"
        if anchor not in text:
            anchor = "#include <linux/kernel.h>\n"
        text = one(text, anchor,
                   anchor + "#include <linux/a52_ack_secure_flight_recorder.h>\n",
                   "DRM recorder include")

    helper_old = '''static inline bool a52_p387_log(unsigned int n)
{
	return n <= 8U || !(n & 63U);
}

'''
    helper_new = '''static inline bool a52_p387_log(unsigned int n)
{
	return n <= 8U || !(n & 63U);
}

/* A52_PHASE389_DRM_RAM_MIRROR_V1 */
static inline void a52_p389_modeset_fail(unsigned int n, unsigned int stage,
					int ret, unsigned int object)
{
	a52_p389_trace(A52_P389_DRM_MODESET_FAIL, n, stage, (u32)ret, object);
}

'''
    text = one(text, helper_old, helper_new, "DRM helper")

    # Mirror only the stage that actually matters right now: whichever
    # Phase387 path returns -EINVAL. The text recorder still retains every
    # Phase387 stage; this avoids duplicating lots of low-value hot-path data.
    needle = '''			a52_ackfr_record("P276 387M n=%u st=0 r=-22 crtc=%d en=%u cm=%x",
					a52_p387_n, crtc->base.id,
					new_crtc_state->enable,
					new_crtc_state->connector_mask);
			return -EINVAL;
'''
    repl = '''			a52_ackfr_record("P276 387M n=%u st=0 r=-22 crtc=%d en=%u cm=%x",
					a52_p387_n, crtc->base.id,
					new_crtc_state->enable,
					new_crtc_state->connector_mask);
			a52_p389_modeset_fail(a52_p387_n, 0U, -EINVAL,
					      (u32)crtc->base.id);
			return -EINVAL;
'''
    text = one(text, needle, repl, "DRM stage0 mirror")

    # Also mirror generic mode_valid/mode_fixup failures if those become active.
    needle = '''			a52_ackfr_record("P276 387M n=%u st=10 r=%d", a52_p387_n, ret);
		return ret;
'''
    repl = '''			a52_ackfr_record("P276 387M n=%u st=10 r=%d", a52_p387_n, ret);
		a52_p389_modeset_fail(a52_p387_n, 10U, ret, 0U);
		return ret;
'''
    text = one(text, needle, repl, "DRM stage10 mirror")
    return text


def validate(root: Path) -> None:
    h = (root / HDR).read_text()
    r = (root / REC).read_text()
    d = (root / DSI).read_text()
    m = (root / DRM).read_text()

    for token in (
        MARK, "A52_P389_DSI_WAIT_ENTER", "A52_P389_USB_STAGE",
        "void a52_p389_trace(u16 event",
    ):
        if token not in h:
            raise SystemExit("Phase389 header token missing: " + token)

    for token in (
        MARK, "0xB1400000ULL", "(7U * SZ_1M)",
        "A52_P389_TEXT_BYTES", "A52_P389_TRACE_BYTES",
        "ioremap_cache(A52_P389_PHYS, A52_P389_BYTES)",
        "CON_PRINTBUFFER | CON_ENABLED | CON_ANYTIME",
        "spin_trylock_irqsave", "crc32c",
        "P389 RAM ready phys=%llx bytes=%x",
    ):
        if token not in r:
            raise SystemExit("Phase389 recorder token missing: " + token)

    for token in (
        "A52_PHASE389_DSI_RAM_MIRROR_V1",
        "A52_P389_DSI_FALLBACK_STATUS",
        "A52_P389_DSI_ISR",
    ):
        if token not in d:
            raise SystemExit("Phase389 DSI token missing: " + token)

    for token in (
        "A52_PHASE389_DRM_RAM_MIRROR_V1",
        "a52_p389_modeset_fail",
    ):
        if token not in m:
            raise SystemExit("Phase389 DRM token missing: " + token)

    # Phase389 is RAM-only. Any debug-partition write path is forbidden.
    for forbidden in (
        '"/dev/block/by-name/debug"',
        "kernel_write(",
        "vfs_write(",
        "filp_open(",
    ):
        if forbidden in r:
            raise SystemExit("Phase389 recorder unexpectedly contains UFS I/O: " + forbidden)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    for rel in (HDR, REC, DSI, DRM):
        if not (root / rel).is_file():
            raise SystemExit("Phase389 missing source: " + str(rel))

    if not ns.check_only:
        p = root / HDR
        p.write_text(patch_header(p.read_text()))
        p = root / REC
        p.write_text(patch_recorder(p.read_text()))
        p = root / DSI
        p.write_text(patch_dsi(p.read_text()))
        p = root / DRM
        p.write_text(patch_drm(p.read_text()))

    validate(root)
    print("Phase389 reserved-RAM forensics: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
