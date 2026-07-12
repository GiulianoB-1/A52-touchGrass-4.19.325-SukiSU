#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/late-compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before late compile repair"

python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
report = Path(sys.argv[2])
repairs = []

# The merged FUSE header contains the stable bad-inode helpers, while Samsung's
# extension state bit remains the final assigned value. Append FUSE_I_BAD after
# that vendor bit so existing state-bit numbering is preserved.
path = root / "fs/fuse/fuse_i.h"
text = path.read_text()
start = text.index("/** FUSE inode state bits */")
end = text.index("\n};", start)
segment = text[start:end]
if "\tFUSE_I_BAD," not in segment:
    anchor = (
        "\t/** Can be filled in by open, to use direct I/O on this file. */\n"
        "\tFUSE_I_ATTR_FORCE_SYNC,"
    )
    replacement = (
        anchor
        + "\n\t/** Inode is unusable after a protocol or I/O failure. */\n"
        + "\tFUSE_I_BAD,"
    )
    if segment.count(anchor) != 1:
        raise SystemExit(
            f"FUSE inode-state anchor mismatch: found {segment.count(anchor)}"
        )
    segment = segment.replace(anchor, replacement, 1)
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("fs/fuse/fuse_i.h=restored-fuse-i-bad-state-bit")

final = path.read_text()
start = final.index("/** FUSE inode state bits */")
end = final.index("\n};", start)
segment = final[start:end]
if segment.count("\tFUSE_I_BAD,") != 1:
    raise SystemExit("FUSE_I_BAD definition validation failed")
if segment.index("FUSE_I_BAD") < segment.index("FUSE_I_ATTR_FORCE_SYNC"):
    raise SystemExit("FUSE_I_BAD must not renumber the Samsung extension bit")
if "set_bit(FUSE_I_BAD" not in final or "test_bit(FUSE_I_BAD" not in final:
    raise SystemExit("FUSE bad-inode helpers are missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- fs/fuse/fuse_i.h
info "Linux $TARGET_VERSION late compile mismatches repaired"
