#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

SCRIPTS = Path('scripts')
PAYLOADS = {
    '217_apply_graphics_service_trace.py': (
        '217_apply_graphics_service_trace.py.z64',
        '2d107af779023f39fde27c0ca31f16ae85ae99c090512da6593d2a898a542247',
        '9ce32caab14993f2513d0788e4fd9513157a76e4f7c3edb909cde9c504db0377',
    ),
    '217_ci.sh': (
        '217_ci.sh.z64',
        '9bdca782316bdd2a5244da26209ec3836754709ba1fb394c5dc83c711704cdc6',
        'c20ba2ab8642d576e437ef1128ad030614b8332bd461cdfe1e7f3edede32cfb9',
    ),
}

for output, (source, expected_encoded_sha, expected_raw_sha) in PAYLOADS.items():
    encoded = (SCRIPTS / source).read_text(encoding='ascii').strip()
    encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
    if encoded_sha != expected_encoded_sha:
        raise SystemExit(f'{source}: encoded sha256 mismatch: {encoded_sha}')
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != expected_raw_sha:
        raise SystemExit(f'{output}: raw sha256 mismatch: {raw_sha}')
    target = SCRIPTS / output
    target.write_bytes(raw)
    target.chmod(0o755)
    print(f'materialized {target} sha256={raw_sha} bytes={len(raw)}')

# Phase 234 intentionally replaces the Phase 210 boot banner while retaining
# the Phase 210 R48 RS48 + CRC32C transport. Update only the inherited Phase
# 217 binary-marker assertion after materialization; decoder self-tests and
# all transport/source checks remain unchanged.
ci = SCRIPTS / '217_ci.sh'
ci_text = ci.read_text(encoding='utf-8')
old_boot_marker = 'BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c'
new_boot_marker = 'BOOT rs=ready phase=234 focus=rscc'
if ci_text.count(old_boot_marker) != 1:
    raise SystemExit(
        'Phase 234 inherited Phase 217 boot-marker audit expected exactly one '
        f'stale token, found {ci_text.count(old_boot_marker)}'
    )
if new_boot_marker in ci_text:
    raise SystemExit('Phase 234 boot-marker audit target already present before repair')
ci_text = ci_text.replace(old_boot_marker, new_boot_marker)
ci.write_text(ci_text, encoding='utf-8')
repaired_ci = ci.read_text(encoding='utf-8')
if old_boot_marker in repaired_ci or repaired_ci.count(new_boot_marker) != 1:
    raise SystemExit('Phase 234 inherited Phase 217 boot-marker audit repair failed')
print('Phase 217 binary-marker audit updated for Phase 234 RSCC-focused boot banner')

patcher = SCRIPTS / '217_apply_graphics_service_trace.py'
text = patcher.read_text(encoding='utf-8')
old_budget = '''    elif path == RECORDER:
        replacements = [
            ('#define A52_R179_HEARTBEAT_LIMIT 120U', '#define A52_R179_HEARTBEAT_LIMIT 60U', 'heartbeat budget'),
        ]'''
new_budget = '''    elif path == RECORDER:
        replacements = [
            ('\\t       !strncmp(message, "DRMPOST ", 8) ||\\n\\t       !strncmp(message, "IONPOST ", 8) ||',
             '\\t       !strncmp(message, "DRMPOST ", 8) ||\\n\\t       !strncmp(message, "GFXPOST ", 8) ||\\n\\t       !strncmp(message, "IONPOST ", 8) ||',
             'graphics critical retention'),
            ('#define A52_R179_HEARTBEAT_LIMIT 120U', '#define A52_R179_HEARTBEAT_LIMIT 60U', 'heartbeat budget'),
        ]'''
old_check = "            RECORDER: ('A52_R179_HEARTBEAT_LIMIT 60U',),"
new_check = "            RECORDER: ('!strncmp(message, \"GFXPOST \", 8)', 'A52_R179_HEARTBEAT_LIMIT 60U'),"
if text.count(old_budget) != 1 or text.count(old_check) != 1:
    raise SystemExit('Phase 217 retention transform anchor mismatch')
text = text.replace(old_budget, new_budget).replace(old_check, new_check)
patcher.write_text(text, encoding='utf-8')
patched_sha = hashlib.sha256(patcher.read_bytes()).hexdigest()
expected_patched_sha = '0c77f60d4a8a00f3c4698d3e1b63377ab9254c567e9a8cbda82b6450ae870827'
if patched_sha != expected_patched_sha:
    raise SystemExit(f'Phase 217 retention transform sha256 mismatch: {patched_sha}')
print(f'patched {patcher} sha256={patched_sha} with critical GFXPOST retention')
