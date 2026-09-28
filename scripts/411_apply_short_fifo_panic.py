#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE411_SHORT_FIFO_PANIC_V1"
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase411 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1" not in text:
        raise SystemExit("Phase411 requires Phase409 DSI lineage")
    if "P276 394F route old=%x" in text or "P276 394F route new=%x" in text:
        raise SystemExit("Phase411 requires Phase394 exact-F0 reroute removed")

    secure = '''	if (dsi_ctrl->secure_mode) {
		SDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_CASE1);
		*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;
		*flags |= DSI_CTRL_CMD_FIFO_STORE;
		DSI_CTRL_DEBUG(dsi_ctrl,
				"override to TPG during secure session\n");
		return;
	}

'''
    force = secure + '''	/* A52_PHASE411_SHORT_FIFO_PANIC_V1
	 * Causal bypass experiment: commands which physically fit in the
	 * controller's FIFO never touch the command GEM/IOVA memory-fetch path.
	 * Longer commands retain the inherited FETCH_MEMORY/non-embedded path.
	 */
	if ((cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE) {
		*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;
		*flags |= DSI_CTRL_CMD_FIFO_STORE;
		return;
	}

'''
    text = one(text, secure, force, "short FIFO route")

    setup = '''	/* Select the tx mode to transfer the command */
	dsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);

'''
    setup_new = setup + '''	if (a52_p293_gdm_armed(dsi_ctrl))
		pr_emerg("A52P411 exact-F0 route flags=%x fifo=%u fetch=%u len=%zu\\n",
			*flags, !!(*flags & DSI_CTRL_CMD_FIFO_STORE),
			!!(*flags & DSI_CTRL_CMD_FETCH_MEMORY), msg->tx_len);

'''
    text = one(text, setup, setup_new, "exact-F0 route printk")

    # Panic only after the existing timeout snapshots and Phase345 flush have
    # been emitted. At this point the DSI transaction is still powered/clocked;
    # no new out-of-window MMIO reads are introduced.
    anchor = '''		if (a52_p293_gdm_armed(dsi_ctrl)) {
			a52_p345_flush(&dsi_ctrl->hw);
			a52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
				(unsigned int)a52_p276r_deep_active(), status, mask);
			a52_p338_emit_atomic_latch();
			a52_ackfr_record("P276 280Z q=2");
			a52_ackfr_retain_timeout_snapshot();
			a52_ackfr_record("P276 332B q=2 retained=1");
		}
'''
    panic_block = anchor + '''
		pr_emerg("A52P411 DMA_DONE TIMEOUT ctrl=%u status=%x irq=%d iova=%llx size=%u pwr=%u host=%u cmd=%u\\n",
			dsi_ctrl->cell_index, status,
			atomic_read(&dsi_ctrl->dma_irq_trig),
			(unsigned long long)dsi_ctrl->cmd_buffer_iova,
			dsi_ctrl->cmd_buffer_size,
			dsi_ctrl->current_state.power_state,
			dsi_ctrl->current_state.host_initialized,
			dsi_ctrl->current_state.cmd_engine_state);
		panic("A52P411 DSI DMA_DONE timeout ctrl=%u status=%x",
			dsi_ctrl->cell_index, status);
'''
    text = one(text, anchor, panic_block, "timeout panic")

    text += (
        "\n/* " + MARK + " */\n"
        "static const char a52_p411_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def validate(text: str) -> None:
    for token in (
        MARK,
        "(cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE",
        "*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;",
        "*flags |= DSI_CTRL_CMD_FIFO_STORE;",
        'pr_emerg("A52P411 exact-F0 route',
        'pr_emerg("A52P411 DMA_DONE TIMEOUT',
        'panic("A52P411 DSI DMA_DONE timeout',
        "a52_p345_flush(&dsi_ctrl->hw);",
        "a52_ackfr_retain_timeout_snapshot();",
    ):
        if token not in text:
            raise SystemExit("Phase411 missing token: " + token)

    # The experiment must not modify the hardware-MMIO sampling sites.
    if text.count('panic("A52P411 DSI DMA_DONE timeout') != 1:
        raise SystemExit("Phase411 panic site count wrong")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    p = ns.root / DSI
    if not p.is_file():
        raise SystemExit("Phase411 dsi_ctrl.c missing")
    if not ns.check_only:
        p.write_text(patch(p.read_text(errors="replace")))
    validate(p.read_text(errors="replace"))
    print("Phase411 short-FIFO causal bypass + timeout panic: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
