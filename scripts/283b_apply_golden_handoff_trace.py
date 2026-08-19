#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
DISPLAY = Path('drivers/a52_display/msm/dsi/dsi_display.c')
MARK = 'A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_display(text: str) -> str:
    if MARK in text:
        return text

    text = replace_one(
        text,
        '#include "dsi_pwr.h"\n',
        '#include "dsi_pwr.h"\n'
        '#include <linux/pm_runtime.h>\n'
        '#include <linux/a52_ack_secure_flight_recorder.h>\n'
        'extern bool a52_p276r_deep_active(void);\n',
        'Phase283 handoff recorder include')

    anchor = '''static const struct of_device_id dsi_display_dt_match[] = {\n\t{.compatible = "qcom,dsi-display"},\n\t{}\n};\n'''
    helper = r'''static const struct of_device_id dsi_display_dt_match[] = {
	{.compatible = "qcom,dsi-display"},
	{}
};

/* A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1
 * Golden continuous-splash setup marks splash ownership, configures the DSI
 * ISR, votes all DSI clocks, and relies on the clock-manager callbacks to vote
 * controller/PHY regulators before the first panel command. Phase283 records
 * that ownership state without changing it.
 */
void a52_p283_display_handoff_snapshot(struct dsi_ctrl *target,
		unsigned int point)
{
	struct dsi_display *display;
	struct dsi_display_ctrl *dc;
	struct regulator *ctrl_gdsc = NULL, *phy_gdsc = NULL;
	int i, j, ctrl_en = -2, phy_en = -2;
	u32 ctrl_ref = 0, phy_ref = 0;

	if (!a52_p276r_deep_active() || !target)
		return;

	for (i = 0; i < MAX_DSI_ACTIVE_DISPLAY; i++) {
		display = boot_displays[i].disp;
		if (!display)
			continue;

		for (j = 0; j < display->ctrl_count; j++) {
			dc = &display->ctrl[j];
			if (!dc->ctrl || dc->ctrl != target)
				continue;

			ctrl_ref = target->pwr_info.digital.refcount;
			if (target->pwr_info.digital.count &&
					target->pwr_info.digital.vregs)
				ctrl_gdsc = target->pwr_info.digital.vregs[0].vreg;
			if (ctrl_gdsc)
				ctrl_en = regulator_is_enabled(ctrl_gdsc);

			if (dc->phy) {
				phy_ref = dc->phy->pwr_info.digital.refcount;
				if (dc->phy->pwr_info.digital.count &&
						dc->phy->pwr_info.digital.vregs)
					phy_gdsc = dc->phy->pwr_info.digital.vregs[0].vreg;
				if (phy_gdsc)
					phy_en = regulator_is_enabled(phy_gdsc);
			}

			a52_ackfr_record("P276 283D0 q=%u s=%u p=%u u=%u c=%u x=%u t=%u",
				point, display->is_cont_splash_enabled, dc->phy_enabled,
				display->ulps_enabled, display->clamp_enabled,
				display->phy_idle_power_off, display->is_tpg_enabled);
			a52_ackfr_record("P276 283D1 q=%u k=%u m=%u v=%u n=%u r=%u",
				point, display->clk_master_idx, display->cmd_master_idx,
				display->video_master_idx, display->ctrl_count,
				display->cmd_engine_refcount);
			a52_ackfr_record("P276 283D2 q=%u cg=%d/%u pg=%d/%u pm=%u",
				point, ctrl_en, ctrl_ref, phy_en, phy_ref,
				pm_runtime_active(&target->pdev->dev) ? 1 : 0);
#if defined(CONFIG_DISPLAY_SAMSUNG)
			if (display->panel && display->panel->panel_private) {
				struct samsung_display_driver_data *vdd =
					display->panel->panel_private;
				a52_ackfr_record("P276 283D3 q=%u ss=%u",
					point, vdd->samsung_splash_enabled ? 1 : 0);
			}
#endif
			return;
		}
	}

	a52_ackfr_record("P276 283DX q=%u i=%u", point, target->cell_index);
}
'''
    return replace_one(text, anchor, helper, 'Phase283 handoff helper insertion')


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE283_DSI_SHARED_ENGINE_PHY_TRACE_V1' not in text:
        raise SystemExit('Phase283 shared-path prerequisite missing')

    text = replace_one(
        text,
        'extern void a52_p283_phy_snapshot(unsigned int index, unsigned int point);\n',
        'extern void a52_p283_phy_snapshot(unsigned int index, unsigned int point);\n'
        'extern void a52_p283_display_handoff_snapshot(struct dsi_ctrl *target, unsigned int point);\n'
        '/* ' + MARK + ' */\n',
        'Phase283 handoff extern')

    text = replace_one(
        text,
        '\ta52_p283_phy_snapshot(dsi_ctrl->cell_index, point);\n',
        '\ta52_p283_phy_snapshot(dsi_ctrl->cell_index, point);\n'
        '\ta52_p283_display_handoff_snapshot(dsi_ctrl, point);\n',
        'Phase283 handoff snapshot call')
    return text


def validate(dsi: str, display: str) -> None:
    for token in [
        MARK,
        'extern void a52_p283_display_handoff_snapshot',
        'a52_p283_display_handoff_snapshot(dsi_ctrl, point);',
        'a52_p283_shared_snapshot(dsi_ctrl, 0);',
        'a52_p283_shared_snapshot(dsi_ctrl, 1);',
        'a52_p283_shared_snapshot(dsi_ctrl, 2);',
    ]:
        if token not in dsi:
            raise SystemExit('Phase283 handoff DSI marker missing: ' + token)

    for token in [
        MARK,
        'void a52_p283_display_handoff_snapshot',
        'P276 283D0 q=%u s=%u p=%u u=%u c=%u x=%u t=%u',
        'P276 283D1 q=%u k=%u m=%u v=%u n=%u r=%u',
        'P276 283D2 q=%u cg=%d/%u pg=%d/%u pm=%u',
        'P276 283D3 q=%u ss=%u',
        'regulator_is_enabled(ctrl_gdsc)',
        'regulator_is_enabled(phy_gdsc)',
        'pm_runtime_active(&target->pdev->dev)',
    ]:
        if token not in display:
            raise SystemExit('Phase283 handoff display marker missing: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    dsi_path = args.root / DSI
    display_path = args.root / DISPLAY
    if not dsi_path.is_file() or not display_path.is_file():
        raise SystemExit('Phase283 handoff DSI/display source missing')

    dsi = dsi_path.read_text()
    display = display_path.read_text()
    if not args.check_only:
        display = patch_display(display)
        dsi = patch_dsi(dsi)
        display_path.write_text(display)
        dsi_path.write_text(dsi)
    validate(dsi, display)
    print('Phase283 Golden continuous-splash handoff trace: PASS')


if __name__ == '__main__':
    main()
