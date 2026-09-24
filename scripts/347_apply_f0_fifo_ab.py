#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
MARK = "A52_PHASE347_F0_FIFO_AB_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase347 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    pos = text.find(name + "(")
    if pos < 0:
        raise SystemExit("Phase347 missing function: " + name)
    start = text.rfind("\n", 0, pos) + 1
    brace = text.find("{", pos)
    if brace < 0:
        raise SystemExit("Phase347 missing function body: " + name)
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise SystemExit("Phase347 unterminated function: " + name)


HELPER = r'''
/* A52_PHASE347_F0_FIFO_AB_V1
 *
 * Single-variable A/B for the exact first Samsung unlock packet observed by
 * Phase293: DCS long write, LASTCOMMAND, payload F0 5A 5A on DSI0.
 *
 * Phase293 must see the original FETCH_MEMORY flags first so its exact-target
 * recorder stays armed. Immediately afterwards we replace only the packet
 * source mode with FIFO_STORE. Packet construction, command engine state,
 * DMA_CTRL, DMA_CMD_LENGTH, SW_TRIGGER, DMA_DONE IRQ/wait, clocks, PHY and
 * panel state all remain on the normal downstream path.
 *
 * Interpretation:
 *   FIFO succeeds -> memory/GEM/IOVA fetch side is implicated.
 *   FIFO stalls    -> fault is downstream of command-memory fetch.
 */
u32 a52_p347_fifo_ab_enabled = 1;
static atomic_t a52_p347_fifo_once = ATOMIC_INIT(0);

static void a52_p347_maybe_switch_f0_fifo(struct dsi_ctrl *dsi_ctrl,
		const struct mipi_dsi_msg *msg, u32 *flags)
{
	const u8 *p;

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
	    *flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return;

	p = msg->tx_buf;
	if (p[0] != 0xf0 || p[1] != 0x5a || p[2] != 0x5a)
		return;

	/* Phase293 has already run and must own this exact transaction. */
	if (!a52_p293_gdm_armed(dsi_ctrl))
		return;
	if (atomic_cmpxchg(&a52_p347_fifo_once, 0, 1) != 0)
		return;

	*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;
	*flags |= DSI_CTRL_CMD_FIFO_STORE;
}
'''


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text

    for token in (
        "A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1",
        "A52_PHASE345_DMA_US_FRONTIER_V1",
        "A52_PHASE346_DMA_RAW_SIDEBAND_V1",
        "static void a52_p293_gdm_try_arm",
        "a52_p293_gdm_try_arm(dsi_ctrl, msg, flags);",
        "a52_p293_gdm_armed(dsi_ctrl)",
    ):
        if token not in text:
            raise SystemExit("Phase347 prerequisite missing: " + token)

    _, end = function_bounds(text, "a52_p293_gdm_try_arm")
    text = text[:end] + "\n" + HELPER + text[end:]

    anchor = "\ta52_p293_gdm_try_arm(dsi_ctrl, msg, flags);\n"
    repl = anchor + "\ta52_p347_maybe_switch_f0_fifo(dsi_ctrl, msg, flags);\n"
    text = one(text, anchor, repl, "exact-target source-mode hook")
    return text


def validate(before: str, after: str) -> None:
    for token in (
        MARK,
        "u32 a52_p347_fifo_ab_enabled = 1;",
        "static atomic_t a52_p347_fifo_once = ATOMIC_INIT(0);",
        "p[0] != 0xf0 || p[1] != 0x5a || p[2] != 0x5a",
        "*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;",
        "*flags |= DSI_CTRL_CMD_FIFO_STORE;",
        "a52_p347_maybe_switch_f0_fifo(dsi_ctrl, msg, flags);",
    ):
        if token not in after:
            raise SystemExit("Phase347 missing token: " + token)

    arm = after.index("a52_p293_gdm_try_arm(dsi_ctrl, msg, flags);")
    switch = after.index("a52_p347_maybe_switch_f0_fifo(dsi_ctrl, msg, flags);")
    if not arm < switch:
        raise SystemExit("Phase347 source-mode switch must follow Phase293 arm")

    protected = (
        "DSI_W32(", "DSI_R32(", "wait_for_completion_timeout(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(",
    )
    for token in protected:
        if after.count(token) != before.count(token):
            raise SystemExit("Phase347 changed protected primitive: " + token)

    if after.count("a52_p293_gdm_try_arm(dsi_ctrl, msg, flags);") != before.count(
            "a52_p293_gdm_try_arm(dsi_ctrl, msg, flags);"):
        raise SystemExit("Phase347 changed Phase293 arm call count")
    if after.count("DSI_CTRL_CMD_FIFO_STORE") != before.count("DSI_CTRL_CMD_FIFO_STORE") + 1:
        raise SystemExit("Phase347 expected exactly one new FIFO_STORE mutation")
    if after.count("DSI_CTRL_CMD_FETCH_MEMORY") != before.count("DSI_CTRL_CMD_FETCH_MEMORY") + 2:
        raise SystemExit("Phase347 unexpected FETCH_MEMORY delta")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / CTRL
    if not path.is_file():
        raise SystemExit("Phase347 source missing: " + str(path))

    text = path.read_text()
    if MARK in text:
        for token in (
            "a52_p347_fifo_ab_enabled",
            "a52_p347_maybe_switch_f0_fifo(dsi_ctrl, msg, flags);",
            "*flags |= DSI_CTRL_CMD_FIFO_STORE;",
        ):
            if token not in text:
                raise SystemExit("Phase347 check-only token missing: " + token)
        print("Phase347 exact-F0 FIFO A/B audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase347 marker missing in check-only mode")

    patched = patch_ctrl(text)
    validate(text, patched)
    path.write_text(patched)
    print("Phase347 exact-F0 FIFO A/B applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
