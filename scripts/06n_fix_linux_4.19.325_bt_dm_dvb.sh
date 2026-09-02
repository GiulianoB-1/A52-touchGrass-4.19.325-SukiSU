#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/bt-dm-dvb-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before Bluetooth/DM/DVB repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))


def replace_between(path: Path, start_marker: str, end_marker: str,
                    replacement: str, label: str) -> None:
    text = path.read_text()
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count != 1 or end_count != 1:
        raise SystemExit(
            f"{label}: marker mismatch start={start_count} end={end_count}"
        )
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    if end <= start:
        raise SystemExit(f"{label}: invalid marker ordering")
    path.write_text(text[:start] + replacement + text[end:])


# The touchGrass baseline intentionally disables these Bluetooth socket entry
# points by commenting out their bodies and returning success. Later stable
# comments nested inside those broad comments and exposed code whose local
# declarations remained commented. Preserve the released Samsung behavior with
# explicit, syntactically safe stubs instead of fragile nested comments.
hci = root / "net/bluetooth/hci_sock.c"
replace_between(
    hci,
    "static int hci_sock_ioctl(struct socket *sock, unsigned int cmd,\n",
    "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n",
    "static int hci_sock_ioctl(struct socket *sock, unsigned int cmd,\n"
    "\t\t\t  unsigned long arg)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n\n",
    "Bluetooth ioctl stub",
)
replace_between(
    hci,
    "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n",
    "static int hci_sock_getname(struct socket *sock, struct sockaddr *addr,\n",
    "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,\n"
    "\t\t\t int addr_len)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n\n",
    "Bluetooth bind stub",
)
replace_between(
    hci,
    "static int hci_sock_getname(struct socket *sock, struct sockaddr *addr,\n",
    "static void hci_sock_cmsg(struct sock *sk, struct msghdr *msg,\n",
    "static int hci_sock_getname(struct socket *sock, struct sockaddr *addr,\n"
    "\t\t\t    int peer)\n"
    "{\n"
    "\treturn 0;\n"
    "}\n\n",
    "Bluetooth getname stub",
)

hci_text = hci.read_text()
for signature in (
    "static int hci_sock_ioctl(struct socket *sock, unsigned int cmd,",
    "static int hci_sock_bind(struct socket *sock, struct sockaddr *addr,",
    "static int hci_sock_getname(struct socket *sock, struct sockaddr *addr,",
):
    if hci_text.count(signature) != 1:
        raise SystemExit(f"unexpected Bluetooth stub count for {signature}")

# Linux 4.19.325 limits in-flight swap bios per target. The direct merge kept
# Samsung's inline-crypto capability bit but dropped this upstream bitfield.
dm_header = root / "include/linux/device-mapper.h"
dm_text = dm_header.read_text()
if "bool limit_swap_bios:1;" not in dm_text:
    anchor = (
        "\t/*\n"
        "\t * Set if inline crypto capabilities from this target's underlying\n"
        "\t * device(s) can be exposed via the device-mapper device.\n"
        "\t */\n"
        "\tbool may_passthrough_inline_crypto:1;\n"
    )
    replacement = (
        "\t/*\n"
        "\t * Set if we need to limit the number of in-flight bios when swapping.\n"
        "\t */\n"
        "\tbool limit_swap_bios:1;\n\n"
        + anchor
    )
    replace_once(dm_header, anchor, replacement, "device-mapper swap bio limit")
elif dm_text.count("bool limit_swap_bios:1;") != 1:
    raise SystemExit("unexpected device-mapper limit_swap_bios count")

# The Samsung DVB extension still queries demux capabilities, but the merged
# declaration list was replaced by upstream's error-return variable list.
dmxdev = root / "drivers/media/dvb-core/dmxdev.c"
dmx_text = dmxdev.read_text()
func_start = dmx_text.index(
    "int dvb_dmxdev_init(struct dmxdev *dmxdev, struct dvb_adapter *dvb_adapter)"
)
func_end = dmx_text.index("EXPORT_SYMBOL(dvb_dmxdev_init);", func_start)
segment = dmx_text[func_start:func_end]
if "struct dmx_caps caps;" not in segment:
    declaration = "\tint i, ret;\n"
    if segment.count(declaration) != 1:
        raise SystemExit(
            f"DVB init declaration anchor mismatch: {segment.count(declaration)}"
        )
    segment = segment.replace(
        declaration,
        declaration + "\tstruct dmx_caps caps;\n",
        1,
    )
    dmx_text = dmx_text[:func_start] + segment + dmx_text[func_end:]
    dmxdev.write_text(dmx_text)
elif segment.count("struct dmx_caps caps;") != 1:
    raise SystemExit("unexpected DVB capability declaration count")

# Exact postconditions.
if (root / "include/linux/device-mapper.h").read_text().count(
    "bool limit_swap_bios:1;"
) != 1:
    raise SystemExit("device-mapper swap bio field repair failed")
updated_dmx = dmxdev.read_text()
updated_start = updated_dmx.index(
    "int dvb_dmxdev_init(struct dmxdev *dmxdev, struct dvb_adapter *dvb_adapter)"
)
updated_end = updated_dmx.index("EXPORT_SYMBOL(dvb_dmxdev_init);", updated_start)
if updated_dmx[updated_start:updated_end].count("struct dmx_caps caps;") != 1:
    raise SystemExit("DVB capability declaration repair failed")
PY

git -C "$KERNEL_DIR" diff --check -- \
  net/bluetooth/hci_sock.c \
  include/linux/device-mapper.h \
  drivers/media/dvb-core/dmxdev.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'bluetooth=restored-explicit-touchgrass-ioctl-bind-getname-stubs\n'
  printf 'device_mapper=restored-limit-swap-bios-target-field\n'
  printf 'dvb=restored-demux-capability-local-declaration\n'
  printf 'result=linux-4.19.325-bluetooth-dm-dvb-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION Bluetooth, device-mapper and DVB compatibility repaired"
