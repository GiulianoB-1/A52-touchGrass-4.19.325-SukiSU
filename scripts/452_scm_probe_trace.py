#!/usr/bin/env python3
"""Phase 452: observe upstream SCM bind without changing its SMC policy.

Only inserts one probe-success dev_info(). No SCM calls, dload changes,
charger writes, reset controller changes or ICE changes.
"""
from pathlib import Path
import argparse, hashlib

TARGET=Path("drivers/firmware/qcom_scm.c")
BASE="624b71d3e63c029b2c14d89d39053d18cc76517e8453c1a23f012b9f27c8df2f"
PATCHED="ac216893ba4841a1745fdd92059baf1c33ab1dbe2b603575b76fb3d32e838fba"
OLD='\t__get_convention();\n\n\t/*'
NEW=('\t__get_convention();\n'
     '\tdev_info(&pdev->dev, "P452 SCM PROBE rc=0 dload_default=%d\\n",\n'
     '\t\tIS_ENABLED(CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT));\n\n\t/*')
def sha(s):return hashlib.sha256(s.encode()).hexdigest()
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    path=a.root/TARGET
    s=path.read_text()
    if not a.check_only:
        if sha(s)!=BASE:raise RuntimeError("P452 unexpected prior qcom_scm.c source SHA")
        if s.count(OLD)!=1:raise RuntimeError("P452 SCM probe-success marker anchor absent")
        s=s.replace(OLD,NEW,1)
        path.write_text(s)
    if sha(s)!=PATCHED or "P452 SCM PROBE rc=0 dload_default=%d" not in s:
        raise RuntimeError("P452 SCM trace source not exact pinned baseline + dev_info")
    print("P452 SCM PROBE TRACE PASS: read-only success marker, other SCM paths byte-identical")
if __name__=="__main__":main()
