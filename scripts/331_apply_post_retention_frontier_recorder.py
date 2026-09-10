#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

CTRL = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1'

REC_OLD = '''\tif (unlikely(atomic_read(&a52_r280_retained)))\n\t\treturn;\n'''
REC_NEW = '''\t/* A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1\n\t * Phase280 still freezes the recorder globally.  Only Phase331's tightly\n\t * scoped post-timeout breadcrumbs are admitted after that freeze.\n\t */\n\tif (unlikely(atomic_read(&a52_r280_retained)) &&\n\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&\n\t      fmt[3] == '6' && fmt[4] == ' ' && fmt[5] == '3' &&\n\t      fmt[6] == '3' && fmt[7] == '1'))\n\t\treturn;\n'''

CTRL_OLD_HEAD = '''\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n\t\t}\n\t\tif (status & mask) {\n\t\t\tstatus |= (DSI_CMD_MODE_DMA_DONE | DSI_BTA_DONE);\n\t\t\tdsi_hw_ops.clear_interrupt_status(&dsi_ctrl->hw,\n\t\t\t\t\tstatus);\n\t\t\tDSI_CTRL_WARN(dsi_ctrl,\n\t\t\t\t\t"dma_tx done but irq not triggered\\n");\n\t\t} else {\n'''
CTRL_NEW_HEAD = '''\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n\t\t\ta52_ackfr_record("P276 331A q=2 st=%x m=%x", status, mask);\n\t\t}\n\t\tif (status & mask) {\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331B q=2 b=1 st=%x", status);\n\t\t\tstatus |= (DSI_CMD_MODE_DMA_DONE | DSI_BTA_DONE);\n\t\t\tdsi_hw_ops.clear_interrupt_status(&dsi_ctrl->hw,\n\t\t\t\t\tstatus);\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331C q=2 b=1 st=%x", status);\n\t\t\tDSI_CTRL_WARN(dsi_ctrl,\n\t\t\t\t\t"dma_tx done but irq not triggered\\n");\n\t\t} else {\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331B q=2 b=0 st=%x", status);\n'''

CTRL_OLD_SAMSUNG = '#if defined(CONFIG_DISPLAY_SAMSUNG)\n\t\t\tstruct samsung_display_driver_data *vdd = ss_get_vdd(dsi_ctrl->cell_index);\n\n\t\t\t/* check physical display connection */\n\t\t\tif (gpio_is_valid(vdd->ub_con_det.gpio)) {\n\t\t\t\tpr_err("[SDE] ub_con_det.gpio(%d) level=%d\\n",\n\t\t\t\t\t\tvdd->ub_con_det.gpio,\n\t\t\t\t\t\tgpio_get_value(vdd->ub_con_det.gpio));\n\t\t\t}\n\n'
CTRL_NEW_SAMSUNG = '#if defined(CONFIG_DISPLAY_SAMSUNG)\n\t\t\tstruct samsung_display_driver_data *vdd = ss_get_vdd(dsi_ctrl->cell_index);\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331D q=2 ss=1 v=%u", !!vdd);\n\n\t\t\t/* check physical display connection */\n\t\t\tif (gpio_is_valid(vdd->ub_con_det.gpio)) {\n\t\t\t\tpr_err("[SDE] ub_con_det.gpio(%d) level=%d\\n",\n\t\t\t\t\t\tvdd->ub_con_det.gpio,\n\t\t\t\t\t\tgpio_get_value(vdd->ub_con_det.gpio));\n\t\t\t}\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331E q=2 gpio=1");\n\n'

CTRL_OLD_DUMP = '''\t\t\tif (!dsi_ctrl->esd_check_underway && !vdd->panel_dead) {\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 P P e=%u d=%u",\n\t\t\t\t\t\tdsi_ctrl->esd_check_underway, vdd->panel_dead);\n\t\t\t\tSDE_DBG_DUMP("all", "dbg_bus", "vbif_dbg_bus", "panic");\n\t\t\t}\n'''
CTRL_NEW_DUMP = '''\t\t\tif (!dsi_ctrl->esd_check_underway && !vdd->panel_dead) {\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 P P e=%u d=%u",\n\t\t\t\t\t\tdsi_ctrl->esd_check_underway, vdd->panel_dead);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 331F q=2 dump=0");\n\t\t\t\tSDE_DBG_DUMP("all", "dbg_bus", "vbif_dbg_bus", "panic");\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 331G q=2 dump=1");\n\t\t\t}\n'''

CTRL_OLD_TAIL = '''#endif\n\t\t\tDSI_CTRL_ERR(dsi_ctrl,\n\t\t\t\t\t"Command transfer failed\\n");\n\t\t}\n\t\tdsi_ctrl_disable_status_interrupt(dsi_ctrl,\n\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE);\n\t}\n'''
CTRL_NEW_TAIL = '''#endif\n\t\t\tif (a52_p276r_deep_active())\n\t\t\t\ta52_ackfr_record("P276 331H q=2 b=0");\n\t\t\tDSI_CTRL_ERR(dsi_ctrl,\n\t\t\t\t\t"Command transfer failed\\n");\n\t\t}\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 331I q=2 dis=0");\n\t\tdsi_ctrl_disable_status_interrupt(dsi_ctrl,\n\t\t\t\t\tDSI_SINT_CMD_MODE_DMA_DONE);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 331J q=2 dis=1");\n\t}\n'''

def one(s,o,n,label):
    c=s.count(o)
    if c != 1:
        raise SystemExit(f'Phase331 {label}: expected 1 match, found {c}')
    return s.replace(o,n,1)

def patch_ctrl(s):
    if MARK in s:
        return s
    if 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' not in s or 'a52_p319_debugbus_snapshot' not in s:
        raise SystemExit('Phase331 requires the Phase319+ timeout lineage')
    s=one(s, CTRL_OLD_HEAD, CTRL_NEW_HEAD, 'timeout branch head')
    s=one(s, CTRL_OLD_SAMSUNG, CTRL_NEW_SAMSUNG, 'Samsung retrieval/gpio frontier')
    s=one(s, CTRL_OLD_DUMP, CTRL_NEW_DUMP, 'Samsung debug dump bracket')
    s=one(s, CTRL_OLD_TAIL, CTRL_NEW_TAIL, 'timeout branch tail')
    anchor='/* A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1 */\nextern void a52_ackfr_retain_timeout_snapshot(void);\n'
    s=one(s, anchor, anchor+'/* '+MARK+' */\n', 'source marker')
    return s

def patch_rec(s):
    if MARK in s:
        return s
    if 'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1' not in s:
        raise SystemExit('Phase331 recorder requires Phase280 retention latch')
    return one(s, REC_OLD, REC_NEW, 'selective post-retention admission')

def validate(c,r):
    reqc=[MARK,'P276 280Z q=2','P276 331A q=2 st=%x m=%x','P276 331B q=2 b=1 st=%x','P276 331C q=2 b=1 st=%x','P276 331B q=2 b=0 st=%x','P276 331D q=2 ss=1 v=%u','P276 331E q=2 gpio=1','P276 331F q=2 dump=0','P276 331G q=2 dump=1','P276 331H q=2 b=0','P276 331I q=2 dis=0','P276 331J q=2 dis=1']
    for x in reqc:
        if x not in c: raise SystemExit('Phase331 ctrl token missing: '+x)
    if MARK not in r or "fmt[7] == '1'" not in r:
        raise SystemExit('Phase331 recorder selective-admission marker missing')
    if not (c.index('P276 280Z q=2') < c.index('a52_ackfr_retain_timeout_snapshot();') < c.index('P276 331A q=2')):
        raise SystemExit('Phase331 post-retention ordering invalid')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--check-only',action='store_true'); a=ap.parse_args()
    cp=a.root/CTRL; rp=a.root/REC
    if not cp.is_file() or not rp.is_file(): raise SystemExit('Phase331 source missing')
    c=cp.read_text(); r=rp.read_text()
    if not a.check_only:
        c=patch_ctrl(c); r=patch_rec(r); cp.write_text(c); rp.write_text(r)
    validate(cp.read_text(),rp.read_text())
    print('Phase331 post-retention frontier recorder: PASS')
if __name__=='__main__': main()
