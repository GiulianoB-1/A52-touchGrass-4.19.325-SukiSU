#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/pageio-reboot-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying ext4 page-I/O and reboot parser compatibility repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: {count}")
    return text.replace(old, new, 1)


# io_submit_add_bh() accounts writeback against the original pagecache page,
# while the bio may contain the encrypted bounce page. Pass both separately.
path = root / "fs/ext4/page-io.c"
text = path.read_text()
old = "\t\tret = io_submit_add_bh(io, inode, bounce_page ?: page, bh);\n"
new = "\t\tret = io_submit_add_bh(io, inode, page, bounce_page, bh);\n"
if old in text:
    text = replace_once(text, old, new, "ext4 page-I/O submission")
    path.write_text(text)
    repairs.append("fs/ext4/page-io.c=passed-pagecache-and-bounce-pages")

# The Samsung reboot parser scopes the temporary conversion result inside
# case 's'. The direct merge retained the opening brace but dropped its close,
# causing all later cases to be parsed inside the block.
path = root / "kernel/reboot.c"
text = path.read_text()
old = (
    "\t\t\t} else\n"
    "\t\t\t\t*mode = REBOOT_SOFT;\n"
    "\t\t\tbreak;\n"
    "\n"
    "\t\tcase 'g':\n"
)
new = (
    "\t\t\t} else\n"
    "\t\t\t\t*mode = REBOOT_SOFT;\n"
    "\t\t\tbreak;\n"
    "\t\t}\n"
    "\n"
    "\t\tcase 'g':\n"
)
if old in text:
    text = replace_once(text, old, new, "reboot case-s closing brace")
    path.write_text(text)
    repairs.append("kernel/reboot.c=closed-case-s-scope")

# Exact postconditions.
pageio = (root / "fs/ext4/page-io.c").read_text()
if pageio.count("io_submit_add_bh(io, inode, page, bounce_page, bh)") != 1:
    raise SystemExit("ext4 page-I/O call repair validation failed")
if "io_submit_add_bh(io, inode, bounce_page ?: page, bh)" in pageio:
    raise SystemExit("obsolete ext4 page-I/O call remains")

reboot = (root / "kernel/reboot.c").read_text()
case_start = reboot.index("\t\tcase 's':")
case_end = reboot.index("\n\t\tcase 'g':", case_start)
case_segment = reboot[case_start:case_end]
if case_segment.count("{") != case_segment.count("}"):
    raise SystemExit("reboot case-s scope remains unbalanced")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "ext4 page-I/O and reboot parser compatibility repairs applied"
