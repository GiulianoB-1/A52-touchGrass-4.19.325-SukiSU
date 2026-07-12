#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before compile repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
assembler = root / "arch/arm64/include/asm/assembler.h"
text = assembler.read_text()
block = (
    "/*\n"
    " * Clear Branch History instruction\n"
    " */\n"
    "\t.macro clearbhb\n"
    "\thint\t#22\n"
    "\t.endm\n"
)
count = text.count(block)
if count != 2:
    raise SystemExit(f"arm64 clearbhb duplicate: expected two definitions, found {count}")
text = text.replace(block + "\n" + block, block, 1)
assembler.write_text(text)

final = assembler.read_text()
if final.count("\t.macro clearbhb\n") != 1:
    raise SystemExit("arm64 clearbhb postcondition failed")
print("applied=arm64 clearbhb duplicate removal")
PY

{
  echo 'target=4.19.250'
  echo 'arm64_clearbhb_definitions=1'
  echo 'result=compile-api-compatible'
} | tee "$REPORT"

info "Linux $TARGET_VERSION compile mismatches repaired"
