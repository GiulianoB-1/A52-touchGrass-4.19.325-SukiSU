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
changed = 0
for source in root.rglob('*'):
    if not source.is_file() or source.suffix not in {'.c', '.h'}:
        continue
    content = source.read_text()
    if old in content:
        source.write_text(content.replace(old, new))
        changed += 1
remaining = []
for source in root.rglob('*'):
    if source.is_file() and source.suffix in {'.c', '.h'} and old in source.read_text():
        remaining.append(str(source))
if remaining:
    raise SystemExit('susfs_def.h includes remain: ' + ', '.join(remaining))
print(f'patched {changed} ReSukiSU SUSFS include files; no susfs_def.h includes remain')
SUSFSINCLUDEPY
'''
if text.count(anchor) != 1:
    raise SystemExit('ReSukiSU kernel link anchor mismatch')
path.write_text(text.replace(anchor, block, 1))
