#!/usr/bin/env python3
from pathlib import Path

P = Path('scripts/289_apply_sticky_fifo_timeout_retention.py')
OLD_TAG = "trig_old = '''void dsi_ctrl_hw_cmn_trigger_command_dma"
NEW_TAG = '# Insert the deferred-path F4 directly after the production SW_TRIGGER'

text = P.read_text()
if NEW_TAG in text:
    print('Phase291 Phase289 anchor repair: already present')
    raise SystemExit(0)
if OLD_TAG not in text:
    raise SystemExit('Phase291 Phase289 anchor repair: old brittle trigger block not found')

start = text.index("    trig_old = '''void dsi_ctrl_hw_cmn_trigger_command_dma")
end_line = "    text = one(text, trig_old, trig_new, 'Phase289 deferred actual trigger slot')\n"
end = text.index(end_line, start) + len(end_line)

replacement = '''    # Insert the deferred-path F4 directly after the production SW_TRIGGER
    # write inside trigger_command_dma(). Do not match the whole function:
    # Phase287 and later passive provenance phases may append read-only records
    # after the write, and those must not make Phase289 source patching brittle.
    trig_start = text.index('void dsi_ctrl_hw_cmn_trigger_command_dma(')
    trig_end = text.index('\\n}\\n', trig_start) + 3
    trig_fn = text[trig_start:trig_end]
    trig_write = '\\tDSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);\\n'
    trig_snap = trig_write + ''' + "'''" + '''\\tif (a52_p289_fifo_trace_active())
\\t\\ta52_p289_snapshot_record(5, 5, (u32)ctrl->index, 1U,
\\t\\t\\t(u32)DSI_R32(ctrl, DSI_STATUS), (u32)DSI_R32(ctrl, DSI_FIFO_STATUS),
\\t\\t\\t(u32)DSI_R32(ctrl, DSI_INT_CTRL));\\n''' + "'''" + '''
    trig_fn = one(trig_fn, trig_write, trig_snap,
                  'Phase289 deferred actual trigger slot')
    text = text[:trig_start] + trig_fn + text[trig_end:]
'''

text = text[:start] + replacement + text[end:]
P.write_text(text)

check = P.read_text()
if NEW_TAG not in check or OLD_TAG in check:
    raise SystemExit('Phase291 Phase289 anchor repair verification failed')
print('Phase291 Phase289 anchor repair: PASS')
