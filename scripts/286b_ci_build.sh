#!/usr/bin/env bash
set -Eeuo pipefail

TARGET="scripts/286_ci_build.sh"
MARK="A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1"

# Phase286 compiled successfully, then its strict post-build audit failed because
# MARK is intentionally only a C source comment. The source audit in Phase286
# already verifies MARK in dsi_display.c before compilation. Remove exactly that
# impossible binary assertion and preserve every real runtime-string audit.
python3 - <<'PY'
from pathlib import Path

p = Path('scripts/286_ci_build.sh')
s = p.read_text()
needle = "    'A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1',\n"
count = s.count(needle)
if count != 1:
    raise SystemExit(f'Phase286B expected exactly one source-only binary audit entry, found {count}')
s = s.replace(needle, '', 1)
p.write_text(s)
print('Phase286B removed only the impossible source-comment binary assertion: PASS')
PY

# Prove the source-level marker audit is still present and all meaningful
# compiled marker checks remain in the underlying Phase286 build script.
grep -Fq "grep -Fq '$MARK' \"\$DISPLAY\"" "$TARGET"
grep -Fq "'P276 286F c=%u z=%x'" "$TARGET"
grep -Fq "'P276 286B c=%u rc=%d'" "$TARGET"
grep -Fq "'P276 286P c=%u rc=%d'" "$TARGET"
grep -Fq "'P276 286A c=%u b=%lx p=%lx i=%lx'" "$TARGET"
grep -Fq "'P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx'" "$TARGET"
grep -Fq "'P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx'" "$TARGET"
! grep -Fq "    'A52_PHASE286_HS_CLOCK_HANDOFF_FIX_V1'," "$TARGET"

bash "$TARGET"
