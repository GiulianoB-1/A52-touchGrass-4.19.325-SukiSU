#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CLK_REL = Path('drivers/a52_display/msm/dsi/dsi_clk_manager.c')
MARK = 'A52_PHASE310_PASSIVE_CLOCK_SNAPSHOT_SANITIZER_V1'
START = 'static int a52_p310_clk_chain_has(struct clk *clk, const char *expected)\n'
END = 'static int _get_clk_mngr_index(struct dsi_clk_mngr *mngr,\n'

SAFE_HELPER = r'''/* A52_PHASE310_PASSIVE_CLOCK_SNAPSHOT_SANITIZER_V1
 * Do not perform provider rate or parent queries from the exact-F0 observer.
 * On this tree those queries can enter provider callbacks and the 10nm VCO
 * recalc path can itself establish handoff_resources. The masks below are
 * derived only from Phase310 observer-owned lifecycle latches. Independent
 * physical source/mux/branch state is captured from Lagoon DISP_CC.
 */
static u32 a52_p310_clk_mask(struct dsi_link_hs_clk_info *hs,
		struct dsi_link_lp_clk_info *lp, bool prepared)
{
	u32 mask = 0;
	int hs_on;
	int lp_on = atomic_read(&a52_p310_lp_enabled);

	if (prepared)
		hs_on = atomic_read(&a52_p310_hs_prepared);
	else
		hs_on = atomic_read(&a52_p310_hs_enabled);

	if (hs_on && hs) {
		if (hs->byte_clk)
			mask |= BIT(0);
		if (hs->pixel_clk)
			mask |= BIT(1);
		if (hs->byte_intf_clk)
			mask |= BIT(2);
	}
	if (lp_on && lp && lp->esc_clk)
		mask |= BIT(3);
	return mask;
}

void a52_p310_clk_snapshot(unsigned int index, unsigned int point)
{
	struct dsi_link_hs_clk_info *hs = READ_ONCE(a52_p310_last_hs);
	struct dsi_link_lp_clk_info *lp = READ_ONCE(a52_p310_last_lp);
	u32 em = a52_p310_clk_mask(hs, lp, false);
	u32 pm = a52_p310_clk_mask(hs, lp, true);

	a52_ackfr_record("P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d",
		point, index,
		atomic_read(&a52_p310_sr_in), atomic_read(&a52_p310_sr_run),
		atomic_read(&a52_p310_sr_skip), atomic_read(&a52_p310_sr_ok),
		atomic_read(&a52_p310_sr_rc), atomic_read(&a52_p310_sr_idx));
	a52_ackfr_record("P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d",
		point, atomic_read(&a52_p310_prepare_in),
		atomic_read(&a52_p310_prepare_ok), atomic_read(&a52_p310_prepare_rc),
		atomic_read(&a52_p310_enable_in), atomic_read(&a52_p310_enable_ok),
		atomic_read(&a52_p310_enable_rc), atomic_read(&a52_p310_hs_start),
		atomic_read(&a52_p310_hs_stop));
	a52_ackfr_record("P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d",
		point, atomic_read(&a52_p310_disable_in),
		atomic_read(&a52_p310_unprepare_in), atomic_read(&a52_p310_lp_start),
		atomic_read(&a52_p310_lp_stop), atomic_read(&a52_p310_update_in),
		atomic_read(&a52_p310_update_ok), atomic_read(&a52_p310_update_rc),
		atomic_read(&a52_p310_last_type), atomic_read(&a52_p310_last_state),
		atomic_read(&a52_p310_last_enable));
	a52_ackfr_record("P276 310E q=%u em=%x pm=%x", point, em, pm);
}

'''


def observer_region(text: str) -> str:
    a = text.find(MARK)
    b = text.find(END, a if a >= 0 else 0)
    if a < 0 or b < 0 or b <= a:
        raise SystemExit('Phase310 passive sanitizer: final helper bounds missing')
    return text[a:b]


def sanitize(text: str) -> str:
    if MARK in text:
        return text
    a = text.find(START)
    if a < 0:
        raise SystemExit('Phase310 passive sanitizer: unsafe helper start missing')
    b = text.find(END, a)
    if b < 0:
        raise SystemExit('Phase310 passive sanitizer: helper end missing')
    out = text[:a] + SAFE_HELPER + text[b:]
    region = observer_region(out)
    for forbidden in ('clk_get_rate(', 'clk_get_parent(', '__clk_is_enabled(', '__clk_is_prepared(', 'a52_p310_clk_chain_has('):
        if forbidden in region:
            raise SystemExit('Phase310 passive sanitizer forbidden helper token remains: ' + forbidden)
    return out


def validate(text: str) -> None:
    required = [
        MARK,
        'P276 310E q=%u em=%x pm=%x',
        'atomic_read(&a52_p310_hs_enabled)',
        'atomic_read(&a52_p310_hs_prepared)',
        'atomic_read(&a52_p310_lp_enabled)',
        'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase310 passive sanitizer required token missing: ' + token)
    region = observer_region(text)
    for forbidden in ('clk_get_rate(', 'clk_get_parent(', '__clk_is_enabled(', '__clk_is_prepared(', 'a52_p310_clk_chain_has('):
        if forbidden in region:
            raise SystemExit('Phase310 passive sanitizer forbidden helper token remains: ' + forbidden)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    path = ns.root / CLK_REL
    if not path.is_file():
        raise SystemExit('Phase310 passive sanitizer source missing: ' + str(path))
    if not ns.check_only:
        path.write_text(sanitize(path.read_text()))
    validate(path.read_text())
    print('Phase310 exact-F0 CCF snapshot passive-safety sanitizer: PASS')


if __name__ == '__main__':
    main()
