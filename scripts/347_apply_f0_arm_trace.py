#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE347_F0_ARM_TRACE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase347 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


TRACE_BLOCK = r'''
/* A52_PHASE347_F0_ARM_TRACE_V1
 *
 * Phase346 hardware result: the raw sideband initialized correctly but p0..p6
 * remained empty.  Therefore Phase347 traces the Phase293/304 exact-F0 arming
 * predicate itself.
 *
 * This is intentionally a separate diagnostic phase: raw writes may occur in
 * the command path and are NOT used for microsecond timing.  The goal is only
 * to learn whether F0 5A 5A is seen and which predicate prevents arming.
 *
 * Trace storage uses the unused upper half of each Phase346 4 KiB mirror:
 *   copy A +0x0800..+0x0fff
 *   copy B +0x1800..+0x1fff
 *
 * A 16-entry rolling ring records near-candidate attempts.  Four fixed slots
 * preserve the latest F0-SEEN, ARMED, P345-BEGIN and P345-END events.
 * Events are buffered in ordinary RAM even before the sideband is mapped and
 * are flushed when Phase346 initializes the mapping.
 */
#define A52_P347_TRACE_OFF          0x0800U
#define A52_P347_FIXED_OFF          0x0c00U
#define A52_P347_SLOT_BYTES         64U
#define A52_P347_RING_SLOTS         16U
#define A52_P347_FIXED_SLOTS        4U
#define A52_P347_MAGIC              0x3734334d52414630ULL
#define A52_P347_COMMIT             0x347c0de5U

enum a52_p347_event {
	A52_P347_EVT_TRY = 1,
	A52_P347_EVT_F0_SEEN = 2,
	A52_P347_EVT_ARMED = 3,
	A52_P347_EVT_P345_BEGIN = 4,
	A52_P347_EVT_P345_END = 5,
};

struct a52_p347_slot {
	u64 magic;
	u64 ns;
	u32 seq;
	u32 event;
	u32 cell;
	u32 flags;
	u32 msg_flags;
	u32 type_len;
	u32 payload;
	u32 reason;
	u32 state;
	u32 aux;
	u32 commit;
	u32 version;
};

static atomic_t a52_p347_seq = ATOMIC_INIT(0);
static struct a52_p347_slot a52_p347_ring[A52_P347_RING_SLOTS];
static struct a52_p347_slot a52_p347_fixed[A52_P347_FIXED_SLOTS];

static void a52_p347_persist_at(unsigned int relative_off,
				const struct a52_p347_slot *slot)
{
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_p346_sideband) || !slot)
		return;
	if (relative_off + sizeof(*slot) > A52_P346_COPY_BYTES)
		return;

	dst0 = (u8 *)a52_p346_sideband + relative_off;
	dst1 = (u8 *)a52_p346_sideband + A52_P346_COPY_BYTES + relative_off;
	memcpy(dst0, slot, sizeof(*slot));
	memcpy(dst1, slot, sizeof(*slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(*slot));
	__flush_dcache_area(dst1, sizeof(*slot));
}

static void a52_p347_flush_pretrace(void)
{
	unsigned int i;

	for (i = 0; i < A52_P347_RING_SLOTS; i++) {
		if (a52_p347_ring[i].magic == A52_P347_MAGIC)
			a52_p347_persist_at(A52_P347_TRACE_OFF +
				i * A52_P347_SLOT_BYTES, &a52_p347_ring[i]);
	}
	for (i = 0; i < A52_P347_FIXED_SLOTS; i++) {
		if (a52_p347_fixed[i].magic == A52_P347_MAGIC)
			a52_p347_persist_at(A52_P347_FIXED_OFF +
				i * A52_P347_SLOT_BYTES, &a52_p347_fixed[i]);
	}
}

void a52_p347_note_arm(u32 event, u32 cell, u32 flags, u32 msg_flags,
			 u32 type_len, u32 payload, u32 reason, u32 state)
{
	struct a52_p347_slot slot;
	unsigned int seq;
	unsigned int ring_index;
	int fixed_index = -1;

	BUILD_BUG_ON(sizeof(struct a52_p347_slot) != A52_P347_SLOT_BYTES);

	memset(&slot, 0, sizeof(slot));
	seq = (unsigned int)atomic_inc_return(&a52_p347_seq) - 1U;
	slot.magic = A52_P347_MAGIC;
	slot.ns = ktime_get_ns();
	slot.seq = seq;
	slot.event = event;
	slot.cell = cell;
	slot.flags = flags;
	slot.msg_flags = msg_flags;
	slot.type_len = type_len;
	slot.payload = payload;
	slot.reason = reason;
	slot.state = state;
	slot.aux = (u32)raw_smp_processor_id();
	slot.commit = A52_P347_COMMIT;
	slot.version = 1U;

	ring_index = seq % A52_P347_RING_SLOTS;
	memcpy(&a52_p347_ring[ring_index], &slot, sizeof(slot));
	wmb();
	a52_p347_persist_at(A52_P347_TRACE_OFF +
		ring_index * A52_P347_SLOT_BYTES, &slot);

	if (event >= A52_P347_EVT_F0_SEEN &&
	    event <= A52_P347_EVT_P345_END)
		fixed_index = (int)event - (int)A52_P347_EVT_F0_SEEN;
	if (fixed_index >= 0 && fixed_index < (int)A52_P347_FIXED_SLOTS) {
		memcpy(&a52_p347_fixed[fixed_index], &slot, sizeof(slot));
		wmb();
		a52_p347_persist_at(A52_P347_FIXED_OFF +
			(unsigned int)fixed_index * A52_P347_SLOT_BYTES, &slot);
	}
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE346_DMA_RAW_SIDEBAND_V1",
        "static void *a52_p346_sideband;",
        "a52_p346_write_slot(0U, &slot);",
        "static void a52_p345_begin(struct dsi_ctrl_hw *ctrl)",
        "static void a52_p345_end(struct dsi_ctrl_hw *ctrl)",
    ):
        if token not in text:
            raise SystemExit("Phase347 HWC prerequisite missing: " + token)

    anchor = "static void *a52_p346_sideband;\n"
    text = one(text, anchor, anchor + TRACE_BLOCK, "trace helper insertion")

    init_old = "\ta52_p346_write_slot(0U, &slot);\n}\n"
    init_new = "\ta52_p346_write_slot(0U, &slot);\n\ta52_p347_flush_pretrace();\n}\n"
    text = one(text, init_old, init_new, "pretrace flush at map init")

    begin_old = """	if (atomic_cmpxchg(&a52_p345_state, 0, 1) != 0)
		return;

	atomic_set(&a52_p345_count, 0);
"""
    begin_new = """	if (atomic_cmpxchg(&a52_p345_state, 0, 1) != 0)
		return;

	a52_p347_note_arm(A52_P347_EVT_P345_BEGIN, 0U, 0U, 0U,
		0U, 0U, 0U, 1U);
	atomic_set(&a52_p345_count, 0);
"""
    text = one(text, begin_old, begin_new, "Phase345 begin event")

    end_old = """	a52_p346_persist_samples();
}
"""
    end_new = """	a52_p346_persist_samples();
	a52_p347_note_arm(A52_P347_EVT_P345_END, 0U, 0U, 0U,
		0U, 0U, 0U, (u32)atomic_read(&a52_p345_state));
}
"""
    text = one(text, end_old, end_new, "Phase345 end event")
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE304_EXACT_F05A5A_VISIBILITY_V1",
        "static void a52_p293_gdm_try_arm(struct dsi_ctrl *dsi_ctrl,",
        "p[0] != 0xF0 || p[1] != 0x5A || p[2] != 0x5A",
        "extern void a52_p346_sideband_init(void);",
    ):
        if token not in text:
            raise SystemExit("Phase347 CTRL prerequisite missing: " + token)

    decl = "extern void a52_p346_sideband_init(void); /* A52_PHASE346_DMA_RAW_SIDEBAND_V1 */\n"
    text = one(
        text,
        decl,
        decl + "extern void a52_p347_note_arm(u32 event, u32 cell, u32 flags, u32 msg_flags, u32 type_len, u32 payload, u32 reason, u32 state); /* " + MARK + " */\n",
        "trace declaration",
    )

    old_decl = """{
	const u8 *p;

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
"""
    new_decl = """{
	const u8 *p;
	u32 p347_reason = 0U;
	u32 p347_payload = 0xffffffffU;
	u32 p347_cell = dsi_ctrl ? (u32)dsi_ctrl->cell_index : 0xffffffffU;
	u32 p347_flags = flags ? *flags : 0xffffffffU;
	u32 p347_msg_flags = msg ? (u32)msg->flags : 0xffffffffU;
	u32 p347_type_len = msg ?
		(((u32)msg->tx_len & 0xffffU) << 16 | ((u32)msg->type & 0xffffU)) :
		0xffffffffU;

	if (!dsi_ctrl)
		p347_reason |= BIT(0);
	if (!msg)
		p347_reason |= BIT(1);
	if (!flags)
		p347_reason |= BIT(2);
	if (dsi_ctrl && dsi_ctrl->cell_index != 0)
		p347_reason |= BIT(3);
	if (flags && *flags != DSI_CTRL_CMD_FETCH_MEMORY)
		p347_reason |= BIT(4);
	if (msg && msg->flags != 0x8)
		p347_reason |= BIT(5);
	if (msg && msg->type != 0x29)
		p347_reason |= BIT(6);
	if (msg && msg->tx_len != 3)
		p347_reason |= BIT(7);
	if (msg && !msg->tx_buf)
		p347_reason |= BIT(8);
	if (msg && msg->tx_buf && msg->tx_len >= 3) {
		p = msg->tx_buf;
		p347_payload = (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16);
	}
	if (p347_payload != 0x005a5af0U)
		p347_reason |= BIT(9);
	if (atomic_read(&a52_p293_gdm_state) != 0)
		p347_reason |= BIT(10);

	if (dsi_ctrl && msg && flags && dsi_ctrl->cell_index == 0 &&
	    (msg->type == 0x29 || p347_payload == 0x005a5af0U))
		a52_p347_note_arm(1U, p347_cell, p347_flags, p347_msg_flags,
			p347_type_len, p347_payload, p347_reason,
			(u32)atomic_read(&a52_p293_gdm_state));
	if (p347_payload == 0x005a5af0U)
		a52_p347_note_arm(2U, p347_cell, p347_flags, p347_msg_flags,
			p347_type_len, p347_payload, p347_reason,
			(u32)atomic_read(&a52_p293_gdm_state));

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
"""
    text = one(text, old_decl, new_decl, "arm predicate telemetry")

    arm_old = """	if (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)
		return;
	p = msg->tx_buf;
"""
    arm_new = """	if (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)
		return;
	a52_p347_note_arm(3U, p347_cell, p347_flags, p347_msg_flags,
		p347_type_len, p347_payload, 0U, 1U);
	p = msg->tx_buf;
"""
    text = one(text, arm_old, arm_new, "successful-arm event")
    return text


def validate(bh: str, ah: str, bc: str, ac: str) -> None:
    both = ah + ac
    for token in (
        MARK,
        "A52_P347_TRACE_OFF          0x0800U",
        "A52_P347_FIXED_OFF          0x0c00U",
        "A52_P347_SLOT_BYTES         64U",
        "A52_P347_MAGIC              0x3734334d52414630ULL",
        "A52_P347_COMMIT             0x347c0de5U",
        "a52_p347_flush_pretrace();",
        "a52_p347_note_arm(1U",
        "a52_p347_note_arm(2U",
        "a52_p347_note_arm(3U",
        "A52_P347_EVT_P345_BEGIN",
        "A52_P347_EVT_P345_END",
        "p347_reason |= BIT(4)",
        "p347_payload != 0x005a5af0U",
    ):
        if token not in both:
            raise SystemExit("Phase347 required token missing: " + token)

    # No DSI/clock/power behavior is changed. Phase347 adds only telemetry RAM
    # writes/cache clean operations and reads of already-available message data.
    for token in (
        "DSI_W32(", "DSI_R32(", "wait_for_completion_timeout(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(",
    ):
        if ah.count(token) != bh.count(token):
            raise SystemExit("Phase347 changed HWC protected primitive: " + token)
        if ac.count(token) != bc.count(token):
            raise SystemExit("Phase347 changed CTRL protected primitive: " + token)

    sw = "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);"
    if ah.count(sw) != bh.count(sw):
        raise SystemExit("Phase347 changed SW_TRIGGER count")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    hp = ns.root / HWC
    cp = ns.root / CTRL
    if not hp.is_file() or not cp.is_file():
        raise SystemExit("Phase347 source missing")

    h = hp.read_text()
    c = cp.read_text()

    if MARK in h + c:
        for token in ("A52_P347_MAGIC", "a52_p347_note_arm(2U", "A52_P347_EVT_P345_BEGIN"):
            if token not in h + c:
                raise SystemExit("Phase347 check-only token missing: " + token)
        print("Phase347 F0 arm trace audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase347 marker missing in check-only mode")

    nh = patch_hwc(h)
    nc = patch_ctrl(c)
    validate(h, nh, c, nc)
    hp.write_text(nh)
    cp.write_text(nc)
    print("Phase347 F0 arm trace applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
