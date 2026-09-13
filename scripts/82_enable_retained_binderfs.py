#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1])
cfg = root / "arch/arm64/configs/a52xq_defconfig"
s = cfg.read_text()

disabled = "# CONFIG_ANDROID_BINDERFS is not set"
if disabled not in s:
    raise SystemExit("Phase81 BinderFS-disabled anchor missing")

s = s.replace(disabled, "CONFIG_ANDROID_BINDERFS=y", 1)
cfg.write_text(s)

print("Phase82: retained Linux 4.19 BinderFS re-enabled")
