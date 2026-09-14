#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/synclink-whitespace-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before whitespace cleanup"

python3 - "$KERNEL_DIR/drivers/tty/synclink_gt.c" "$REPORT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = Path(sys.argv[2])
text = path.read_text()
repairs = []

# Linux 4.19.325 itself contains two mixed space/tab indentation lines in
# synclink_gt.c. They are behaviorally harmless but make git diff --check fail
# when the stable delta is applied onto the Samsung tree.
fixes = (
    ("\t \tset_gtsignals(info);", "\t\tset_gtsignals(info);",
     "set_gtsignals-indent"),
    (" \tget_gtsignals(info);", "\tget_gtsignals(info);",
     "get_gtsignals-indent"),
)

for old, new, label in fixes:
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1:
        text = text.replace(old, new, 1)
        repairs.append(label)
    elif old_count == 0 and new_count >= 1:
        pass
    else:
        raise SystemExit(
            f"synclink whitespace {label} mismatch: old={old_count}, new={new_count}"
        )

path.write_text(text)

final = path.read_text()
for old, new, label in fixes:
    if old in final:
        raise SystemExit(f"synclink whitespace remains for {label}")
    if new not in final:
        raise SystemExit(f"synclink cleaned form missing for {label}")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/tty/synclink_gt.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'scope=drivers/tty/synclink_gt.c\n'
  printf 'behavior_change=none\n'
  printf 'result=linux-4.19.325-upstream-whitespace-cleaned\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION upstream synclink whitespace cleaned"
