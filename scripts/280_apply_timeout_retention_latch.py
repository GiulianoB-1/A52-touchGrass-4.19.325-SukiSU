#!/usr/bin/env python3
from pathlib import Path
import argparse
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
DSI=Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK='A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1'
def one(t,o,n):
 c=t.count(o)
 if c!=1: raise SystemExit(f'expected one anchor, got {c}')
 return t.replace(o,n,1)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--check-only',action='store_true'); a=ap.parse_args()
 rp=a.root/REC; dp=a.root/DSI; r=rp.read_text(); d=dp.read_text()
 if not a.check_only and MARK not in r:
  r=one(r,'static atomic_t a52_r179_backend_seen = ATOMIC_INIT(0);\n','static atomic_t a52_r179_backend_seen = ATOMIC_INIT(0);\n/* '+MARK+' */\nstatic atomic_t a52_r280_retained = ATOMIC_INIT(0);\n')
  r=one(r,'\tif (!fmt)\n\t\treturn;\n','\tif (!fmt)\n\t\treturn;\n\tif (unlikely(atomic_read(&a52_r280_retained)))\n\t\treturn;\n')
  r=one(r,'EXPORT_SYMBOL_GPL(a52_ackfr_record);\n','EXPORT_SYMBOL_GPL(a52_ackfr_record);\n\nvoid a52_ackfr_retain_timeout_snapshot(void)\n{\n\tatomic_set(&a52_r280_retained, 1);\n}\nEXPORT_SYMBOL_GPL(a52_ackfr_retain_timeout_snapshot);\n')
  rp.write_text(r)
 if not a.check_only and MARK not in d:
  anchor='''extern void a52_p279_display_fault_snapshot(unsigned int point);\n'''
  d=one(d,anchor,anchor+'/* '+MARK+' */\nextern void a52_ackfr_retain_timeout_snapshot(void);\n')
  old='''\t\tif (a52_p276r_deep_active() && dsi_hw_ops.get_error_status)\n\t\t\ta52_ackfr_record("P276 H E e=%llx",\n\t\t\t\t(unsigned long long)dsi_hw_ops.get_error_status(&dsi_ctrl->hw));\n\t\tif (status & mask) {\n'''
  new='''\t\tif (a52_p276r_deep_active() && dsi_hw_ops.get_error_status)\n\t\t\ta52_ackfr_record("P276 H E e=%llx",\n\t\t\t\t(unsigned long long)dsi_hw_ops.get_error_status(&dsi_ctrl->hw));\n\t\tif (a52_p276r_deep_active()) {\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n\t\t}\n\t\tif (status & mask) {\n'''
  d=one(d,old,new); dp.write_text(d)
 r=rp.read_text(); d=dp.read_text()
 for x in [MARK,'a52_r280_retained','a52_ackfr_retain_timeout_snapshot']:
  if x not in r: raise SystemExit('recorder marker missing '+x)
 for x in [MARK,'P276 280Z q=2','a52_ackfr_retain_timeout_snapshot();']:
  if x not in d: raise SystemExit('DSI marker missing '+x)
 if not (d.index('a52_p278_display_smmu_snapshot(2);') < d.index('a52_p279_display_fault_snapshot(2);') < d.index('P276 H E e=%llx') < d.index('P276 280Z q=2') < d.index('a52_ackfr_retain_timeout_snapshot();')): raise SystemExit('ordering invalid')
 print('Phase280 timeout retention latch: PASS')
if __name__=='__main__': main()
