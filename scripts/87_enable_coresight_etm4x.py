#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 87_enable_coresight_etm4x.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
cfg = root / "arch/arm64/configs/a52xq_defconfig"

text = cfg.read_text()

old = "# CONFIG_CORESIGHT_SOURCE_ETM4X is not set\n"
new = "CONFIG_CORESIGHT_SOURCE_ETM4X=y\n"

if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("CORESIGHT_SOURCE_ETM4X config state missing")

cfg.write_text(text)

checks = {
    "coresight": "CONFIG_CORESIGHT=y\n" in text,
    "tmc": "CONFIG_CORESIGHT_LINK_AND_SINK_TMC=y\n" in text,
    "etm4x": "CONFIG_CORESIGHT_SOURCE_ETM4X=y\n" in text,
    "perf_events": "CONFIG_PERF_EVENTS=y\n" in text,
}

failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("phase87 ETM4X staging audit failed: " + ", ".join(failed))

print("phase87_coresight_etm4x=enabled")
for k in sorted(checks):
    print(f"{k}=PASS")
