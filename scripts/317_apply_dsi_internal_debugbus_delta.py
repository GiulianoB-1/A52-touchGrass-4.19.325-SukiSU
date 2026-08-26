#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1'

def one(text, old, new, label):
    n=text.count(old)
    if n!=1: raise SystemExit(f'Phase317 {label}: expected 1 match, found {n}')
    return text.replace(old,new,1)

def helper(flavor):
    if flavor=='gki':
        return r'''/* A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1
 * Matched Golden/GKI post-outcome internal DSI debug-bus observer.
 * This intentionally touches only DSI_DEBUG_BUS_CTL after the exact F0
 * completion wait has already succeeded or timed out. The original selector
 * is restored before returning. No functional DSI/PHY/clock/reset/regulator
 * state is changed by this observer.
 */
#define A52_P317_TEST_MASK(id, tp) ((((id) & 0x3) << 12) | (((tp) & 0x3f) << 4) | BIT(0))

void a52_p317_debugbus_snapshot(struct dsi_ctrl_hw *ctrl)
{
	u32 saved, block, test, n;
	u32 v[4];

	if (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	a52_ackfr_record("P276 317H ctl=%x", saved);
	for (block = 0; block < 4; block++) {
		for (test = 0; test < 64; test += 4) {
			for (n = 0; n < 4; n++) {
				DSI_W32(ctrl, DSI_DEBUG_BUS_CTL,
					A52_P317_TEST_MASK(block, test + n));
				wmb();
				v[n] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
			}
			a52_ackfr_record("P276 317B b=%u t=%u %x %x %x %x",
				block, test, v[0], v[1], v[2], v[3]);
		}
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
	a52_ackfr_record("P276 317Z ctl=%x st=%x", saved,
		DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS));
}

'''
    return r'''/* A52_PHASE317_DSI_INTERNAL_DEBUGBUS_DELTA_V1
 * Matched Golden/GKI post-outcome internal DSI debug-bus observer.
 * This intentionally touches only DSI_DEBUG_BUS_CTL after the exact F0
 * completion wait has already succeeded or timed out. The original selector
 * is restored before returning. No functional DSI/PHY/clock/reset/regulator
 * state is changed by this observer.
 */
#define A52_G317_TEST_MASK(id, tp) ((((id) & 0x3) << 12) | (((tp) & 0x3f) << 4) | BIT(0))

void a52_g317_debugbus_snapshot(struct dsi_ctrl_hw *ctrl)
{
	u32 saved, block, test, n;
	u32 v[4];

	if (!a52_g315_trace_active() || !ctrl || !ctrl->base)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	pr_info("TG317 H ctl=%x\n", saved);
	for (block = 0; block < 4; block++) {
		for (test = 0; test < 64; test += 4) {
			for (n = 0; n < 4; n++) {
				DSI_W32(ctrl, DSI_DEBUG_BUS_CTL,
					A52_G317_TEST_MASK(block, test + n));
				wmb();
				v[n] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
			}
			pr_info("TG317 B%u T%u %x %x %x %x\n",
				block, test, v[0], v[1], v[2], v[3]);
		}
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
	pr_info("TG317 Z ctl=%x st=%x\n", saved,
		DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS));
}

'''

def patch_hwc(text, flavor):
    token = 'a52_p317_debugbus_snapshot(struct dsi_ctrl_hw *ctrl)' if flavor == 'gki' else 'a52_g317_debugbus_snapshot(struct dsi_ctrl_hw *ctrl)'
    if token in text: return text
    if flavor=='gki':
        anchor='/* A52_PHASE307_V3_PHY_CLOCKLANE_CORRELATION_V1\n'
    else:
        anchor='void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,\n'
    return one(text,anchor,helper(flavor)+anchor,'HWC helper anchor')

def patch_ctrl(text,flavor):
    token = 'a52_p317_debugbus_snapshot(&dsi_ctrl->hw);' if flavor == 'gki' else 'a52_g317_debugbus_snapshot(&dsi_ctrl->hw);'
    if token in text: return text
    if flavor=='gki':
        decl='extern void a52_p316_fault_snapshot(struct dsi_ctrl_hw *ctrl, unsigned int point);\n'
        text=one(text,decl,decl+'extern void a52_p317_debugbus_snapshot(struct dsi_ctrl_hw *ctrl);\n','GKI declaration')
        old='''\t\ta52_ackfr_record("P276 303 S08 ret=%d irq=%d in=%x st=%x", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS));\n\t}\n'''
        new='''\t\ta52_ackfr_record("P276 303 S08 ret=%d irq=%d in=%x st=%x", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS));\n\t\ta52_p317_debugbus_snapshot(&dsi_ctrl->hw);\n\t}\n'''
        return one(text,old,new,'GKI q2 hook')
    decl='extern void a52_g315_launch_snapshot(struct dsi_ctrl_hw *ctrl,\n\t\t\t\t     unsigned int point);\n'
    text=one(text,decl,decl+'extern void a52_g317_debugbus_snapshot(struct dsi_ctrl_hw *ctrl);\n','Golden declaration')
    old='''\tif (a52_g315_armed(dsi_ctrl)) {\n\t\ta52_g315_launch_snapshot(&dsi_ctrl->hw, 2);\n\t\tpr_info("TG315 DONE ret=%d irq=%d\\n", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tatomic_set(&a52_g315_state, 2);\n\t}\n'''
    new='''\tif (a52_g315_armed(dsi_ctrl)) {\n\t\ta52_g315_launch_snapshot(&dsi_ctrl->hw, 2);\n\t\tpr_info("TG315 DONE ret=%d irq=%d\\n", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\ta52_g317_debugbus_snapshot(&dsi_ctrl->hw);\n\t\tatomic_set(&a52_g315_state, 2);\n\t}\n'''
    return one(text,old,new,'Golden q2 hook')

def validate(ctrl,hwc,flavor):
    both=ctrl+hwc
    req=[MARK,'DSI_DEBUG_BUS_CTL','DSI_DEBUG_BUS_STATUS']
    if flavor=='gki': req += ['P276 317H ctl=%x','P276 317B b=%u t=%u %x %x %x %x','P276 317Z ctl=%x st=%x','a52_p317_debugbus_snapshot(&dsi_ctrl->hw);']
    else:req += ['TG317 H ctl=%x','TG317 B%u T%u %x %x %x %x','TG317 Z ctl=%x st=%x','a52_g317_debugbus_snapshot(&dsi_ctrl->hw);']
    for t in req:
        if t not in both: raise SystemExit('Phase317 missing '+t)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--flavor',choices=['gki','golden'],required=True);p.add_argument('--check-only',action='store_true');a=p.parse_args()
    ctrl=a.root/'dsi_ctrl.c';hwc=a.root/'dsi_ctrl_hw_cmn.c'
    if not ctrl.is_file() or not hwc.is_file(): raise SystemExit('source missing')
    if not a.check_only:
        hwc.write_text(patch_hwc(hwc.read_text(),a.flavor));ctrl.write_text(patch_ctrl(ctrl.read_text(),a.flavor))
    validate(ctrl.read_text(),hwc.read_text(),a.flavor)
    print('Phase317',a.flavor,'post-outcome matched DSI internal debug-bus observer: PASS')
if __name__=='__main__': main()
