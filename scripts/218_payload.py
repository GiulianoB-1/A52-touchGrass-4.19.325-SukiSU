#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

S = Path('scripts')
parts = tuple(sorted((S / '226_driver_chunks').glob('*.txt')))
if len(parts) != 1:
    raise SystemExit(f'expected 1 Phase 226 driver chunk, got {len(parts)}')
encoded = ''.join(p.read_text(encoding='ascii').strip() for p in parts)
if hashlib.sha256(encoded.encode('ascii')).hexdigest() != '486c217c936662e54fb9038c023e8cc4661c54fc3fd0e26d21cfe8d017f03ae7':
    raise SystemExit('Phase 226 driver encoded sha256 mismatch')
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != 'e4b2ec578d8f1ce8a2d140abc374a0f21abb895f8e57d5d8643c9930a5684d6b':
    raise SystemExit('Phase 226 driver raw sha256 mismatch')
exec(compile(raw, 'scripts/226_payload_driver.py', 'exec'), {'__name__': '__main__'})

# Phase 234 changes only the recorder identification banner while retaining
# the Phase 210 R48 RS48 + CRC32C transport. The embedded Phase 226 payload
# first verifies the pristine Phase 217 CI script, so normalize the inherited
# binary-marker assertion only after that integrity gate has completed.
ci_path = S / '217_ci.sh'
ci_source = ci_path.read_text(encoding='utf-8')
old_boot_marker = 'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c'
new_boot_marker = 'BOOT rs=ready phase=234 focus=rscc'
old_count = ci_source.count(old_boot_marker)
new_count = ci_source.count(new_boot_marker)
if old_count != 1 or new_count != 0:
    raise SystemExit(
        'Phase 234 inherited Phase 217 boot-marker audit anchor mismatch: '
        f'old={old_count} new={new_count}'
    )
ci_path.write_text(
    ci_source.replace(old_boot_marker, new_boot_marker),
    encoding='utf-8',
)
ci_repaired = ci_path.read_text(encoding='utf-8')
if old_boot_marker in ci_repaired or ci_repaired.count(new_boot_marker) != 1:
    raise SystemExit('Phase 234 inherited Phase 217 boot-marker audit repair failed')
print('Phase 217 binary-marker audit updated for Phase 234 RSCC-focused boot banner')

# The Phase 226 ioctl and connect trace points use the exported recorder API.
# Add its existing public header to both translation units through the patcher,
# then require the include during the patcher's own source verification.
path = S / '226_apply_odsign_gate_trace.py'
source = path.read_text(encoding='utf-8')
repair_token = 'Phase 226 recorder header include repair'
if repair_token not in source:
    include_patch = '''    # Phase 226 recorder header include repair\n    recorder_header = '#include <linux/a52_ack_secure_flight_recorder.h>\\n'\n    if recorder_header not in text:\n        text, include_count = re.subn(\n            r'(^#include <linux/[^>]+>\\n)',\n            r'\\1' + recorder_header,\n            text,\n            count=1,\n            flags=re.MULTILINE,\n        )\n        if include_count != 1:\n            raise RuntimeError(\n                f'{path}: recorder header include: expected one anchor'\n            )\n\n'''
    for function_name in ('patch_ioctl', 'patch_socket'):
        anchor = (
            f'def {function_name}(path: Path) -> None:\n'
            "    text = path.read_text(encoding='utf-8')\n"
            '    if MARKER in text:\n'
            "        raise RuntimeError(f'{path}: Phase 226 marker already present')\n\n"
        )
        count = source.count(anchor)
        if count != 1:
            raise SystemExit(
                f'expected one {function_name} recorder-header anchor, found {count}'
            )
        source = source.replace(anchor, anchor + include_patch, 1)

    verify_replacements = (
        (
            "        'fs/ioctl.c': (MARKER, 'ODSPOST 226 io-%s'),\n",
            "        'fs/ioctl.c': (MARKER, 'ODSPOST 226 io-%s',\n"
            "            '#include <linux/a52_ack_secure_flight_recorder.h>'),\n",
        ),
        (
            "        'net/socket.c': (MARKER, 'ODSPOST 226 con-%s'),\n",
            "        'net/socket.c': (MARKER, 'ODSPOST 226 con-%s',\n"
            "            '#include <linux/a52_ack_secure_flight_recorder.h>'),\n",
        ),
    )
    for old, new in verify_replacements:
        count = source.count(old)
        if count != 1:
            raise SystemExit(
                f'expected one Phase 226 header verification anchor, found {count}'
            )
        source = source.replace(old, new, 1)
    path.write_text(source, encoding='utf-8')

repaired = path.read_text(encoding='utf-8')
if repaired.count(repair_token) != 2:
    raise SystemExit('Phase 226 recorder-header repair verification failed')
if repaired.count("'#include <linux/a52_ack_secure_flight_recorder.h>'") != 2:
    raise SystemExit('Phase 226 recorder-header source audit verification failed')
print('Phase 226 recorder header includes repaired for ioctl and socket')
