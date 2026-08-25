#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL_REL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
PHY_REL = Path("drivers/a52_display/msm/dsi/dsi_phy.c")
DISPLAY_REL = Path("drivers/a52_display/msm/dsi/dsi_display.c")

CTRL_MARK = "A52_PHASE314_GKI_F0_FULL_CTRL_PRESTATE_RECORDER_V1"
PHY_MARK = "A52_PHASE314_GKI_F0_FULL_PHY_PRESTATE_RECORDER_V1"
DISPLAY_MARK = "A52_PHASE314_GKI_F0_LIFECYCLE_HISTORY_RECORDER_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase314 {label}: expected one match, found {n}")
    return text.replace(old, new, 1)


def inject_after_call_containing(text: str, needle: str, insertion: str, label: str) -> str:
    pos = text.find(needle)
    if pos < 0:
        raise SystemExit(f"Phase314 {label}: marker not found: {needle}")
    end = text.find(");", pos)
    if end < 0:
        raise SystemExit(f"Phase314 {label}: could not find call terminator")
    end += 2
    return text[:end] + insertion + text[end:]


def patch_ctrl(text: str) -> str:
    if CTRL_MARK in text:
        return text
    for required in (
        "P276 303 S00p p=%02x%02x%02x",
        "P276 303 S03b ln=%x ck=%x",
        "P276 303 S09e ack=%x to=%x phy=%x ctl=%x",
    ):
        if required not in text:
            raise SystemExit("Phase314 CTRL inherited observer missing: " + required)

    arm = "static void a52_p293_gdm_try_arm(struct dsi_ctrl *dsi_ctrl,"
    if arm not in text:
        raise SystemExit("Phase314 CTRL exact-F0 arm function anchor missing")

    helper = r'''
/* A52_PHASE314_GKI_F0_FULL_CTRL_PRESTATE_RECORDER_V1
 * Read-only first-F0 controller snapshot. These status/error registers are
 * cleared by driver writes, not by reads, so the recorder must never ack,
 * clear, reset, trigger, gate, or otherwise establish hardware state.
 */
extern void a52_ackfr_record(const char *fmt, ...);

static void a52_p314_ctrl_prestate(struct dsi_ctrl *dsi_ctrl)
{
	void __iomem *b;
	static bool recorded;

	if (!dsi_ctrl || recorded)
		return;
	b = dsi_ctrl->hw.base;
	if (!b)
		return;
	recorded = true;

	a52_ackfr_record("P276 314C0 %x %x %x %x %x %x",
		readl_relaxed(b + 0x0004), readl_relaxed(b + 0x0008),
		readl_relaxed(b + 0x000c), readl_relaxed(b + 0x011c),
		readl_relaxed(b + 0x0120), readl_relaxed(b + 0x00a8));
	a52_ackfr_record("P276 314C1 %x %x %x %x %x %x",
		readl_relaxed(b + 0x00ac), readl_relaxed(b + 0x00b0),
		readl_relaxed(b + 0x0310), readl_relaxed(b + 0x0084),
		readl_relaxed(b + 0x0088), readl_relaxed(b + 0x003c));
	a52_ackfr_record("P276 314C2 %x %x %x %x %x %x",
		readl_relaxed(b + 0x0048), readl_relaxed(b + 0x004c),
		readl_relaxed(b + 0x0050), readl_relaxed(b + 0x0090),
		readl_relaxed(b + 0x0040), readl_relaxed(b + 0x0044));
	a52_ackfr_record("P276 314C3 %x %x %x %x %x %x",
		readl_relaxed(b + 0x0068), readl_relaxed(b + 0x00c0),
		readl_relaxed(b + 0x00b4), readl_relaxed(b + 0x0110),
		readl_relaxed(b + 0x010c), readl_relaxed(b + 0x02c8));
	a52_ackfr_record("P276 314C4 %x %x %x %x %x %x",
		readl_relaxed(b + 0x00b8), readl_relaxed(b + 0x00bc),
		readl_relaxed(b + 0x00c4), readl_relaxed(b + 0x00cc),
		readl_relaxed(b + 0x012c), readl_relaxed(b + 0x0118));
	a52_ackfr_record("P276 314C5 %x %x %x %x %x %x",
		readl_relaxed(b + 0x0200), readl_relaxed(b + 0x0210),
		readl_relaxed(b + 0x02bc), readl_relaxed(b + 0x02c0),
		readl_relaxed(b + 0x02cc), readl_relaxed(b + 0x0330));
	a52_ackfr_record("P276 314C6 %x %x %x %x %x %x",
		readl_relaxed(b + 0x02d8), readl_relaxed(b + 0x0124),
		readl_relaxed(b + 0x0128), readl_relaxed(b + 0x02b8),
		readl_relaxed(b + 0x01e8), readl_relaxed(b + 0x0130));
	a52_ackfr_record("P276 314C7 %x %x %x %x %x %x",
		readl_relaxed(b + 0x0010), readl_relaxed(b + 0x0020),
		readl_relaxed(b + 0x0054), readl_relaxed(b + 0x0194),
		readl_relaxed(b + 0x01b8), readl_relaxed(b + 0x02b4));

	a52_ackfr_record("P276 314S0 %u %u %u %u %u %u",
		dsi_ctrl->current_state.power_state,
		dsi_ctrl->current_state.controller_state,
		dsi_ctrl->current_state.cmd_engine_state,
		dsi_ctrl->current_state.vid_engine_state,
		dsi_ctrl->current_state.host_initialized,
		dsi_ctrl->current_state.tpg_enabled);
	a52_ackfr_record("P276 314S1 %x %u %u %u %u %u",
		dsi_ctrl->irq_info.irq_stat_mask,
		(unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig),
		dsi_ctrl->dma_wait_queued, dsi_ctrl->secure_mode,
		dsi_ctrl->esd_check_underway, dsi_ctrl->phy_isolation_enabled);
	a52_ackfr_record("P276 314S2 %x %x %x %u %u %u",
		dsi_ctrl->cmd_buffer_iova, dsi_ctrl->cmd_buffer_size,
		dsi_ctrl->cmd_len, dsi_ctrl->null_insertion_enabled,
		dsi_ctrl->split_link_supported, dsi_ctrl->cmd_mode);
	a52_ackfr_record("P276 314S3 %x %x %u %u %u %u",
		dsi_ctrl->cell_index, dsi_ctrl->refcount,
		dsi_ctrl->modeupdated, dsi_ctrl->error_interrupt_count,
		dsi_ctrl->irq_info.cmd_dma_done.done,
		dsi_ctrl->irq_info.bta_done.done);
}

'''
    text = replace_once(text, arm, helper + arm, "CTRL helper insertion")

    s00p = "P276 303 S00p p=%02x%02x%02x"
    text = inject_after_call_containing(
        text, s00p, "\n\ta52_p314_ctrl_prestate(dsi_ctrl);",
        "CTRL first-F0 call")
    return text


def patch_phy(text: str) -> str:
    if PHY_MARK in text:
        return text
    for required in (
        "A52_PHASE312_GKI_F0_PHY_DEPENDENCY_RECORDER_V1",
        "P276 312T0 %x %x %x %x %x %x",
        "P276 312L0 l=%u %x %x %x %x %x",
        "P276 308T q=%u %x %x %x %x %x",
    ):
        if required not in text:
            raise SystemExit("Phase314 PHY inherited observer missing: " + required)

    anchor = '\t\ta52_ackfr_record("P276 312T0 %x %x %x %x %x %x",\n'
    block = r'''\t\t/* A52_PHASE314_GKI_F0_FULL_PHY_PRESTATE_RECORDER_V1
\t\t * Complement Phase307/308/312 with the remaining common/status
\t\t * registers and per-lane TEST_DATAPATH. Reads only; q0 only.
\t\t */
\t\ta52_ackfr_record("P276 314P0 %x %x %x %x %x %x",
\t\t\treadl_relaxed(base + 0x010), readl_relaxed(base + 0x014),
\t\t\treadl_relaxed(base + 0x018), readl_relaxed(base + 0x01c),
\t\t\treadl_relaxed(base + 0x020), readl_relaxed(base + 0x024));
\t\ta52_ackfr_record("P276 314P1 %x %x %x %x %x %x",
\t\t\treadl_relaxed(base + 0x028), readl_relaxed(base + 0x02c),
\t\t\treadl_relaxed(base + 0x030), readl_relaxed(base + 0x034),
\t\t\treadl_relaxed(base + 0x038), readl_relaxed(base + 0x098));
\t\ta52_ackfr_record("P276 314P2 %x %x %x %x %x %x",
\t\t\treadl_relaxed(base + 0x09c), readl_relaxed(base + 0x0a0),
\t\t\treadl_relaxed(base + 0x0a4), readl_relaxed(base + 0x0a8),
\t\t\treadl_relaxed(base + 0x0ec), readl_relaxed(base + 0x0f4));
\t\ta52_ackfr_record("P276 314P3 %x %u %x %x %x %x",
\t\t\treadl_relaxed(base + 0x0f8), phy->dsi_phy_state,
\t\t\treadl_relaxed(base + 0x210), readl_relaxed(base + 0x290),
\t\t\treadl_relaxed(base + 0x310), readl_relaxed(base + 0x390));
\t\ta52_ackfr_record("P276 314P4 %x %x %x %x %x %x",
\t\t\treadl_relaxed(base + 0x410),
\t\t\treadl_relaxed(base + 0x22c), readl_relaxed(base + 0x2ac),
\t\t\treadl_relaxed(base + 0x32c), readl_relaxed(base + 0x3ac),
\t\t\treadl_relaxed(base + 0x42c));
'''
    return replace_once(text, anchor, block + anchor, "PHY q0 prestate insertion")


def patch_display_callback(text: str, name: str, event: int,
                           clk_name: str, state_name: str) -> str:
    start = text.find(f"int {name}(")
    if start < 0:
        raise SystemExit(f"Phase314 display function missing: {name}")
    needle = "\tstruct dsi_display *display = priv;\n"
    pos = text.find(needle, start)
    if pos < 0:
        raise SystemExit(f"Phase314 {name}: display assignment anchor missing")
    # The assignment must belong to this callback, before its first return.
    ret = text.find("\n\treturn ", start)
    if ret >= 0 and pos > ret:
        raise SystemExit(f"Phase314 {name}: display assignment escaped function")
    call = (
        f"\ta52_p314_display_history({event}, display, {clk_name}, "
        f"l_type, {state_name});\n"
    )
    end = pos + len(needle)
    return text[:end] + call + text[end:]


def patch_display(text: str) -> str:
    if DISPLAY_MARK in text:
        return text

    anchor = "#define to_dsi_display(x) container_of(x, struct dsi_display, host)\n"
    helper = r'''
/* A52_PHASE314_GKI_F0_LIFECYCLE_HISTORY_RECORDER_V1
 * Passive breadcrumbs for clock/continuous-splash state transitions that can
 * establish the inherited hardware state observed at the first F0.
 */
extern void a52_ackfr_record(const char *fmt, ...);

static void a52_p314_display_history(unsigned int event,
\t\t\t\t    struct dsi_display *display,
\t\t\t\t    unsigned int clk,
\t\t\t\t    unsigned int l_type,
\t\t\t\t    unsigned int state)
{
\tif (!display) {
\t\ta52_ackfr_record("P276 314H e=%u null=1", event);
\t\treturn;
\t}

\ta52_ackfr_record("P276 314H e=%u c=%x l=%x s=%x sp=%u",
\t\tevent, clk, l_type, state, display->is_cont_splash_enabled);
\ta52_ackfr_record("P276 314HF e=%u cl=%u pi=%u ul=%u cg=%x",
\t\tevent, display->clamp_enabled, display->phy_idle_power_off,
\t\tdisplay->ulps_enabled, display->clk_gating_config);
}

'''
    text = replace_once(text, anchor, anchor + helper, "display history helper")

    text = patch_display_callback(
        text, "dsi_pre_clkon_cb", 1, "clk_type", "new_state")
    text = patch_display_callback(
        text, "dsi_post_clkon_cb", 2, "clk", "curr_state")
    text = patch_display_callback(
        text, "dsi_pre_clkoff_cb", 3, "clk", "new_state")
    text = patch_display_callback(
        text, "dsi_post_clkoff_cb", 4, "clk_type", "curr_state")

    host_anchor = (
        '\tif (display->is_cont_splash_enabled) {\n'
        '\t\tDSI_DEBUG("cont splash enabled, host enable not required\\n");\n'
    )
    host_new = (
        '\tif (display->is_cont_splash_enabled) {\n'
        '\t\ta52_p314_display_history(5, display, 0, 0, 0);\n'
        '\t\tDSI_DEBUG("cont splash enabled, host enable not required\\n");\n'
    )
    text = replace_once(text, host_anchor, host_new, "host-enable cont-splash history")

    resync_anchor = (
        "\t\tif (!display->is_cont_splash_enabled)\n"
        "\t\t\tdsi_display_toggle_resync_fifo(display);\n"
    )
    resync_new = (
        "\t\ta52_p314_display_history(6, display, clk, l_type, curr_state);\n"
        "\t\tif (!display->is_cont_splash_enabled)\n"
        "\t\t\tdsi_display_toggle_resync_fifo(display);\n"
    )
    text = replace_once(text, resync_anchor, resync_new, "resync decision history")
    return text


def validate(ctrl: str, phy: str, display: str) -> None:
    required = (
        CTRL_MARK, PHY_MARK, DISPLAY_MARK,
        "P276 314C0 %x %x %x %x %x %x",
        "P276 314C7 %x %x %x %x %x %x",
        "P276 314S0 %u %u %u %u %u %u",
        "P276 314S3 %x %x %u %u %u %u",
        "P276 314P0 %x %x %x %x %x %x",
        "P276 314P4 %x %x %x %x %x %x",
        "P276 314H e=%u c=%x l=%x s=%x sp=%u",
        "P276 314HF e=%u cl=%u pi=%u ul=%u cg=%x",
        "a52_p314_display_history(5, display, 0, 0, 0);",
        "a52_p314_display_history(6, display, clk, l_type, curr_state);",
        "P276 303 S00p p=%02x%02x%02x",
        "P276 312T1 %x %x %x %x %x %x",
        "P276 308T q=%u %x %x %x %x %x",
    )
    combined = ctrl + phy + display
    for token in required:
        if token not in combined:
            raise SystemExit("Phase314 required token missing: " + token)
    if ctrl.count(CTRL_MARK) != 1:
        raise SystemExit(f"Phase314 CTRL marker count {ctrl.count(CTRL_MARK)} != 1")
    if ctrl.count("a52_p314_ctrl_prestate(dsi_ctrl);") != 1:
        raise SystemExit("Phase314 exact-F0 CTRL snapshot call missing/not unique")
    if phy.count(PHY_MARK) != 1:
        raise SystemExit(f"Phase314 PHY marker count {phy.count(PHY_MARK)} != 1")
    if display.count(DISPLAY_MARK) != 1:
        raise SystemExit(
            f"Phase314 DISPLAY marker count {display.count(DISPLAY_MARK)} != 1")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    paths = {
        "ctrl": ns.root / CTRL_REL,
        "phy": ns.root / PHY_REL,
        "display": ns.root / DISPLAY_REL,
    }
    for p in paths.values():
        if not p.is_file():
            raise SystemExit("Phase314 source missing: " + str(p))

    if not ns.check_only:
        paths["ctrl"].write_text(patch_ctrl(paths["ctrl"].read_text()))
        paths["phy"].write_text(patch_phy(paths["phy"].read_text()))
        paths["display"].write_text(patch_display(paths["display"].read_text()))

    validate(
        paths["ctrl"].read_text(),
        paths["phy"].read_text(),
        paths["display"].read_text(),
    )
    print("Phase314 exhaustive first-F0 prestate recorder: PASS")


if __name__ == "__main__":
    main()
