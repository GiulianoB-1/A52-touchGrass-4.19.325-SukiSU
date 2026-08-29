#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL_GKI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
HWC_GKI = Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')
CTRL_GOLDEN = Path('dsi_ctrl.c')
HWC_GOLDEN = Path('dsi_ctrl_hw_cmn.c')
MARK = 'A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase319 {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def helper(flavor: str) -> str:
    if flavor == 'gki':
        return r'''/* A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1
 * Matched Golden/GKI exact-F0 temporal observer for six raw Qualcomm DSI
 * debug-bus selectors identified by the Phase317 post-outcome comparison.
 * q0 is the last observer immediately before SW_TRIGGER, q1 is the first
 * observer immediately after SW_TRIGGER, and q2 is after completion outcome.
 * Only DSI_DEBUG_BUS_CTL is written, and the original selector is restored.
 * No clock/PHY/reset/regulator/delay/retry/repair behavior is introduced.
 */
static const u32 a52_p319_selectors[6] = {
	0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211,
};

void a52_p319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	u32 saved, restored, i;
	u32 v[6];

	if (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	for (i = 0; i < 6; i++) {
		DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p319_selectors[i]);
		wmb();
		v[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
	restored = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);

	a52_ackfr_record("P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x",
		point, saved, v[0], v[1], v[2], v[3], v[4], v[5], restored);
}

'''
    return r'''/* A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1
 * Matched Golden/GKI exact-F0 temporal observer for six raw Qualcomm DSI
 * debug-bus selectors identified by the Phase317 post-outcome comparison.
 * q0 is the last observer immediately before SW_TRIGGER, q1 is the first
 * observer immediately after SW_TRIGGER, and q2 is after completion outcome.
 * Only DSI_DEBUG_BUS_CTL is written, and the original selector is restored.
 * No clock/PHY/reset/regulator/delay/retry/repair behavior is introduced.
 */
static const u32 a52_g319_selectors[6] = {
	0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211,
};

void a52_g319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)
{
	u32 saved, restored, i;
	u32 v[6];

	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	for (i = 0; i < 6; i++) {
		DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_g319_selectors[i]);
		wmb();
		v[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
	restored = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);

	pr_info("TG319 B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x\n",
		point, saved, v[0], v[1], v[2], v[3], v[4], v[5], restored);
}

'''


def patch_hwc_gki(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1' not in text:
        raise SystemExit('Phase319 GKI requires exact Phase316 reconstruction')

    anchor = 'static void a52_p307_hw_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point)\n{\n'
    text = one(text, anchor, helper('gki') + anchor, 'GKI helper insertion')

    old = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_p307_hw_snapshot(ctrl, 0);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_p307_hw_snapshot(ctrl, 1);\n\t\tif (a52_p293_gdm_trace_active()) {\n'''
    new = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_p307_hw_snapshot(ctrl, 0);\n\t\ta52_p319_debugbus_snapshot(ctrl, 0);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_p319_debugbus_snapshot(ctrl, 1);\n\t\ta52_p307_hw_snapshot(ctrl, 1);\n\t\tif (a52_p293_gdm_trace_active()) {\n'''
    text = one(text, old, new, 'GKI memory q0/q1')

    old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_p307_hw_snapshot(ctrl, 0);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_p307_hw_snapshot(ctrl, 1);\n\tif (a52_p293_gdm_trace_active()) {\n'''
    new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_p307_hw_snapshot(ctrl, 0);\n\ta52_p319_debugbus_snapshot(ctrl, 0);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_p319_debugbus_snapshot(ctrl, 1);\n\ta52_p307_hw_snapshot(ctrl, 1);\n\tif (a52_p293_gdm_trace_active()) {\n'''
    return one(text, old, new, 'GKI deferred q0/q1')


def patch_ctrl_gki(text: str) -> str:
    if 'a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);' in text:
        return text
    decl = 'extern void a52_p316_fault_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n'
    text = one(text, decl, decl + 'extern void a52_p319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n',
               'GKI declaration')
    old = '''\tif (a52_p293_gdm_armed(dsi_ctrl)) {\n\t\ta52_ackfr_record("P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x",\n'''
    new = '''\tif (a52_p293_gdm_armed(dsi_ctrl)) {\n\t\ta52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);\n\t\ta52_ackfr_record("P276 307C q=2 st=%x ln=%x ck=%x cc=%x in=%x",\n'''
    return one(text, old, new, 'GKI q2')


def patch_hwc_golden(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE315G_GOLDEN_F0_FULL_PRESTATE_REFERENCE_V1' not in text:
        raise SystemExit('Phase319 Golden requires exact Phase315G reconstruction')

    anchor = 'void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,\n\t\t\t      unsigned int point)\n{\n'
    text = one(text, anchor, helper('golden') + anchor, 'Golden helper insertion')

    old = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_g315_full_snapshot(ctrl);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_g315_launch_snapshot(ctrl, 1);\n\t}\n'''
    new = '''\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {\n\t\ta52_g315_full_snapshot(ctrl);\n\t\ta52_g319_debugbus_snapshot(ctrl, 0);\n\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\t\ta52_g319_debugbus_snapshot(ctrl, 1);\n\t\ta52_g315_launch_snapshot(ctrl, 1);\n\t}\n'''
    text = one(text, old, new, 'Golden memory q0/q1')

    old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_g315_full_snapshot(ctrl);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_g315_launch_snapshot(ctrl, 1);\n}\n'''
    new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)\n{\n\ta52_g315_full_snapshot(ctrl);\n\ta52_g319_debugbus_snapshot(ctrl, 0);\n\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\n\ta52_g319_debugbus_snapshot(ctrl, 1);\n\ta52_g315_launch_snapshot(ctrl, 1);\n}\n'''
    return one(text, old, new, 'Golden deferred q0/q1')


def patch_ctrl_golden(text: str) -> str:
    if 'a52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);' in text:
        return text
    decl = '''extern void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,\n\t\t\t\t     unsigned int point);\n'''
    text = one(text, decl, decl + 'extern void a52_g319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n',
               'Golden declaration')
    old = '''\tif (a52_g315_armed(dsi_ctrl)) {\n\t\ta52_g315_launch_snapshot(&dsi_ctrl->hw, 2);\n'''
    new = '''\tif (a52_g315_armed(dsi_ctrl)) {\n\t\ta52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);\n\t\ta52_g315_launch_snapshot(&dsi_ctrl->hw, 2);\n'''
    return one(text, old, new, 'Golden q2')


def validate(ctrl: str, hwc: str, flavor: str) -> None:
    both = ctrl + hwc
    required = [MARK, 'DSI_DEBUG_BUS_CTL', 'DSI_DEBUG_BUS_STATUS',
                '0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211']
    if flavor == 'gki':
        required += [
            'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x',
            'a52_p319_debugbus_snapshot(ctrl, 0);',
            'a52_p319_debugbus_snapshot(ctrl, 1);',
            'a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);',
        ]
    else:
        required += [
            'TG319 B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x',
            'a52_g319_debugbus_snapshot(ctrl, 0);',
            'a52_g319_debugbus_snapshot(ctrl, 1);',
            'a52_g319_debugbus_snapshot(&dsi_ctrl->hw, 2);',
        ]
    for token in required:
        if token not in both:
            raise SystemExit('Phase319 required token missing: ' + token)
    if 'A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1' in both:
        raise SystemExit('Phase319 must not inherit the Phase317 256-selector sweep')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True,
                    help='GKI common root for --flavor gki, DSI directory for --flavor golden')
    ap.add_argument('--flavor', choices=('gki', 'golden'), required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()

    if ns.flavor == 'gki':
        cp = ns.root / CTRL_GKI
        hp = ns.root / HWC_GKI
    else:
        cp = ns.root / CTRL_GOLDEN
        hp = ns.root / HWC_GOLDEN

    for p in (cp, hp):
        if not p.is_file():
            raise SystemExit('Phase319 source missing: ' + str(p))

    if not ns.check_only:
        if ns.flavor == 'gki':
            hp.write_text(patch_hwc_gki(hp.read_text()))
            cp.write_text(patch_ctrl_gki(cp.read_text()))
        else:
            hp.write_text(patch_hwc_golden(hp.read_text()))
            cp.write_text(patch_ctrl_golden(cp.read_text()))

    validate(cp.read_text(), hp.read_text(), ns.flavor)
    print(f'Phase319 {ns.flavor} six-selector q0/q1/q2 temporal observer: PASS')


if __name__ == '__main__':
    main()
