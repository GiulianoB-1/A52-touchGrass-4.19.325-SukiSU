#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE348_DSI_CALL_TRACE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase348 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


TRACE_BLOCK = r'''
/* A52_PHASE348_DSI_CALL_TRACE_V1
 *
 * Phase347 hardware result had a valid Phase346 init marker but no Phase347
 * records. Phase348 removes ambiguity by writing an unconditional init marker
 * and tracing the first 24 entries into a52_p293_gdm_try_arm(), regardless of
 * packet type/payload. Fixed slots retain the latest relevant special event.
 *
 * Uses only the upper half of each existing Phase346 4 KiB mirror:
 *   ring  +0x0800: 24 x 64 B = 0x600
 *   fixed +0x0e00:  8 x 64 B = 0x200
 * so each copy ends exactly at +0x1000.
 */
#define A52_P348_RING_OFF           0x0800U
#define A52_P348_FIXED_OFF          0x0e00U
#define A52_P348_SLOT_BYTES         64U
#define A52_P348_RING_SLOTS         24U
#define A52_P348_FIXED_SLOTS        8U
#define A52_P348_MAGIC              0x3834334c4c414344ULL
#define A52_P348_COMMIT             0x348c0de5U

enum a52_p348_event {
	A52_P348_EVT_INIT = 1,
	A52_P348_EVT_CALL = 2,
	A52_P348_EVT_TYPE29 = 3,
	A52_P348_EVT_FETCH = 4,
	A52_P348_EVT_F0_PREFIX = 5,
	A52_P348_EVT_F0_EXACT = 6,
	A52_P348_EVT_ARMED = 7,
	A52_P348_EVT_P345_BEGIN = 8,
	A52_P348_EVT_P345_END = 9,
};

struct a52_p348_slot {
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

static atomic_t a52_p348_seq = ATOMIC_INIT(0);
static struct a52_p348_slot a52_p348_pre_ring[A52_P348_RING_SLOTS];
static struct a52_p348_slot a52_p348_pre_fixed[A52_P348_FIXED_SLOTS];

static void a52_p348_persist_at(unsigned int relative_off,
				const struct a52_p348_slot *slot)
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

static void a52_p348_store_fixed(unsigned int fixed_index,
				 const struct a52_p348_slot *slot)
{
	if (fixed_index >= A52_P348_FIXED_SLOTS)
		return;
	memcpy(&a52_p348_pre_fixed[fixed_index], slot, sizeof(*slot));
	wmb();
	a52_p348_persist_at(A52_P348_FIXED_OFF +
		fixed_index * A52_P348_SLOT_BYTES, slot);
}

static void a52_p348_flush_pretrace(void)
{
	unsigned int i;

	for (i = 0; i < A52_P348_RING_SLOTS; i++) {
		if (a52_p348_pre_ring[i].magic == A52_P348_MAGIC)
			a52_p348_persist_at(A52_P348_RING_OFF +
				i * A52_P348_SLOT_BYTES, &a52_p348_pre_ring[i]);
	}
	for (i = 0; i < A52_P348_FIXED_SLOTS; i++) {
		if (a52_p348_pre_fixed[i].magic == A52_P348_MAGIC)
			a52_p348_persist_at(A52_P348_FIXED_OFF +
				i * A52_P348_SLOT_BYTES, &a52_p348_pre_fixed[i]);
	}
}

void a52_p348_note(u32 event, u32 fixed_index, u32 cell, u32 flags,
			 u32 msg_flags, u32 type_len, u32 payload,
			 u32 reason, u32 state)
{
	struct a52_p348_slot slot;
	unsigned int seq;

	BUILD_BUG_ON(sizeof(struct a52_p348_slot) != A52_P348_SLOT_BYTES);

	memset(&slot, 0, sizeof(slot));
	seq = (unsigned int)atomic_inc_return(&a52_p348_seq) - 1U;
	slot.magic = A52_P348_MAGIC;
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
	slot.commit = A52_P348_COMMIT;
	slot.version = 1U;

	/* Only the first 24 generic CALL entries are persisted. */
	if (event == A52_P348_EVT_CALL && seq < A52_P348_RING_SLOTS) {
		memcpy(&a52_p348_pre_ring[seq], &slot, sizeof(slot));
		wmb();
		a52_p348_persist_at(A52_P348_RING_OFF +
			seq * A52_P348_SLOT_BYTES, &slot);
	}

	if (fixed_index < A52_P348_FIXED_SLOTS)
		a52_p348_store_fixed(fixed_index, &slot);
}

static void a52_p348_init_marker(void)
{
	a52_p348_note(A52_P348_EVT_INIT, 0U, 0xffffffffU, 0xffffffffU,
		0xffffffffU, 0xffffffffU, 0xffffffffU, 0U, 0U);
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
            raise SystemExit("Phase348 HWC prerequisite missing: " + token)

    anchor = "static void *a52_p346_sideband;\n"
    text = one(text, anchor, anchor + TRACE_BLOCK, "trace helper insertion")

    init_old = "\ta52_p346_write_slot(0U, &slot);\n}\n"
    init_new = (
        "\ta52_p346_write_slot(0U, &slot);\n"
        "\ta52_p348_init_marker();\n"
        "\ta52_p348_flush_pretrace();\n"
        "}\n"
    )
    text = one(text, init_old, init_new, "unconditional Phase348 init marker")

    begin_old = """	if (atomic_cmpxchg(&a52_p345_state, 0, 1) != 0)
		return;

	atomic_set(&a52_p345_count, 0);
"""
    begin_new = """	if (atomic_cmpxchg(&a52_p345_state, 0, 1) != 0)
		return;

	a52_p348_note(A52_P348_EVT_P345_BEGIN, 6U, 0U, 0U, 0U,
		0U, 0U, 0U, 1U);
	atomic_set(&a52_p345_count, 0);
"""
    text = one(text, begin_old, begin_new, "Phase345 begin fixed event")

    end_old = """	a52_p346_persist_samples();
}
"""
    end_new = """	a52_p346_persist_samples();
	a52_p348_note(A52_P348_EVT_P345_END, 7U, 0U, 0U, 0U,
		0U, 0U, 0U, (u32)atomic_read(&a52_p345_state));
}
"""
    text = one(text, end_old, end_new, "Phase345 end fixed event")
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
            raise SystemExit("Phase348 CTRL prerequisite missing: " + token)

    decl = 'extern void a52_p346_sideband_init(void); /* A52_PHASE346_DMA_RAW_SIDEBAND_V1 */\n'
    text = one(
        text,
        decl,
        decl + 'extern void a52_p348_note(u32 event, u32 fixed_index, u32 cell, u32 flags, u32 msg_flags, u32 type_len, u32 payload, u32 reason, u32 state); /* ' + MARK + ' */\n',
        "trace declaration",
    )

    old = """{
	const u8 *p;

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
"""
    new = """{
	const u8 *p;
	u32 p348_reason = 0U;
	u32 p348_payload = 0xffffffffU;
	u32 p348_cell = dsi_ctrl ? (u32)dsi_ctrl->cell_index : 0xffffffffU;
	u32 p348_flags = flags ? *flags : 0xffffffffU;
	u32 p348_msg_flags = msg ? (u32)msg->flags : 0xffffffffU;
	u32 p348_type_len = msg ?
		((((u32)msg->tx_len & 0xffffU) << 16) |
		 ((u32)msg->type & 0xffffU)) : 0xffffffffU;
	u32 p348_state = (u32)atomic_read(&a52_p293_gdm_state);

	if (!dsi_ctrl)
		p348_reason |= BIT(0);
	if (!msg)
		p348_reason |= BIT(1);
	if (!flags)
		p348_reason |= BIT(2);
	if (dsi_ctrl && dsi_ctrl->cell_index != 0)
		p348_reason |= BIT(3);
	if (flags && *flags != DSI_CTRL_CMD_FETCH_MEMORY)
		p348_reason |= BIT(4);
	if (msg && msg->flags != 0x8)
		p348_reason |= BIT(5);
	if (msg && msg->type != 0x29)
		p348_reason |= BIT(6);
	if (msg && msg->tx_len != 3)
		p348_reason |= BIT(7);
	if (msg && !msg->tx_buf)
		p348_reason |= BIT(8);
	if (msg && msg->tx_buf && msg->tx_len >= 1) {
		p = msg->tx_buf;
		p348_payload = (u32)p[0];
		if (msg->tx_len >= 2)
			p348_payload |= (u32)p[1] << 8;
		if (msg->tx_len >= 3)
			p348_payload |= (u32)p[2] << 16;
	}
	if (!(msg && msg->tx_buf && msg->tx_len >= 3 &&
	      p348_payload == 0x005a5af0U))
		p348_reason |= BIT(9);
	if (p348_state != 0U)
		p348_reason |= BIT(10);

	/* First 24 entries prove whether this hook is reached at all. */
	a52_p348_note(2U, 0xffffffffU, p348_cell, p348_flags,
		p348_msg_flags, p348_type_len, p348_payload,
		p348_reason, p348_state);

	if (msg && msg->type == 0x29)
		a52_p348_note(3U, 1U, p348_cell, p348_flags,
			p348_msg_flags, p348_type_len, p348_payload,
			p348_reason, p348_state);
	if (flags && *flags == DSI_CTRL_CMD_FETCH_MEMORY)
		a52_p348_note(4U, 2U, p348_cell, p348_flags,
			p348_msg_flags, p348_type_len, p348_payload,
			p348_reason, p348_state);
	if (msg && msg->tx_buf && msg->tx_len >= 1 &&
	    (p348_payload & 0xffU) == 0xf0U)
		a52_p348_note(5U, 3U, p348_cell, p348_flags,
			p348_msg_flags, p348_type_len, p348_payload,
			p348_reason, p348_state);
	if (msg && msg->tx_buf && msg->tx_len >= 3 &&
	    p348_payload == 0x005a5af0U)
		a52_p348_note(6U, 4U, p348_cell, p348_flags,
			p348_msg_flags, p348_type_len, p348_payload,
			p348_reason, p348_state);

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
"""
    text = one(text, old, new, "entry trace")

    arm_old = """	if (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)
		return;
"""
    arm_new = """	if (atomic_cmpxchg(&a52_p293_gdm_state, 0, 1) != 0)
		return;
	a52_p348_note(7U, 5U, p348_cell, p348_flags,
		p348_msg_flags, p348_type_len, p348_payload, 0U, 1U);
"""
    text = one(text, arm_old, arm_new, "successful arm")
    return text


def validate(bh: str, ah: str, bc: str, ac: str) -> None:
    both = ah + ac
    for token in (
        MARK,
        "A52_P348_RING_OFF           0x0800U",
        "A52_P348_FIXED_OFF          0x0e00U",
        "A52_P348_RING_SLOTS         24U",
        "A52_P348_MAGIC              0x3834334c4c414344ULL",
        "A52_P348_COMMIT             0x348c0de5U",
        "a52_p348_init_marker();",
        "a52_p348_note(2U, 0xffffffffU",
        "a52_p348_note(3U, 1U",
        "a52_p348_note(4U, 2U",
        "a52_p348_note(5U, 3U",
        "a52_p348_note(6U, 4U",
        "a52_p348_note(7U, 5U",
        "A52_P348_EVT_P345_BEGIN",
        "A52_P348_EVT_P345_END",
    ):
        if token not in both:
            raise SystemExit("Phase348 required token missing: " + token)

    for token in (
        "DSI_W32(", "DSI_R32(", "wait_for_completion_timeout(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(",
    ):
        if ah.count(token) != bh.count(token):
            raise SystemExit("Phase348 changed HWC protected primitive: " + token)
        if ac.count(token) != bc.count(token):
            raise SystemExit("Phase348 changed CTRL protected primitive: " + token)

    sw = "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);"
    if ah.count(sw) != bh.count(sw):
        raise SystemExit("Phase348 changed SW_TRIGGER count")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    hp = ns.root / HWC
    cp = ns.root / CTRL
    if not hp.is_file() or not cp.is_file():
        raise SystemExit("Phase348 source missing")

    h = hp.read_text()
    c = cp.read_text()

    if MARK in h + c:
        for token in (
            "A52_P348_MAGIC",
            "a52_p348_init_marker();",
            "a52_p348_note(2U, 0xffffffffU",
            "a52_p348_note(7U, 5U",
        ):
            if token not in h + c:
                raise SystemExit("Phase348 check-only token missing: " + token)
        print("Phase348 DSI call trace audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase348 marker missing in check-only mode")

    nh = patch_hwc(h)
    nc = patch_ctrl(c)
    validate(h, nh, c, nc)
    hp.write_text(nh)
    cp.write_text(nc)
    print("Phase348 DSI call trace applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
