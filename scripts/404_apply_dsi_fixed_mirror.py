#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE404_DSI_FIXED_MIRROR_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase404 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


P404_BLOCK = r'''
/* A52_PHASE404_DSI_FIXED_MIRROR_V1
 *
 * Mirror only Phase348 DSI call-trace events into the already-collected
 * Phase392 4 KiB metadata header.  The original Phase348 lane at B1BFA000 is
 * outside the collector's B1000000..B1AFFFFF window, so it is invisible in
 * recovery captures.
 *
 * Header layout:
 *   0x800..0xbff copy0: 16 x 64-byte slots
 *   0xc00..0xfff copy1: 16 x 64-byte slots
 * Slots 0..7 mirror Phase348 fixed events; slots 8..15 keep the first eight
 * generic a52_p293_gdm_try_arm() calls.  No scheduler, IRQ, UFS, USB or NoC
 * instrumentation is added.
 */
#define A52_P404_OFF0          0x800U
#define A52_P404_OFF1          0xc00U
#define A52_P404_SLOT_BYTES    64U
#define A52_P404_SLOTS         16U
#define A52_P404_MAGIC         0x3430344953445841ULL
#define A52_P404_COMMIT        0x404c0de5U

struct a52_p404_dsi_slot {
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
	u32 cpu;
	u32 crc32c;
	u32 commit;
} __packed;

void a52_p404_dsi_note(u32 slot_index, u32 seq, u32 event, u32 cell,
		       u32 flags, u32 msg_flags, u32 type_len, u32 payload,
		       u32 reason, u32 state)
{
	struct a52_p404_dsi_slot slot;
	void __iomem *base;

	BUILD_BUG_ON(sizeof(struct a52_p404_dsi_slot) != A52_P404_SLOT_BYTES);
	BUILD_BUG_ON(A52_P404_OFF1 +
		     A52_P404_SLOTS * A52_P404_SLOT_BYTES >
		     A52_P392_HEADER_BYTES);

	if (slot_index >= A52_P404_SLOTS)
		return;
	base = READ_ONCE(a52_p392_base);
	if (!base)
		return;

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P404_MAGIC;
	slot.ns = ktime_get_boottime_ns();
	slot.seq = seq;
	slot.event = event;
	slot.cell = cell;
	slot.flags = flags;
	slot.msg_flags = msg_flags;
	slot.type_len = type_len;
	slot.payload = payload;
	slot.reason = reason;
	slot.state = state;
	slot.cpu = (u32)raw_smp_processor_id();
	slot.crc32c = a52_p392_crc32c(&slot,
		offsetof(struct a52_p404_dsi_slot, crc32c));
	slot.commit = A52_P404_COMMIT;

	memcpy_toio((u8 __iomem *)base + A52_P404_OFF0 +
		    slot_index * A52_P404_SLOT_BYTES, &slot, sizeof(slot));
	memcpy_toio((u8 __iomem *)base + A52_P404_OFF1 +
		    slot_index * A52_P404_SLOT_BYTES, &slot, sizeof(slot));
	wmb();
}
EXPORT_SYMBOL_GPL(a52_p404_dsi_note);

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE392_PERSISTENT_GAP_DUAL_BACKEND_V1" not in text:
        raise SystemExit("Phase404 requires Phase392 persistent gap")
    anchor = "static atomic64_t a52_p392_dropped = ATOMIC64_INIT(0);\n"
    return one(text, anchor, anchor + P404_BLOCK, "Phase404 fixed lane block")


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE348_DSI_CALL_TRACE_V1" not in text:
        raise SystemExit("Phase404 requires Phase348 DSI call trace")

    decl = "extern void a52_ackfr_record(const char *fmt, ...);\n"
    extra = (
        decl +
        "extern void a52_p404_dsi_note(u32 slot_index, u32 seq, u32 event, u32 cell, "
        "u32 flags, u32 msg_flags, u32 type_len, u32 payload, u32 reason, u32 state); "
        "/* " + MARK + " */\n"
    )
    text = one(text, decl, extra, "Phase404 declaration")

    anchor = """	slot.commit = A52_P348_COMMIT;
	slot.version = 1U;

	/* Only the first 24 generic CALL entries are persisted. */
"""
    inject = """	slot.commit = A52_P348_COMMIT;
	slot.version = 1U;

	/* A52_PHASE404_DSI_FIXED_MIRROR_V1 */
	if (event == A52_P348_EVT_CALL && seq < 8U)
		a52_p404_dsi_note(8U + seq, seq, event, cell, flags,
			msg_flags, type_len, payload, reason, state);
	if (fixed_index < 8U)
		a52_p404_dsi_note(fixed_index, seq, event, cell, flags,
			msg_flags, type_len, payload, reason, state);

	/* Only the first 24 generic CALL entries are persisted. */
"""
    return one(text, anchor, inject, "Phase348 mirror hook")


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    hwc = (root / HWC).read_text(errors="replace")
    for token in (
        MARK,
        "#define A52_P404_OFF0          0x800U",
        "#define A52_P404_OFF1          0xc00U",
        "#define A52_P404_SLOTS         16U",
        "A52_P404_MAGIC",
        "A52_P404_COMMIT",
        "a52_p404_dsi_note",
        "EXPORT_SYMBOL_GPL(a52_p404_dsi_note);",
    ):
        if token not in rec:
            raise SystemExit("Phase404 recorder token missing: " + token)
    for token in (
        MARK,
        "event == A52_P348_EVT_CALL && seq < 8U",
        "a52_p404_dsi_note(8U + seq",
        "a52_p404_dsi_note(fixed_index",
    ):
        if token not in hwc:
            raise SystemExit("Phase404 HWC token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REC, HWC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase404 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / REC
        p.write_text(patch_rec(p.read_text(errors="replace")))
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase404 DSI fixed mirror: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
