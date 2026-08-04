#!/usr/bin/env python3
import base64, hashlib, zlib
from pathlib import Path

S = Path('scripts')
PAYLOADS = {
    '218_apply_keymint_qsee_trace.py': (
        '218_apply_keymint_qsee_trace.py.z64',
        '24ee36d67b6dcb4f36923b913b96a496e3745d956c9ea8a3be97ce9f4839a33d',
        'f07ae99840472fc73f02731953ae975bea50709254d10059ae3d75c2d642bcbc',
    ),
    '218_phase217_wrapper.py': (
        '218_phase217_wrapper.py.z64',
        '8468edbec26a3cd202449b58d5271434762376e312341c917c080716d57d47c4',
        '1801a46098190fdb60eaa76516b66c8e5f338446a02e0043d27ace7fb3f59c7d',
    ),
    '222_apply_sg_boot_progress_trace.py': (
        '222_apply_sg_boot_progress_trace.py.z64',
        '288e28c4f3363c4efec8e029756d933d37bd4370f0491d2dd8b4b7081a7014a2',
        'ce372f94c7a94b66fc78a09b94d10907ef9136b0126588b9177640e87035de24',
    ),
    '222_compare_runtime_traces.py': (
        '222_compare_runtime_traces.py.z64',
        '180589aa68014eee1f19182b440b7308cb4335c2cf65287566dc98d8866b7b4f',
        'f8eeb8dbfcf22e005428558cf5aeaca71ece7b50b4fa0d4a28e80342fb6f5073',
    ),
}

for out, (src, expected_encoded_sha, expected_raw_sha) in PAYLOADS.items():
    encoded = (S / src).read_text(encoding='ascii').strip()
    encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
    if encoded_sha != expected_encoded_sha:
        raise SystemExit(f'{src}: encoded sha256 mismatch: {encoded_sha}')
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != expected_raw_sha:
        raise SystemExit(f'{out}: raw sha256 mismatch: {raw_sha}')
    target = S / out
    target.write_bytes(raw)
    target.chmod(0o755)
    print(f'materialized {target} sha256={raw_sha} bytes={len(raw)}')

# Phase 221 remains the sole configuration change. Phase 222 adds only
# metadata tracing around Samsung librpmb.so's SG discovery and sparse later
# boot milestones. Keep CHR_DEV_SG built in for the paired runtime comparison.
ci = S / '217_ci.sh'
expected_ci_sha = 'c20ba2ab8642d576e437ef1128ad030614b8332bd461cdfe1e7f3edede32cfb9'
ci_sha = hashlib.sha256(ci.read_bytes()).hexdigest()
if ci_sha != expected_ci_sha:
    raise SystemExit(f'217_ci.sh pre-Phase221 sha256 mismatch: {ci_sha}')

text = ci.read_text(encoding='utf-8')
marker = 'PHASE221_CHR_DEV_SG_CONFIG'
if marker in text:
    raise SystemExit('Phase 221 config block unexpectedly already present')
lines = text.splitlines(keepends=True)
set_lines = [i for i, line in enumerate(lines)
             if line.strip().startswith('set -') and 'pipefail' in line]
if len(set_lines) != 1:
    raise SystemExit(f'expected one strict-mode line in 217_ci.sh, got {len(set_lines)}')
block = '''\n# PHASE221_CHR_DEV_SG_CONFIG\nPHASE221_CONFIG=workspace/gki-phase199-out/.config\ntest -x gki/common/scripts/config\ntest -f "$PHASE221_CONFIG"\ngki/common/scripts/config --file "$PHASE221_CONFIG" --enable CHR_DEV_SG\ngrep -Fxq 'CONFIG_CHR_DEV_SG=y' "$PHASE221_CONFIG"\n\n'''
lines.insert(set_lines[0] + 1, block)

post = '''\n# PHASE221_CHR_DEV_SG_AUDIT\ngrep -Fxq 'CONFIG_CHR_DEV_SG=y' workspace/gki-phase199-out/.config\ngrep -Fxq 'CONFIG_CHR_DEV_SG=y' artifacts/a52xq-graphics-startup-trace/config/final.config\ntest -s workspace/gki-phase199-out/drivers/scsi/sg.o\nprintf '%s\\n' 'Phase 221 CHR_DEV_SG compile and artifact audit: PASS'\n\n# PHASE222_TRACE_CONTRACT_AUDIT\ngrep -Fq 'A52_PHASE222_SG_BOOT_PROGRESS_TRACE' workspace/gki-phase199-src/drivers/scsi/sg.c 2>/dev/null || \\\n  grep -Fq 'A52_PHASE222_SG_BOOT_PROGRESS_TRACE' gki/common/drivers/scsi/sg.c\ngrep -Fq 'SGPOST 222' artifacts/a52xq-graphics-startup-trace/compile/Image\ngrep -Fq 'BOOTPOST 222' artifacts/a52xq-graphics-startup-trace/compile/Image\nprintf '%s\\n' 'Phase 222 SG and later-boot trace marker audit: PASS'\n'''
lines.append(post)
ci.write_text(''.join(lines), encoding='utf-8')
ci.chmod(0o755)
patched = ci.read_text(encoding='utf-8')
for required in (
    marker,
    'gki/common/scripts/config --file "$PHASE221_CONFIG" --enable CHR_DEV_SG',
    "grep -Fxq 'CONFIG_CHR_DEV_SG=y' artifacts/a52xq-graphics-startup-trace/config/final.config",
    'test -s workspace/gki-phase199-out/drivers/scsi/sg.o',
    'PHASE222_TRACE_CONTRACT_AUDIT',
    "grep -Fq 'SGPOST 222' artifacts/a52xq-graphics-startup-trace/compile/Image",
    "grep -Fq 'BOOTPOST 222' artifacts/a52xq-graphics-startup-trace/compile/Image",
):
    if patched.count(required) != 1:
        raise SystemExit(f'Phase 222 transformed 217_ci.sh marker mismatch: {required}')
patched_sha = hashlib.sha256(ci.read_bytes()).hexdigest()
print(f'patched {ci} sha256={patched_sha} with Phase 221 SG config and Phase 222 trace gates')
