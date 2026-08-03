#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

SCRIPTS = Path('scripts')


def materialize_chunks(prefix: str, output: str, expected_z64_sha256: str, expected_raw_sha256: str) -> None:
    parts = sorted(SCRIPTS.glob(prefix + '.part*'))
    if not parts:
        raise SystemExit(f'no payload chunks found for {prefix}')
    encoded = ''.join(part.read_text(encoding='ascii').strip() for part in parts)
    encoded_sha = hashlib.sha256(encoded.encode('ascii')).hexdigest()
    if encoded_sha != expected_z64_sha256:
        raise SystemExit(f'{prefix}: encoded sha256 {encoded_sha} != {expected_z64_sha256}')
    raw = zlib.decompress(base64.b64decode(encoded, validate=True))
    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != expected_raw_sha256:
        raise SystemExit(f'{prefix}: raw sha256 {raw_sha} != {expected_raw_sha256}')
    out = SCRIPTS / output
    out.write_bytes(raw)
    out.chmod(0o755)
    print(f'materialized {out} sha256={raw_sha} bytes={len(raw)} chunks={len(parts)}')


materialize_chunks(
    '216_apply_qsee_deep_trace.py.z64',
    '216_apply_qsee_deep_trace.py',
    '9021298d16284e2a29e37f7381e8233b668bc4fa41a8eceae0ccdde4268d11cc',
    'a4850304d99f9b0f8d091553f4bd7e34a5668d11c1272a41a9953e7c90c9c343',
)

PHASE217_PATCHER_Z64 = (
    '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'
)

# TouchGrass was compared first at exact commit
# 6bf351bdf18bdb228db79e66f14a7a9c0178e5d7. Its Binder/binderfs device
# contract and required Android kernel interfaces match this build, so there is
# no justified TouchGrass kernel option to copy for the missing DRM-client open.
phase217_encoded = ''.join(PHASE217_PATCHER_Z64)
phase217_encoded_sha = hashlib.sha256(phase217_encoded.encode('ascii')).hexdigest()
if phase217_encoded_sha != '77c312ed5714ff73cad6cd6d4f70020c9d711877f143a201151dfe39227bd857':
    raise SystemExit(f'Phase 217 patcher encoded sha256 mismatch: {phase217_encoded_sha}')
phase217_raw = zlib.decompress(base64.b64decode(phase217_encoded, validate=True))
phase217_raw_sha = hashlib.sha256(phase217_raw).hexdigest()
if phase217_raw_sha != 'ece288e322303b9ea407bb08e6613e69d344dfcd5121c3a748fbd1287c0284b5':
    raise SystemExit(f'Phase 217 patcher raw sha256 mismatch: {phase217_raw_sha}')
phase217_out = SCRIPTS / '216_apply_qsee_deep_trace.py'
phase217_out.write_bytes(phase217_raw)
phase217_out.chmod(0o755)
print(
    f'layered Phase 217 init-service recorder into {phase217_out} '
    f'sha256={phase217_raw_sha} bytes={len(phase217_raw)}'
)

ci_encoded = (SCRIPTS / '216_ci.sh.z64').read_text(encoding='ascii').strip()
ci_encoded_sha = hashlib.sha256(ci_encoded.encode('ascii')).hexdigest()
if ci_encoded_sha != '280f74e00a6f4ea320b543e7db2e2d51a4a3ba42af41d009f572faebe8d691f6':
    raise SystemExit(f'216_ci.sh.z64: encoded sha256 mismatch: {ci_encoded_sha}')
ci_raw = zlib.decompress(base64.b64decode(ci_encoded, validate=True))
ci_raw_sha = hashlib.sha256(ci_raw).hexdigest()
if ci_raw_sha != 'a9b10bb1c6903f4fddcc13074c8fd7c3e44387d18709bc5135c036c8fc062e1e':
    raise SystemExit(f'216_ci.sh: raw sha256 mismatch: {ci_raw_sha}')

ci_text = ci_raw.decode('utf-8')
source_gate_replacements = {
    "grep -Fq 'A52_R210_RS_PARITY 48U' \"$REC\"":
        "grep -Fq 'A52_R179_RS_ROOTS 48U' \"$REC\"",
    "grep -Fq 'A52_R199_CRC32C_POLY 0x82f63b78U' \"$REC\"":
        "grep -Fq '0x82f63b78U' \"$REC\"",
}
for old_gate, new_gate in source_gate_replacements.items():
    if ci_text.count(old_gate) != 1:
        raise SystemExit(f'expected exactly one stale Phase 216 source gate: {old_gate}')
    ci_text = ci_text.replace(old_gate, new_gate)

# Preserve every inherited Phase 216 audit, then require the new service-launch
# strings in both source and the compiled Image before artifact upload.
ci_text += r"""

phase217_root="${GITHUB_WORKSPACE:-$PWD}"
phase217_exec="$phase217_root/gki/common/fs/exec.c"
phase217_exit="$phase217_root/gki/common/kernel/exit.c"
phase217_image="$phase217_root/artifacts/a52xq-qsee-deep-trace/compile/Image"
test -s "$phase217_exec"
test -s "$phase217_exit"
test -s "$phase217_image"
grep -Fq '#define A52_R217_EXEC_LIMIT 192U' "$phase217_exec"
grep -Fq '#define A52_R217_EXIT_LIMIT 128U' "$phase217_exit"
grep -Fq 'INITPOST 217 E n=%u p=%d g=%d pp=%d c=%.12s x=%.28s' "$phase217_exec"
grep -Fq 'INITPOST 217 R n=%u rc=%d c=%.16s' "$phase217_exec"
grep -Fq 'INITPOST 217 X n=%u p=%d g=%d pp=%d c=%.16s x=%lx' "$phase217_exit"
grep -aFq 'INITPOST 217 E n=%u p=%d g=%d pp=%d c=%.12s x=%.28s' "$phase217_image"
grep -aFq 'INITPOST 217 R n=%u rc=%d c=%.16s' "$phase217_image"
grep -aFq 'INITPOST 217 X n=%u p=%d g=%d pp=%d c=%.16s x=%lx' "$phase217_image"
echo 'Phase 217 init-service source and compiled marker audit: PASS'
"""

ci_raw = ci_text.encode('utf-8')
patched_ci_sha = hashlib.sha256(ci_raw).hexdigest()

ci_out = SCRIPTS / '216_ci.sh'
ci_out.write_bytes(ci_raw)
ci_out.chmod(0o755)
print(
    f'materialized {ci_out} original_sha256={ci_raw_sha} '
    f'patched_sha256={patched_ci_sha} bytes={len(ci_raw)}'
)
