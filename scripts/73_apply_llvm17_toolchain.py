#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
path = root / "scripts" / "common.sh"
text = path.read_text()

old = '  local clang="$KERNEL_DIR/toolchain/clang/host/linux-x86/clang-r383902/bin/clang"\n'
new = (
    '  local clang_default="$KERNEL_DIR/toolchain/clang/host/linux-x86/clang-r383902/bin/clang"\n'
    '  local clang="${AUTOFDO_CLANG_BIN:-$clang_default}"\n'
)
if old not in text:
    raise SystemExit("configure_toolchain clang anchor not found")
text = text.replace(old, new, 1)

anchor = '  test -x "$clang" || fail "Bundled clang was not found: $clang"\n'
replacement = (
    '  test -x "$clang" || fail "Selected clang was not found: $clang"\n'
    '  "$clang" --version | head -1 | tee "$ARTIFACTS_DIR/selected-clang-version.txt"\n'
)
if anchor not in text:
    raise SystemExit("clang validation anchor not found")
text = text.replace(anchor, replacement, 1)

path.write_text(text)
print("autofdo_a0_toolchain_override=applied")
