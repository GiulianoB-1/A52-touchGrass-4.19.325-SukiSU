#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/event-usb-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before event/USB repair"

python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
report = Path(sys.argv[2])
repairs = []


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    old_count = text.count(old)
    new_count = text.count(new)

    if old_count == 1:
        path.write_text(text.replace(old, new, 1))
        repairs.append(label)
        return

    if old_count == 0 and new_count == 1:
        return

    raise SystemExit(
        f"{label}: anchor mismatch old={old_count}, new={new_count}"
    )


# Linux stable changed timerqueue_head to rb_root_cached. The Qualcomm vendor
# file retained the removed head and next members after the checkpoint merge.
event_timer = root / "drivers/soc/qcom/event_timer.c"
replace_once(
    event_timer,
    "static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {\n"
    "\t.head = RB_ROOT,\n"
    "\t.next = NULL,\n"
    "};\n",
    "static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {\n"
    "\t.rb_root = RB_ROOT_CACHED,\n"
    "};\n",
    "event_timer=updated-percpu-timerqueue-head",
)

# The address-zero serialization logic uses retry_locked, but the declaration
# was lost while merging the upstream USB hub changes.
hub = root / "drivers/usb/core/hub.c"
replace_once(
    hub,
    "\tstatic int unreliable_port = -1;\n"
    "#ifdef CONFIG_USB_DEBUG_DETAILED_LOG\n",
    "\tstatic int unreliable_port = -1;\n"
    "\tbool retry_locked;\n"
    "#ifdef CONFIG_USB_DEBUG_DETAILED_LOG\n",
    "usb_hub=restored-retry-locked-declaration",
)

# The checkpoint merge retained code using two DWC3 locals while dropping their
# declarations. Restore the declarations without changing the surrounding
# Samsung gadget implementation.
dwc3_gadget = root / "drivers/usb/dwc3/gadget.c"
replace_once(
    dwc3_gadget,
    "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
    "{\n"
    "\tu8\t\t\ttrbs_left;\n",
    "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
    "{\n"
    "\tstruct dwc3_trb\t*tmp;\n"
    "\tu8\t\t\ttrbs_left;\n",
    "dwc3_gadget=restored-previous-trb-declaration",
)
replace_once(
    dwc3_gadget,
    "{\n"
    "\tstruct dwc3 *dwc = dep->dwc;\n"
    "\tint ret;\n\n"
    "\t/*\n"
    "\t * If the HWO is set, it implies the TRB is still being\n",
    "{\n"
    "\tstruct dwc3 *dwc = dep->dwc;\n"
    "\tint request_status;\n"
    "\tint ret;\n\n"
    "\t/*\n"
    "\t * If the HWO is set, it implies the TRB is still being\n",
    "dwc3_gadget=restored-request-status-declaration",
)

# Match the Linux 4.19.250 implementation, which accepts a 64-bit timeout.
xhci_h = root / "drivers/usb/host/xhci.h"
replace_once(
    xhci_h,
    "int xhci_handshake(void __iomem *ptr, u32 mask, u32 done, int usec);\n",
    "int xhci_handshake(void __iomem *ptr, u32 mask, u32 done, u64 timeout_us);\n",
    "xhci=matched-handshake-timeout-type",
)

# Validate the exact post-merge state before allowing the build to continue.
event_text = event_timer.read_text()
if ".head = RB_ROOT" in event_text or ".next = NULL" in event_text:
    raise SystemExit("obsolete timerqueue_head initializer remains")
if event_text.count(".rb_root = RB_ROOT_CACHED") != 1:
    raise SystemExit("timerqueue_head cached-root initializer count is not one")

hub_text = hub.read_text()
if hub_text.count("bool retry_locked;") != 1:
    raise SystemExit("hub retry_locked declaration count is not one")

gadget_text = dwc3_gadget.read_text()
if gadget_text.count("struct dwc3_trb\t*tmp;") != 1:
    raise SystemExit("DWC3 tmp declaration count is not one")
if gadget_text.count("int request_status;") != 1:
    raise SystemExit("DWC3 request_status declaration count is not one")

xhci_text = xhci_h.read_text()
if "xhci_handshake(void __iomem *ptr, u32 mask, u32 done, int usec)" in xhci_text:
    raise SystemExit("obsolete xHCI handshake prototype remains")
if xhci_text.count(
    "xhci_handshake(void __iomem *ptr, u32 mask, u32 done, u64 timeout_us)"
) != 1:
    raise SystemExit("xHCI 64-bit handshake prototype count is not one")

lib_makefile = (root / "lib/Makefile").read_text()
if lib_makefile.count("sha1.o chacha20.o irq_regs.o") != 1:
    raise SystemExit("Linux stable chacha20 object entry count is not one")
if not (root / "lib/chacha20.c").is_file():
    raise SystemExit("Linux stable lib/chacha20.c source is missing")
if not (root / "lib/chacha.c").is_file():
    raise SystemExit("vendor lib/chacha.c source is missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  drivers/soc/qcom/event_timer.c \
  drivers/usb/core/hub.c \
  drivers/usb/dwc3/gadget.c \
  drivers/usb/host/xhci.h

info "Linux $TARGET_VERSION Qualcomm event timer and USB compatibility repaired"
