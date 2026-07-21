#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/f2fs-cp-reason-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Restoring the Linux $TARGET_VERSION F2FS xattr checkpoint reason"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
path = root / "fs/f2fs/f2fs.h"
text = path.read_text()
repairs = []

start = text.index("enum cp_reason_type {")
end = text.index("\n};", start)
segment = text[start:end]

if "\tCP_XATTR_DIR," not in segment:
    old = "\tCP_RECOVER_DIR,\n\tNR_CP_REASON,"
    new = "\tCP_RECOVER_DIR,\n\tCP_XATTR_DIR,\n\tNR_CP_REASON,"
    count = segment.count(old)
    if count != 1:
        raise SystemExit(f"F2FS checkpoint reason anchor mismatch: {count}")
    segment = segment.replace(old, new, 1)
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("fs/f2fs/f2fs.h=restored-cp-xattr-dir-before-reason-count")

text = path.read_text()
start = text.index("enum cp_reason_type {")
end = text.index("\n};", start)
segment = text[start:end]
if segment.count("\tCP_XATTR_DIR,") != 1:
    raise SystemExit("CP_XATTR_DIR definition validation failed")
if segment.index("CP_XATTR_DIR") > segment.index("NR_CP_REASON"):
    raise SystemExit("CP_XATTR_DIR must precede NR_CP_REASON")

trace = (root / "include/trace/events/f2fs.h").read_text()
if "{ CP_XATTR_DIR," not in trace:
    raise SystemExit("F2FS trace mapping for CP_XATTR_DIR is missing")
file_c = (root / "fs/f2fs/file.c").read_text()
if "cp_reason = CP_XATTR_DIR;" not in file_c:
    raise SystemExit("F2FS xattr checkpoint consumer is missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "F2FS xattr checkpoint reason restored"
