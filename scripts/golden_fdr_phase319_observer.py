#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CTRL = Path('techpack/display/msm/dsi/dsi_ctrl.c')
HWC = Path('techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c')
MARK = 'A52_PHASE319_GOLDEN_FDR_SIXPOINT_TEMPORAL_OBSERVER_V1'


def once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase319 FDR {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_GOLDEN_DMA_DONE_REFERENCE_V1' not in text:
        raise SystemExit('Phase319 FDR requires the proven Golden DMA_DONE recorder first')

    anchor = 'static struct a52_gdm_snapshot a52_gdm;\n\n'
    helper = r'''static struct a52_gdm_snapshot a52_gdm;

/* A52_PHASE319_GOLDEN_FDR_SIXPOINT_TEMPORAL_OBSERVER_V1
 * Exact-F05A5A single-shot arm for the hardware-proven Golden FDR runtime.
 * q0/q1 are sampled in dsi_ctrl_hw_cmn.c immediately around SW_TRIGGER and
 * q2 is sampled after the normal completion outcome. No establishment,
 * recovery, clock, PHY, regulator, reset, retry or timeout behavior is added.
 */
static atomic_t a52_fdr319_state = ATOMIC_INIT(0); /* 0 idle, 1 armed, 2 done */
/* Build-time lineage identity only. Kept in .rodata so Image audit can prove
 * the existing hardware-proven Golden FDR DMA_DONE base is still present.
 * This has no runtime reads, writes, branches, logging or timing effect.
 */
static const char a52_fdr319_base_identity[] __attribute__((used)) =
	"A52_GOLDEN_DMA_DONE_REFERENCE_V1";
extern void a52_fdr319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl,
					 unsigned int point);

bool a52_fdr319_trace_active(void)
{
	return atomic_read(&a52_fdr319_state) == 1;
}

static void a52_fdr319_try_arm(struct dsi_ctrl *dsi_ctrl,
			       const struct mipi_dsi_msg *msg, u32 flags)
{
	const u8 *p;

	if (!dsi_ctrl || !msg || dsi_ctrl->cell_index != 0 ||
	    flags != DSI_CTRL_CMD_FETCH_MEMORY || msg->flags != 0x8 ||
	    msg->type != 0x29 || msg->tx_len != 3 || !msg->tx_buf)
		return;

	p = msg->tx_buf;
	if (p[0] != 0xf0 || p[1] != 0x5a || p[2] != 0x5a)
		return;

	if (atomic_cmpxchg(&a52_fdr319_state, 0, 1) == 0)
		pr_info("TG319F ARM c=0 in=%x mf=%x t=%x l=%u p=%02x%02x%02x\n",
			flags, msg->flags, msg->type, (unsigned int)msg->tx_len,
			p[0], p[1], p[2]);
}

'''
    text = once(text, anchor, helper, 'controller helper insertion')

    old = '\ta52_gdm_arm(dsi_ctrl, msg, *flags);\n\n\t/* Select the tx mode to transfer the command */\n'
    new = ('\ta52_gdm_arm(dsi_ctrl, msg, *flags);\n'
           '\ta52_fdr319_try_arm(dsi_ctrl, msg, *flags);\n\n'
           '\t/* Select the tx mode to transfer the command */\n')
    text = once(text, old, new, 'exact target arm hook')

    old = 'done:\n\tif (a52_gdm_armed(dsi_ctrl)) {\n'
    new = ('done:\n'
           '\tif (a52_fdr319_trace_active()) {\n'
           '\t\ta52_fdr319_debugbus_snapshot(&dsi_ctrl->hw, 2);\n'
           '\t\tatomic_set(&a52_fdr319_state, 2);\n'
           '\t}\n'
           '\tif (a52_gdm_armed(dsi_ctrl)) {\n')
    return once(text, old, new, 'q2 completion hook')


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text

    anchor = '#include "sde_dbg.h"\n\n'
    helper = r'''#include "sde_dbg.h"

/* A52_PHASE319_GOLDEN_FDR_SIXPOINT_TEMPORAL_OBSERVER_V1
 * Six raw Qualcomm DSI debug-bus selectors sampled on the exact Golden FDR
 * F05A5A transaction. Only DSI_DEBUG_BUS_CTL is written, then restored to the
 * exact saved selector. The restored control/status are recorded as r/z.
 */
extern bool a52_fdr319_trace_active(void);

static const u32 a52_fdr319_selectors[6] = {
	0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211,
};

void a52_fdr319_debugbus_snapshot(struct dsi_ctrl_hw *ctrl,
				  unsigned int point)
{
	u32 saved, restored_ctl, restored_status, i;
	u32 v[6];

	if (!a52_fdr319_trace_active() || !ctrl || !ctrl->base || ctrl->index != 0)
		return;

	saved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	for (i = 0; i < 6; i++) {
		DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_fdr319_selectors[i]);
		wmb();
		v[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);
	}
	DSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);
	wmb();
	restored_ctl = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);
	restored_status = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);

	pr_info("TG319F B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x\n",
		point, saved, v[0], v[1], v[2], v[3], v[4], v[5],
		restored_status, restored_ctl);
}

'''
    text = once(text, anchor, helper, 'hardware helper insertion')

    old = '''\t/* wait for writes to complete before kick off */
\twmb();

\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER))
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
}
'''
    new = '''\t/* wait for writes to complete before kick off */
\twmb();

\tif (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
\t\ta52_fdr319_debugbus_snapshot(ctrl, 0);
\t\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\t\ta52_fdr319_debugbus_snapshot(ctrl, 1);
\t}
}
'''
    text = once(text, old, new, 'memory q0/q1 hooks')

    old = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
}
'''
    new = '''void dsi_ctrl_hw_cmn_trigger_command_dma(struct dsi_ctrl_hw *ctrl)
{
\ta52_fdr319_debugbus_snapshot(ctrl, 0);
\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
\ta52_fdr319_debugbus_snapshot(ctrl, 1);
}
'''
    return once(text, old, new, 'deferred q0/q1 hooks')


def validate(ctrl: str, hwc: str) -> None:
    both = ctrl + hwc
    required = [
        MARK,
        'A52_GOLDEN_DMA_DONE_REFERENCE_V1',
        'TG319F ARM c=0',
        'TG319F B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x',
        '0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211',
        'a52_fdr319_debugbus_snapshot(ctrl, 0);',
        'a52_fdr319_debugbus_snapshot(ctrl, 1);',
        'a52_fdr319_debugbus_snapshot(&dsi_ctrl->hw, 2);',
        'restored_ctl = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);',
        'p[0] != 0xf0 || p[1] != 0x5a || p[2] != 0x5a',
    ]
    for token in required:
        if token not in both:
            raise SystemExit('Phase319 FDR required token missing: ' + token)
    if hwc.count('a52_fdr319_debugbus_snapshot(ctrl, 0);') != 2:
        raise SystemExit('Phase319 FDR q0 hook count must be exactly two trigger paths')
    if hwc.count('a52_fdr319_debugbus_snapshot(ctrl, 1);') != 2:
        raise SystemExit('Phase319 FDR q1 hook count must be exactly two trigger paths')
    if ctrl.count('a52_fdr319_debugbus_snapshot(&dsi_ctrl->hw, 2);') != 1:
        raise SystemExit('Phase319 FDR q2 hook count must be exactly one')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    cp = ns.root / CTRL
    hp = ns.root / HWC
    for p in (cp, hp):
        if not p.is_file():
            raise SystemExit('Phase319 FDR source missing: ' + str(p))
    if not ns.check_only:
        cp.write_text(patch_ctrl(cp.read_text(encoding='utf-8')), encoding='utf-8')
        hp.write_text(patch_hwc(hp.read_text(encoding='utf-8')), encoding='utf-8')
    validate(cp.read_text(encoding='utf-8'), hp.read_text(encoding='utf-8'))
    print('Phase319 Golden FDR six-selector q0/q1/q2 observer: PASS')


if __name__ == '__main__':
    main()
