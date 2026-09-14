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

# Two declarations from the upstream gadget changes can be lost in the
# Samsung/upstream merge. Validate them structurally instead of depending on
# Samsung's tab alignment.
dwc3_gadget = root / "drivers/usb/dwc3/gadget.c"
text = dwc3_gadget.read_text()

calc_sig = "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)"
calc_start = text.index(calc_sig)
calc_end = text.index("\nstatic void __dwc3_prepare_one_trb", calc_start)
calc = text[calc_start:calc_end]

if "tmp = dwc3_ep_prev_trb(" in calc:
    if "struct dwc3_trb" not in calc or "*tmp;" not in calc:
        decl_anchor = "{\n"
        calc = calc.replace(
            decl_anchor,
            decl_anchor + "\tstruct dwc3_trb\t\t*tmp;\n",
            1,
        )
        text = text[:calc_start] + calc + text[calc_end:]
        repairs.append("dwc3_gadget=restored-previous-trb-declaration")
else:
    # A pure 4.19.325 merge uses request/num_trbs instead of the previous-TRB
    # test and therefore correctly has no tmp variable.
    if "req = next_request(&dep->started_list);" not in calc:
        raise SystemExit("dwc3_calc_trbs_left has an unrecognized merge shape")

cleanup_sig = "static int dwc3_gadget_ep_cleanup_completed_request(struct dwc3_ep *dep,"
cleanup_start = text.index(cleanup_sig)
cleanup_end = text.index(
    "\nstatic void dwc3_gadget_ep_cleanup_completed_requests", cleanup_start
)
cleanup = text[cleanup_start:cleanup_end]

if "request_status =" in cleanup and "int request_status;" not in cleanup:
    ret_anchor = "\tint ret;\n"
    if cleanup.count(ret_anchor) != 1:
        raise SystemExit(
            f"DWC3 cleanup ret declaration anchor mismatch: {cleanup.count(ret_anchor)}"
        )
    cleanup = cleanup.replace(
        ret_anchor,
        "\tint request_status;\n" + ret_anchor,
        1,
    )
    text = text[:cleanup_start] + cleanup + text[cleanup_end:]
    repairs.append("dwc3_gadget=restored-request-status-declaration")
elif "request_status =" in cleanup and cleanup.count("int request_status;") != 1:
    raise SystemExit("DWC3 request_status declaration count is invalid")

dwc3_gadget.write_text(text)

# Resolve/validate FunctionFS merge artifacts structurally. The current
# 4.19.206 -> 4.19.325 merge often already contains the correct Samsung form,
# so do not require exact historical text from the older uplift attempt.
ffs = root / "drivers/usb/gadget/function/f_fs.c"
text = ffs.read_text()

# Endpoint descriptor speed switch: exactly one SUPER and one SUPER_PLUS case.
desc_marker = "case FUNCTIONFS_ENDPOINT_DESC:"
desc_start = text.index(desc_marker)
desc_end = text.index("\n\tdefault:", desc_start)
desc = text[desc_start:desc_end]
dup = (
    "\t\tcase USB_SPEED_SUPER_PLUS:\n"
    "\t\tcase USB_SPEED_SUPER:\n"
    "\t\tcase USB_SPEED_SUPER_PLUS:\n"
)
if dup in desc:
    desc = desc.replace(
        dup,
        "\t\tcase USB_SPEED_SUPER:\n"
        "\t\tcase USB_SPEED_SUPER_PLUS:\n",
        1,
    )
    text = text[:desc_start] + desc + text[desc_end:]
    repairs.append("functionfs=removed-duplicate-super-plus-case")
elif desc.count("case USB_SPEED_SUPER_PLUS:") != 1 or desc.count("case USB_SPEED_SUPER:") != 1:
    raise SystemExit(
        "FunctionFS endpoint descriptor speed switch has an unexpected merge shape"
    )

# ffs_fs_mount(): remove the historical stray closing brace only if present.
mount_sig = "ffs_fs_mount(struct file_system_type *t, int flags,"
mount_start = text.index(mount_sig)
mount_end = text.index("\nstatic void\nffs_fs_kill_sb", mount_start)
mount = text[mount_start:mount_end]
stray = (
    "\trv = mount_nodev(t, flags, &data, ffs_sb_fill);\n"
    "\tif (IS_ERR(rv) && data.ffs_data)\n"
    "\t\tffs_data_put(data.ffs_data);\n"
    "\t}\n\n"
    "\treturn rv;\n"
)
fixed_mount = (
    "\trv = mount_nodev(t, flags, &data, ffs_sb_fill);\n"
    "\tif (IS_ERR(rv) && data.ffs_data)\n"
    "\t\tffs_data_put(data.ffs_data);\n\n"
    "\treturn rv;\n"
)
if stray in mount:
    mount = mount.replace(stray, fixed_mount, 1)
    text = text[:mount_start] + mount + text[mount_end:]
    repairs.append("functionfs=removed-stray-mount-brace")
elif "rv = mount_nodev(t, flags, &data, ffs_sb_fill);" not in mount or "\treturn rv;\n" not in mount:
    raise SystemExit("FunctionFS mount path has an unexpected merge shape")

# The bind path may declare ffs_data alone or together with Samsung's ffs
# variable. Accept either, but ensure the local exists before it is assigned.
bind_sig = "static inline struct f_fs_opts *ffs_do_functionfs_bind"
bind_start = text.index(bind_sig)
bind_end = text.index("\nstatic int ffs_func_bind", bind_start)
bind = text[bind_start:bind_end]
if "*ffs_data;" not in bind:
    legacy = "\tstruct ffs_data *ffs = ffs_opts->dev->ffs_data;\n"
    if legacy in bind:
        bind = bind.replace(
            legacy,
            "\tstruct ffs_data *ffs_data;\n",
            1,
        )
        text = text[:bind_start] + bind + text[bind_end:]
        repairs.append("functionfs=restored-locked-ffs-data-declaration")
    else:
        raise SystemExit("FunctionFS bind path uses ffs_data without a declaration")
if "ffs_data = ffs_opts->dev->ffs_data;" not in bind and "ffs_data = ffs_opts->dev->ffs_data;" not in text[bind_start:bind_end]:
    raise SystemExit("FunctionFS bind path does not snapshot ffs_data")

ffs.write_text(text)

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
calc_start = gadget_text.index("static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)")
calc_end = gadget_text.index("\nstatic void __dwc3_prepare_one_trb", calc_start)
calc = gadget_text[calc_start:calc_end]
if "tmp = dwc3_ep_prev_trb(" in calc:
    if "struct dwc3_trb" not in calc or "*tmp;" not in calc:
        raise SystemExit("DWC3 previous-TRB path uses tmp without a declaration")

cleanup_start = gadget_text.index(
    "static int dwc3_gadget_ep_cleanup_completed_request(struct dwc3_ep *dep,"
)
cleanup_end = gadget_text.index(
    "\nstatic void dwc3_gadget_ep_cleanup_completed_requests", cleanup_start
)
cleanup = gadget_text[cleanup_start:cleanup_end]
if "request_status =" in cleanup and cleanup.count("int request_status;") != 1:
    raise SystemExit("DWC3 request_status declaration count is not one")

ffs_text = ffs.read_text()
desc_start = ffs_text.index("case FUNCTIONFS_ENDPOINT_DESC:")
desc_end = ffs_text.index("\n\tdefault:", desc_start)
desc = ffs_text[desc_start:desc_end]
if desc.count("case USB_SPEED_SUPER_PLUS:") != 1:
    raise SystemExit("FunctionFS SuperSpeedPlus case count is not one")
if desc.count("case USB_SPEED_SUPER:") != 1:
    raise SystemExit("FunctionFS SuperSpeed case count is not one")

mount_start = ffs_text.index("ffs_fs_mount(struct file_system_type *t, int flags,")
mount_end = ffs_text.index("\nstatic void\nffs_fs_kill_sb", mount_start)
mount = ffs_text[mount_start:mount_end]
if "rv = mount_nodev(t, flags, &data, ffs_sb_fill);" not in mount or "\treturn rv;\n" not in mount:
    raise SystemExit("FunctionFS mount postcondition failed")

bind_start = ffs_text.index("static inline struct f_fs_opts *ffs_do_functionfs_bind")
bind_end = ffs_text.index("\nstatic int ffs_func_bind", bind_start)
bind = ffs_text[bind_start:bind_end]
if "*ffs_data;" not in bind:
    raise SystemExit("FunctionFS ffs_data local declaration is missing")
if "ffs_data = ffs_opts->dev->ffs_data;" not in bind:
    raise SystemExit("FunctionFS ffs_data snapshot is missing")

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
