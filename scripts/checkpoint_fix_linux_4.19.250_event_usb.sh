#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/event-usb-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before event/USB repair"

python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import re
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
text = dwc3_gadget.read_text()

# dwc3_calc_trbs_left() exists in several Samsung formatting variants. The
# semantic requirement is exactly one local tmp pointer whenever the retained
# Samsung previous-TRB check uses it.
func_marker = "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
if text.count(func_marker) != 1:
    raise SystemExit("drivers/usb/dwc3/gadget.c: dwc3_calc_trbs_left anchor is not unique")
func_start = text.index(func_marker)
func_end = text.find("\nstatic ", func_start + len(func_marker))
if func_end < 0:
    raise SystemExit("drivers/usb/dwc3/gadget.c: dwc3_calc_trbs_left end anchor missing")
segment = text[func_start:func_end]
if "tmp = dwc3_ep_prev_trb(dep, dep->trb_enqueue);" not in segment:
    raise SystemExit("drivers/usb/dwc3/gadget.c: Samsung previous-TRB check is missing")
tmp_decl_re = re.compile(r"^[ \t]*struct[ \t]+dwc3_trb[ \t]+\*tmp;[ \t]*$", re.MULTILINE)
tmp_decls = list(tmp_decl_re.finditer(segment))
if len(tmp_decls) == 0:
    brace = segment.find("{\n")
    if brace < 0:
        raise SystemExit("drivers/usb/dwc3/gadget.c: TRB function opening brace missing")
    insert = "\tstruct dwc3_trb\t*tmp;\n"
    segment = segment[:brace + 2] + insert + segment[brace + 2:]
    text = text[:func_start] + segment + text[func_end:]
    repairs.append("dwc3_gadget=restored-previous-trb-declaration")
elif len(tmp_decls) == 1:
    repairs.append("dwc3_gadget=previous-trb-declaration-already-present")
else:
    raise SystemExit(
        f"drivers/usb/dwc3/gadget.c: temporary TRB declaration count is {len(tmp_decls)}"
    )

# request_status is another Samsung-local declaration that can survive the merge
# with different surrounding formatting. Add it only when the identifier is used
# in the gadget source but no declaration exists.
request_decl_re = re.compile(r"^[ \t]*int[ \t]+request_status;[ \t]*$", re.MULTILINE)
request_decls = list(request_decl_re.finditer(text))
request_used = "request_status" in request_decl_re.sub("", text)
if len(request_decls) == 0 and request_used:
    anchor = "\tstruct dwc3 *dwc = dep->dwc;\n"
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise SystemExit(
            f"drivers/usb/dwc3/gadget.c: request_status insertion anchor count is {anchor_count}"
        )
    text = text.replace(anchor, anchor + "\tint request_status;\n", 1)
    repairs.append("dwc3_gadget=restored-request-status-declaration")
elif len(request_decls) == 1:
    repairs.append("dwc3_gadget=request-status-declaration-already-present")
elif len(request_decls) > 1:
    raise SystemExit(
        f"drivers/usb/dwc3/gadget.c: request_status declaration count is {len(request_decls)}"
    )

dwc3_gadget.write_text(text)

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
if len(re.findall(r"^[ \\t]*struct[ \\t]+dwc3_trb[ \\t]+\\*tmp;[ \\t]*$", gadget_text, re.MULTILINE)) != 1:
    raise SystemExit("DWC3 tmp declaration count is not one")
if "request_status" in gadget_text and len(re.findall(r"^[ \\t]*int[ \\t]+request_status;[ \\t]*$", gadget_text, re.MULTILINE)) != 1:
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
