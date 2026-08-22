#!/usr/bin/env bash
set -Eeuo pipefail

SRC="$PWD/scripts/297_qemu_trace_lab.sh"
TMP="$PWD/workspace/phase298-qemu-trace-lab.generated.sh"

test -s "$SRC"
mkdir -p "$PWD/workspace"

python3 - "$SRC" "$TMP" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
out = Path(sys.argv[2])
text = src.read_text()

needle = '''# Build a generic ARM64 virt kernel from the already-patched exact Phase296
# source tree. This config is deliberately separate from the phone config.
'''

if text.count(needle) != 1:
    raise SystemExit('Phase298: Phase297 QEMU insertion point missing or ambiguous')
if 'PHASE298_QEMU_KBUILD_ISOLATION_V1' in text:
    raise SystemExit('Phase298: source Phase297 script already contains Phase298 isolation')

block = r'''# PHASE298_QEMU_KBUILD_ISOLATION_V1
# Phase296 reconstructs the real A52 phone tree by grafting hardware-only
# vendor driver directories into drivers/Makefile.  That is correct for the
# phone build, but ARM64 defconfig also traverses those grafts even though QEMU
# virt can never instantiate them.  Phase297 therefore died while compiling
# A52 DP/SCM/recorder code before qemu-system-aarch64 was ever launched.
#
# The exact phone Image and strict A52 display objects have already been built
# and audited above.  From this point forward, isolate only the generic QEMU
# Kbuild graph by removing active a52_* path tokens from drivers/Makefile.  The
# source files themselves remain byte-for-byte present for the archived audit.
A52_DRIVER_MAKEFILE="$ROOT/drivers/Makefile"
test -s "$A52_DRIVER_MAKEFILE"
cp "$A52_DRIVER_MAKEFILE" "$ANALYSIS/drivers-Makefile.phone.txt"

python3 - "$A52_DRIVER_MAKEFILE" "$ANALYSIS/a52-kbuild-isolation.txt" <<'PYISO'
from pathlib import Path
import sys

path = Path(sys.argv[1])
audit = Path(sys.argv[2])
original = path.read_text().splitlines()
rewritten = []
removed = []

for lineno, line in enumerate(original, 1):
    code, sep, comment = line.partition('#')
    if 'a52_' not in code:
        rewritten.append(line)
        continue

    indent = code[:len(code) - len(code.lstrip())]
    tokens = code.split()
    dropped = [tok for tok in tokens if 'a52_' in tok]
    kept = [tok for tok in tokens if 'a52_' not in tok]

    if not dropped:
        rewritten.append(line)
        continue

    removed.append(f'{lineno}: {line}')

    # Preserve unrelated Kbuild entries if an A52 path shared the line.
    # If only the assignment and A52 paths remain, comment the original line.
    if kept and kept[-1] not in {'=', '+=', ':='}:
        rebuilt = indent + ' '.join(kept)
        if sep and comment:
            rebuilt += '  # ' + comment.strip()
        rewritten.append(rebuilt.rstrip())
    else:
        rewritten.append(indent + '# PHASE298_QEMU_ISOLATED: ' + line.strip())

if not removed:
    raise SystemExit('Phase298: no active a52_* Kbuild entries found in drivers/Makefile')

active = []
for lineno, line in enumerate(rewritten, 1):
    code = line.split('#', 1)[0]
    if 'a52_' in code:
        active.append(f'{lineno}: {line}')
if active:
    raise SystemExit('Phase298: active a52_* Kbuild entries remain: ' + repr(active))

path.write_text('\n'.join(rewritten) + '\n')
audit.write_text(
    'PHASE298_QEMU_KBUILD_ISOLATION_V1\n'
    'Removed active A52-only path tokens after exact phone object audit:\n' +
    '\n'.join(removed) + '\n'
)
print('Phase298 isolated A52-only QEMU Kbuild entries:')
for item in removed:
    print('  ' + item)
PYISO

cp "$A52_DRIVER_MAKEFILE" "$ANALYSIS/drivers-Makefile.qemu.txt"
for dir in a52_display a52_secure a52_pil; do
  test -d "$ROOT/drivers/$dir"
done
for rel in \
  drivers/a52_display/msm/msm_drv.c \
  drivers/a52_display/msm/msm_atomic.c \
  drivers/a52_display/msm/sde/sde_kms.c \
  drivers/a52_secure/a52_ack_secure_flight_recorder.c; do
  test -s "$ROOT/$rel"
done

'''

out.write_text(text.replace(needle, block + needle, 1))
print(f'Phase298 generated isolated QEMU driver: {out}')
PY

chmod +x "$TMP"
bash "$TMP"
