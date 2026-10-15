#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.200
HEADER="$KERNEL_DIR/include/asm-generic/vmlinux.lds.h"
REPORT="$ARTIFACTS_DIR/noinstr-linker-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before linker-script repair"
test -f "$HEADER" || fail "Generic linker-script header is missing"

python3 - "$HEADER" "$REPORT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = Path(sys.argv[2])
text = path.read_text()
slash_n = chr(92) + "\n"
misplaced = "\n\t\tNOINSTR_TEXT" + ("\t" * 6) + slash_n + "#define SECURITY_INIT"

if text.count(misplaced) == 1:
    text = text.replace(misplaced, "\n#define SECURITY_INIT", 1)
elif text.count(misplaced) != 0:
    raise SystemExit("unexpected number of misplaced NOINSTR_TEXT entries")

anchor = "\t\t*(TEXT_CFI_MAIN)"
if text.count(anchor) != 1:
    raise SystemExit("TEXT_CFI_MAIN linker anchor mismatch")
pos = text.index(anchor)
line_end = text.index("\n", pos) + 1
insert = "\t\tNOINSTR_TEXT" + ("\t" * 6) + slash_n

if text[line_end:].startswith(insert):
    result = "already-correct"
else:
    if not text[line_end:].startswith("\t\t*(.text..refcount)"):
        raise SystemExit("TEXT_CFI_MAIN successor is not .text..refcount")
    text = text[:line_end] + insert + text[line_end:]
    result = "moved-from-security-init-to-text-text"

path.write_text(text)
final = path.read_text()
if final.count("\t\tNOINSTR_TEXT") != 1:
    raise SystemExit("NOINSTR_TEXT use count is not exactly one")
if misplaced in final:
    raise SystemExit("misplaced NOINSTR_TEXT entry remains")
final_pos = final.index(anchor)
final_line_end = final.index("\n", final_pos) + 1
if not final[final_line_end:].startswith(insert + "\t\t*(.text..refcount)"):
    raise SystemExit("NOINSTR_TEXT is not directly between TEXT_CFI_MAIN and .text..refcount")
report.write_text(
    f"kernel_version=4.19.200\n"
    f"result={result}\n"
    "location=TEXT_TEXT_after_TEXT_CFI_MAIN\n"
    "misplaced_security_init_entry=no\n"
)
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- include/asm-generic/vmlinux.lds.h
info "Linux $TARGET_VERSION NOINSTR linker placement repaired"
