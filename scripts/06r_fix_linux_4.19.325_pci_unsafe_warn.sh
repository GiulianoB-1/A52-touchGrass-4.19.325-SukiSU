#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/pci-unsafe-warn-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before PCI repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
pci = root / "include/linux/pci.h"
text = pci.read_text()

field = "\tunsigned int\t\tunsafe_warn:1;\t/* warned about RW1C config write */\n"
anchor = "\tunsigned int\t\tis_added:1;\n"

bus_start = text.index("struct pci_bus {\n")
bus_end = text.index("\n};", bus_start)
segment = text[bus_start:bus_end]

if field not in segment:
    if segment.count(anchor) != 1:
        raise SystemExit(
            f"pci_bus is_added anchor mismatch: {segment.count(anchor)}"
        )
    segment = segment.replace(anchor, anchor + field, 1)
    text = text[:bus_start] + segment + text[bus_end:]
    pci.write_text(text)
elif segment.count(field) != 1:
    raise SystemExit("unexpected pci_bus unsafe_warn field count")

final = pci.read_text()
final_start = final.index("struct pci_bus {\n")
final_end = final.index("\n};", final_start)
final_segment = final[final_start:final_end]

if final_segment.count(field) != 1:
    raise SystemExit("pci_bus unsafe_warn repair failed")
if final_segment.index(field) < final_segment.index(anchor):
    raise SystemExit("pci_bus unsafe_warn field ordering is incorrect")
for reserve in range(1, 5):
    token = f"ANDROID_KABI_RESERVE({reserve});"
    if final_segment.count(token) != 1:
        raise SystemExit(f"PCI bus KABI reserve changed unexpectedly: {token}")
PY

git -C "$KERNEL_DIR" diff --check -- include/linux/pci.h

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'pci_bus=restored-unsafe-rw1c-warning-bit\n'
  printf 'android_kabi_reserves=preserved-1-through-4\n'
  printf 'result=linux-4.19.325-pci-unsafe-warning-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION PCI unsafe warning compatibility repaired"
