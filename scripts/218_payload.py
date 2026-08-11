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

# Phase 252 deliberately enables the legacy Qualcomm bus client/provider
# contract.  The inherited Phase 217 shell script still performs a final
# byte-for-byte cmp of its saved config against final.config after all wrapper
# overlays have run.  Replace exactly that stale gate after the Phase 218/226
# payload has finished materializing and editing 217_ci.sh.  The replacement
# remains fail-closed and delegates to the Phase 252 semantic validator.
ci_path = S / '217_ci.sh'
ci_text = ci_path.read_text(encoding='utf-8')
ci_lines = ci_text.splitlines()
cmp_lines = [
    index for index, line in enumerate(ci_lines)
    if line.strip().startswith('cmp ')
    and 'before-phase217.config' in line
    and 'final.config' in line
]
if len(cmp_lines) != 1:
    raise SystemExit(
        'Phase 252 expected exactly one inherited Phase 217 config cmp, '
        f'found {len(cmp_lines)}'
    )
index = cmp_lines[0]
indent = ci_lines[index][:len(ci_lines[index]) - len(ci_lines[index].lstrip())]
ci_lines[index] = (
    indent
    + 'python3 scripts/252_config_retention_gate.py '
      'artifacts/a52xq-graphics-startup-trace/config/before-phase217.config '
      'artifacts/a52xq-graphics-startup-trace/config/final.config'
)
ci_path.write_text('\n'.join(ci_lines) + '\n', encoding='utf-8')
patched_ci = ci_path.read_text(encoding='utf-8')
if 'cmp ' in '\n'.join(
    line for line in patched_ci.splitlines()
    if 'before-phase217.config' in line and 'final.config' in line
):
    raise SystemExit('Phase 252 stale config cmp survived replacement')
if patched_ci.count('scripts/252_config_retention_gate.py') != 1:
    raise SystemExit('Phase 252 semantic retention gate insertion audit failed')
print('Phase 252 replaced inherited Phase 217 bytewise config cmp with semantic gate')

# Phase 255 diagnostic: locate which generated helper owns the stale Phase 219
# workflow lookup. Exclude this source file itself, then stop before any build.
target = 'a52-ack510-phase219-postalloc-slab.yml'
matches = []
for candidate in sorted(S.rglob('*')):
    if not candidate.is_file() or candidate.name == '218_payload.py':
        continue
    if candidate.suffix not in ('.py', '.sh', '.yml', '.yaml', '.txt'):
        continue
    try:
        candidate_lines = candidate.read_text(encoding='utf-8').splitlines()
    except UnicodeDecodeError:
        continue
    for index, line in enumerate(candidate_lines):
        if target in line:
            matches.append((candidate, candidate_lines, index))
if len(matches) != 1:
    names = ', '.join(str(item[0]) for item in matches) or '<none>'
    raise SystemExit(
        f'Phase 255 diagnostic expected exactly one generated {target} lookup, '
        f'found {len(matches)} in {names}'
    )
candidate, candidate_lines, center = matches[0]
start = max(0, center - 30)
end = min(len(candidate_lines), center + 31)
print(f'===== PHASE255_STALE_PHASE219_OWNER {candidate} =====')
for lineno in range(start, end):
    print(f'{lineno + 1:05d}: {candidate_lines[lineno]}')
print('===== PHASE255_STALE_PHASE219_OWNER_END =====')
raise SystemExit('Phase 255 diagnostic stop after locating stale Phase 219 audit owner')
