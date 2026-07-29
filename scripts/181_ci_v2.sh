#!/usr/bin/env bash
set -Eeuo pipefail

python3 - <<'PY'
from pathlib import Path

path = Path('scripts/181_apply.py')
text = path.read_text(encoding='utf-8')
old = '''        "\\tret = driver_sysfs_add(dev);\\n",
        "\\tret = driver_sysfs_add(dev);\\n"
        "\\tif (a52_display_probe_device(dev))\\n"
        "\\t\\ta52_ackfr_record(\\"DISP RP sysfs dev=%s rc=%d\\", dev_name(dev), ret);\\n",
'''
new = '''        "\\tret = driver_sysfs_add(dev);\\n"
        "\\tif (a52_run40_preprobe_target(dev)) {\\n",
        "\\tret = driver_sysfs_add(dev);\\n"
        "\\tif (a52_display_probe_device(dev))\\n"
        "\\t\\ta52_ackfr_record(\\"DISP RP sysfs dev=%s rc=%d\\", dev_name(dev), ret);\\n"
        "\\tif (a52_run40_preprobe_target(dev)) {\\n",
'''
if text.count(old) != 1:
    raise SystemExit(f'phase181 apply-source sysfs anchor count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
PY

exec bash scripts/181_ci.sh
