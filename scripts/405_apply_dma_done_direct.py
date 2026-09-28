#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_PHASE405_DMA_DONE_DIRECT_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase405 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase405 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase405 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase405 closing brace missing: {sig}")


P405_BLOCK = r'''
/* A52_PHASE405_DMA_DONE_DIRECT_V1
 *
 * Fixed, two-copy DSI DMA_DONE flight recorder in the Phase392 metadata page.
 * Phase394 owns 0x200..0x4ff; Phase405 starts exactly at 0x500.
 *
 * copy0: 0x500..0xa7f = 11 x 128 bytes
 * copy1: 0xa80..0xfff = 11 x 128 bytes
 *
 * Each stage has a dedicated slot, so later traffic cannot overwrite an
 * earlier stage.  Only already-formatted P276 303/387 records are mirrored.
 */
#define A52_P405_OFF0          0x500U
#define A52_P405_OFF1          0xa80U
#define A52_P405_SLOT_BYTES    128U
#define A52_P405_SLOTS         11U
#define A52_P405_MAGIC         0x3530344953445841ULL
#define A52_P405_COMMIT        0x405c0de5U

struct a52_p405_slot {
	u64 magic;
	u64 ns;
	u32 seq;
	u32 stage;
	u16 cpu;
	u16 len;
	u32 reserved0;
	char text[88];
	u32 crc32c;
	u32 commit;
} __packed;

static atomic_t a52_p405_seq = ATOMIC_INIT(0);

static int a52_p405_stage(const char *message)
{
	if (!message)
		return -1;
	if (!strncmp(message, "P276 303 S00 ", 13))
		return 0;
	if (!strncmp(message, "P276 303 S03 ", 13))
		return 1;
	if (!strncmp(message, "P276 303 S04 ", 13))
		return 2;
	if (!strncmp(message, "P276 303 S05 ", 13))
		return 3;
	if (!strncmp(message, "P276 303 S06 ", 13))
		return 4;
	if (!strncmp(message, "P276 387D e ", 12))
		return 5;
	if (!strncmp(message, "P276 387I ", 10))
		return 6;
	if (!strncmp(message, "P276 387D w ", 12))
		return 7;
	if (!strncmp(message, "P276 387D f ", 12))
		return 8;
	if (!strncmp(message, "P276 303 S08 ", 13))
		return 9;
	if (!strncmp(message, "P276 303 S09 ", 13))
		return 10;
	return -1;
}

static void a52_p405_dsi_mirror(const char *message)
{
	struct a52_p405_slot slot;
	void __iomem *base;
	size_t len;
	int stage;

	BUILD_BUG_ON(sizeof(struct a52_p405_slot) != A52_P405_SLOT_BYTES);
	BUILD_BUG_ON(A52_P405_OFF1 +
		     A52_P405_SLOTS * A52_P405_SLOT_BYTES >
		     A52_P392_HEADER_BYTES);

	stage = a52_p405_stage(message);
	if (stage < 0)
		return;
	base = READ_ONCE(a52_p392_base);
	if (!base)
		return;

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P405_MAGIC;
	slot.ns = ktime_get_boottime_ns();
	slot.seq = (u32)atomic_inc_return(&a52_p405_seq);
	slot.stage = (u32)stage;
	slot.cpu = (u16)raw_smp_processor_id();
	len = strnlen(message, sizeof(slot.text) - 1U);
	slot.len = (u16)len;
	memcpy(slot.text, message, len);
	slot.text[len] = '\0';
	slot.crc32c = a52_p392_crc32c(&slot,
		offsetof(struct a52_p405_slot, crc32c));
	slot.commit = A52_P405_COMMIT;

	memcpy_toio((u8 __iomem *)base + A52_P405_OFF0 +
		    (unsigned int)stage * A52_P405_SLOT_BYTES,
		    &slot, sizeof(slot));
	memcpy_toio((u8 __iomem *)base + A52_P405_OFF1 +
		    (unsigned int)stage * A52_P405_SLOT_BYTES,
		    &slot, sizeof(slot));
	wmb();
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE392_PERSISTENT_GAP_DUAL_BACKEND_V1" not in text:
        raise SystemExit("Phase405 requires Phase392 persistent gap")

    sig = "static u32 a52_p392_crc32c(const void *buffer, size_t len)"
    _, end = function_bounds(text, sig)
    text = text[:end] + "\n" + P405_BLOCK + text[end:]

    pattern = re.compile(
        r'(\/\* A52_PHASE392_PERSISTENT_GAP_MIRROR_CALL_V1 \*\/\n'
        r'\s*a52_p392_gap_write\(([^;]+)\);)'
    )
    m = pattern.search(text)
    if not m:
        raise SystemExit("Phase405 Phase392 mirror call missing")
    dst = m.group(2).strip()
    repl = m.group(1) + f"\n\ta52_p405_dsi_mirror({dst}); /* {MARK} */"
    text = text[:m.start()] + repl + text[m.end():]
    return text


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE394_MODE_FIXUP_FIFO_EARLY_V1" not in text:
        raise SystemExit("Phase405 requires Phase394 lineage")

    block = '''\t/* A52_PHASE394_MODE_FIXUP_FIFO_EARLY_V1
\t * Binary DSI test: this helper has already matched only controller 0,
\t * FETCH_MEMORY, flags 0x8, type 0x29, len 3, payload F0 5A 5A.
\t * Route only that command through the controller FIFO.
\t */
\ta52_ackfr_record("P276 394F route old=%x", *flags);
\t*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;
\t*flags |= DSI_CTRL_CMD_FIFO_STORE;
\ta52_ackfr_record("P276 394F route new=%x", *flags);
'''
    if block not in text:
        raise SystemExit("Phase405 exact-F0 FIFO reroute block missing")
    text = text.replace(
        block,
        f'\t/* {MARK}: restore original exact-F0 FETCH_MEMORY path. */\n',
        1,
    )
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")

    for token in (
        MARK,
        "#define A52_P405_OFF0          0x500U",
        "#define A52_P405_OFF1          0xa80U",
        "#define A52_P405_SLOTS         11U",
        "A52_P405_MAGIC",
        "A52_P405_COMMIT",
        "a52_p405_dsi_mirror(",
        "P276 303 S05 ",
        "P276 303 S06 ",
        "P276 387D e ",
        "P276 387I ",
        "P276 387D w ",
        "P276 387D f ",
        "P276 303 S08 ",
        "P276 303 S09 ",
    ):
        if token not in rec:
            raise SystemExit("Phase405 recorder token missing: " + token)

    if 'P276 394F route old=%x' in dsi or 'P276 394F route new=%x' in dsi:
        raise SystemExit("Phase405 Phase394 exact-F0 FIFO route still active")

    sig = "static void a52_p293_gdm_try_arm(struct dsi_ctrl *dsi_ctrl,"
    start, end = function_bounds(dsi, sig)
    arm = dsi[start:end]
    if "*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;" in arm:
        raise SystemExit("Phase405 exact-F0 helper still clears FETCH_MEMORY")
    if "*flags |= DSI_CTRL_CMD_FIFO_STORE;" in arm:
        raise SystemExit("Phase405 exact-F0 helper still forces FIFO_STORE")

    for token in (
        "*flags != DSI_CTRL_CMD_FETCH_MEMORY",
        "p[0] != 0xF0 || p[1] != 0x5A || p[2] != 0x5A",
        "P276 303 S00 c=0 in=%x mf=%x t=%x l=%u",
        "P276 303 S04 irq=%d in=%x st=%x",
        "P276 303 S08 ret=%d irq=%d in=%x st=%x",
        "P276 387D e trig=%d irqn=%d sw=%x ref=%u hw=%x",
        "P276 387D w ret=%d trig=%d hw=%x",
        "P276 387I irq=%d st=%x raw=%x err=%llx",
    ):
        if token not in dsi:
            raise SystemExit("Phase405 DSI token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REC, DSI):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase405 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / REC
        p.write_text(patch_rec(p.read_text(errors="replace")))
        p = ns.root / DSI
        p.write_text(patch_dsi(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase405 original DMA path + direct DSI fixed recorder: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
