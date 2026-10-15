#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 08_patch_resukisu_susfs_includes.py <generated-build-script>")

path = Path(sys.argv[1])
text = path.read_text()
anchor = 'ln -s ../KernelSU/kernel "$KERNEL_DIR/drivers/kernelsu"\n'
block = r'''ln -s ../KernelSU/kernel "$KERNEL_DIR/drivers/kernelsu"
python3 - "$KERNEL_DIR/drivers/kernelsu" <<'SUSFSINCLUDEPY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
old = '#include <linux/susfs_def.h>\n'
new = '#include <linux/susfs.h>\n'
replacements = {
    'susfs_is_current_proc_umounted()': 'true',
    'susfs_set_current_proc_umounted();': '/* unavailable in SUSFS 1.4.2 */',
    'extern struct work_struct susfs_extra_works;': '/* susfs_extra_works unavailable in SUSFS 1.4.2 */',
    'schedule_work(&susfs_extra_works);': '/* susfs_extra_works unavailable in SUSFS 1.4.2 */',
}
include_changes = 0
api_changes = 0
for source in root.rglob('*'):
    if not source.is_file() or source.suffix not in {'.c', '.h'}:
        continue
    content = source.read_text()
    updated = content
    if old in updated:
        updated = updated.replace(old, new)
        include_changes += 1
    for before, after in replacements.items():
        count = updated.count(before)
        if count:
            updated = updated.replace(before, after)
            api_changes += count
    if updated != content:
        source.write_text(updated)
remaining = []
unsupported = []
for source in root.rglob('*'):
    if not source.is_file() or source.suffix not in {'.c', '.h'}:
        continue
    content = source.read_text()
    if old in content:
        remaining.append(str(source))
    for symbol in ('susfs_is_current_proc_umounted', 'susfs_set_current_proc_umounted', 'susfs_extra_works'):
        if symbol in content:
            unsupported.append(f'{source}:{symbol}')
if remaining:
    raise SystemExit('susfs_def.h includes remain: ' + ', '.join(remaining))
if unsupported:
    raise SystemExit('unsupported SUSFS 1.4.2 API references remain: ' + ', '.join(unsupported))
print(f'patched {include_changes} SUSFS include files and compiled out {api_changes} unsupported API references')
SUSFSINCLUDEPY
'''
if text.count(anchor) != 1:
    raise SystemExit('ReSukiSU kernel link anchor mismatch')
path.write_text(text.replace(anchor, block, 1))
