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

# Validate the exact post-merge state before allowing the build to continue.
event_text = event_timer.read_text()
if ".head = RB_ROOT" in event_text or ".next = NULL" in event_text:
    raise SystemExit("obsolete timerqueue_head initializer remains")
if event_text.count(".rb_root = RB_ROOT_CACHED") != 1:
    raise SystemExit("timerqueue_head cached-root initializer count is not one")

hub_text = hub.read_text()
if hub_text.count("bool retry_locked;") != 1:
    raise SystemExit("hub retry_locked declaration count is not one")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  drivers/soc/qcom/event_timer.c \
  drivers/usb/core/hub.c

info "Linux $TARGET_VERSION Qualcomm event timer and USB compatibility repaired"
