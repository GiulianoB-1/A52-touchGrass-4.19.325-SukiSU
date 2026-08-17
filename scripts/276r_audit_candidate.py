#!/usr/bin/env python3
from pathlib import Path
import json, sys

if len(sys.argv) != 2:
    raise SystemExit('usage: 276r_audit_candidate.py <phase276r-out>')
r = Path(sys.argv[1])
for p in [
    'compile/Image', 'config/final.config', 'package/boot.img', 'package/Image.gz',
    'source/dsi_panel.c', 'source/dsi_display.c', 'source/dsi_ctrl.c',
    'audit/phase276r-deep-path-parity-before.txt',
]:
    if not (r/p).is_file() or (r/p).stat().st_size == 0:
        raise SystemExit('missing ' + p)
if (r/'config/final.config').read_bytes() != (r/'audit/phase276-final.config').read_bytes():
    raise SystemExit('config mutated')

for f, toks in {
    'source/dsi_panel.c': [
        'A52_PHASE276R_DEEP_TARGET_TRACKER_V1',
        'P276 T Q p=%d g=%d y=%d',
        'a52_p276r_deep_active',
    ],
    'source/dsi_display.c': [
        'A52_PHASE276R_DSI_HOST_DEEP_FRONTIER_V1',
        'P276 D H s=0',
        'dsi_ctrl_cmd_transfer',
    ],
    'source/dsi_ctrl.c': [
        'A52_PHASE276R_DSI_CTRL_DEEP_FRONTIER_V1',
        'A52_PHASE276R_FINAL_DMA_ROOT_CAUSE_RECORDER_V5',
        '#include "dsi_ctrl_reg.h"',
        'P276 D C s=0 f=%x',
        'P276 D M s=0 f=%x mt=%u l=%u',
        'P276 D K s=0 f=%x',
        'P276 D W s=0',
        'P276 D M w=0',
        'P276 D M w=1 v=%d',
        'P276 P S st=%x dn=%u a=%d im=%x ir=%u',
        'P276 P P e=%u d=%u',
        'P276 H K o=%llx l=%u h=%x',
        'P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x',
        'P276 H E e=%llx',
    ],
}.items():
    s = (r/f).read_text()
    for t in toks:
        if t not in s:
            raise SystemExit(f'{f}: missing {t}')

img = (r/'compile/Image').read_bytes()
for t in [
    'P276 T Q p=%d g=%d y=%d',
    'P276 D H s=0',
    'P276 D C s=0 f=%x',
    'P276 D M s=0 f=%x mt=%u l=%u',
    'P276 D K s=0 f=%x',
    'P276 D W s=0',
    'P276 D M w=0',
    'P276 D M w=1 v=%d',
    'P276 T O i=%d p=0 mt=%u tl=%u fl=%x',
    'P276 P S st=%x dn=%u a=%d im=%x ir=%u',
    'P276 P P e=%u d=%u',
    'P276 H K o=%llx l=%u h=%x',
    'P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x',
    'P276 H E e=%llx',
]:
    if t.encode() not in img:
        raise SystemExit('Image marker missing ' + t)

par = (r/'audit/phase276r-deep-path-parity-before.txt').read_text()
for t in [
    'function=dsi_host_transfer',
    'function=dsi_ctrl_cmd_transfer',
    'function=dsi_message_tx',
    'function=dsi_kickoff_msg_tx',
    'function=dsi_ctrl_dma_cmd_wait_for_done',
    'function=dsi_ctrl_hw_cmn_kickoff_command',
    'function=dsi_ctrl_hw_cmn_get_error_status',
    'function=dsi_catalog_cmn_init',
    'path_exact_match=1',
    'low_level_hw_exact_match=1',
    'all_exact_match=1',
]:
    if t not in par:
        raise SystemExit('parity missing ' + t)

print(json.dumps({
    'status': 'phase276r-v5-audit-pass',
    'deep_functions_touchgrass_exact_before_instrumentation': True,
    'low_level_dma_hw_functions_touchgrass_exact_before_instrumentation': True,
    'post_trigger_hw_readback_present': True,
    'config_unchanged': True,
    'hardware_validated': False,
}, indent=2))
