#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/pci-unsafe-warn-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before PCI compatibility repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
pci = root / "include/linux/pci.h"
text = pci.read_text()

struct_start = text.index("struct pci_bus {\n")
struct_end = text.index("};\n", struct_start) + 3
segment = text[struct_start:struct_end]

reserve = "\tANDROID_KABI_RESERVE(1);\n"
used = "\tANDROID_KABI_USE(1, unsigned int unsafe_warn:1);\n"

if used not in segment:
    if segment.count(reserve) != 1:
        raise SystemExit(
            f"pci_bus ABI reserve anchor mismatch: {segment.count(reserve)}"
        )
    segment = segment.replace(reserve, used, 1)
    text = text[:struct_start] + segment + text[struct_end:]
    pci.write_text(text)
elif segment.count(used) != 1:
    raise SystemExit("unexpected pci_bus unsafe_warn KABI field count")

final = pci.read_text()
final_start = final.index("struct pci_bus {\n")
final_end = final.index("};\n", final_start) + 3
final_segment = final[final_start:final_end]
if final_segment.count(used) != 1:
    raise SystemExit("pci_bus unsafe_warn KABI repair failed")
for remaining in (2, 3, 4):
    anchor = f"\tANDROID_KABI_RESERVE({remaining});\n"
    if final_segment.count(anchor) != 1:
        raise SystemExit(f"pci_bus ABI reserve {remaining} was not preserved")
PY

git -C "$KERNEL_DIR" diff --check -- include/linux/pci.h

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'pci_bus=used-kabi-reserve-1-for-unsafe-warn-bit\n'
  printf 'result=linux-4.19.325-pci-unsafe-write-warning-compatible\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION PCI unsafe-write warning compatibility repaired"
