#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/elevator-fuse-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying block elevator and FUSE inode-state compatibility repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []

# The Samsung BFQ-aware elevator_init_mq() retained early jumps to the label
# used by an older locking variant. The merged function has only the upstream
# out: return path, and no lock is taken here, so all early exits target out.
path = root / "block/elevator.c"
text = path.read_text()
start = text.index("int elevator_init_mq(struct request_queue *q)")
end = text.index("\n\n\n/*\n * switch to new_e", start)
segment = text[start:end]
count = segment.count("goto out_unlock;")
if count:
    if count != 3:
        raise SystemExit(f"unexpected elevator out_unlock jump count: {count}")
    segment = segment.replace("goto out_unlock;", "goto out;")
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("block/elevator.c=redirected-mq-early-exits-to-out")

# FUSE gained helpers that mark an inode unusable, but the corresponding state
# bit was lost. Append it after Samsung's existing extension bit to preserve all
# previously assigned vendor state-bit numbers.
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
    replacement = anchor + "\n\t/** Inode is unusable after a protocol or I/O failure. */\n\tFUSE_I_BAD,"
    if segment.count(anchor) != 1:
        raise SystemExit("FUSE inode-state anchor mismatch")
    segment = segment.replace(anchor, replacement, 1)
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("fs/fuse/fuse_i.h=restored-fuse-i-bad-state-bit")

# Exact postconditions.
elevator = (root / "block/elevator.c").read_text()
start = elevator.index("int elevator_init_mq(struct request_queue *q)")
end = elevator.index("\n\n\n/*\n * switch to new_e", start)
segment = elevator[start:end]
if "goto out_unlock;" in segment:
    raise SystemExit("obsolete elevator out_unlock jump remains")
if segment.count("goto out;") != 3 or segment.count("\nout:\n") != 1:
    raise SystemExit("elevator_init_mq exit-path validation failed")
if 'elevator_get(q, "bfq", false)' not in segment:
    raise SystemExit("Samsung BFQ selection was lost")

fuse = (root / "fs/fuse/fuse_i.h").read_text()
start = fuse.index("/** FUSE inode state bits */")
end = fuse.index("\n};", start)
segment = fuse[start:end]
if segment.count("\tFUSE_I_BAD,") != 1:
    raise SystemExit("FUSE_I_BAD definition validation failed")
if segment.index("FUSE_I_BAD") < segment.index("FUSE_I_ATTR_FORCE_SYNC"):
    raise SystemExit("FUSE_I_BAD must not renumber the Samsung extension bit")
if "set_bit(FUSE_I_BAD" not in fuse or "test_bit(FUSE_I_BAD" not in fuse:
    raise SystemExit("FUSE bad-inode helpers are missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "block elevator and FUSE inode-state compatibility repairs applied"
