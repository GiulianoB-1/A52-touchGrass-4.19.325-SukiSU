#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

DISPLAY = Path('drivers/a52_display/msm/dsi/dsi_display.c')
MARK = 'A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1'


def patch_display(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1' not in text:
        raise SystemExit('Phase286: Phase284 clock-causality prerequisite missing')
    if 'A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1' not in text:
        raise SystemExit('Phase286: Phase283 handoff prerequisite missing')

    # Use clk_get_rate only to gate the repair. No clock is re-read for target
    # selection; the targets are the already-derived ctrl->clk_freq values.
    if '#include <linux/clk.h>\n' not in text:
        anchor = '#include <linux/err.h>\n'
        if text.count(anchor) != 1:
            raise SystemExit('Phase286: linux/err.h include anchor missing/ambiguous')
        text = text.replace(anchor, anchor + '#include <linux/clk.h>\n', 1)

    start_token = 'static int dsi_display_set_clk_src(struct dsi_display *display)\n{'
    next_token = '\nstatic int dsi_display_phy_reset_config(struct dsi_display *display,'
    start = text.find(start_token)
    if start < 0:
        raise SystemExit('Phase286: dsi_display_set_clk_src() missing')
    end = text.find(next_token, start)
    if end < 0:
        raise SystemExit('Phase286: dsi_display_set_clk_src() end anchor missing')
    fn = text[start:end]
    if fn.count('\treturn 0;\n}') != 1:
        raise SystemExit('Phase286: expected exactly one success return in dsi_display_set_clk_src()')

    repair = r'''

	/* A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1
	 * Phase285 hardware evidence showed a valid non-zero DSI target was cached,
	 * then the RCG parent switch succeeded, but byte/pixel/byte-interface leaf,
	 * parent and PLL-source readbacks all remained 0 Hz.  The original splash
	 * rate-application call had been skipped before those targets existed and no
	 * later HS set-rate call occurred.
	 *
	 * Repair only that proven state: after source/parent selection, if a target
	 * is non-zero while the corresponding live HS clock is still zero, run the
	 * existing Qualcomm byte/pixel rate setters once. Normal non-zero clocks are
	 * untouched. The Phase284 M1/M2 probes remain in those setters and therefore
	 * record request/rc/actual/parent for hardware validation of this fix.
	 */
	display_for_each_ctrl(i, display) {
		unsigned long byte_actual, pixel_actual, intf_actual = 0;
		u32 zero_mask = 0;

		ctrl = &display->ctrl[i];
		if (!ctrl->ctrl)
			continue;
		if (!ctrl->ctrl->clk_info.hs_link_clks.byte_clk ||
				!ctrl->ctrl->clk_info.hs_link_clks.pixel_clk)
			continue;
		if (!ctrl->ctrl->clk_freq.byte_clk_rate ||
				!ctrl->ctrl->clk_freq.pix_clk_rate)
			continue;
		if (ctrl->ctrl->clk_info.hs_link_clks.byte_intf_clk &&
				!ctrl->ctrl->clk_freq.byte_intf_clk_rate)
			continue;

		byte_actual = clk_get_rate(ctrl->ctrl->clk_info.hs_link_clks.byte_clk);
		pixel_actual = clk_get_rate(ctrl->ctrl->clk_info.hs_link_clks.pixel_clk);
		if (ctrl->ctrl->clk_info.hs_link_clks.byte_intf_clk)
			intf_actual = clk_get_rate(
				ctrl->ctrl->clk_info.hs_link_clks.byte_intf_clk);

		if (!byte_actual)
			zero_mask |= BIT(0);
		if (!pixel_actual)
			zero_mask |= BIT(1);
		if (ctrl->ctrl->clk_info.hs_link_clks.byte_intf_clk && !intf_actual)
			zero_mask |= BIT(2);
		if (!zero_mask)
			continue;

		a52_ackfr_record("P276 286F c=%u z=%x", ctrl->ctrl->cell_index,
			zero_mask);
		if (zero_mask & (BIT(0) | BIT(2))) {
			rc = dsi_clk_set_byte_clk_rate(display->dsi_clk_handle,
				ctrl->ctrl->clk_freq.byte_clk_rate,
				ctrl->ctrl->clk_freq.byte_intf_clk_rate, i);
			a52_ackfr_record("P276 286B c=%u rc=%d",
				ctrl->ctrl->cell_index, rc);
			if (rc)
				return rc;
		}

		if (zero_mask & BIT(1)) {
			rc = dsi_clk_set_pixel_clk_rate(display->dsi_clk_handle,
				ctrl->ctrl->clk_freq.pix_clk_rate, i);
			a52_ackfr_record("P276 286P c=%u rc=%d",
				ctrl->ctrl->cell_index, rc);
			if (rc)
				return rc;
		}

		a52_ackfr_record("P276 286A c=%u b=%lx p=%lx i=%lx",
			ctrl->ctrl->cell_index,
			clk_get_rate(ctrl->ctrl->clk_info.hs_link_clks.byte_clk),
			clk_get_rate(ctrl->ctrl->clk_info.hs_link_clks.pixel_clk),
			ctrl->ctrl->clk_info.hs_link_clks.byte_intf_clk ?
			clk_get_rate(ctrl->ctrl->clk_info.hs_link_clks.byte_intf_clk) : 0);
	}
'''
    fn = fn.replace('\treturn 0;\n}', repair + '\n\treturn 0;\n}', 1)
    return text[:start] + fn + text[end:]


def validate(text: str) -> None:
    need = [
        MARK,
        '#include <linux/clk.h>',
        'static int dsi_display_set_clk_src(struct dsi_display *display)',
        'P276 286F c=%u z=%x',
        'P276 286B c=%u rc=%d',
        'P276 286P c=%u rc=%d',
        'P276 286A c=%u b=%lx p=%lx i=%lx',
        'dsi_clk_set_byte_clk_rate(display->dsi_clk_handle,',
        'dsi_clk_set_pixel_clk_rate(display->dsi_clk_handle,',
        'ctrl->ctrl->clk_freq.byte_clk_rate',
        'ctrl->ctrl->clk_freq.pix_clk_rate',
        'ctrl->ctrl->clk_freq.byte_intf_clk_rate',
        'zero_mask |= BIT(0);',
        'zero_mask |= BIT(1);',
        'zero_mask |= BIT(2);',
    ]
    for token in need:
        if token not in text:
            raise SystemExit('Phase286 validation missing: ' + token)
    start = text.index('static int dsi_display_set_clk_src(struct dsi_display *display)')
    fix = text.index(MARK, start)
    nxt = text.index('static int dsi_display_phy_reset_config', start)
    if not (start < fix < nxt):
        raise SystemExit('Phase286 repair is not inside dsi_display_set_clk_src()')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    path = ns.root / DISPLAY
    if not path.is_file():
        raise SystemExit('Phase286: dsi_display.c missing')
    text = path.read_text()
    if not ns.check_only:
        text = patch_display(text)
        path.write_text(text)
    validate(path.read_text())
    print('Phase286 HS clock handoff repair: PASS')


if __name__ == '__main__':
    main()
