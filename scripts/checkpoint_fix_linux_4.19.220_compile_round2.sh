#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.220
REPORT="$ARTIFACTS_DIR/compile-api-fix-round2-$TARGET_VERSION.txt"
HCI_SOCK="$KERNEL_DIR/net/bluetooth/hci_sock.c"
EVENT_TIMER="$KERNEL_DIR/drivers/soc/qcom/event_timer.c"
USB_HUB="$KERNEL_DIR/drivers/usb/core/hub.c"
DWC3_GADGET="$KERNEL_DIR/drivers/usb/dwc3/gadget.c"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"
for path in "$HCI_SOCK" "$EVENT_TIMER" "$USB_HUB" "$DWC3_GADGET"; do
  test -f "$path" || fail "Required source is missing: $path"
done

info "Repairing second Linux $TARGET_VERSION compile mismatch set"
python3 - "$HCI_SOCK" "$EVENT_TIMER" "$USB_HUB" "$DWC3_GADGET" "$REPORT" <<'PY'
from pathlib import Path
import sys

hci_sock = Path(sys.argv[1])
event_timer = Path(sys.argv[2])
usb_hub = Path(sys.argv[3])
dwc3_gadget = Path(sys.argv[4])
report = Path(sys.argv[5])
rows = []

# The Samsung source intentionally disables hci_sock_bind() and returns success.
# The stable merge activated the middle of the upstream implementation while its
# local declarations remained inside the vendor comment. Restore the exact vendor
# policy as a small explicit stub rather than importing a partial socket path.
text = hci_sock.read_text()
start_marker = "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n"
end_marker = "static int hci_sock_getname(struct socket *sock, struct sockaddr *addr,\n"
if text.count(start_marker) != 1 or text.count(end_marker) != 1:
    raise SystemExit("net/bluetooth/hci_sock.c: bind/getname function anchors are not unique")
start = text.index(start_marker)
end = text.index(end_marker, start)
old_region = text[start:end]
if "hdev = hci_pi(sk)->hdev;" not in old_region:
    raise SystemExit("net/bluetooth/hci_sock.c: expected partially activated bind body")
if "return 0;" not in old_region:
    raise SystemExit("net/bluetooth/hci_sock.c: vendor bind stub return is missing")
new_region = (
    "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n"
    "\t\t\t int addr_len)\n"
    "{\n"
    "\t/* Binding is intentionally disabled by the Samsung vendor tree. */\n"
    "\treturn 0;\n"
    "}\n\n"
)
text = text[:start] + new_region + text[end:]
if text.count("static int hci_sock_bind(") != 1:
    raise SystemExit("net/bluetooth/hci_sock.c: bind definition count is not one")
hci_sock.write_text(text)
rows.append("hci_sock_bind=vendor_stub_restored\n")

# timerqueue_head switched from separate head/next members to rb_root_cached.
# Keep the per-CPU Qualcomm queue and initialize it through the new field.
text = event_timer.read_text()
old_timer_head = (
    "static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {\n"
    "\t.head = RB_ROOT,\n"
    "\t.next = NULL,\n"
    "};\n"
)
new_timer_head = (
    "static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {\n"
    "\t.rb_root = RB_ROOT_CACHED,\n"
    "};\n"
)
if text.count(old_timer_head) != 1:
    raise SystemExit("drivers/soc/qcom/event_timer.c: legacy timerqueue initializer not found once")
text = text.replace(old_timer_head, new_timer_head, 1)
if text.count(".rb_root = RB_ROOT_CACHED") != 1:
    raise SystemExit("drivers/soc/qcom/event_timer.c: cached RB root initializer count is not one")
event_timer.write_text(text)
rows.append("event_timer_queue=rb_root_cached\n")

# The upstream address0 serialization logic was merged, but its local state flag
# was dropped from the Samsung function declaration block.
text = usb_hub.read_text()
hub_anchor = "\tstatic int unreliable_port = -1;\n"
if text.count(hub_anchor) != 1:
    raise SystemExit("drivers/usb/core/hub.c: unreliable_port anchor not found once")
if text.count("retry_locked") != 3:
    raise SystemExit("drivers/usb/core/hub.c: expected three retry_locked uses")
if "\tbool retry_locked;\n" in text:
    raise SystemExit("drivers/usb/core/hub.c: retry_locked declaration already exists")
text = text.replace(hub_anchor, hub_anchor + "\tbool retry_locked;\n", 1)
usb_hub.write_text(text)
rows.append("usb_hub_retry_locked=declaration_restored\n")

# Preserve the Samsung HWO-based TRB-ring fullness check. The merge retained the
# check but dropped the temporary TRB pointer declaration.
text = dwc3_gadget.read_text()
gadget_anchor = (
    "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
    "{\n"
    "\tu8\t\t\ttrbs_left;\n"
)
gadget_fixed = (
    "static u32 dwc3_calc_trbs_left(struct dwc3_ep *dep)\n"
    "{\n"
    "\tstruct dwc3_trb\t\t*tmp;\n"
    "\tu8\t\t\ttrbs_left;\n"
)
if text.count(gadget_anchor) != 1:
    raise SystemExit("drivers/usb/dwc3/gadget.c: TRB count declaration anchor not found once")
if text.count("tmp = dwc3_ep_prev_trb(dep, dep->trb_enqueue);") != 1:
    raise SystemExit("drivers/usb/dwc3/gadget.c: Samsung previous-TRB check is missing")
text = text.replace(gadget_anchor, gadget_fixed, 1)
if text.count("struct dwc3_trb\t\t*tmp;") != 1:
    raise SystemExit("drivers/usb/dwc3/gadget.c: temporary TRB pointer count is not one")
dwc3_gadget.write_text(text)
rows.append("dwc3_trb_pointer=declaration_restored\n")

report.write_text("".join(rows))
PY

git -C "$KERNEL_DIR" diff --check
cat "$REPORT"
info "Second Linux $TARGET_VERSION compile mismatch set repaired"
