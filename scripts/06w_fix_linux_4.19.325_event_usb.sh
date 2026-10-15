#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/event-usb-compat-$TARGET_VERSION.txt"
TMP_REPORT="$ARTIFACTS_DIR/.event-usb-repairs.tmp"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before event/USB repair"

python3 - "$KERNEL_DIR" "$TMP_REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
tmp_report = Path(sys.argv[2])
repairs = []


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count == 1:
        path.write_text(text.replace(old, new, 1))
        repairs.append(label)
        return
    if count == 0 and new in text:
        return
    raise SystemExit(f"{label}: anchor mismatch old={count}, new={text.count(new)}")


# timerqueue_head changed from a plain rb_root plus cached next pointer to
# rb_root_cached. Keep Qualcomm's per-CPU static initialization, expressed with
# the Linux 4.19.325 member and initializer.
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

# The upstream address-zero serialization was merged into hub_port_connect(),
# but its lock-state declaration was dropped.
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

# Preserve both the Qualcomm host auto-retry bit and the upstream split-disable
# bit. They are bit 14 in different Global User Control registers.
dwc3_core_h = root / "drivers/usb/dwc3/core.h"
text = dwc3_core_h.read_text()
if "#define DWC3_GUCTL_HSTINAUTORETRY" not in text:
    anchor = "#define DWC3_GCTL_DSBLCLKGTNG\t\tBIT(0)\n"
    if text.count(anchor) != 1:
        raise SystemExit(f"DWC3 GUCTL host-auto-retry anchor mismatch: {text.count(anchor)}")
    text = text.replace(
        anchor,
        anchor + "\n/* Global User Control Register */\n"
        "#define DWC3_GUCTL_HSTINAUTORETRY\tBIT(14)\n",
        1,
    )
    repairs.append("dwc3_core_h=restored-host-in-auto-retry-bit")

if "#define DWC3_GUCTL3_SPLITDISABLE" not in text:
    anchor = "/* Global User Control Register 3 */\n"
    if text.count(anchor) != 1:
        raise SystemExit(f"DWC3 GUCTL3 anchor mismatch: {text.count(anchor)}")
    text = text.replace(
        anchor,
        anchor + "#define DWC3_GUCTL3_SPLITDISABLE\t\tBIT(14)\n",
        1,
    )
    repairs.append("dwc3_core_h=restored-split-disable-bit")
dwc3_core_h.write_text(text)

# Two declarations from the upstream gadget changes were lost while retaining
# the code that uses them.
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

# Resolve three FunctionFS textual merge artifacts while preserving Samsung's
# surrounding implementation.
ffs = root / "drivers/usb/gadget/function/f_fs.c"
replace_once(
    ffs,
    "\t\tcase USB_SPEED_SUPER_PLUS:\n"
    "\t\tcase USB_SPEED_SUPER:\n"
    "\t\tcase USB_SPEED_SUPER_PLUS:\n",
    "\t\tcase USB_SPEED_SUPER:\n"
    "\t\tcase USB_SPEED_SUPER_PLUS:\n",
    "functionfs=removed-duplicate-super-plus-case",
)
replace_once(
    ffs,
    "\trv = mount_nodev(t, flags, &data, ffs_sb_fill);\n"
    "\tif (IS_ERR(rv) && data.ffs_data)\n"
    "\t\tffs_data_put(data.ffs_data);\n"
    "\t}\n\n"
    "\treturn rv;\n",
    "\trv = mount_nodev(t, flags, &data, ffs_sb_fill);\n"
    "\tif (IS_ERR(rv) && data.ffs_data)\n"
    "\t\tffs_data_put(data.ffs_data);\n\n"
    "\treturn rv;\n",
    "functionfs=removed-stray-mount-brace",
)
replace_once(
    ffs,
    "\tstruct ffs_data *ffs = ffs_opts->dev->ffs_data;\n"
    "\tint ret;\n",
    "\tstruct ffs_data *ffs_data;\n"
    "\tint ret;\n",
    "functionfs=restored-locked-ffs-data-declaration",
)

# Match the Linux 4.19.325 implementation, which accepts a 64-bit timeout.
xhci_h = root / "drivers/usb/host/xhci.h"
replace_once(
    xhci_h,
    "int xhci_handshake(void __iomem *ptr, u32 mask, u32 done, int usec);\n",
    "int xhci_handshake(void __iomem *ptr, u32 mask, u32 done, u64 timeout_us);\n",
    "xhci=matched-handshake-timeout-type",
)

# Postconditions for every compile blocker repaired by this pass.
event_text = event_timer.read_text()
if ".head = RB_ROOT" in event_text or ".next = NULL" in event_text:
    raise SystemExit("obsolete timerqueue_head initializer remains")
if event_text.count(".rb_root = RB_ROOT_CACHED") != 1:
    raise SystemExit("timerqueue_head cached-root initializer count is not one")

hub_text = hub.read_text()
if hub_text.count("bool retry_locked;") != 1:
    raise SystemExit("hub retry_locked declaration count is not one")

core_h_text = dwc3_core_h.read_text()
for symbol in ("DWC3_GUCTL_HSTINAUTORETRY", "DWC3_GUCTL3_SPLITDISABLE"):
    if core_h_text.count(f"#define {symbol}") != 1:
        raise SystemExit(f"DWC3 register symbol count is not one: {symbol}")

gadget_text = dwc3_gadget.read_text()
if gadget_text.count("struct dwc3_trb\t*tmp;") != 1:
    raise SystemExit("DWC3 tmp declaration count is not one")
if gadget_text.count("int request_status;") != 1:
    raise SystemExit("DWC3 request_status declaration count is not one")

ffs_text = ffs.read_text()
if "case USB_SPEED_SUPER_PLUS:\n\t\tcase USB_SPEED_SUPER:\n\t\tcase USB_SPEED_SUPER_PLUS:" in ffs_text:
    raise SystemExit("duplicate FunctionFS SuperSpeedPlus case remains")
if ffs_text.count("struct ffs_data *ffs_data;") < 1:
    raise SystemExit("FunctionFS ffs_data declaration is missing")

xhci_text = xhci_h.read_text()
if "xhci_handshake(void __iomem *ptr, u32 mask, u32 done, int usec)" in xhci_text:
    raise SystemExit("obsolete xHCI handshake prototype remains")
if xhci_text.count("xhci_handshake(void __iomem *ptr, u32 mask, u32 done, u64 timeout_us)") != 1:
    raise SystemExit("xHCI 64-bit handshake prototype count is not one")

tmp_report.write_text("\n".join(repairs) + "\n")
PY

git -C "$KERNEL_DIR" diff --check -- \
  drivers/soc/qcom/event_timer.c \
  drivers/usb/core/hub.c \
  drivers/usb/dwc3/core.h \
  drivers/usb/dwc3/gadget.c \
  drivers/usb/gadget/function/f_fs.c \
  drivers/usb/host/xhci.h

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  cat "$TMP_REPORT"
  printf 'result=linux-4.19.325-event-and-usb-compile-compatibility-repaired\n'
} | tee "$REPORT"
rm -f "$TMP_REPORT"

info "Linux $TARGET_VERSION Qualcomm event timer and USB compatibility repaired"
