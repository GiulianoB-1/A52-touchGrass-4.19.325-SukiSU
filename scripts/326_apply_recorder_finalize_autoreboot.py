#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE326_RECORDER_FINALIZE_AUTOREBOOT_V1'
DELAY_MS = 3000


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase326 {label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if 'a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);' not in text:
        raise SystemExit('Phase326 requires exact Phase319 q2 observer hook')
    if 'A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1' in text:
        raise SystemExit('Phase326 dsi_ctrl unexpectedly contains Phase325 RCG marker')

    text = one(
        text,
        '#include <linux/of_irq.h>\n',
        '#include <linux/of_irq.h>\n#include <linux/reboot.h>\n#include <linux/workqueue.h>\n',
        'reboot/workqueue includes',
    )

    decl = 'extern void a52_ackfr_record(const char *fmt, ...);\n'
    helper = rf'''extern void a52_ackfr_record(const char *fmt, ...);

/* {MARK}
 * Test-control-only finalizer for the exact Phase319 target recorder.
 * Display/clock/PHY behavior is untouched until the armed target has already
 * emitted its q2 completion evidence and the existing timeout diagnostics have
 * returned. Then a one-shot delayed work item reboots from process context so
 * RAMOOPS can be recovered without an arbitrary multi-minute wait.
 */
#define A52_P326_REBOOT_DELAY_MS {DELAY_MS}U
static atomic_t a52_p326_reboot_armed = ATOMIC_INIT(0);

static void a52_p326_reboot_workfn(struct work_struct *work)
{{
	(void)work;
	a52_ackfr_record("P276 326R reboot now");
	kernel_restart("a52_phase326_capture_done");
}}

static DECLARE_DELAYED_WORK(a52_p326_reboot_work, a52_p326_reboot_workfn);

static void a52_p326_schedule_reboot(void)
{{
	if (atomic_cmpxchg(&a52_p326_reboot_armed, 0, 1) != 0)
		return;

	a52_ackfr_record("P276 326R final q2=1 delay_ms=%u",
		A52_P326_REBOOT_DELAY_MS);
	schedule_delayed_work(&a52_p326_reboot_work,
		msecs_to_jiffies(A52_P326_REBOOT_DELAY_MS));
}}
'''
    text = one(text, decl, helper, 'finalizer helper insertion')

    old = '''\tu32 status;\n\tu32 mask = DSI_CMD_MODE_DMA_DONE;\n\tstruct dsi_ctrl_hw_ops dsi_hw_ops;\n'''
    new = '''\tu32 status;\n\tu32 mask = DSI_CMD_MODE_DMA_DONE;\n\tstruct dsi_ctrl_hw_ops dsi_hw_ops;\n\tbool a52_p326_q2_recorded = false;\n'''
    text = one(text, old, new, 'q2 completion local')

    old = '''\t\ta52_ackfr_record("P276 303 S08 ret=%d irq=%d in=%x st=%x", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS));\n\t}\n'''
    new = '''\t\ta52_ackfr_record("P276 303 S08 ret=%d irq=%d in=%x st=%x", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS));\n\t\ta52_p326_q2_recorded = true;\n\t}\n'''
    text = one(text, old, new, 'q2 recorded latch')

    old = '''done:\n\tdsi_ctrl->dma_wait_queued = false;\n}\n'''
    new = '''done:\n\tif (a52_p326_q2_recorded)\n\t\ta52_p326_schedule_reboot();\n\tdsi_ctrl->dma_wait_queued = false;\n}\n'''
    return one(text, old, new, 'post-diagnostics finalizer arm')


def validate(text: str) -> None:
    required = [
        MARK,
        '#include <linux/reboot.h>',
        '#include <linux/workqueue.h>',
        '#define A52_P326_REBOOT_DELAY_MS 3000U',
        'static DECLARE_DELAYED_WORK(a52_p326_reboot_work, a52_p326_reboot_workfn);',
        'atomic_cmpxchg(&a52_p326_reboot_armed, 0, 1)',
        'P276 326R final q2=1 delay_ms=%u',
        'P276 326R reboot now',
        'kernel_restart("a52_phase326_capture_done")',
        'bool a52_p326_q2_recorded = false;',
        'a52_p326_q2_recorded = true;',
        'if (a52_p326_q2_recorded)\n\t\ta52_p326_schedule_reboot();',
        'a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);',
        'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d',
        'P276 303 DONE success=0 target=0/8/20/29/3',
        'a52_ackfr_retain_timeout_snapshot();',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase326 required token missing: ' + token)

    if text.count(MARK) != 1:
        raise SystemExit(f'Phase326 marker count must be 1, got {text.count(MARK)}')
    if text.count('kernel_restart("a52_phase326_capture_done")') != 1:
        raise SystemExit('Phase326 must contain exactly one capture-done restart')
    if text.count('a52_p326_schedule_reboot();') != 1:
        raise SystemExit('Phase326 finalizer must be armed from exactly one site')

    # Fail closed on ordering: q2 evidence first, existing timeout/fault tail next,
    # and only then the finalizer at the function's done label.
    q2 = text.index('a52_p319_debugbus_snapshot(&dsi_ctrl->hw, 2);')
    tail = text.index('a52_ackfr_retain_timeout_snapshot();', q2)
    arm = text.index('a52_p326_schedule_reboot();', tail)
    if not q2 < tail < arm:
        raise SystemExit('Phase326 ordering invariant failed: q2 < timeout-tail < reboot-arm')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    p = ns.root / TARGET
    if not p.is_file():
        raise SystemExit('Phase326 source missing: ' + str(p))
    text = p.read_text()
    if not ns.check_only:
        text = patch(text)
        p.write_text(text)
    validate(p.read_text())
    print('Phase326 recorder-finalize delayed autoreboot: PASS')


if __name__ == '__main__':
    main()
