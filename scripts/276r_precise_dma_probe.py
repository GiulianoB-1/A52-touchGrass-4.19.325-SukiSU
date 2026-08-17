#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <dsi_ctrl.c>")
p = Path(sys.argv[1])
s = p.read_text()

MARK = 'A52_PHASE276R_FINAL_DMA_ROOT_CAUSE_RECORDER_V5'
SNAP = 'P276 P S st=%x dn=%u a=%d im=%x ir=%u'
PANIC = 'P276 P P e=%u d=%u'
PROG = 'P276 H K o=%llx l=%u h=%x'
READBACK = 'P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x'
ERR = 'P276 H E e=%llx'

for token in [MARK, SNAP, PANIC, PROG, READBACK, ERR]:
    if token in s:
        raise SystemExit(f'V5 token already present unexpectedly: {token}')

# Named register offsets only. This include is observation support; no HW writes.
hw_inc = '#include "dsi_ctrl_hw.h"\n'
reg_inc = '#include "dsi_ctrl_reg.h"\n'
if s.count(hw_inc) != 1:
    raise SystemExit(f'dsi_ctrl_hw include count {s.count(hw_inc)}, expected 1')
if reg_inc not in s:
    s = s.replace(hw_inc, hw_inc + reg_inc, 1)

anchor = 'extern bool a52_p276r_deep_active(void);\n'
if s.count(anchor) != 1:
    raise SystemExit(f'deep-active declaration count {s.count(anchor)}, expected 1')
s = s.replace(anchor, anchor + '/* ' + MARK + ' */\n', 1)

# Exact synchronous embedded memory-DMA branch already identified on hardware as s=4.
# Record the software values handed to the Golden-identical low-level kickoff, then
# take one post-trigger readback of ordinary config/status registers. No register is
# read between the low-level programming writes and the software trigger.
old = '''\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=0");\n\t\t\t\tdsi_hw_ops.kickoff_command(\n\t\t\t\t\t\t&dsi_ctrl->hw,\n\t\t\t\t\t\tcmd_mem,\n\t\t\t\t\t\thw_flags);\n\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=1");\n'''
new = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n\t\t\t\t\t\t(unsigned long long)cmd_mem->offset,\n\t\t\t\t\t\t(unsigned int)cmd_mem->length, hw_flags);\n\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=0");\n\t\t\t\tdsi_hw_ops.kickoff_command(\n\t\t\t\t\t\t&dsi_ctrl->hw,\n\t\t\t\t\t\tcmd_mem,\n\t\t\t\t\t\thw_flags);\n\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=1");\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x",\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_CTRL),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_STATUS),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_COMMAND_MODE_DMA_CTRL),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_INT_CTRL),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_CLK_CTRL),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_CLK_STATUS),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_DMA_CMD_OFFSET),\n\t\t\t\t\t\treadl_relaxed(dsi_ctrl->hw.base + DSI_DMA_CMD_LENGTH));\n'''
if s.count(old) != 1:
    raise SystemExit(f'synchronous memory kickoff anchor count {s.count(old)}, expected 1')
s = s.replace(old, new, 1)

# Keep the proven V3 status discriminator and add one read-only hardware error snapshot.
# Golden dsi_ctrl_hw_cmn_get_error_status() performs register reads only; clearing is
# implemented by a separate clear_error_status() operation.
old = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (status & mask) {\n'''
new = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 P S st=%x dn=%u a=%d im=%x ir=%u",\n\t\t\t\tstatus, !!(status & mask),\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\t\tdsi_ctrl->irq_info.irq_stat_mask,\n\t\t\t\tdsi_ctrl->irq_info.irq_stat_refcount[DSI_SINT_CMD_MODE_DMA_DONE]);\n\t\tif (a52_p276r_deep_active() && dsi_hw_ops.get_error_status)\n\t\t\ta52_ackfr_record("P276 H E e=%llx",\n\t\t\t\t(unsigned long long)dsi_hw_ops.get_error_status(&dsi_ctrl->hw));\n\t\tif (status & mask) {\n'''
if s.count(old) != 1:
    raise SystemExit(f'DMA timeout/status anchor count {s.count(old)}, expected 1')
s = s.replace(old, new, 1)

old = '''\t\t\tif (!dsi_ctrl->esd_check_underway && !vdd->panel_dead) {\n\t\t\t\tSDE_DBG_DUMP("all", "dbg_bus", "vbif_dbg_bus", "panic");\n\t\t\t}\n'''
new = '''\t\t\tif (!dsi_ctrl->esd_check_underway && !vdd->panel_dead) {\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 P P e=%u d=%u",\n\t\t\t\t\t\tdsi_ctrl->esd_check_underway, vdd->panel_dead);\n\t\t\t\tSDE_DBG_DUMP("all", "dbg_bus", "vbif_dbg_bus", "panic");\n\t\t\t}\n'''
if s.count(old) != 1:
    raise SystemExit(f'Samsung panic marker anchor count {s.count(old)}, expected 1')
s = s.replace(old, new, 1)

for token in [MARK, SNAP, PANIC, PROG, READBACK, ERR]:
    if s.count(token) != 1:
        raise SystemExit(f'V5 token {token!r} count {s.count(token)}, expected 1')
if s.count(reg_inc) != 1:
    raise SystemExit(f'dsi_ctrl_reg include count {s.count(reg_inc)}, expected 1')

p.write_text(s)
print('Phase276R final DMA root-cause recorder V5 staged')
