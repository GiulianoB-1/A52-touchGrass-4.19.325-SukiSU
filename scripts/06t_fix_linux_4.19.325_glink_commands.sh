#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/glink-command-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before GLINK repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
glink = root / "drivers/rpmsg/qcom_glink_native.c"
text = glink.read_text()

rpm_tail = (
    "#define RPM_CMD_READ_NOTIF\t\t13\n"
    "#define RPM_CMD_RX_DONE_W_REUSE\t\t14\n"
    "#define RPM_CMD_SIGNALS\t\t\t15\n"
)
aliases = (
    "\n"
    "/* Upstream GLINK names share the Samsung RPM wire command values. */\n"
    "#define GLINK_CMD_VERSION\t\tRPM_CMD_VERSION\n"
    "#define GLINK_CMD_VERSION_ACK\t\tRPM_CMD_VERSION_ACK\n"
    "#define GLINK_CMD_OPEN\t\t\tRPM_CMD_OPEN\n"
    "#define GLINK_CMD_CLOSE\t\t\tRPM_CMD_CLOSE\n"
    "#define GLINK_CMD_OPEN_ACK\t\tRPM_CMD_OPEN_ACK\n"
    "#define GLINK_CMD_INTENT\t\tRPM_CMD_INTENT\n"
    "#define GLINK_CMD_RX_DONE\t\tRPM_CMD_RX_DONE\n"
    "#define GLINK_CMD_RX_INTENT_REQ\tRPM_CMD_RX_INTENT_REQ\n"
    "#define GLINK_CMD_RX_INTENT_REQ_ACK\tRPM_CMD_RX_INTENT_REQ_ACK\n"
    "#define GLINK_CMD_TX_DATA\t\tRPM_CMD_TX_DATA\n"
    "#define GLINK_CMD_CLOSE_ACK\t\tRPM_CMD_CLOSE_ACK\n"
    "#define GLINK_CMD_TX_DATA_CONT\t\tRPM_CMD_TX_DATA_CONT\n"
    "#define GLINK_CMD_READ_NOTIF\t\tRPM_CMD_READ_NOTIF\n"
    "#define GLINK_CMD_RX_DONE_W_REUSE\tRPM_CMD_RX_DONE_W_REUSE\n"
)

if aliases not in text:
    if text.count(rpm_tail) != 1:
        raise SystemExit(f"GLINK RPM command-table anchor mismatch: {text.count(rpm_tail)}")
    text = text.replace(rpm_tail, rpm_tail + aliases, 1)
    glink.write_text(text)
elif text.count(aliases) != 1:
    raise SystemExit("unexpected GLINK command alias block count")

final = glink.read_text()
if final.count(aliases) != 1:
    raise SystemExit("GLINK command aliases were not installed exactly once")

for definition in aliases.splitlines():
    if definition.startswith("#define GLINK_CMD_"):
        if final.count(definition + "\n") != 1:
            raise SystemExit(
                f"GLINK command definition postcondition failed: {definition}"
            )

if final.count("#define RPM_CMD_SIGNALS") != 1 or "case RPM_CMD_SIGNALS:" not in final:
    raise SystemExit("Samsung GLINK signals extension was not preserved")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/rpmsg/qcom_glink_native.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'glink=aliased-upstream-command-names-to-samsung-wire-values\n'
  printf 'signals=preserved-samsung-command-15-extension\n'
  printf 'result=linux-4.19.325-glink-command-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION GLINK command compatibility repaired"
