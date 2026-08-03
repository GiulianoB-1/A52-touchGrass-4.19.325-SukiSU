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
        '9ad51c8526112ed7742a6f5051536de7738aad1391302901a276b827a84f0517',
        'a3df6626fa9aa9c0a541808985b4b660951a5159d3bbe1391bd69696a0aef3e3',
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

# Phase 221: exact TouchGrass comparison shows a52xq_defconfig enables the
# SCSI generic character device while the GKI output config disables it.
# Samsung's qseecomd reaches librpmb.so, scans /dev, and exits before opening
# qseecom. Enable only CHR_DEV_SG before Phase 217 olddefconfig/compile, while
# retaining the Phase 220 all-open recorder to prove the hardware boundary.
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

# A build cannot be accepted unless olddefconfig retained the setting, sg.o was
# compiled, and the artifact's final config records the behavior change.
post = '''\n# PHASE221_CHR_DEV_SG_AUDIT\ngrep -Fxq 'CONFIG_CHR_DEV_SG=y' workspace/gki-phase199-out/.config\ngrep -Fxq 'CONFIG_CHR_DEV_SG=y' artifacts/a52xq-graphics-startup-trace/config/final.config\ntest -s workspace/gki-phase199-out/drivers/scsi/sg.o\nprintf '%s\\n' 'Phase 221 CHR_DEV_SG compile and artifact audit: PASS'\n'''
lines.append(post)
ci.write_text(''.join(lines), encoding='utf-8')
ci.chmod(0o755)
patched = ci.read_text(encoding='utf-8')
for required in (
    marker,
    'gki/common/scripts/config --file "$PHASE221_CONFIG" --enable CHR_DEV_SG',
    "grep -Fxq 'CONFIG_CHR_DEV_SG=y' artifacts/a52xq-graphics-startup-trace/config/final.config",
    'test -s workspace/gki-phase199-out/drivers/scsi/sg.o',
):
    if patched.count(required) != 1:
        raise SystemExit(f'Phase 221 transformed 217_ci.sh marker mismatch: {required}')
patched_sha = hashlib.sha256(ci.read_bytes()).hexdigest()
print(f'patched {ci} sha256={patched_sha} with Phase 221 CONFIG_CHR_DEV_SG=y gate')
