#!/usr/bin/env python3
from pathlib import Path

PATCHER = Path('scripts/292_apply_full_dma_chain_sticky_recorder.py')
MARK = '# A52_PHASE292_PATCHER_ANCHOR_DRIFT_FIX_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Phase292 patcher fix {label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


def main() -> None:
    if not PATCHER.is_file():
        raise SystemExit(f'Phase292 patcher fix missing: {PATCHER}')
    text = PATCHER.read_text()
    if MARK in text:
        print('Phase292 patcher anchor-drift fix already applied: PASS')
        return

    # Avoid duplicating dsi_ctrl_reg.h while adding the DSI_R32 helper include.
    text = replace_one(
        text,
        '''    text = one(text, '#include "dsi_ctrl_hw.h"\\n',
               '#include "dsi_ctrl_hw.h"\\n#include "dsi_ctrl_reg.h"\\n#include "dsi_hw.h"\\n',
               'read-only register includes')\n''',
        '''    text = one(text, '#include "dsi_ctrl_reg.h"\\n',
               '#include "dsi_ctrl_reg.h"\\n#include "dsi_hw.h"\\n',
               'read-only register include')\n''',
        'DSI include',
    )

    # The inherited tree has the same two-tab ARM statements in both the normal
    # message kickoff and deferred broadcast-trigger functions. Scope Phase292
    # ARM0/ARM1 to the normal kickoff function that owns the Phase282 target.
    arm_start = "    arm0 = '\\t\\tatomic_set(&dsi_ctrl->dma_irq_trig, 0);\\n'\n"
    arm_end = '    # Restrict WAIT anchors to the DMA wait worker, not the video-frame wait.\n'
    a = text.find(arm_start)
    b = text.find(arm_end, a)
    if a < 0 or b < 0:
        raise SystemExit('Phase292 patcher fix ARM block anchors missing')
    arm_block = text[a:b]
    arm_block = replace_one(
        arm_block,
        arm_start,
        """    ka, kb, kickoff_fn = fn_slice(text,\n        'static void dsi_kickoff_msg_tx(',\n        '\\nstatic void dsi_ctrl_validate_msg_flags(', 'message kickoff')\n""" + arm_start,
        'ARM function scope',
    )
    arm_block = replace_one(
        arm_block,
        '    text = one(text, arm0,',
        '    kickoff_fn = one(kickoff_fn, arm0,',
        'ARM0 target',
    )
    arm_block = replace_one(
        arm_block,
        '    text = one(text, arm1,',
        '    kickoff_fn = one(kickoff_fn, arm1,',
        'ARM1 target',
    )
    arm_block = arm_block.rstrip() + '\n    text = text[:ka] + kickoff_fn + text[kb:]\n\n'
    text = text[:a] + arm_block + text[b:]

    # Phase289 inserted snapshots before the translated status read. Anchor the
    # new timeout bank directly on that real status assignment instead.
    text = replace_one(
        text,
        """    timeout = '''\\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n\\t\\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\\n'''\n""",
        "    timeout = '\\t\\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\\n'\n",
        'timeout status anchor',
    )

    # Current TouchGrass names the ISR setup helper with a leading underscore.
    text = replace_one(
        text,
        "'\\nstatic int dsi_ctrl_register_isr(', 'ISR')",
        "'\\nstatic int _dsi_ctrl_setup_isr(', 'ISR')",
        'ISR end anchor',
    )

    # Phase291 recovery lives directly in the HS set-rate function on this tree.
    text = replace_one(
        text,
        "    anchor = 'static int dsi_core_clk_set_rate('\n",
        "    anchor = 'static int dsi_link_hs_clk_set_rate('\n",
        'clock declaration anchor',
    )

    text = text.replace('def patch_dsi(text: str) -> str:\n', MARK + '\ndef patch_dsi(text: str) -> str:\n', 1)
    PATCHER.write_text(text)
    print('Phase292 patcher anchor-drift fix applied: PASS')


if __name__ == '__main__':
    main()
