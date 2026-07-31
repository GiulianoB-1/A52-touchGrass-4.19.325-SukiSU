#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.210
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
MAIN_C="$KERNEL_DIR/init/main.c"
REPORT="$ARTIFACTS_DIR/hardware-test-instrumentation-$TARGET_VERSION.txt"
MARKER='TOUCHGRASS HARDWARE TEST: Linux 4.19.210 UN1CA checkpoint'

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"
test -f "$DEFCONFIG" || fail "a52xq defconfig is missing"
test -f "$MAIN_C" || fail "init/main.c is missing"

info "Increasing last_kmsg capacity and adding a unique early boot marker"
python3 - "$DEFCONFIG" "$MAIN_C" "$REPORT" "$MARKER" <<'PY'
from pathlib import Path
import sys

defconfig = Path(sys.argv[1])
main_c = Path(sys.argv[2])
report = Path(sys.argv[3])
marker = sys.argv[4]

lines = [line for line in defconfig.read_text().splitlines()
         if not line.startswith("CONFIG_LOG_BUF_SHIFT=")]
lines.append("CONFIG_LOG_BUF_SHIFT=21")
defconfig.write_text("\n".join(lines) + "\n")

text = main_c.read_text()
anchor = '\tpr_notice("%s", linux_banner);\n'
insert = anchor + f'\tpr_emerg("{marker}\\n");\n'
if marker not in text:
    if text.count(anchor) != 1:
        raise SystemExit(f"init/main.c: expected one Linux banner anchor, found {text.count(anchor)}")
    text = text.replace(anchor, insert, 1)
    main_c.write_text(text)

report.write_text(
    "kernel_version=4.19.210\n"
    "log_buf_shift=21\n"
    "log_buffer_bytes=2097152\n"
    f"boot_marker={marker}\n"
)
PY

grep -Fxq 'CONFIG_LOG_BUF_SHIFT=21' "$DEFCONFIG" || fail "Large kernel log buffer was not enabled"
grep -Fq "$MARKER" "$MAIN_C" || fail "Unique boot marker was not inserted"
git -C "$KERNEL_DIR" diff --check
cat "$REPORT"
